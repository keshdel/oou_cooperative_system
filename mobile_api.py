"""
REST API for Mobile App Integration
All routes are prefixed with /api/mobile/
"""

from flask import Blueprint, jsonify, request, current_app, g
from functools import wraps
from werkzeug.security import check_password_hash
from database import get_db
import jwt
import datetime
import random

mobile_api = Blueprint('mobile_api', __name__)


# ── JWT helpers ───────────────────────────────────────────────────────────────

def _generate_token(user_id, username, role):
    payload = {
        'user_id': user_id,
        'username': username,
        'role': role,
        'iat': datetime.datetime.utcnow(),
        'exp': datetime.datetime.utcnow() + datetime.timedelta(days=7),
    }
    return jwt.encode(payload, current_app.config['SECRET_KEY'], algorithm='HS256')


def jwt_required(f):
    """Decorator — validates Bearer JWT and stores payload in flask.g."""
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.headers.get('Authorization', '')
        if not auth.startswith('Bearer '):
            return jsonify({'success': False, 'error': 'Missing Authorization header'}), 401
        token = auth[7:]
        try:
            payload = jwt.decode(
                token,
                current_app.config['SECRET_KEY'],
                algorithms=['HS256']
            )
            g.user_id  = payload['user_id']
            g.username = payload['username']
            g.role     = payload['role']
        except jwt.ExpiredSignatureError:
            return jsonify({'success': False, 'error': 'Token has expired'}), 401
        except jwt.InvalidTokenError:
            return jsonify({'success': False, 'error': 'Invalid token'}), 401
        return f(*args, **kwargs)
    return decorated


# ── Data helpers ──────────────────────────────────────────────────────────────

def _get_member(db, user_id):
    """Find the member record linked to a system user via matching email."""
    user = db.execute('SELECT email FROM users WHERE id = ?', (user_id,)).fetchone()
    if not user or not user['email']:
        return None
    return db.execute('SELECT * FROM members WHERE email = ?', (user['email'],)).fetchone()


