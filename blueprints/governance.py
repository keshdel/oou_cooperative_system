"""
governance.py — cooperative governance content:
  * Events / announcements (AGM, meeting dates) shown on the members' banner.
  * Minutes-of-meeting repository (upload → stored in the DB → browse/download).
"""
import io
import os
import calendar as pycal
from datetime import datetime, timedelta

from flask import (Blueprint, render_template, redirect, url_for, request, flash,
                   send_file, abort, jsonify)
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename

from database import get_db, last_insert_id
from utils import role_required, audit, notify, member_for_user
from email_service import send_email

governance = Blueprint('governance', __name__)

MEETING_TYPES = [
    ('announcement', 'Announcement'),
    ('general', 'General Meeting'),
    ('agm', 'Annual General Meeting (AGM)'),
    ('committee', 'Committee Meeting'),
    ('exco', 'Executive Meeting'),
    ('training', 'Training / Event'),
]
_MEETING_TYPE_LABELS = dict(MEETING_TYPES)


def _coop_name(db):
    row = db.execute("SELECT value FROM settings WHERE key = 'coop_name'").fetchone()
    return (row['value'] if row and row['value'] else 'Your Cooperative')


def _fmt_event_when(event):
    """Human 'date · time' string for an event row (values are strings)."""
    date = (event['event_date'] or '')[:10] if event['event_date'] else 'Date to be confirmed'
    st = event['start_time'] if 'start_time' in event.keys() else ''
    et = event['end_time'] if 'end_time' in event.keys() else ''
    time = ''
    if st and et:
        time = f' · {st}–{et}'
    elif st:
        time = f' · {st}'
    return f'{date}{time}'


def _rsvp_summary(db, event_id):
    rows = db.execute(
        "SELECT response, COUNT(*) AS c FROM event_rsvps WHERE event_id = ? GROUP BY response",
        (event_id,)).fetchall()
    summary = {'attending': 0, 'maybe': 0, 'not_attending': 0}
    for r in rows:
        if r['response'] in summary:
            summary[r['response']] = r['c']
    summary['total'] = summary['attending'] + summary['maybe'] + summary['not_attending']
    summary['attended'] = db.execute(
        "SELECT COUNT(*) FROM event_rsvps WHERE event_id = ? AND attended = 1", (event_id,)
    ).fetchone()[0]
    return summary


def _event_email_html(member, event, intro, event_url, coop):
    label = _MEETING_TYPE_LABELS.get((event['event_type'] or 'general'), 'Meeting')
    where = event['meeting_link'] or event['location'] or 'To be confirmed'
    where_html = (f'<a href="{event["meeting_link"]}" style="color:#082b66;">{event["meeting_link"]}</a>'
                  if event['meeting_link'] else (event['location'] or 'To be confirmed'))
    agenda = (event['agenda'] if 'agenda' in event.keys() else '') or event['description'] or ''
    agenda_para = f'<p style="margin:16px 0 0;color:#334155;"><strong>Agenda:</strong> {agenda}</p>' if agenda else ''
    name = (member['first_name'] or 'Member')
    return (
        f'<p style="margin:0 0 14px;">Dear {name},</p>'
        f'<p style="margin:0 0 16px;color:#334155;">{intro}</p>'
        '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" '
        'style="border-collapse:collapse;border:1px solid #e4e9f2;border-radius:8px;overflow:hidden;">'
        f'<tr><td style="padding:10px 14px;color:#5b6b82;width:130px;border-bottom:1px solid #eef2f8;">Meeting</td>'
        f'<td style="padding:10px 14px;color:#0e1a2c;font-weight:600;border-bottom:1px solid #eef2f8;">{event["title"]} <span style="color:#5b6b82;font-weight:400;">({label})</span></td></tr>'
        f'<tr><td style="padding:10px 14px;color:#5b6b82;border-bottom:1px solid #eef2f8;">When</td>'
        f'<td style="padding:10px 14px;color:#0e1a2c;border-bottom:1px solid #eef2f8;">{_fmt_event_when(event)}</td></tr>'
        f'<tr><td style="padding:10px 14px;color:#5b6b82;">Where</td>'
        f'<td style="padding:10px 14px;color:#0e1a2c;">{where_html}</td></tr>'
        '</table>'
        f'{agenda_para}'
        '<table role="presentation" cellspacing="0" cellpadding="0" style="margin:20px 0 4px;">'
        f'<tr><td style="background:#082b66;border-radius:6px;padding:12px 24px;">'
        f'<a href="{event_url}" style="color:#fff;text-decoration:none;font-weight:700;font-size:14px;">View &amp; RSVP &rarr;</a>'
        '</td></tr></table>'
    )


