"""
Marketing lead capture and admin lead inbox for CoopMS.
"""

import csv
import io
import json
import os
import time
from datetime import datetime
from urllib.parse import urlparse

from flask import Blueprint, Response, abort, current_app, jsonify, redirect, render_template, request, url_for, flash
from flask_login import current_user, login_required
from markupsafe import escape

from database import get_db, last_insert_id
from email_service import send_email
from extensions import csrf
from utils import audit, role_required


marketing = Blueprint('marketing', __name__)

LEAD_STATUSES = ('new', 'contacted', 'demo_booked', 'proposal_sent', 'won', 'lost')
_RECENT_SUBMISSIONS = {}


def marketing_hq_enabled() -> bool:
    return os.environ.get('MARKETING_HQ', '0') == '1'


def _require_marketing_hq():
    if not marketing_hq_enabled():
        abort(404)


def _client_ip() -> str:
    forwarded = request.headers.get('X-Forwarded-For', '')
    if forwarded:
        return forwarded.split(',')[0].strip()[:80]
    return (request.remote_addr or '')[:80]


def _rate_limited(ip: str) -> bool:
    now = time.time()
    window_seconds = 15 * 60
    max_hits = 8
    hits = [ts for ts in _RECENT_SUBMISSIONS.get(ip, []) if now - ts < window_seconds]
    if len(hits) >= max_hits:
        _RECENT_SUBMISSIONS[ip] = hits
        return True
    hits.append(now)
    _RECENT_SUBMISSIONS[ip] = hits
    return False


def _clean(value, limit=500):
    return (str(value or '').strip())[:limit]


def _origin_allowed() -> bool:
    origin = request.headers.get('Origin') or request.headers.get('Referer') or ''
    if not origin:
        return True
    origin_host = urlparse(origin).netloc.lower()
    request_host = (request.host or '').lower()
    allowed = {h.strip().lower() for h in os.environ.get('MARKETING_ALLOWED_ORIGINS', '').split(',') if h.strip()}
    allowed.update({request_host, 'cooperativems.com', 'www.cooperativems.com'})
    return origin_host in allowed


def _notification_recipients(db):
    configured = os.environ.get('MARKETING_LEAD_NOTIFY_EMAIL', '').strip()
    if configured:
        return [x.strip() for x in configured.split(',') if x.strip()]
    settings = {
        row['key']: row['value']
        for row in db.execute(
            "SELECT key, value FROM settings WHERE key IN ('subscription_email', 'email')"
        ).fetchall()
    }
    candidates = [settings.get('subscription_email', ''), settings.get('email', '')]
    admins = db.execute(
        "SELECT email FROM users WHERE role = 'admin' AND is_active = 1 AND email IS NOT NULL AND email != ''"
    ).fetchall()
    candidates.extend([row['email'] for row in admins])
    seen = set()
    recipients = []
    for email in candidates:
        email = (email or '').strip()
        if email and email.lower() not in seen:
            recipients.append(email)
            seen.add(email.lower())
    return recipients[:5]


def _send_lead_alert(db, lead):
    recipients = _notification_recipients(db)
    if not recipients:
        return
    subject = f"New CoopMS demo lead - {lead['society_name']}"
    safe = {k: escape(lead.get(k) or '') for k in lead.keys()}
    html = f"""
    <h2>New CoopMS demo request</h2>
    <p>A new lead has been captured from the public CoopMS website.</p>
    <table cellpadding="6" cellspacing="0" style="border-collapse:collapse;">
      <tr><td><strong>Name</strong></td><td>{safe['full_name']}</td></tr>
      <tr><td><strong>Email</strong></td><td>{safe['email']}</td></tr>
      <tr><td><strong>Phone</strong></td><td>{safe['phone']}</td></tr>
      <tr><td><strong>Cooperative</strong></td><td>{safe['society_name']}</td></tr>
      <tr><td><strong>Type</strong></td><td>{safe['society_type']}</td></tr>
      <tr><td><strong>Members</strong></td><td>{safe['member_count']}</td></tr>
      <tr><td><strong>Priority</strong></td><td>{safe['priority']}</td></tr>
      <tr><td><strong>Source</strong></td><td>{safe['utm_source'] or 'direct'}</td></tr>
    </table>
    <p><strong>Message</strong><br>{safe['message'].replace(chr(10), '<br>')}</p>
    <p><a href="{url_for('marketing.leads_inbox', _external=True)}">Open Lead Inbox</a></p>
    """
    for recipient in recipients:
        send_email(recipient, subject, html, background=True)


