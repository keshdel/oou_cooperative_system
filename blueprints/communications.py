from datetime import datetime
from html import escape

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from database import get_db, last_insert_id
from email_service import send_email
from utils import audit, member_savings_balance, role_required

communications = Blueprint('communications', __name__, url_prefix='/communications')


AUDIENCES = {
    'active': 'All active members',
    'incomplete_profile': 'Members with incomplete profile',
    'with_loan_balance': 'Members with active loan balance',
    'no_savings_this_month': 'Members with no savings this month',
    'selected': 'Selected members',
}


MESSAGE_PRESETS = {
    'profile_update': {
        'label': 'Profile update reminder',
        'audience': 'incomplete_profile',
        'title': 'Profile update reminder',
        'subject': 'Action required: complete your cooperative profile',
        'body': (
            'Dear {first_name},\n\n'
            'Please review your CoopMS member profile and complete any outstanding information required for your cooperative records.\n\n'
            'Member number: {member_number}\n'
            'Profile completion: {profile_completion}\n'
            'Current savings balance: {savings_balance}\n'
            'Loan balance: {loan_balance}\n'
            'Portal: {portal_link}\n\n'
            'Thank you,\n'
            'Cooperative Administration'
        ),
    },
    'savings_reminder': {
        'label': 'Monthly savings reminder',
        'audience': 'no_savings_this_month',
        'title': 'Monthly savings reminder',
        'subject': 'Reminder: monthly cooperative savings contribution',
        'body': (
            'Dear {first_name},\n\n'
            'This is a reminder to make or confirm your monthly cooperative savings contribution for the current period.\n\n'
            'Member number: {member_number}\n'
            'Monthly savings target: {monthly_savings}\n'
            'Savings due day: {savings_due_day}\n'
            'Current savings balance: {savings_balance}\n'
            'Portal: {portal_link}\n\n'
            'Thank you,\n'
            'Cooperative Administration'
        ),
    },
    'loan_repayment': {
        'label': 'Loan repayment reminder',
        'audience': 'with_loan_balance',
        'title': 'Loan repayment reminder',
        'subject': 'Reminder: cooperative loan repayment',
        'body': (
            'Dear {first_name},\n\n'
            'This is a reminder to review your cooperative loan repayment position and confirm that your repayment is up to date.\n\n'
            'Member number: {member_number}\n'
            'Loan balance: {loan_balance}\n'
            'Estimated monthly repayment: {loan_monthly_payment}\n'
            'Next repayment date: {loan_next_payment_date}\n'
            'Portal: {portal_link}\n\n'
            'Thank you,\n'
            'Cooperative Administration'
        ),
    },
    'balance_notice': {
        'label': 'Balance and statement notice',
        'audience': 'active',
        'title': 'Member balance and statement notice',
        'subject': 'Your cooperative account position is available',
        'body': (
            'Dear {first_name},\n\n'
            'Your cooperative account position is available for review in the member portal.\n\n'
            'Member number: {member_number}\n'
            'Current savings balance: {savings_balance}\n'
            'Loan balance: {loan_balance}\n'
            'Profile completion: {profile_completion}\n'
            'Portal: {portal_link}\n\n'
            'Thank you,\n'
            'Cooperative Administration'
        ),
    },
    'general_notice': {
        'label': 'General cooperative notice',
        'audience': 'active',
        'title': 'Cooperative member notice',
        'subject': 'Cooperative member notice',
        'body': (
            'Dear {first_name},\n\n'
            'This is an official cooperative notice from the administration team.\n\n'
            'Member number: {member_number}\n'
            'Portal: {portal_link}\n\n'
            'Thank you,\n'
            'Cooperative Administration'
        ),
    },
}


def _profile_percent(member):
    required = [
        'first_name', 'last_name', 'email', 'phone', 'date_of_birth',
        'address', 'city', 'state', 'country', 'occupation',
        'bank_name', 'account_name', 'account_number',
        'emergency_contact_name', 'emergency_contact_phone',
        'nominee_name', 'nominee_relationship', 'nominee_phone',
    ]
    done = 0
    for field in required:
        value = member.get(field)
        if value is not None and str(value).strip():
            done += 1
    return round((done / len(required)) * 100) if required else 100