def _notify_members_of_event(db, event, kind='invite', send_mail=True):
    """Email + in-app notify every active member about an event (invite / update)."""
    coop = _coop_name(db)
    label = _MEETING_TYPE_LABELS.get((event['event_type'] or 'general'), 'meeting')
    if kind == 'update':
        subject = f'Updated: {event["title"]} — {_fmt_event_when(event)}'
        intro = f'The details for this {label.lower()} have been updated. Please review the new schedule below.'
    elif kind == 'reminder':
        subject = f'Reminder: {event["title"]} — {_fmt_event_when(event)}'
        intro = f'A friendly reminder about this upcoming {label.lower()}. We look forward to seeing you.'
    else:
        subject = f'You are invited: {event["title"]} — {_fmt_event_when(event)}'
        intro = f'You are invited to the following {label.lower()}. Please let us know if you will attend.'
    try:
        event_url = url_for('governance.event_detail', event_id=event['id'], _external=True)
    except Exception:
        event_url = ''
    action_url = url_for('governance.event_detail', event_id=event['id'])
    members = db.execute(
        "SELECT id, first_name, last_name, email FROM members WHERE status = 'active'").fetchall()
    emailed = 0
    for m in members:
        if not m['email']:
            continue
        u = db.execute("SELECT id FROM users WHERE email = ?", (m['email'],)).fetchone()
        if u:
            notify(db, u['id'], subject, intro, 'info', action_url)
        if send_mail:
            send_email(m['email'], subject, _event_email_html(m, event, intro, event_url, coop),
                       background=True)
            emailed += 1
    db.commit()
    return emailed


def _send_due_reminders(db, within_days=1):
    """Send a one-time reminder for active meetings happening within the next
    `within_days` day(s) that haven't been reminded yet. Idempotent via
    events.reminder_sent_at — safe to call from a page load or a cron endpoint."""
    today = datetime.now().date()
    lo = today.strftime('%Y-%m-%d')
    hi = (today + timedelta(days=within_days + 1)).strftime('%Y-%m-%d')   # exclusive upper bound
    events = db.execute(
        "SELECT * FROM events WHERE is_active = 1 AND reminder_sent_at IS NULL "
        "AND event_date IS NOT NULL AND event_date >= ? AND event_date < ? "
        "ORDER BY event_date", (lo, hi)).fetchall()
    sent = 0
    for ev in events:
        try:
            _notify_members_of_event(db, ev, kind='reminder', send_mail=True)
            db.execute("UPDATE events SET reminder_sent_at = ? WHERE id = ?",
                       (datetime.now(), ev['id']))
            db.commit()
            sent += 1
        except Exception:
            db.rollback()
    return sent

_ALLOWED = {'pdf', 'doc', 'docx', 'txt', 'jpg', 'jpeg', 'png'}
_MIME = {
    'pdf': 'application/pdf', 'doc': 'application/msword',
    'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'txt': 'text/plain', 'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'png': 'image/png',
}


