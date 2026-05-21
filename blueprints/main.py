from flask import Blueprint, render_template
from flask_login import login_required

from database import get_db

main = Blueprint('main', __name__)


@main.route('/dashboard')
@login_required
def dashboard():
    db = get_db()

    members_count      = db.execute('SELECT COUNT(*) FROM members').fetchone()[0] or 0
    total_savings      = db.execute('SELECT SUM(amount) FROM savings').fetchone()[0] or 0
    total_loans        = db.execute('SELECT SUM(amount) FROM loans WHERE status = "active"').fetchone()[0] or 0
    total_investments  = db.execute('SELECT SUM(amount) FROM investments').fetchone()[0] or 0

    recent_savings = db.execute('''
        SELECT s.*, m.first_name || " " || m.last_name as member_name
        FROM savings s JOIN members m ON s.member_id = m.id
        ORDER BY s.date DESC LIMIT 5
    ''').fetchall()

    recent_loans = db.execute('''
        SELECT l.*, m.first_name || " " || m.last_name as member_name
        FROM loans l JOIN members m ON l.member_id = m.id
        ORDER BY l.date_applied DESC LIMIT 5
    ''').fetchall()

    return render_template('dashboard.html',
                           members_count=members_count,
                           total_savings=total_savings,
                           total_loans=total_loans,
                           total_investments=total_investments,
                           recent_savings=recent_savings,
                           recent_loans=recent_loans)