def _member_loan_balance(db, member_id):
    row = db.execute(
        "SELECT COALESCE(SUM(balance), 0) FROM loans WHERE member_id = ? AND status = 'active'",
        (member_id,),
    ).fetchone()
    return float(row[0] or 0) if row else 0.0


def _member_loan_summary(db, member_id):
    row = db.execute('''
        SELECT balance, tenure, total_repayment, next_payment_date
        FROM loans
        WHERE member_id = ? AND status = 'active' AND COALESCE(balance, 0) > 0
        ORDER BY COALESCE(next_payment_date, approved_at, date_applied) ASC
        LIMIT 1
    ''', (member_id,)).fetchone()
    if not row:
        return 0.0, 'Not scheduled'
    tenure = int(row['tenure'] or 0)
    total_repayment = float(row['total_repayment'] or 0)
    monthly_payment = (total_repayment / tenure) if tenure > 0 and total_repayment > 0 else 0.0
    return monthly_payment, (str(row['next_payment_date'])[:10] if row['next_payment_date'] else 'Not scheduled')


def _settings_value(db, key, default=''):
    row = db.execute('SELECT value FROM settings WHERE key = ?', (key,)).fetchone()
    return str(row['value']) if row and row['value'] is not None else default


def _portal_link():
    return url_for('portal.member_portal', _external=True)


def _member_context(db, member):
    savings_balance = member_savings_balance(db, member['id'])
    loan_balance = _member_loan_balance(db, member['id'])
    loan_monthly_payment, loan_next_payment_date = _member_loan_summary(db, member['id'])
    monthly_savings = float(member.get('monthly_savings') or 0)
    return {
        'first_name': member['first_name'] or '',
        'last_name': member['last_name'] or '',
        'full_name': f"{member['first_name'] or ''} {member['last_name'] or ''}".strip(),
        'member_number': member['member_number'] or '',
        'email': member['email'] or '',
        'phone': member['phone'] or '',
        'savings_balance': f"NGN {savings_balance:,.2f}",
        'monthly_savings': f"NGN {monthly_savings:,.2f}",
        'savings_due_day': _settings_value(db, 'savings_due_day', '10'),
        'loan_balance': f"NGN {loan_balance:,.2f}",
        'loan_monthly_payment': f"NGN {loan_monthly_payment:,.2f}",
        'loan_next_payment_date': loan_next_payment_date,
        'profile_completion': f"{_profile_percent(member)}%",
        'portal_link': _portal_link(),
    }


def _render_message(template, context):
    rendered = template or ''
    for key, value in context.items():
        rendered = rendered.replace('{' + key + '}', str(value))
    return rendered