def upcoming_events(db, limit=5):
    """Active events dated today or later — used for the members' banner."""
    try:
        today = datetime.now().strftime('%Y-%m-%d')
        return db.execute(
            "SELECT * FROM events WHERE is_active = 1 AND (event_date IS NULL OR event_date >= ?) "
            "ORDER BY event_date ASC LIMIT ?", (today, limit)
        ).fetchall()
    except Exception:
        return []


# ── Member / all-user views ──────────────────────────────────────────────────

@governance.route('/events')
@login_required
def events_list():
    db = get_db()
    try:
        _send_due_reminders(db)   # opportunistic: fire due reminders when members check events
    except Exception:
        pass
    today = datetime.now().strftime('%Y-%m-%d')
    upcoming = db.execute(
        "SELECT * FROM events WHERE is_active = 1 AND (event_date IS NULL OR event_date >= ?) "
        "ORDER BY event_date ASC", (today,)).fetchall()
    past = db.execute(
        "SELECT * FROM events WHERE is_active = 1 AND event_date < ? "
        "ORDER BY event_date DESC LIMIT 30", (today,)).fetchall()
    return render_template('governance/events.html', upcoming=upcoming, past=past)


@governance.route('/minutes')
@login_required
def minutes_list():
    db = get_db()
    minutes = db.execute(
        "SELECT id, title, meeting_type, meeting_date, file_name, notes, uploaded_at "
        "FROM meeting_minutes ORDER BY meeting_date DESC, id DESC").fetchall()
    return render_template('governance/minutes.html', minutes=minutes)


@governance.route('/minutes/<int:minute_id>/download')
@login_required
def minutes_download(minute_id):
    db = get_db()
    m = db.execute("SELECT file_name, file_mime, file_data FROM meeting_minutes WHERE id = ?",
                   (minute_id,)).fetchone()
    if not m or not m['file_data']:
        abort(404)
    data = bytes(m['file_data'])
    return send_file(io.BytesIO(data), mimetype=m['file_mime'] or 'application/octet-stream',
                     as_attachment=True, download_name=m['file_name'] or f'minutes-{minute_id}')


# ── Admin / secretary management ─────────────────────────────────────────────

@governance.route('/governance')
@login_required
@role_required('admin', 'secretary')
def manage():
    db = get_db()
    try:
        _send_due_reminders(db)
    except Exception:
        pass
    events = db.execute('''
        SELECT e.*,
               (SELECT COUNT(*) FROM event_rsvps r WHERE r.event_id = e.id AND r.response = 'attending') AS attending_count
        FROM events e ORDER BY e.event_date DESC, e.id DESC''').fetchall()
    minutes = db.execute(
        "SELECT id, title, meeting_type, meeting_date, file_name, notes, uploaded_at "
        "FROM meeting_minutes ORDER BY meeting_date DESC, id DESC").fetchall()
    return render_template('governance/manage.html', events=events, minutes=minutes,
                           meeting_types=MEETING_TYPES, meeting_labels=_MEETING_TYPE_LABELS,
                           today=datetime.now().strftime('%Y-%m-%d'))