@marketing.route('/marketing/leads')
@login_required
@role_required('admin', 'secretary')
def leads_inbox():
    _require_marketing_hq()
    db = get_db()
    status = request.args.get('status', '').strip()
    q = request.args.get('q', '').strip()
    clauses = []
    params = []
    if status in LEAD_STATUSES:
        clauses.append('status = ?')
        params.append(status)
    if q:
        like = f'%{q}%'
        clauses.append('(full_name LIKE ? OR email LIKE ? OR society_name LIKE ? OR phone LIKE ?)')
        params.extend([like, like, like, like])
    where = ('WHERE ' + ' AND '.join(clauses)) if clauses else ''
    leads = db.execute(f'''
        SELECT *
        FROM marketing_leads
        {where}
        ORDER BY created_at DESC
        LIMIT 500
    ''', params).fetchall()
    stats = db.execute('''
        SELECT status, COUNT(*) AS count
        FROM marketing_leads
        GROUP BY status
    ''').fetchall()
    status_counts = {row['status']: row['count'] for row in stats}
    return render_template(
        'marketing/leads.html',
        leads=leads,
        statuses=LEAD_STATUSES,
        status_counts=status_counts,
        current_status=status,
        q=q,
    )


@marketing.route('/marketing/leads/<int:lead_id>/status', methods=['POST'])
@login_required
@role_required('admin', 'secretary')
def update_lead_status(lead_id):
    _require_marketing_hq()
    db = get_db()
    status = request.form.get('status', '').strip()
    notes = _clean(request.form.get('notes'), 2000)
    if status not in LEAD_STATUSES:
        flash('Invalid lead status.', 'danger')
        return redirect(url_for('marketing.leads_inbox'))
    lead = db.execute('SELECT * FROM marketing_leads WHERE id = ?', (lead_id,)).fetchone()
    if not lead:
        flash('Lead not found.', 'danger')
        return redirect(url_for('marketing.leads_inbox'))
    db.execute('''
        UPDATE marketing_leads
           SET status = ?, notes = ?, updated_at = ?
         WHERE id = ?
    ''', (status, notes, datetime.now(), lead_id))
    db.execute('''
        INSERT INTO marketing_lead_events
            (lead_id, event_type, description, actor_user_id, actor_username, data)
        VALUES (?, 'status_update', ?, ?, ?, ?)
    ''', (
        lead_id,
        f"Status changed to {status}",
        current_user.id,
        current_user.username,
        json.dumps({'status': status, 'notes': notes}),
    ))
    audit(db, 'UPDATE_MARKETING_LEAD', 'marketing', f'Updated lead #{lead_id} to {status}')
    db.commit()
    flash('Lead updated.', 'success')
    return redirect(url_for('marketing.leads_inbox'))


@marketing.route('/marketing/leads/<int:lead_id>')
@login_required
@role_required('admin', 'secretary')
def lead_detail(lead_id):
    _require_marketing_hq()
    db = get_db()
    lead = db.execute('SELECT * FROM marketing_leads WHERE id = ?', (lead_id,)).fetchone()
    if not lead:
        flash('Lead not found.', 'danger')
        return redirect(url_for('marketing.leads_inbox'))
    events = db.execute(
        'SELECT * FROM marketing_lead_events WHERE lead_id = ? ORDER BY created_at DESC',
        (lead_id,),
    ).fetchall()
    return render_template('marketing/lead_detail.html', lead=lead, events=events, statuses=LEAD_STATUSES)


