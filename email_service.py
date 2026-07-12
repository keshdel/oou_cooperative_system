"""
email_service.py — Outgoing email, two back-ends supported:

  1. Resend API  (requires a verified domain — best for production)
  2. SMTP relay  (works with any email address — good for getting started fast)
     Recommended provider when you have no domain: Brevo (brevo.com, free,
     300 emails/day, verify sender email only — no domain needed).

Priority:  Resend is tried first if RESEND_API_KEY is set.
           SMTP is used if smtp_host + smtp_user + smtp_pass are configured.

All send_* helpers are fire-and-forget: they log failures but never raise,
so an email error never crashes the main request.
"""
import os
import json
import logging
import smtplib
import ssl
import urllib.error
import urllib.request
from email.utils import parseaddr
from email.mime.multipart import MIMEMultipart
from email.mime.text      import MIMEText

log = logging.getLogger(__name__)


# ── Config helpers ─────────────────────────────────────────────────────────────

def _db_setting(key: str) -> str:
    """Read one value from the settings table (returns '' on any error)."""
    try:
        from database import get_db
        db  = get_db()
        row = db.execute('SELECT value FROM settings WHERE key = ?', (key,)).fetchone()
        return (row['value'] or '').strip() if row else ''
    except Exception:
        return ''


def _env_first(*names: str) -> str:
    """Return the first non-empty environment value from a list of aliases."""
    for name in names:
        value = os.environ.get(name, '').strip()
        if value:
            return value
    return ''


def _cfg(env_var: str, db_key: str, *aliases: str) -> str:
    """Env var takes precedence; falls back to DB setting."""
    return _env_first(env_var, *aliases) or _db_setting(db_key)


def _truthy(value: str) -> bool:
    return value.strip().lower() in {'1', 'true', 'yes', 'on'}


def _is_enabled() -> bool:
    configured = _cfg('MAIL_ENABLED', 'mail_enabled', 'ENABLE_EMAIL_NOTIFICATIONS')
    return _truthy(configured)


# ── Resend back-end ────────────────────────────────────────────────────────────

def _send_via_resend(to: str, subject: str, html: str) -> bool:
    api_key  = _cfg('RESEND_API_KEY', 'resend_api_key')
    from_addr = _cfg('MAIL_FROM',      'mail_from') or 'OOU Cooperative <noreply@cooperative.com>'
    if not api_key:
        return False
    try:
        import resend
        resend.api_key = api_key
        resend.Emails.send({
            'from':    from_addr,
            'to':      [to] if isinstance(to, str) else list(to),
            'subject': subject,
            'html':    html,
        })
        log.info('Resend OK: "%s" → %s', subject, to)
        return True
    except Exception as exc:
        log.error('Resend failed ("%s" → %s): %s', subject, to, exc)
        return False


# ── SMTP back-end (Gmail, Brevo, Outlook, any provider) ───────────────────────

def _sender_from_address(from_addr: str) -> dict:
    """Convert 'Name <email@example.com>' into Brevo's sender object."""
    name, email = parseaddr(from_addr)
    sender = {'email': email or from_addr}
    if name:
        sender['name'] = name
    return sender


def _recipient_list(to) -> list:
    recipients = [to] if isinstance(to, str) else list(to)
    return [{'email': parseaddr(recipient)[1] or recipient} for recipient in recipients]