def _get_savings(db, member_id):
    rows = db.execute(
        '''SELECT amount, month, late_fee, date, receipt_number
           FROM savings WHERE member_id = ?
           ORDER BY date DESC LIMIT 12''',
        (member_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def _get_loans(db, member_id):
    rows = db.execute(
        '''SELECT loan_number, amount, purpose, tenure, interest_rate,
                  total_repayment, balance, status, date_applied
           FROM loans WHERE member_id = ?
           ORDER BY date_applied DESC''',
        (member_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def _get_transactions(db, member_id, limit=10):
    savings = db.execute(
        '''SELECT 'saving' as type, amount, date, receipt_number as reference
           FROM savings WHERE member_id = ?
           ORDER BY date DESC LIMIT ?''',
        (member_id, limit)
    ).fetchall()
    repayments = db.execute(
        '''SELECT 'repayment' as type, r.amount, r.date, r.repayment_number as reference
           FROM repayments r
           JOIN loans l ON r.loan_id = l.id
           WHERE l.member_id = ?
           ORDER BY r.date DESC LIMIT ?''',
        (member_id, limit)
    ).fetchall()
    combined = [dict(r) for r in savings] + [dict(r) for r in repayments]
    combined.sort(key=lambda x: x.get('date') or '', reverse=True)
    return combined[:limit]


def _get_notifications(db, user_id, limit=10):
    rows = db.execute(
        '''SELECT title, message, notification_type, is_read, created_at
           FROM notifications WHERE user_id = ?
           ORDER BY created_at DESC LIMIT ?''',
        (user_id, limit)
    ).fetchall()
    return [dict(r) for r in rows]


def _card_data(member):
    return {
        'member_number': member['member_number'],
        'full_name':     f"{member['first_name']} {member['last_name']}",
        'status':        member['status'],
        'card_number':   member['card_number'],
        'card_status':   member['card_status'],
        'date_joined':   str(member['date_joined'])[:10] if member['date_joined'] else '',
        'total_savings': float(member['total_savings'] or 0),
    }


# ── Routes ────────────────────────────────────────────────────────────────────

@mobile_api.route('/api/mobile/login', methods=['POST'])
def mobile_login():
    """Authenticate and return a 7-day JWT token."""
    data = request.get_json(silent=True) or {}
    username = (data.get('username') or '').strip()
    password = data.get('password') or ''

    if not username or not password:
        return jsonify({'success': False, 'error': 'username and password are required'}), 400

    db = get_db()
    user = db.execute(
        'SELECT * FROM users WHERE username = ? AND is_active = 1', (username,)
    ).fetchone()

    if not user or not check_password_hash(user['password_hash'], password):
        return jsonify({'success': False, 'error': 'Invalid credentials'}), 401

    token = _generate_token(user['id'], user['username'], user['role'])
    return jsonify({
        'success': True,
        'token':   token,
        'user': {
            'id':       user['id'],
            'username': user['username'],
            'role':     user['role'],
            'email':    user['email'] or '',
        },
    })


@mobile_api.route('/api/mobile/dashboard')
@jwt_required
def mobile_dashboard():
    """Return savings, loans, recent transactions and notifications for the user."""
    db     = get_db()
    member = _get_member(db, g.user_id)

    if not member:
        return jsonify({
            'success': False,
            'error': 'No member profile is linked to this account. Contact admin.'
        }), 404

    mid = member['id']
    return jsonify({
        'success': True,
        'member': {
            'name':          f"{member['first_name']} {member['last_name']}",
            'member_number': member['member_number'],
            'total_savings': float(member['total_savings'] or 0),
            'status':        member['status'],
        },
        'savings':              _get_savings(db, mid),
        'loans':                _get_loans(db, mid),
        'recent_transactions':  _get_transactions(db, mid),
        'notifications':        _get_notifications(db, g.user_id),
    })


@mobile_api.route('/api/mobile/card')
@jwt_required
def mobile_card():
    """Return digital membership card data."""
    db     = get_db()
    member = _get_member(db, g.user_id)

    if not member:
        return jsonify({'success': False, 'error': 'Member profile not found'}), 404

    return jsonify({'success': True, 'card': _card_data(member)})


@mobile_api.route('/api/mobile/pay', methods=['POST'])
@jwt_required
def mobile_payment():
    """Record a loan repayment made from the mobile app."""
    data    = request.get_json(silent=True) or {}
    loan_id = data.get('loan_id')
    amount  = data.get('amount')

    if not loan_id or amount is None:
        return jsonify({'success': False, 'error': 'loan_id and amount are required'}), 400

    try:
        amount = float(amount)
        if amount <= 0:
            return jsonify({'success': False, 'error': 'amount must be greater than zero'}), 400
    except (TypeError, ValueError):
        return jsonify({'success': False, 'error': 'amount must be a number'}), 400

    db     = get_db()
    member = _get_member(db, g.user_id)
    if not member:
        return jsonify({'success': False, 'error': 'Member profile not found'}), 404

    loan = db.execute(
        'SELECT * FROM loans WHERE id = ? AND member_id = ? AND status = "active"',
        (loan_id, member['id'])
    ).fetchone()

    if not loan:
        return jsonify({'success': False, 'error': 'Active loan not found for this account'}), 404

    try:
        repayment_number = f"REP/{datetime.datetime.now().strftime('%Y%m%d')}/{random.randint(1000,9999)}"
        new_balance      = max(0.0, float(loan['balance'] or 0) - amount)
        new_status       = 'completed' if new_balance == 0 else 'active'

        db.execute(
            '''INSERT INTO repayments (repayment_number, loan_id, amount, payment_method, date)
               VALUES (?, ?, ?, ?, ?)''',
            (repayment_number, loan_id, amount, 'mobile', datetime.datetime.now())
        )
        db.execute(
            'UPDATE loans SET balance = ?, status = ? WHERE id = ?',
            (new_balance, new_status, loan_id)
        )
        db.commit()

        return jsonify({
            'success':          True,
            'repayment_number': repayment_number,
            'amount_paid':      amount,
            'new_balance':      new_balance,
            'loan_status':      new_status,
        })
    except Exception as e:
        db.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500
