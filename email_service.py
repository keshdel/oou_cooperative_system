"""
email_service.py — Outgoing email via Resend (https://resend.com).

Configuration (either env var OR Settings → Email in the admin UI):
  RESEND_API_KEY   your Resend API key  (required to send)
  MAIL_FROM        sender address shown in recipient inbox
                   format: "Name <email@domain.com>"
                   default: "OOU Cooperative <noreply@cooperative.com>"

All send_* functions are fire-and-forget: they log failures but never
raise exceptions, so an email error never crashes the main request.
"""
import os
import logging

log = logging.getLogger(__name__)


# ── Config helpers ─────────────────────────────────────────────────────────────

def _get_setting(key: str, env_fallback: str = '') -> str:
    """Read a value from the DB settings table, falling back to an env var."""
    val = os.environ.get(env_fallback or key.upper(), '').strip()
    if val:
        return val
    try:
        from database import get_db
        db = get_db()
        row = db.execute('SELECT value FROM settings WHERE key = ?', (key,)).fetchone()
        return (row['value'] or '').strip() if row else ''
    except Exception:
        return ''


def _api_key() -> str:
    return _get_setting('resend_api_key', 'RESEND_API_KEY')


def _from_addr() -> str:
    addr = _get_setting('mail_from', 'MAIL_FROM')
    return addr or 'OOU Cooperative <noreply@cooperative.com>'


# ── Core send ──────────────────────────────────────────────────────────────────

def send_email(to: str, subject: str, html: str, text: str = '') -> bool:
    """
    Send one email via Resend.
    Returns True on success, False on any failure (key missing, API error, etc.).
    """
    api_key = _api_key()
    if not api_key:
        log.warning('Resend API key not configured — email skipped: "%s"', subject)
        return False
    try:
        import resend
        resend.api_key = api_key
        params = {
            'from':    _from_addr(),
            'to':      [to] if isinstance(to, str) else list(to),
            'subject': subject,
            'html':    html,
        }
        if text:
            params['text'] = text
        resend.Emails.send(params)
        log.info('Email sent via Resend: "%s" → %s', subject, to)
        return True
    except Exception as exc:
        log.error('Resend send failed ("%s" → %s): %s', subject, to, exc)
        return False


# ── Public send helpers ────────────────────────────────────────────────────────

def send_welcome_email(recipient: str, member: dict) -> None:
    try:
        from flask import render_template
        html = render_template('emails/welcome.html', member=member, login_url='')
    except Exception:
        full_name = member.get('full_name', 'Member')
        num = member.get('member_number', '')
        html = (
            f'<p>Dear {full_name},</p>'
            f'<p>Welcome to OOU Cooperative! Your member number is <strong>{num}</strong>.</p>'
            f'<p>Please log in to your member portal to view your account.</p>'
        )
    send_email(recipient, 'Welcome to OOU Cooperative!', html)


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
                               member=member, rejection_reason=rejection_reason,
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
    send_email(recipient, 'Payment Confirmation — OOU Cooperative', html)


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
    send_email(recipient, 'Reset Your Password — OOU Cooperative', html)
