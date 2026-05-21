# email_service.py
from extensions import mail
from flask_mail import Message
from flask import render_template


def send_welcome_email(recipient, member):
    """Send welcome email to new member. `member` is a dict."""
    msg = Message(
        subject="Welcome to OOU Cooperative!",
        recipients=[recipient],
        sender=('OOU Cooperative', 'noreply@ooucoop.org')
    )
    msg.html = render_template('emails/welcome.html', member=member, login_url='https://yourapp.com/login')
    msg.body = (
        f"Dear {member['full_name']}, welcome to OOU Cooperative! "
        f"Your member number is {member['member_number']}."
    )
    mail.send(msg)


def send_loan_approval_email(recipient, member, loan, loan_url=None):
    """Send loan approval notification."""
    msg = Message(
        subject="Your Loan Has Been Approved!",
        recipients=[recipient]
    )
    msg.html = render_template('emails/loan-approval.html', member=member, loan=loan, loan_url=loan_url or '')
    mail.send(msg)


def send_loan_rejection_email(recipient, member, rejection_reason='', contact_url=''):
    """Send loan rejection notification."""
    msg = Message(
        subject="Update on Your Loan Application",
        recipients=[recipient]
    )
    msg.html = render_template('emails/loan-rejection.html', member=member, rejection_reason=rejection_reason, contact_url=contact_url)
    mail.send(msg)


def send_password_reset_email(recipient, user, reset_url):
    """Send password reset link."""
    msg = Message(
        subject="Reset Your Password - OOU Cooperative",
        recipients=[recipient]
    )
    msg.html = render_template('emails/password-reset.html', user=user, reset_url=reset_url)
    mail.send(msg)


def send_payment_confirmation_email(recipient, member, transaction, transaction_url=''):
    """Send payment receipt."""
    msg = Message(
        subject="Payment Confirmation - OOU Cooperative",
        recipients=[recipient]
    )
    msg.html = render_template('emails/payment-confirmation.html', member=member, transaction=transaction, transaction_url=transaction_url)
    mail.send(msg)
