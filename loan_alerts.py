"""
loan_alerts.py — treat every loan request like a sales lead.

The problem this solves: a member submitted a loan request through the app and
nobody on the management committee knew until the member phoned. A loan request
is the cooperative's equivalent of a checkout on an e-commerce site — the moment
it is created it must (a) be logged, (b) alert every decision maker instantly,
(c) carry the full application with it, and (d) keep chasing until somebody
acts.

What this module provides:

    notify_loan_submitted(db, loan_id, channel)   — instant fan-out on submission
    notify_stage_advanced(db, loan_id, stage)     — the next approver is alerted
    notify_guarantors_complete(db, loan_id)       — guarantor consent completed
    mark_first_response(db, loan_id, ...)         — first officer touch (SLA clock)
    run_pipeline_sweep(db)                        — reminders + escalation

Every one of those writes rows to `loan_request_events`, so the cooperative can
always answer "who was told, through which channel, and when?".

All entry points are best-effort: an email or push failure must never break or
roll back the member's application.
"""

import logging
from datetime import datetime, timedelta

import loan_workflow as lw
from loan_pdf import build_loan_application_pdf, loan_application_context, money

log = logging.getLogger(__name__)


# ── Roles ─────────────────────────────────────────────────────────────────────
#
# The bye-laws name the offices; the system stores them as user roles.
# President is the top authority, which this system models as `admin`.

ROLE_LABELS = {
    'admin':     'President',
    'treasurer': 'Treasurer',
    'secretary': 'General Secretary',
    'exco':      'Exco Member',
}
DEFAULT_ALERT_ROLES = ('admin', 'treasurer', 'secretary', 'exco')

# Event types written to loan_request_events
EVENT_SUBMITTED   = 'submitted'
EVENT_ALERT       = 'alert_sent'
EVENT_REMINDER    = 'reminder_sent'
EVENT_ESCALATED   = 'escalated'
EVENT_STAGE       = 'stage_advanced'
EVENT_VIEWED      = 'viewed'
EVENT_FAILED      = 'alert_failed'


# ── Settings ──────────────────────────────────────────────────────────────────

def _setting(db, key, default=''):
    try:
        row = db.execute('SELECT value FROM settings WHERE key = ?', (key,)).fetchone()
        value = (row['value'] if row else None)
        return default if value in (None, '') else value
    except Exception:
        return default


def _flag(db, key, default='1') -> bool:
    return str(_setting(db, key, default)).strip().lower() in {'1', 'true', 'yes', 'on'}


def _hours(db, key, default) -> float:
    try:
        return float(_setting(db, key, str(default)))
    except (TypeError, ValueError):
        return float(default)


def alerts_enabled(db) -> bool:
    return _flag(db, 'loan_alert_enabled', '1')


def alert_roles(db) -> tuple:
    raw = _setting(db, 'loan_alert_roles', ','.join(DEFAULT_ALERT_ROLES))
    roles = tuple(r.strip().lower() for r in str(raw).split(',') if r.strip())
    return roles or DEFAULT_ALERT_ROLES


def extra_alert_emails(db) -> list:
    raw = _setting(db, 'loan_alert_extra_emails', '')
    return [e.strip() for e in str(raw).replace(';', ',').split(',') if e.strip() and '@' in e]


# ── Links ─────────────────────────────────────────────────────────────────────

def staff_loan_path(loan_id) -> str:
    return f'/loans/{loan_id}'


def loan_link(db, loan_id) -> str:
    """Absolute URL to the staff loan page when we can build one, else the path.

    Alerts are also sent from the background sweep, where there is no request to
    derive a host from — hence the `app_base_url` setting as a fallback.
    """
    path = staff_loan_path(loan_id)
    base = str(_setting(db, 'app_base_url', '')).strip().rstrip('/')
    if base:
        return f'{base}{path}'
    try:
        from flask import has_request_context, request
        if has_request_context():
            return request.url_root.rstrip('/') + path
    except Exception:
        pass
    return path


# ── Event log ─────────────────────────────────────────────────────────────────