def _body_to_html(body):
    paragraphs = []
    facts = []
    portal_url = ''
    fact_labels = {
        'current savings balance': 'Savings Balance',
        'monthly savings target': 'Monthly Savings Target',
        'savings due day': 'Savings Due Day',
        'loan balance': 'Loan Balance',
        'estimated monthly repayment': 'Estimated Monthly Repayment',
        'next repayment date': 'Next Repayment Date',
        'profile completion': 'Profile Completion',
        'member number': 'Member Number',
    }

    for raw_block in (body or '').splitlines():
        block = raw_block.strip()
        if not block:
            continue

        key, sep, value = block.partition(':')
        normalized_key = key.strip().lower()
        clean_value = value.strip()

        if sep and normalized_key == 'portal':
            portal_url = clean_value
            safe_url = escape(clean_value, quote=True)
            paragraphs.append(
                '<p style="margin:0 0 14px;color:#334155;font-size:15px;line-height:1.65;">'
                '<strong>Portal:</strong> '
                f'<a href="{safe_url}" style="color:#0f766e;text-decoration:none;">{escape(clean_value)}</a>'
                '</p>'
            )
        elif sep and normalized_key in fact_labels:
            facts.append((fact_labels[normalized_key], clean_value))
        else:
            paragraphs.append(
                '<p style="margin:0 0 14px;color:#334155;font-size:15px;line-height:1.65;">'
                f'{escape(block)}'
                '</p>'
            )

    fact_html = ''
    if facts:
        fact_items = ''.join(
            '<tr>'
            f'<td style="padding:10px 12px;color:#64748b;font-size:13px;border-bottom:1px solid #e2e8f0;">{escape(label)}</td>'
            f'<td style="padding:10px 12px;color:#0f172a;font-size:14px;font-weight:700;text-align:right;border-bottom:1px solid #e2e8f0;">{escape(value)}</td>'
            '</tr>'
            for label, value in facts
        )
        fact_html = (
            '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" '
            'style="border-collapse:collapse;margin:4px 0 18px;border:1px solid #e2e8f0;border-radius:8px;overflow:hidden;">'
            f'{fact_items}'
            '</table>'
        )

    cta_html = ''
    if portal_url:
        safe_url = escape(portal_url, quote=True)
        cta_html = (
            '<p style="margin:22px 0 6px;">'
            f'<a href="{safe_url}" '
            'style="display:inline-block;background:#0f766e;color:#ffffff;text-decoration:none;'
            'font-weight:700;font-size:14px;padding:12px 18px;border-radius:6px;">'
            'Open Member Portal'
            '</a></p>'
        )

    body_html = ''.join(paragraphs) or '<p style="margin:0;color:#334155;font-size:15px;"> </p>'
    return (
        '<div style="font-family:Arial,Helvetica,sans-serif;">'
        '<div style="border:1px solid #dbeafe;border-radius:8px;overflow:hidden;background:#ffffff;">'
        '<div style="background:#0f172a;color:#ffffff;padding:14px 18px;">'
        '<div style="font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:#93c5fd;font-weight:700;">'
        'CoopMS Member Communication'
        '</div>'
        '<div style="font-size:19px;font-weight:800;margin-top:3px;">Member Portal Notice</div>'
        '</div>'
        '<div style="padding:18px;">'
        f'{body_html}'
        f'{fact_html}'
        f'{cta_html}'
        '<div style="margin-top:20px;padding-top:14px;border-top:1px solid #e2e8f0;'
        'color:#64748b;font-size:12px;line-height:1.5;">'
        'This message was sent from CoopMS for cooperative administration, member records, and account servicing.'
        '</div>'
        '</div>'
        '</div>'
        '</div>'
    )


def _members_for_audience(db, audience, selected_ids=None):
    selected_ids = selected_ids or []
    if audience == 'selected':
        if not selected_ids:
            return []
        placeholders = ','.join('?' for _ in selected_ids)
        return db.execute(
            f"SELECT * FROM members WHERE id IN ({placeholders}) ORDER BY first_name, last_name",
            tuple(selected_ids),
        ).fetchall()
    if audience == 'incomplete_profile':
        rows = db.execute(
            "SELECT * FROM members WHERE status = 'active' ORDER BY first_name, last_name"
        ).fetchall()
        return [m for m in rows if _profile_percent(m) < 100]
    if audience == 'with_loan_balance':
        return db.execute('''
            SELECT DISTINCT m.*
            FROM members m
            JOIN loans l ON l.member_id = m.id
            WHERE m.status = 'active' AND l.status = 'active' AND COALESCE(l.balance, 0) > 0
            ORDER BY m.first_name, m.last_name
        ''').fetchall()
    if audience == 'no_savings_this_month':
        month = datetime.now().strftime('%Y-%m')
        return db.execute('''
            SELECT m.*
            FROM members m
            WHERE m.status = 'active'
              AND NOT EXISTS (
                SELECT 1 FROM savings s WHERE s.member_id = m.id AND s.month = ?
              )
            ORDER BY m.first_name, m.last_name
        ''', (month,)).fetchall()
    return db.execute(
        "SELECT * FROM members WHERE status = 'active' ORDER BY first_name, last_name"
    ).fetchall()


@communications.route('/')
@login_required
@role_required('admin', 'secretary')
def index():
    db = get_db()
    campaigns = db.execute(
        'SELECT * FROM communication_campaigns ORDER BY created_at DESC LIMIT 50'
    ).fetchall()
    return render_template('communications/index.html', campaigns=campaigns)