def _send_via_brevo(to: str, subject: str, html: str, text: str = '') -> bool:
    api_key = _cfg('BREVO_API_KEY', 'brevo_api_key', 'SENDINBLUE_API_KEY')
    from_addr = (
        _cfg('MAIL_FROM', 'mail_from', 'MAIL_DEFAULT_SENDER', 'COOP_EMAIL')
        or 'OOU Cooperative <noreply@cooperative.com>'
    )
    if not api_key:
        return False

    payload = {
        'sender': _sender_from_address(from_addr),
        'to': _recipient_list(to),
        'subject': subject,
        'htmlContent': html,
    }
    if text:
        payload['textContent'] = text

    request = urllib.request.Request(
        'https://api.brevo.com/v3/smtp/email',
        data=json.dumps(payload).encode('utf-8'),
        headers={
            'accept': 'application/json',
            'api-key': api_key,
            'content-type': 'application/json',
        },
        method='POST',
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            if 200 <= response.status < 300:
                log.info('Brevo API OK: "%s" â†’ %s', subject, to)
                return True
            log.error('Brevo API failed ("%s" â†’ %s): HTTP %s',
                      subject, to, response.status)
            return False
    except urllib.error.HTTPError as exc:
        body = exc.read(500).decode('utf-8', errors='replace')
        log.error('Brevo API failed ("%s" â†’ %s): HTTP %s %s',
                  subject, to, exc.code, body)
        return False
    except Exception as exc:
        log.error('Brevo API failed ("%s" â†’ %s): %s', subject, to, exc)
        return False


def _send_via_smtp(to: str, subject: str, html: str, text: str = '') -> bool:
    host     = _cfg('SMTP_HOST',     'smtp_host', 'MAIL_SERVER')
    port_str = _cfg('SMTP_PORT',     'smtp_port', 'MAIL_PORT') or '587'
    user     = _cfg('SMTP_USER',     'smtp_user', 'MAIL_USERNAME')
    password = _cfg('SMTP_PASS',     'smtp_pass', 'MAIL_PASSWORD')
    from_addr = (
        _cfg('MAIL_FROM', 'mail_from', 'MAIL_DEFAULT_SENDER', 'COOP_EMAIL')
        or user
    )
    use_ssl = _truthy(_env_first('SMTP_USE_SSL', 'MAIL_USE_SSL'))
    use_tls = _truthy(_env_first('SMTP_USE_TLS', 'MAIL_USE_TLS'))

    if not (host and user and password):
        return False

    try:
        port = int(port_str)
        msg  = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From']    = from_addr
        msg['To']      = to
        if text:
            msg.attach(MIMEText(text, 'plain'))
        msg.attach(MIMEText(html, 'html'))

        ctx = ssl.create_default_context()
        smtp_cls = smtplib.SMTP_SSL if use_ssl else smtplib.SMTP
        with smtp_cls(host, port, timeout=10) as server:
            server.ehlo()
            if use_tls and not use_ssl:
                server.starttls(context=ctx)
            server.login(user, password)
            server.sendmail(from_addr, [to], msg.as_string())
        log.info('SMTP OK: "%s" → %s', subject, to)
        return True
    except Exception as exc:
        log.error('SMTP failed ("%s" → %s): %s', subject, to, exc)
        return False


# ── Core send (tries Resend first, then SMTP) ─────────────────────────────────

def send_email(to: str, subject: str, html: str, text: str = '') -> bool:
    """
    Send one transactional email.
    Tries Resend if configured, falls back to SMTP.
    Returns True on success, False on failure / not configured.
    """
    if not _is_enabled():
        log.debug('Email disabled — skipped: "%s"', subject)
        return False

    if _cfg('RESEND_API_KEY', 'resend_api_key'):
        if _send_via_resend(to, subject, html):
            return True
        log.warning('Resend failed; trying next email provider for "%s"', subject)

    if _cfg('BREVO_API_KEY', 'brevo_api_key', 'SENDINBLUE_API_KEY'):
        if _send_via_brevo(to, subject, html, text):
            return True
        log.warning('Brevo API failed; trying SMTP fallback for "%s"', subject)

    if _cfg('SMTP_HOST', 'smtp_host', 'MAIL_SERVER'):
        return _send_via_smtp(to, subject, html, text)

    log.warning('No email provider configured — skipped: "%s"', subject)
    return False


# ── Public send helpers ────────────────────────────────────────────────────────

def send_welcome_email(recipient: str, member: dict) -> None:
    try:
        from flask import render_template
        html = render_template('emails/welcome.html', member=member, login_url='')
    except Exception:
        full_name = member.get('full_name', 'Member')
        num       = member.get('member_number', '')
        html = (
            f'<p>Dear {full_name},</p>'
            f'<p>Welcome to OOU Cooperative! Your member number is <strong>{num}</strong>.</p>'
            f'<p>Please log in to your member portal to view your account.</p>'
        )
    send_email(recipient, 'Welcome to OOU Cooperative!', html)


def send_member_onboarding_email(recipient: str, member: dict, username: str,
                                 temporary_password: str, login_url: str,
                                 profile_url: str = '') -> None:
    full_name = member.get('full_name') or 'Member'
    member_number = member.get('member_number') or ''
    html = (
        f'<p>Dear {full_name},</p>'
        f'<p>Your cooperative portal profile has been created.</p>'
        f'<table cellpadding="6" cellspacing="0" style="border-collapse:collapse">'
        f'<tr><td><strong>Member number</strong></td><td>{member_number}</td></tr>'
        f'<tr><td><strong>Username</strong></td><td>{username}</td></tr>'
        f'<tr><td><strong>Temporary password</strong></td><td>{temporary_password}</td></tr>'
        f'</table>'
        f'<p>Sign in with the temporary password, then choose your own password before continuing.</p>'
        f'<p><a href="{login_url}">Open member portal</a></p>'
    )
    if profile_url:
        html += f'<p>After setting your password, review your profile here: <a href="{profile_url}">{profile_url}</a></p>'
    html += '<p>If any profile detail is wrong, contact the cooperative office.</p>'

    text = (
        f'Dear {full_name},\n\n'
        f'Your cooperative portal profile has been created.\n'
        f'Member number: {member_number}\n'
        f'Username: {username}\n'
        f'Temporary password: {temporary_password}\n\n'
        f'Login: {login_url}\n'
        f'After login, choose your own password and review your profile.\n'
    )
    send_email(recipient, 'Set up your Cooperative Portal Account', html, text)


def send_loan_approval_email(recipient: str, member: dict,
                              loan: dict, loan_url: str = '') -> None:
    try:
        from flask import render_template
        html = render_template('emails/loan-approval.html',
                               member=member, loan=loan, loan_url=loan_url)
    except Exception:
        amount = loan.get('amount', 0)
        html = (
            f'<p>Dear {member.get("full_name", "Member")},</p>'
            f'<p>Your loan application for <strong>&#8358;{amount:,.2f}</strong> '
            f'has been <strong>approved</strong>.</p>'
            f'<p>The funds will be disbursed shortly. Log in to your portal for details.</p>'
        )
    send_email(recipient, 'Your Loan Has Been Approved!', html)


def send_loan_rejection_email(recipient: str, member: dict,
                               rejection_reason: str = '',
                               contact_url: str = '') -> None:
    try:
        from flask import render_template
        html = render_template('emails/loan-rejection.html',
                               member=member,
                               rejection_reason=rejection_reason,
                               contact_url=contact_url)
    except Exception:
        reason_line = f'<p>Reason: {rejection_reason}</p>' if rejection_reason else ''
        html = (
            f'<p>Dear {member.get("full_name", "Member")},</p>'
            f'<p>We regret to inform you that your loan application could not be '
            f'approved at this time.</p>'
            f'{reason_line}'
            f'<p>Please contact us if you have any questions.</p>'
        )
    send_email(recipient, 'Update on Your Loan Application', html)


def send_payment_confirmation_email(recipient: str, member: dict,
                                     transaction: dict,
                                     transaction_url: str = '') -> None:
    try:
        from flask import render_template
        html = render_template('emails/payment-confirmation.html',
                               member=member, transaction=transaction,
                               transaction_url=transaction_url)
    except Exception:
        amount = transaction.get('amount', 0)
        html = (
            f'<p>Dear {member.get("full_name", "Member")},</p>'
            f'<p>Your savings payment of <strong>&#8358;{amount:,.2f}</strong> '
            f'has been recorded successfully.</p>'
            f'<p>Log in to your portal to view your updated balance.</p>'
        )
    send_email(recipient, 'Payment Confirmation - OOU Cooperative', html)


def send_loan_repayment_email(recipient: str, member: dict, loan: dict,
                               repayment: dict, repayment_url: str = '') -> None:
    """Notify a member that a loan repayment was recorded."""
    full_name = member.get('full_name') or (
        f"{member.get('first_name', '')} {member.get('last_name', '')}".strip()
    ) or 'Member'
    amount = float(repayment.get('amount') or 0)
    balance = float(repayment.get('balance') or repayment.get('new_balance') or 0)
    principal = float(repayment.get('principal_paid') or 0)
    interest = float(repayment.get('interest_paid') or 0)
    date = repayment.get('date') or ''
    reference = repayment.get('repayment_number') or repayment.get('reference') or ''
    loan_number = loan.get('loan_number') or ''

    html = (
        f'<p>Dear {full_name},</p>'
        f'<p>A loan repayment has been recorded on your cooperative account.</p>'
        f'<table cellpadding="6" cellspacing="0" style="border-collapse:collapse">'
        f'<tr><td><strong>Loan number</strong></td><td>{loan_number}</td></tr>'
        f'<tr><td><strong>Repayment reference</strong></td><td>{reference}</td></tr>'
        f'<tr><td><strong>Date</strong></td><td>{date}</td></tr>'
        f'<tr><td><strong>Amount paid</strong></td><td>&#8358;{amount:,.2f}</td></tr>'
        f'<tr><td><strong>Principal portion</strong></td><td>&#8358;{principal:,.2f}</td></tr>'
        f'<tr><td><strong>Interest portion</strong></td><td>&#8358;{interest:,.2f}</td></tr>'
        f'<tr><td><strong>Outstanding balance</strong></td><td>&#8358;{balance:,.2f}</td></tr>'
        f'</table>'
    )
    if repayment_url:
        html += f'<p><a href="{repayment_url}">View your loan details</a></p>'
    html += '<p>Please contact the cooperative office if this entry does not match your records.</p>'

    text = (
        f'Dear {full_name},\n\n'
        f'Loan repayment recorded.\n'
        f'Loan: {loan_number}\nReference: {reference}\nDate: {date}\n'
        f'Amount paid: NGN {amount:,.2f}\nPrincipal: NGN {principal:,.2f}\n'
        f'Interest: NGN {interest:,.2f}\nOutstanding balance: NGN {balance:,.2f}\n'
    )
    send_email(recipient, 'Loan Repayment Recorded - OOU Cooperative', html, text)


def send_guarantor_request_email(recipient: str, guarantor: dict, applicant: dict,
                                 loan_number: str, amount: float) -> None:
    """Ask a member to stand as guarantor for a loan."""
    full = (f"{guarantor.get('first_name', '')} {guarantor.get('last_name', '')}".strip()
            or 'Member')
    app_name = f"{applicant['first_name']} {applicant['last_name']}"
    html = (
        f'<p>Dear {full},</p>'
        f'<p><strong>{app_name}</strong> has requested you to stand as guarantor for a '
        f'loan of <strong>&#8358;{float(amount):,.2f}</strong> (ref {loan_number}).</p>'
        f'<p>Please log in to your member portal to <strong>accept or decline</strong> this request.</p>'
    )
    send_email(recipient, 'Guarantor Request - OOU Cooperative', html)


def send_loan_stage_email(recipient: str, member: dict, loan_number: str,
                          stage_label: str) -> None:
    """Notify a member their loan advanced to a new approval stage."""
    full = (f"{member.get('first_name', '')} {member.get('last_name', '')}".strip()
            or 'Member')
    html = (
        f'<p>Dear {full},</p>'
        f'<p>Your loan application (ref {loan_number}) has progressed: '
        f'<strong>{stage_label}</strong>.</p>'
        f'<p>Log in to your member portal for details.</p>'
    )
    send_email(recipient, 'Loan Application Update - OOU Cooperative', html)


def send_password_reset_email(recipient: str, user: dict, reset_url: str) -> None:
    try:
        from flask import render_template
        html = render_template('emails/password-reset.html',
                               user=user, reset_url=reset_url)
    except Exception:
        html = (
            f'<p>Dear {user.get("full_name", user.get("username", "User"))},</p>'
            f'<p>Click the link below to reset your password (valid for 1 hour):</p>'
            f'<p><a href="{reset_url}">{reset_url}</a></p>'
            f'<p>If you did not request this, you can ignore this email.</p>'
        )
    send_email(recipient, 'Reset Your Password - OOU Cooperative', html)