@governance.route('/governance/events/add', methods=['POST'])
@login_required
@role_required('admin', 'secretary')
def add_event():
    db = get_db()
    title = request.form.get('title', '').strip()
    if not title:
        flash('Event title is required.', 'danger')
        return redirect(url_for('governance.manage'))
    # Meeting link (virtual AGM/meeting) — only accept http(s) URLs.
    link = request.form.get('meeting_link', '').strip()
    if link and not link.lower().startswith(('http://', 'https://')):
        link = 'https://' + link
    db.execute('''INSERT INTO events
                  (title, event_type, event_date, start_time, end_time, location, meeting_link,
                   agenda, description, created_by)
                  VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
               (title, request.form.get('event_type', 'general'),
                request.form.get('event_date') or None,
                request.form.get('start_time', '').strip() or None,
                request.form.get('end_time', '').strip() or None,
                request.form.get('location', '').strip(), link or None,
                request.form.get('agenda', '').strip() or None,
                request.form.get('description', '').strip(), current_user.id))
    db.commit()
    event = db.execute("SELECT * FROM events WHERE id = ?", (last_insert_id(db),)).fetchone()
    audit(db, 'ADD_EVENT', 'governance', f'Added event: {title}')
    invited = 0
    if request.form.get('send_invite') == '1':
        invited = _notify_members_of_event(db, event, kind='invite', send_mail=True)
    flash(f'Meeting published{f" — invite emailed to {invited} member(s)" if invited else ""}.',
          'success')
    return redirect(url_for('governance.manage'))


@governance.route('/governance/events/<int:event_id>/toggle', methods=['POST'])
@login_required
@role_required('admin', 'secretary')
def toggle_event(event_id):
    db = get_db()
    e = db.execute("SELECT is_active FROM events WHERE id = ?", (event_id,)).fetchone()
    if e:
        db.execute("UPDATE events SET is_active = ? WHERE id = ?",
                   (0 if e['is_active'] else 1, event_id))
        db.commit()
    return redirect(url_for('governance.manage'))


@governance.route('/governance/events/<int:event_id>/delete', methods=['POST'])
@login_required
@role_required('admin', 'secretary')
def delete_event(event_id):
    db = get_db()
    db.execute("DELETE FROM event_rsvps WHERE event_id = ?", (event_id,))
    db.execute("DELETE FROM events WHERE id = ?", (event_id,))
    db.commit()
    flash('Event deleted.', 'info')
    return redirect(url_for('governance.manage'))


# ── Meeting detail · RSVP · reschedule · attendance ──────────────────────────

@governance.route('/events/<int:event_id>')
@login_required
def event_detail(event_id):
    db = get_db()
    event = db.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
    if not event:
        flash('Meeting not found.', 'danger')
        return redirect(url_for('governance.events_list'))
    summary = _rsvp_summary(db, event_id)
    my_rsvp = None
    member = member_for_user(db)
    if member:
        row = db.execute("SELECT response FROM event_rsvps WHERE event_id = ? AND member_id = ?",
                         (event_id, member['id'])).fetchone()
        my_rsvp = row['response'] if row else None
    is_manager = getattr(current_user, 'role', '') in ('admin', 'secretary', 'exco', 'treasurer')
    register = []
    if is_manager:
        register = db.execute('''
            SELECT m.id AS member_id, m.member_number,
                   m.first_name || ' ' || m.last_name AS name,
                   COALESCE(r.response, '') AS response, COALESCE(r.attended, 0) AS attended
            FROM members m
            LEFT JOIN event_rsvps r ON r.event_id = ? AND r.member_id = m.id
            WHERE m.status = 'active'
            ORDER BY name''', (event_id,)).fetchall()
    minutes = db.execute(
        "SELECT id, title, meeting_date, file_name FROM meeting_minutes WHERE event_id = ? ORDER BY id DESC",
        (event_id,)).fetchall()
    return render_template('governance/event_detail.html', event=event, summary=summary,
                           my_rsvp=my_rsvp, register=register, is_manager=is_manager,
                           minutes=minutes, meeting_labels=_MEETING_TYPE_LABELS,
                           when=_fmt_event_when(event))


@governance.route('/events/<int:event_id>/rsvp', methods=['POST'])
@login_required
def rsvp(event_id):
    db = get_db()
    member = member_for_user(db)
    if not member:
        flash('Only members can RSVP to a meeting.', 'warning')
        return redirect(url_for('governance.event_detail', event_id=event_id))
    response = request.form.get('response', 'attending')
    if response not in ('attending', 'maybe', 'not_attending'):
        response = 'attending'
    existing = db.execute("SELECT id FROM event_rsvps WHERE event_id = ? AND member_id = ?",
                          (event_id, member['id'])).fetchone()
    if existing:
        db.execute("UPDATE event_rsvps SET response = ?, responded_at = ? WHERE id = ?",
                   (response, datetime.now(), existing['id']))
    else:
        db.execute("INSERT INTO event_rsvps (event_id, member_id, response, responded_at) "
                   "VALUES (?, ?, ?, ?)", (event_id, member['id'], response, datetime.now()))
    db.commit()
    flash('Your response has been recorded. Thank you.', 'success')
    return redirect(url_for('governance.event_detail', event_id=event_id))


@governance.route('/governance/events/<int:event_id>/edit', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'secretary')
def edit_event(event_id):
    db = get_db()
    event = db.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
    if not event:
        flash('Meeting not found.', 'danger')
        return redirect(url_for('governance.manage'))
    if request.method == 'GET':
        return render_template('governance/edit_event.html', event=event, meeting_types=MEETING_TYPES)
    title = request.form.get('title', '').strip() or event['title']
    link = request.form.get('meeting_link', '').strip()
    if link and not link.lower().startswith(('http://', 'https://')):
        link = 'https://' + link
    new_date = request.form.get('event_date') or None
    new_start = request.form.get('start_time', '').strip() or None
    db.execute('''UPDATE events SET title=?, event_type=?, event_date=?, start_time=?, end_time=?,
                  location=?, meeting_link=?, agenda=?, description=? WHERE id=?''',
               (title, request.form.get('event_type', event['event_type']), new_date, new_start,
                request.form.get('end_time', '').strip() or None,
                request.form.get('location', '').strip(), link or None,
                request.form.get('agenda', '').strip() or None,
                request.form.get('description', '').strip(), event_id))
    db.commit()
    updated = db.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
    audit(db, 'EDIT_EVENT', 'governance', f'Edited event #{event_id}: {title}')
    notified = 0
    if request.form.get('notify_members') == '1':
        notified = _notify_members_of_event(db, updated, kind='update', send_mail=True)
    flash(f'Meeting updated{f" — {notified} member(s) notified of the new schedule" if notified else ""}.',
          'success')
    return redirect(url_for('governance.event_detail', event_id=event_id))


@governance.route('/governance/events/<int:event_id>/attendance', methods=['POST'])
@login_required
@role_required('admin', 'secretary')
def mark_attendance(event_id):
    db = get_db()
    attended_ids = set(request.form.getlist('attended'))
    members = db.execute("SELECT id FROM members WHERE status = 'active'").fetchall()
    for m in members:
        att = 1 if str(m['id']) in attended_ids else 0
        ex = db.execute("SELECT id FROM event_rsvps WHERE event_id = ? AND member_id = ?",
                        (event_id, m['id'])).fetchone()
        if ex:
            db.execute("UPDATE event_rsvps SET attended = ? WHERE id = ?", (att, ex['id']))
        elif att:
            db.execute("INSERT INTO event_rsvps (event_id, member_id, response, attended) "
                       "VALUES (?, ?, 'attending', 1)", (event_id, m['id']))
    db.commit()
    audit(db, 'MARK_ATTENDANCE', 'governance', f'Recorded attendance for event #{event_id}')
    flash('Attendance register saved.', 'success')
    return redirect(url_for('governance.event_detail', event_id=event_id))


# ── Calendar + reminders ─────────────────────────────────────────────────────

@governance.route('/events/calendar')
@login_required
def calendar_view():
    db = get_db()
    now = datetime.now()
    try:
        year = int(request.args.get('year', now.year))
        month = int(request.args.get('month', now.month))
        if not 1 <= month <= 12:
            year, month = now.year, now.month
    except (TypeError, ValueError):
        year, month = now.year, now.month
    first = f'{year:04d}-{month:02d}-01'
    nextm = f'{year + 1:04d}-01-01' if month == 12 else f'{year:04d}-{month + 1:02d}-01'
    rows = db.execute(
        "SELECT id, title, event_type, event_date, start_time FROM events "
        "WHERE is_active = 1 AND event_date >= ? AND event_date < ? "
        "ORDER BY event_date, start_time", (first, nextm)).fetchall()
    by_day = {}
    for e in rows:
        d = (e['event_date'] or '')[:10]
        if len(d) >= 10 and d[8:10].isdigit():
            by_day.setdefault(int(d[8:10]), []).append(e)
    weeks = pycal.Calendar(firstweekday=6).monthdayscalendar(year, month)   # Sunday-first
    prev_y, prev_m = (year, month - 1) if month > 1 else (year - 1, 12)
    next_y, next_m = (year, month + 1) if month < 12 else (year + 1, 1)
    return render_template('governance/calendar.html', weeks=weeks, by_day=by_day,
                           year=year, month=month, month_name=pycal.month_name[month],
                           prev_y=prev_y, prev_m=prev_m, next_y=next_y, next_m=next_m,
                           today=now.strftime('%Y-%m-%d'), meeting_labels=_MEETING_TYPE_LABELS)


@governance.route('/governance/reminders/run', methods=['GET', 'POST'])
def run_reminders():
    """Send due meeting reminders. Callable two ways:
      * by a daily cron with ?token=<GOVERNANCE_CRON_TOKEN> (no login), or
      * by a logged-in admin/secretary (returns to the manage page)."""
    db = get_db()
    token = os.environ.get('GOVERNANCE_CRON_TOKEN', '').strip()
    provided = (request.args.get('token') or request.form.get('token') or '').strip()
    if token and provided and provided == token:
        return jsonify({'sent': _send_due_reminders(db)})
    if not current_user.is_authenticated or getattr(current_user, 'role', '') not in ('admin', 'secretary'):
        abort(403)
    n = _send_due_reminders(db)
    flash(f'{n} reminder(s) sent for meetings in the next day.' if n
          else 'No meetings are due for a reminder right now.', 'info')
    return redirect(url_for('governance.manage'))


@governance.route('/governance/minutes/upload', methods=['POST'])
@login_required
@role_required('admin', 'secretary')
def upload_minutes():
    db = get_db()
    title = request.form.get('title', '').strip()
    if not title:
        flash('Minutes title is required.', 'danger')
        return redirect(url_for('governance.manage'))
    f = request.files.get('file')
    file_name = file_mime = None
    data = None
    if f and f.filename:
        ext = f.filename.rsplit('.', 1)[-1].lower() if '.' in f.filename else ''
        if ext not in _ALLOWED:
            flash(f'File type .{ext} is not allowed. Use PDF, DOC, DOCX, TXT, or an image.', 'danger')
            return redirect(url_for('governance.manage'))
        data = f.read()
        if len(data) > 10 * 1024 * 1024:
            flash('File too large (max 10 MB).', 'danger')
            return redirect(url_for('governance.manage'))
        file_name = secure_filename(f.filename)
        file_mime = _MIME.get(ext, 'application/octet-stream')
    db.execute('''INSERT INTO meeting_minutes
                  (title, meeting_type, meeting_date, file_name, file_mime, file_data, notes,
                   event_id, uploaded_by)
                  VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
               (title, request.form.get('meeting_type', 'general'),
                request.form.get('meeting_date') or None, file_name, file_mime, data,
                request.form.get('notes', '').strip(),
                request.form.get('event_id') or None, current_user.id))
    db.commit()
    audit(db, 'UPLOAD_MINUTES', 'governance', f'Uploaded minutes: {title}')
    flash('Minutes saved to the repository.', 'success')
    return redirect(url_for('governance.manage'))


@governance.route('/governance/minutes/<int:minute_id>/delete', methods=['POST'])
@login_required
@role_required('admin', 'secretary')
def delete_minutes(minute_id):
    db = get_db()
    db.execute("DELETE FROM meeting_minutes WHERE id = ?", (minute_id,))
    db.commit()
    flash('Minutes deleted.', 'info')
    return redirect(url_for('governance.manage'))