@marketing.route('/marketing/leads/export.csv')
@login_required
@role_required('admin', 'secretary')
def export_leads():
    _require_marketing_hq()
    db = get_db()
    rows = db.execute('SELECT * FROM marketing_leads ORDER BY created_at DESC').fetchall()
    buf = io.StringIO()
    writer = csv.writer(buf)
    headers = [
        'created_at', 'status', 'full_name', 'email', 'phone', 'society_name',
        'society_type', 'member_count', 'current_system', 'priority', 'message',
        'utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content',
        'referrer', 'landing_page', 'consent_accepted', 'crm_sync_status', 'notes'
    ]
    writer.writerow(headers)
    for row in rows:
        writer.writerow([row.get(h, '') for h in headers])
    audit(db, 'EXPORT_MARKETING_LEADS', 'marketing', f'Exported {len(rows)} marketing leads')
    db.commit()
    return Response(
        buf.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': 'attachment; filename=coopms_marketing_leads.csv'},
    )


@marketing.route('/api/marketing/leads', methods=['POST'])
@csrf.exempt
def capture_lead():
    _require_marketing_hq()
    if not _origin_allowed():
        return jsonify({'ok': False, 'error': 'Origin not allowed'}), 403
    ip = _client_ip()
    if _rate_limited(ip):
        return jsonify({'ok': False, 'error': 'Too many submissions. Please try again later.'}), 429
    payload = request.get_json(silent=True) or request.form.to_dict()
    if _clean(payload.get('company_website'), 200):
        return jsonify({'ok': True})
    full_name = _clean(payload.get('full_name'), 160)
    email = _clean(payload.get('email'), 200).lower()
    society_name = _clean(payload.get('society_name'), 200)
    consent = str(payload.get('consent_accepted', '')).lower() in {'1', 'true', 'yes', 'on'}
    if not full_name or not email or '@' not in email or not society_name:
        return jsonify({'ok': False, 'error': 'Please provide name, email, and cooperative name.'}), 400
    if not consent:
        return jsonify({'ok': False, 'error': 'Consent is required before we can contact you.'}), 400

    db = get_db()
    now = datetime.now()
    lead_values = {
        'full_name': full_name,
        'email': email,
        'phone': _clean(payload.get('phone'), 80),
        'society_name': society_name,
        'society_type': _clean(payload.get('society_type'), 160),
        'member_count': _clean(payload.get('member_count'), 80),
        'current_system': _clean(payload.get('current_system'), 160),
        'priority': _clean(payload.get('priority'), 160),
        'message': _clean(payload.get('message'), 3000),
        'consent_accepted': 1,
        'utm_source': _clean(payload.get('utm_source'), 200),
        'utm_medium': _clean(payload.get('utm_medium'), 200),
        'utm_campaign': _clean(payload.get('utm_campaign'), 200),
        'utm_term': _clean(payload.get('utm_term'), 200),
        'utm_content': _clean(payload.get('utm_content'), 200),
        'referrer': _clean(payload.get('referrer'), 1000),
        'landing_page': _clean(payload.get('landing_page'), 1000),
        'ip_address': ip,
        'user_agent': _clean(request.headers.get('User-Agent'), 500),
        'created_at': now,
        'updated_at': now,
    }
    db.execute('''
        INSERT INTO marketing_leads (
            full_name, email, phone, society_name, society_type, member_count,
            current_system, priority, message, consent_accepted, utm_source,
            utm_medium, utm_campaign, utm_term, utm_content, referrer,
            landing_page, ip_address, user_agent, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', tuple(lead_values[k] for k in [
        'full_name', 'email', 'phone', 'society_name', 'society_type', 'member_count',
        'current_system', 'priority', 'message', 'consent_accepted', 'utm_source',
        'utm_medium', 'utm_campaign', 'utm_term', 'utm_content', 'referrer',
        'landing_page', 'ip_address', 'user_agent', 'created_at', 'updated_at'
    ]))
    lead_id = last_insert_id(db)
    db.execute('''
        INSERT INTO marketing_lead_events
            (lead_id, event_type, description, data)
        VALUES (?, 'created', 'Lead captured from public website', ?)
    ''', (lead_id, json.dumps({k: lead_values.get(k, '') for k in ('utm_source', 'utm_campaign', 'landing_page')})))
    db.commit()

    lead = dict(lead_values)
    lead['id'] = lead_id
    try:
        _send_lead_alert(db, lead)
    except Exception as exc:
        current_app.logger.warning('Marketing lead alert failed: %s', exc)
    return jsonify({'ok': True, 'lead_id': lead_id})