@communications.route('/new', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'secretary')
def new_campaign():
    db = get_db()
    all_members = db.execute(
        "SELECT id, member_number, first_name, last_name, email, phone, status FROM members ORDER BY first_name, last_name"
    ).fetchall()
    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        audience = request.form.get('audience', 'active').strip()
        subject = request.form.get('subject', '').strip()
        body = request.form.get('body', '').strip()
        channel = request.form.get('channel', 'email').strip()
        selected_ids = [int(v) for v in request.form.getlist('member_ids') if str(v).isdigit()]

        if channel != 'email':
            flash('Only email sending is enabled in this phase. WhatsApp will be added after consent/template setup.', 'warning')
            return redirect(url_for('communications.new_campaign'))
        if audience not in AUDIENCES:
            flash('Choose a valid audience.', 'danger')
            return redirect(url_for('communications.new_campaign'))
        if not title or not subject or not body:
            flash('Title, subject, and message body are required.', 'danger')
            return redirect(url_for('communications.new_campaign'))

        members = _members_for_audience(db, audience, selected_ids)
        sent = failed = skipped = 0
        campaign_id = None
        try:
            db.execute('''
                INSERT INTO communication_campaigns
                    (title, audience, channel, subject, body, status, recipient_count, created_by, created_at)
                VALUES (?, ?, ?, ?, ?, 'sending', ?, ?, ?)
            ''', (title, audience, channel, subject, body, len(members), current_user.id, datetime.now()))
            campaign_id = last_insert_id(db)

            for member in members:
                destination = (member['email'] or '').strip()
                if not destination:
                    skipped += 1
                    db.execute('''
                        INSERT INTO communication_recipients
                            (campaign_id, member_id, channel, destination, status, error, created_at)
                        VALUES (?, ?, 'email', '', 'skipped', 'Missing email address', ?)
                    ''', (campaign_id, member['id'], datetime.now()))
                    continue
                ctx = _member_context(db, member)
                member_subject = _render_message(subject, ctx)
                member_body = _render_message(body, ctx)
                ok = send_email(destination, member_subject, _body_to_html(member_body))
                status = 'sent' if ok else 'failed'
                if ok:
                    sent += 1
                else:
                    failed += 1
                db.execute('''
                    INSERT INTO communication_recipients
                        (campaign_id, member_id, channel, destination, status, error, sent_at, created_at)
                    VALUES (?, ?, 'email', ?, ?, ?, ?, ?)
                ''', (
                    campaign_id, member['id'], destination, status,
                    '' if ok else 'Email provider returned failure',
                    datetime.now() if ok else None, datetime.now(),
                ))

            final_status = 'sent' if failed == 0 else ('partial' if sent else 'failed')
            db.execute('''
                UPDATE communication_campaigns
                   SET status = ?, sent_count = ?, failed_count = ?, skipped_count = ?, sent_at = ?
                 WHERE id = ?
            ''', (final_status, sent, failed, skipped, datetime.now(), campaign_id))
            db.commit()
            audit(db, 'SEND_COMMUNICATION', 'communications',
                  f'Campaign {campaign_id}: {sent} sent, {failed} failed, {skipped} skipped')
            flash(f'Campaign sent: {sent} sent, {failed} failed, {skipped} skipped.', 'success' if failed == 0 else 'warning')
            return redirect(url_for('communications.campaign_detail', campaign_id=campaign_id))
        except Exception as e:
            db.rollback()
            flash(f'Could not send campaign: {e}', 'danger')
            return redirect(url_for('communications.new_campaign'))

    return render_template('communications/new.html',
                           audiences=AUDIENCES, members=all_members,
                           presets=MESSAGE_PRESETS,
                           default_preset=MESSAGE_PRESETS['profile_update'])


@communications.route('/<int:campaign_id>')
@login_required
@role_required('admin', 'secretary')
def campaign_detail(campaign_id):
    db = get_db()
    campaign = db.execute(
        'SELECT * FROM communication_campaigns WHERE id = ?', (campaign_id,)
    ).fetchone()
    if not campaign:
        flash('Campaign not found.', 'danger')
        return redirect(url_for('communications.index'))
    recipients = db.execute('''
        SELECT cr.*, m.member_number, m.first_name, m.last_name
        FROM communication_recipients cr
        LEFT JOIN members m ON m.id = cr.member_id
        WHERE cr.campaign_id = ?
        ORDER BY cr.id
    ''', (campaign_id,)).fetchall()
    return render_template('communications/detail.html', campaign=campaign, recipients=recipients)