def log_event(db, loan_id, event_type, stage='', channel='', recipient=None,
              delivery='', status='sent', detail=''):
    """Append one row to the loan request pipeline log. Never raises."""
    recipient = recipient or {}
    try:
        db.execute('''
            INSERT INTO loan_request_events
                (loan_id, event_type, stage, channel, recipient_user_id, recipient_name,
                 recipient_role, recipient_email, delivery, status, detail, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (loan_id, event_type, stage or '', channel or '',
              recipient.get('user_id'), recipient.get('name', ''),
              recipient.get('role', ''), recipient.get('email', ''),
              delivery or '', status or '', (detail or '')[:1000], datetime.now()))
    except Exception:
        log.exception('Could not log loan request event %s for loan %s', event_type, loan_id)


def loan_events(db, loan_id, limit=100):
    try:
        return db.execute(
            'SELECT * FROM loan_request_events WHERE loan_id = ? ORDER BY id DESC LIMIT ?',
            (loan_id, limit),
        ).fetchall()
    except Exception:
        return []


# ── Recipients ────────────────────────────────────────────────────────────────

def alert_recipients(db, roles=None, exclude_user_ids=()):
    """Active staff users who should hear about a loan request.

    Returns dicts: {'user_id', 'name', 'email', 'role', 'role_label'}.
    Extra addresses from `loan_alert_extra_emails` are appended with no user id
    (email only — they get no in-app notification).
    """
    roles = tuple(roles) if roles else alert_roles(db)
    recipients = []
    seen_emails = set()
    if roles:
        placeholders = ','.join(['?'] * len(roles))
        try:
            rows = db.execute(
                f'''SELECT id, username, full_name, email, role FROM users
                    WHERE role IN ({placeholders}) AND COALESCE(is_active, 1) = 1
                    ORDER BY role, id''',
                list(roles),
            ).fetchall()
        except Exception:
            log.exception('Could not resolve loan alert recipients')
            rows = []
        for row in rows:
            if row['id'] in set(exclude_user_ids or ()):
                continue
            email = (row['email'] or '').strip()
            if email:
                seen_emails.add(email.lower())
            recipients.append({
                'user_id': row['id'],
                'name': row['full_name'] or row['username'] or '',
                'email': email,
                'role': row['role'],
                'role_label': ROLE_LABELS.get(row['role'], (row['role'] or '').title()),
            })
    for email in extra_alert_emails(db):
        if email.lower() in seen_emails:
            continue
        seen_emails.add(email.lower())
        recipients.append({'user_id': None, 'name': email, 'email': email,
                           'role': 'observer', 'role_label': 'Copied recipient'})
    return recipients


def _stage_owner_role(stage) -> str:
    return lw.STAGE_ROLE.get(stage, '')


# ── Dispatch ──────────────────────────────────────────────────────────────────

def _dispatch(db, loan_id, recipients, title, message, action_url, event_type,
              stage='', channel='', html='', attachments=None, notification_type='info'):
    """Send one alert to many recipients: in-app + push + email, each logged."""
    from utils import notify
    from email_service import send_email

    sent_inapp = sent_email = 0
    for person in recipients:
        if person.get('user_id'):
            try:
                notify(db, person['user_id'], title, message, notification_type, action_url)
                sent_inapp += 1
                log_event(db, loan_id, event_type, stage, channel, person, 'inapp', 'sent')
            except Exception as exc:
                log_event(db, loan_id, EVENT_FAILED, stage, channel, person, 'inapp',
                          'failed', str(exc))
        if person.get('email'):
            try:
                queued = send_email(person['email'], title, html or f'<p>{message}</p>',
                                    background=True, attachments=attachments)
                sent_email += 1 if queued else 0
                log_event(db, loan_id, event_type, stage, channel, person, 'email',
                          'queued' if queued else 'skipped',
                          '' if queued else 'Email delivery is disabled or not configured')
            except Exception as exc:
                log_event(db, loan_id, EVENT_FAILED, stage, channel, person, 'email',
                          'failed', str(exc))
    return sent_inapp, sent_email


def _touch_alert_columns(db, loan_id, reminder=False, escalated=False):
    """Keep the alert counters on the loan row itself so the loans list can show
    'alerted 3x, last 2h ago' without a join."""
    now = datetime.now()
    try:
        db.execute(
            'UPDATE loans SET alert_count = COALESCE(alert_count, 0) + 1, '
            'last_alert_at = ?, first_alert_at = COALESCE(first_alert_at, ?) WHERE id = ?',
            (now, now, loan_id))
        if reminder:
            db.execute('UPDATE loans SET last_reminder_at = ? WHERE id = ?', (now, loan_id))
        if escalated:
            db.execute('UPDATE loans SET escalated_at = ? WHERE id = ?', (now, loan_id))
    except Exception:
        log.exception('Could not update alert counters on loan %s', loan_id)


def _application_pdf(db, loan_id):
    """(attachments, filename) for the alert email — [] when PDFs are switched
    off or reportlab is unavailable."""
    if not _flag(db, 'loan_alert_attach_pdf', '1'):
        return [], ''
    pdf_bytes, filename = build_loan_application_pdf(db, loan_id)
    if not pdf_bytes:
        return [], ''
    return [{'filename': filename, 'content': pdf_bytes,
             'mimetype': 'application/pdf'}], filename


# ── Email bodies ──────────────────────────────────────────────────────────────

def _summary_rows(ctx):
    loan, member = ctx['loan'], ctx['member']
    name = 'Member'
    if member:
        name = f"{member['first_name'] or ''} {member['last_name'] or ''}".strip() or 'Member'
    return [
        ('Applicant', f"{name} ({(member['member_number'] if member else '') or 'no member number'})"),
        ('Reference', loan['loan_number'] or f"#{loan['id']}"),
        ('Amount requested', money(loan['amount'])),
        ('Purpose', loan['purpose'] or '—'),
        ('Tenure', f"{loan['tenure'] or 0} months at {float(loan['interest_rate'] or 0):g}%"),
        ('Monthly repayment', money(ctx['monthly_payment'])),
        ('Total repayable', money(ctx['total_repayment'])),
        ('Savings balance', money(ctx['savings_balance'])),
        ('Submitted', str(loan['date_applied'] or '')[:16]),
        ('Current stage', ctx['stage_label']),
    ]


def _alert_html(headline, lead_paragraph, rows, action_url, cta_label,
                attached_name='', footer_note=''):
    cells = ''.join(
        f'<tr><td style="padding:6px 10px;background:#f1f4f9;border:1px solid #dee2e6;'
        f'font-weight:bold;width:42%;">{label}</td>'
        f'<td style="padding:6px 10px;border:1px solid #dee2e6;">{value}</td></tr>'
        for label, value in rows
    )
    attachment_line = (
        f'<p style="margin:16px 0 0;padding:10px 12px;background:#fff8e5;border-left:4px solid #f4b51c;">'
        f'The member\'s full application is attached to this email as '
        f'<strong>{attached_name}</strong> — applicant details, accepted repayment schedule, '
        f'guarantors, consents and due-diligence status.</p>'
        if attached_name else ''
    )
    button = (
        f'<p style="margin:22px 0 0;"><a href="{action_url}" '
        f'style="background:#082b66;color:#ffffff;text-decoration:none;padding:12px 22px;'
        f'border-radius:6px;display:inline-block;font-weight:bold;">{cta_label}</a></p>'
        if action_url else ''
    )
    note = (f'<p style="margin:18px 0 0;color:#6c757d;font-size:13px;">{footer_note}</p>'
            if footer_note else '')
    return (
        f'<h2 style="margin:0 0 10px;color:#082b66;font-size:19px;">{headline}</h2>'
        f'<p style="margin:0 0 14px;">{lead_paragraph}</p>'
        f'<table style="border-collapse:collapse;width:100%;font-size:14px;">{cells}</table>'
        f'{attachment_line}{button}{note}'
    )


# ── Public API ────────────────────────────────────────────────────────────────

def notify_loan_submitted(db, loan_id, channel='portal', commit=True):
    """Fan out the instant alert for a newly submitted loan request.

    Call this right after the application has been committed. Every configured
    officer gets an in-app notification, a push (via notify) and an email with
    the application PDF attached — regardless of which approval stage the loan
    starts in, because the committee wants to know a request exists even while
    guarantors are still consenting.

    Returns the number of recipients alerted (0 if alerts are off).
    """
    try:
        if not alerts_enabled(db):
            log_event(db, loan_id, EVENT_SUBMITTED, channel=channel, status='skipped',
                      detail='Loan request alerts are disabled in settings')
            if commit:
                db.commit()
            return 0

        ctx = loan_application_context(db, loan_id)
        if not ctx:
            return 0
        loan = ctx['loan']
        stage = ctx['stage']
        now = datetime.now()
        try:
            db.execute('UPDATE loans SET submission_channel = COALESCE(submission_channel, ?), '
                       'stage_entered_at = COALESCE(stage_entered_at, ?) WHERE id = ?',
                       (channel, now, loan_id))
        except Exception:
            log.exception('Could not stamp submission metadata on loan %s', loan_id)

        log_event(db, loan_id, EVENT_SUBMITTED, stage, channel,
                  detail=f"Loan request logged: {money(loan['amount'])} {loan['purpose'] or ''}".strip())

        member_name = 'A member'
        if ctx['member']:
            member_name = (f"{ctx['member']['first_name'] or ''} "
                           f"{ctx['member']['last_name'] or ''}").strip() or 'A member'

        owner_label = lw.STAGE_ACTOR_LABEL.get(stage, '')
        if stage == lw.STAGE_GUARANTORS:
            next_action = ('Guarantor consent is being collected now; the Secretary review '
                           'starts automatically as soon as it completes.')
        elif owner_label:
            next_action = f'Next action sits with the {owner_label}.'
        else:
            next_action = 'Please review it in the system.'

        attachments, attached_name = _application_pdf(db, loan_id)
        action_url = loan_link(db, loan_id)
        title = f"New Loan Request — {money(loan['amount'])}"
        message = (f"{member_name} requested {money(loan['amount'])} "
                   f"({loan['purpose'] or 'loan'}, {loan['tenure'] or 0} months). "
                   f"Ref {loan['loan_number']}. {next_action}")
        html = _alert_html(
            'New loan request submitted',
            f'<strong>{member_name}</strong> just submitted a loan request through the '
            f'cooperative app. It is logged and waiting for the management committee.',
            _summary_rows(ctx), action_url, 'Open the application',
            attached_name,
            f'{next_action} You are receiving this because you hold an office that reviews '
            f'loan requests. Reply times are tracked against the cooperative service standard.',
        )

        recipients = alert_recipients(db)
        _dispatch(db, loan_id, recipients, title, message, staff_loan_path(loan_id),
                  EVENT_ALERT, stage, channel, html, attachments,
                  notification_type='warning')
        _touch_alert_columns(db, loan_id)

        # The applicant gets the receipt an e-commerce buyer would expect.
        _confirm_to_applicant(db, ctx, channel)

        if commit:
            db.commit()
        return len(recipients)
    except Exception:
        log.exception('Loan submission alert failed for loan %s', loan_id)
        try:
            if commit:
                db.commit()
        except Exception:
            pass
        return 0


def _confirm_to_applicant(db, ctx, channel):
    """Acknowledge receipt to the member — they should never have to phone in to
    find out whether their request arrived."""
    from utils import notify_member
    from email_service import send_email

    member, loan = ctx['member'], ctx['loan']
    if not member or not member['email']:
        return
    stage_note = ('We are collecting your guarantors\' consent now.'
                  if ctx['stage'] == lw.STAGE_GUARANTORS
                  else f"It is now with the {lw.STAGE_ACTOR_LABEL.get(ctx['stage'], 'committee')}.")
    message = (f"We received your {money(loan['amount'])} loan request "
               f"(ref {loan['loan_number']}). The management committee has been notified. "
               f"{stage_note}")
    notify_member(db, member['email'], 'Loan Request Received', message, 'success', '/my-loans')
    try:
        send_email(
            member['email'], 'We received your loan request',
            _alert_html(
                'Your loan request has been received',
                f"Dear {member['first_name'] or 'Member'}, your request was logged and the "
                f"management committee has been alerted automatically.",
                [(label, value) for label, value in _summary_rows(ctx)
                 if label not in ('Applicant', 'Savings balance')],
                '', '', '',
                f'{stage_note} You can track progress at any time under "My Loans" in the app.'),
            background=True)
    except Exception:
        log.exception('Applicant loan receipt email failed')


def notify_stage_advanced(db, loan_id, new_stage, actor_name='', commit=False):
    """Alert the officer who owns the new stage (with the application attached),
    and log the handover."""
    try:
        if not alerts_enabled(db):
            return 0
        ctx = loan_application_context(db, loan_id)
        if not ctx:
            return 0
        loan = ctx['loan']
        now = datetime.now()
        try:
            db.execute('UPDATE loans SET stage_entered_at = ?, last_reminder_at = NULL, '
                       'escalated_at = NULL WHERE id = ?', (now, loan_id))
        except Exception:
            log.exception('Could not stamp stage entry on loan %s', loan_id)

        stage_label = lw.STAGE_LABELS.get(new_stage, new_stage)
        log_event(db, loan_id, EVENT_STAGE, new_stage,
                  detail=f'Moved to {stage_label}' + (f' by {actor_name}' if actor_name else ''))

        owner_role = _stage_owner_role(new_stage)
        if not owner_role:
            if commit:
                db.commit()
            return 0
        recipients = alert_recipients(db, roles=(owner_role,))
        if not recipients:
            # Nobody holds that office — tell the President so it cannot stall.
            recipients = alert_recipients(db, roles=('admin',))
            log_event(db, loan_id, EVENT_ESCALATED, new_stage, status='sent',
                      detail=f'No active user holds the {owner_role} role; alerted the President')

        attachments, attached_name = _application_pdf(db, loan_id)
        action_url = loan_link(db, loan_id)
        title = 'Loan Request Awaiting Your Approval'
        message = (f"Loan {loan['loan_number']} ({money(loan['amount'])}) is now "
                   f"{stage_label.lower()}. Please review and decide.")
        html = _alert_html(
            'A loan request is waiting for you',
            f"Loan <strong>{loan['loan_number']}</strong> has reached your desk: "
            f"<strong>{stage_label}</strong>.",
            _summary_rows(ctx), action_url, 'Review and decide', attached_name,
            'The cooperative tracks how long each stage takes — please act as soon as you can.')
        _dispatch(db, loan_id, recipients, title, message, staff_loan_path(loan_id),
                  EVENT_ALERT, new_stage, 'workflow', html, attachments,
                  notification_type='warning')
        _touch_alert_columns(db, loan_id)
        if commit:
            db.commit()
        return len(recipients)
    except Exception:
        log.exception('Stage alert failed for loan %s', loan_id)
        return 0


def notify_guarantors_complete(db, loan_id, commit=False):
    """Guarantor consent finished — hand the request to the Secretary loudly."""
    return notify_stage_advanced(db, loan_id, lw.STAGE_SECRETARY,
                                 actor_name='guarantor consent', commit=commit)


def mark_first_response(db, loan_id, user_id=None, user_name='', role=''):
    """Record the first time an officer opened a pending request (the response
    time an e-commerce team would call 'time to first touch'). Never raises."""
    try:
        row = db.execute('SELECT status, first_response_at FROM loans WHERE id = ?',
                         (loan_id,)).fetchone()
        if not row or row['status'] != 'pending' or row['first_response_at']:
            return False
        db.execute('UPDATE loans SET first_response_at = ? WHERE id = ?',
                   (datetime.now(), loan_id))
        log_event(db, loan_id, EVENT_VIEWED,
                  recipient={'user_id': user_id, 'name': user_name, 'role': role},
                  delivery='system', status='sent',
                  detail='First officer view of this request')
        db.commit()
        return True
    except Exception:
        log.exception('Could not record first response for loan %s', loan_id)
        return False


# ── Reminder / escalation sweep ───────────────────────────────────────────────

def _parse_ts(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip().replace('T', ' ')
    for fmt in ('%Y-%m-%d %H:%M:%S.%f', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%d'):
        try:
            return datetime.strptime(text[:26], fmt)
        except ValueError:
            continue
    return None


def pending_requests(db):
    """Pending loan requests with the age of their current stage, oldest first."""
    try:
        rows = db.execute('''
            SELECT l.*, m.first_name, m.last_name, m.member_number
            FROM loans l LEFT JOIN members m ON m.id = l.member_id
            WHERE l.status = 'pending'
            ORDER BY COALESCE(l.stage_entered_at, l.date_applied)
        ''').fetchall()
    except Exception:
        log.exception('Could not list pending loan requests')
        return []
    now = datetime.now()
    out = []
    for row in rows:
        entered = _parse_ts(row['stage_entered_at']) or _parse_ts(row['date_applied']) or now
        item = dict(row)
        item['stage_entered'] = entered
        item['hours_waiting'] = max((now - entered).total_seconds() / 3600.0, 0.0)
        item['stage_label'] = lw.STAGE_LABELS.get(row['approval_stage'] or '', row['approval_stage'] or '')
        out.append(item)
    return out


def run_pipeline_sweep(db, now=None, commit=True):
    """Chase every pending loan request that has gone quiet.

    * Past the SLA  → remind the officer who owns the current stage.
    * Past the escalation window → alert the President and all exco.
    * Guarantor stage → remind the guarantors who have not answered.

    Safe to run as often as you like: `loan_alert_reminder_hours` throttles how
    often the same loan can be chased. Returns a summary dict.
    """
    summary = {'checked': 0, 'reminded': 0, 'escalated': 0, 'guarantors_chased': 0}
    try:
        if not alerts_enabled(db):
            return summary
        now = now or datetime.now()
        sla = _hours(db, 'loan_alert_sla_hours', 24)
        gap = _hours(db, 'loan_alert_reminder_hours', 12)
        escalate_after = _hours(db, 'loan_alert_escalate_hours', 48)

        for item in pending_requests(db):
            summary['checked'] += 1
            stage = item['approval_stage'] or lw.STAGE_SECRETARY
            waiting = item['hours_waiting']
            if waiting < sla:
                continue
            last_chase = _parse_ts(item.get('last_reminder_at'))
            if last_chase and (now - last_chase) < timedelta(hours=gap):
                continue

            if stage == lw.STAGE_GUARANTORS:
                if _chase_guarantors(db, item, waiting):
                    summary['guarantors_chased'] += 1
                continue

            escalating = waiting >= escalate_after
            if _chase_officers(db, item, waiting, escalating):
                summary['escalated' if escalating else 'reminded'] += 1

        if commit:
            db.commit()
    except Exception:
        log.exception('Loan pipeline sweep failed')
        try:
            if commit:
                db.rollback()
        except Exception:
            pass
    return summary


def _chase_officers(db, item, waiting_hours, escalating):
    loan_id = item['id']
    stage = item['approval_stage'] or lw.STAGE_SECRETARY
    ctx = loan_application_context(db, loan_id)
    if not ctx:
        return False
    owner_role = _stage_owner_role(stage)
    owner_label = lw.STAGE_ACTOR_LABEL.get(stage, 'approver')
    applicant = f"{item.get('first_name') or ''} {item.get('last_name') or ''}".strip() or 'A member'
    waited = f'{waiting_hours:.0f} hour{"s" if waiting_hours >= 2 else ""}'

    if escalating:
        recipients = alert_recipients(db)          # President + every officer
        title = 'OVERDUE Loan Request — Escalated'
        headline = 'A loan request has been waiting too long'
        lead = (f"Loan <strong>{item['loan_number']}</strong> from <strong>{applicant}</strong> "
                f"has been sitting at the <strong>{owner_label}</strong> stage for {waited} "
                f"with no decision. This is escalated to the President and the full exco.")
        event = EVENT_ESCALATED
    else:
        recipients = alert_recipients(db, roles=(owner_role,)) or alert_recipients(db, roles=('admin',))
        title = 'Reminder — Loan Request Awaiting You'
        headline = 'This loan request is still waiting'
        lead = (f"Loan <strong>{item['loan_number']}</strong> from <strong>{applicant}</strong> "
                f"has been awaiting your {owner_label} decision for {waited}.")
        event = EVENT_REMINDER

    attachments, attached_name = _application_pdf(db, loan_id)
    message = (f"Loan {item['loan_number']} ({money(item['amount'])}) has waited {waited} at the "
               f"{owner_label} stage. Please act now.")
    html = _alert_html(headline, lead, _summary_rows(ctx), loan_link(db, loan_id),
                       'Open the application', attached_name,
                       'Members see how long approvals take — a fast decision, even a decline, '
                       'is better than silence.')
    _dispatch(db, loan_id, recipients, title, message, staff_loan_path(loan_id),
              event, stage, 'sweep', html, attachments, notification_type='warning')
    _touch_alert_columns(db, loan_id, reminder=True, escalated=escalating)
    return bool(recipients)


def _chase_guarantors(db, item, waiting_hours):
    """Nudge guarantors who have not answered — plus the Secretary, so the
    committee knows the request is stuck outside its own hands."""
    from utils import notify_member
    from email_service import send_guarantor_request_email

    loan_id = item['id']
    pending = db.execute('''
        SELECT lg.id, m.first_name, m.last_name, m.email
        FROM loan_guarantors lg JOIN members m ON m.id = lg.member_id
        WHERE lg.loan_id = ? AND lg.status = 'pending'
    ''', (loan_id,)).fetchall()
    if not pending:
        return False
    applicant = f"{item.get('first_name') or ''} {item.get('last_name') or ''}".strip() or 'A member'
    for guarantor in pending:
        if not guarantor['email']:
            continue
        notify_member(db, guarantor['email'], 'Guarantor Response Still Needed',
                      f"{applicant} is still waiting for your guarantor decision on loan "
                      f"{item['loan_number']} ({money(item['amount'])}).",
                      'warning', '/my-guarantor-requests')
        try:
            send_guarantor_request_email(guarantor['email'], dict(guarantor),
                                         {'first_name': item.get('first_name') or '',
                                          'last_name': item.get('last_name') or ''},
                                         item['loan_number'], item['amount'])
        except Exception:
            log.exception('Guarantor reminder email failed')
        log_event(db, loan_id, EVENT_REMINDER, lw.STAGE_GUARANTORS, 'sweep',
                  {'name': f"{guarantor['first_name']} {guarantor['last_name']}".strip(),
                   'email': guarantor['email'], 'role': 'guarantor'},
                  'email', 'queued', 'Guarantor consent reminder')

    secretaries = alert_recipients(db, roles=('secretary', 'admin'))
    message = (f"Loan {item['loan_number']} ({money(item['amount'])}) has waited "
               f"{waiting_hours:.0f}h for guarantor consent — {len(pending)} guarantor(s) "
               f"have not responded.")
    _dispatch(db, loan_id, secretaries, 'Loan Request Stuck on Guarantors', message,
              staff_loan_path(loan_id), EVENT_REMINDER, lw.STAGE_GUARANTORS, 'sweep',
              notification_type='warning')
    _touch_alert_columns(db, loan_id, reminder=True)
    return True


# ── Reporting ─────────────────────────────────────────────────────────────────

def pipeline_snapshot(db):
    """Counts the loans dashboard shows: how many requests are waiting, how long
    the oldest has waited, and how many have breached the service standard."""
    sla = _hours(db, 'loan_alert_sla_hours', 24)
    items = pending_requests(db)
    overdue = [i for i in items if i['hours_waiting'] >= sla]
    never_alerted = [i for i in items if not i.get('first_alert_at')]
    return {
        'pending': len(items),
        'overdue': len(overdue),
        'never_alerted': len(never_alerted),
        'sla_hours': sla,
        'oldest_hours': max((i['hours_waiting'] for i in items), default=0.0),
        'items': items,
    }
