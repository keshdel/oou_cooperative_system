import random
from datetime import datetime
from io import BytesIO

from flask import Blueprint, render_template, redirect, url_for, request, flash, make_response
from flask_login import login_required, current_user, logout_user
from werkzeug.security import check_password_hash, generate_password_hash

from database import get_db
from utils import audit

portal = Blueprint('portal', __name__)


@portal.route('/member/portal')
@login_required
def member_portal():
    return render_template('member/portal.html')


@portal.route('/my-savings')
@login_required
def my_savings():
    return render_template('member/my-savings.html')


@portal.route('/saving-detail/<int:saving_id>')
@login_required
def saving_detail(saving_id):
    return render_template('member/saving-detail.html')


@portal.route('/my-loans')
@login_required
def my_loans():
    return render_template('member/my-loans.html')


@portal.route('/loan-detail/<int:loan_id>')
@login_required
def loan_detail(loan_id):
    return render_template('member/loan-detail.html')


@portal.route('/apply-loan-member', methods=['GET', 'POST'])
@login_required
def apply_loan_member():
    db = get_db()

    max_tenure_row = db.execute("SELECT value FROM settings WHERE key = 'max_tenure_months'").fetchone()
    max_tenure = int(max_tenure_row['value']) if max_tenure_row else 18

    interest_rows = db.execute("SELECT key, value FROM settings WHERE key LIKE 'interest_%'").fetchall()
    interest_rates_raw = {row['key']: float(row['value']) for row in interest_rows}
    interest_rates = {
        'interest_regular':   interest_rates_raw.get('interest_regular', 11),
        'interest_housing':   interest_rates_raw.get('interest_housing', 9),
        'interest_emergency': interest_rates_raw.get('interest_emergency', 10),
        'interest_asset':     interest_rates_raw.get('interest_asset', 10),
    }

    member = db.execute('SELECT * FROM members WHERE email = ?', (current_user.email,)).fetchone()
    if not member:
        flash('Member profile not found. Please ensure your email is registered with the cooperative.', 'danger')
        return redirect(url_for('main.dashboard'))

    if request.method == 'POST':
        amount = float(request.form.get('amount', 0))
        purpose = request.form.get('purpose')
        tenure = int(request.form.get('tenure', 0))

        if amount <= 0 or not purpose or tenure <= 0:
            flash('All fields are required and must be valid.', 'danger')
            return redirect(url_for('portal.apply_loan_member'))

        try:
            if member['date_joined']:
                try:
                    date_joined = datetime.fromisoformat(member['date_joined'].replace('Z', '+00:00'))
                except ValueError:
                    date_joined = datetime.strptime(member['date_joined'], '%Y-%m-%d %H:%M:%S')
                months_as_member = (datetime.now() - date_joined).days / 30
                if months_as_member < 6:
                    flash('You must be a member for at least 6 months to apply for a loan.', 'danger')
                    return redirect(url_for('portal.apply_loan_member'))
            else:
                flash('Your join date is missing. Please contact admin.', 'danger')
                return redirect(url_for('portal.apply_loan_member'))

            if member['total_savings'] < 50000:
                flash(f'Minimum savings of ₦50,000 required (current: ₦{member["total_savings"]:,.2f}).', 'danger')
                return redirect(url_for('portal.apply_loan_member'))

            outstanding = db.execute(
                'SELECT id FROM loans WHERE member_id = ? AND status = "active"', (member['id'],)
            ).fetchone()
            if outstanding:
                flash('You already have an active loan. Please complete it before applying for a new one.', 'danger')
                return redirect(url_for('portal.apply_loan_member'))

            max_loan = member['total_savings'] * 2
            if amount > max_loan:
                flash(f'Maximum loan amount is ₦{max_loan:,.2f} (2x your savings).', 'danger')
                return redirect(url_for('portal.apply_loan_member'))

            purpose_to_key = {
                'Housing': 'interest_housing',
                'Emergency': 'interest_emergency',
                'Asset Purchase': 'interest_asset',
            }
            rate_key = purpose_to_key.get(purpose, 'interest_regular')
            interest_rate = interest_rates.get(rate_key, 11)

            monthly_interest = (interest_rate / 100) / 12
            if monthly_interest > 0:
                monthly_payment = (amount * monthly_interest * (1 + monthly_interest) ** tenure
                                   / ((1 + monthly_interest) ** tenure - 1))
            else:
                monthly_payment = amount / tenure
            total_repayment = monthly_payment * tenure

            loan_number = f"LOAN/{datetime.now().strftime('%Y%m%d')}/{random.randint(1000, 9999)}"
            db.execute('''
                INSERT INTO loans (
                    loan_number, member_id, amount, purpose, tenure, interest_rate,
                    total_repayment, balance, status, date_applied
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (loan_number, member['id'], amount, purpose, tenure, interest_rate,
                  total_repayment, total_repayment, 'pending', datetime.now()))
            db.commit()
            flash('Loan application submitted successfully! Pending approval.', 'success')
            return redirect(url_for('portal.my_loans'))

        except Exception as e:
            db.rollback()
            flash(f'Error applying for loan: {str(e)}', 'danger')
            return redirect(url_for('portal.apply_loan_member'))

    return render_template('member/apply-loan.html',
                           member=member,
                           max_tenure=max_tenure,
                           interest_rates=interest_rates)


@portal.route('/loan-calculator')
@login_required
def loan_calculator():
    return render_template('member/loan-calculator.html')


@portal.route('/my-cards')
@login_required
def my_cards():
    return render_template('member/my-cards.html')


@portal.route('/profile')
@login_required
def profile():
    return render_template('member/profile.html')


@portal.route('/edit-profile', methods=['GET', 'POST'])
@login_required
def edit_profile():
    if request.method == 'POST':
        flash('Profile updated successfully!', 'success')
        return redirect(url_for('portal.profile'))
    return render_template('member/edit-profile.html')


@portal.route('/change-password', methods=['GET', 'POST'])
@login_required
def change_password():
    if request.method == 'POST':
        current_password = request.form.get('current_password')
        new_password = request.form.get('new_password')
        confirm_password = request.form.get('confirm_password')

        if not current_password or not new_password or not confirm_password:
            flash('All fields are required', 'danger')
            return redirect(url_for('portal.change_password'))

        if new_password != confirm_password:
            flash('New passwords do not match', 'danger')
            return redirect(url_for('portal.change_password'))

        if len(new_password) < 8:
            flash('Password must be at least 8 characters long', 'danger')
            return redirect(url_for('portal.change_password'))

        db = get_db()
        user = db.execute('SELECT * FROM users WHERE id = ?', (current_user.id,)).fetchone()
        if not check_password_hash(user['password_hash'], current_password):
            flash('Current password is incorrect', 'danger')
            return redirect(url_for('portal.change_password'))

        new_hash = generate_password_hash(new_password)
        db.execute('UPDATE users SET password_hash = ? WHERE id = ?', (new_hash, current_user.id))
        db.commit()
        flash('Password changed successfully! Please login with your new password.', 'success')
        logout_user()
        return redirect(url_for('auth.login'))

    return render_template('change-password.html')


@portal.route('/nominee', methods=['GET', 'POST'])
@login_required
def nominee():
    return render_template('member/nominee.html')


@portal.route('/transactions')
@login_required
def transactions():
    return render_template('member/transactions.html')


@portal.route('/statements')
@login_required
def statements():
    return render_template('member/statements.html')


@portal.route('/notifications')
@login_required
def notifications():
    from flask import jsonify
    db = get_db()
    active_filter = request.args.get('filter', 'all')
    page = max(1, request.args.get('page', 1, type=int))
    per_page = 20

    base_query = 'SELECT * FROM notifications WHERE user_id = ?'
    params = [current_user.id]

    if active_filter == 'unread':
        base_query += ' AND is_read = 0'
    elif active_filter == 'important':
        base_query += " AND notification_type IN ('warning', 'danger')"

    total = db.execute(
        base_query.replace('SELECT *', 'SELECT COUNT(*)'), params
    ).fetchone()[0]

    notifs = db.execute(
        base_query + ' ORDER BY created_at DESC LIMIT ? OFFSET ?',
        params + [per_page, (page - 1) * per_page]
    ).fetchall()

    pages = max(1, (total + per_page - 1) // per_page)
    return render_template('member/notifications.html',
                           notifications=notifs,
                           filter=active_filter,
                           page=page,
                           pages=pages,
                           total=total)


@portal.route('/notifications/mark-read/<int:notif_id>', methods=['POST'])
@login_required
def mark_notification_read(notif_id):
    from flask import jsonify
    db = get_db()
    db.execute(
        'UPDATE notifications SET is_read = 1, read_at = ? WHERE id = ? AND user_id = ?',
        (datetime.now(), notif_id, current_user.id)
    )
    db.commit()
    return jsonify({'ok': True})


@portal.route('/notifications/mark-all-read', methods=['POST'])
@login_required
def mark_all_notifications_read():
    from flask import jsonify
    db = get_db()
    db.execute(
        'UPDATE notifications SET is_read = 1, read_at = ? WHERE user_id = ? AND is_read = 0',
        (datetime.now(), current_user.id)
    )
    db.commit()
    return jsonify({'ok': True})


@portal.route('/support', methods=['GET', 'POST'])
@login_required
def support():
    return render_template('member/support.html')


@portal.route('/member/statement/<int:member_id>')
@login_required
def member_statement(member_id):
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.lib.units import inch
        from reportlab.platypus import SimpleDocTemplate, Spacer, Table, TableStyle, Paragraph

        db = get_db()
        member = db.execute('SELECT * FROM members WHERE id = ?', (member_id,)).fetchone()
        if not member:
            flash('Member not found', 'danger')
            return redirect(url_for('members.members_list'))

        savings_rows = db.execute(
            'SELECT date, month, amount, late_fee FROM savings WHERE member_id = ? ORDER BY date DESC',
            (member_id,)
        ).fetchall()
        loan_rows = db.execute(
            'SELECT loan_number, amount, balance, status FROM loans WHERE member_id = ?',
            (member_id,)
        ).fetchall()

        buffer = BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4)
        styles = getSampleStyleSheet()
        elements = []

        elements.append(Paragraph('OOU Cooperative - Member Statement', styles['Title']))
        elements.append(Spacer(1, 0.2 * inch))
        elements.append(Paragraph(
            f"<b>Member:</b> {member['first_name']} {member['last_name']}", styles['Normal']
        ))
        elements.append(Paragraph(
            f"<b>Member #:</b> {member['member_number'] or 'N/A'}", styles['Normal']
        ))
        elements.append(Paragraph(
            f"<b>Date:</b> {datetime.now().strftime('%d/%m/%Y')}", styles['Normal']
        ))
        elements.append(Spacer(1, 0.2 * inch))

        total_savings = sum(s['amount'] for s in savings_rows)
        total_loans = sum(l['amount'] for l in loan_rows if l['status'] == 'active')

        summary = Table([
            ['Description', 'Amount'],
            ['Total Savings', f"₦{total_savings:,.2f}"],
            ['Active Loans', f"₦{total_loans:,.2f}"],
            ['Net Position', f"₦{total_savings - total_loans:,.2f}"],
        ])
        summary.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 14),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
            ('GRID', (0, 0), (-1, -1), 1, colors.black),
        ]))
        elements.append(summary)
        elements.append(Spacer(1, 0.3 * inch))
        elements.append(Paragraph('<b>Savings History</b>', styles['Heading2']))
        elements.append(Spacer(1, 0.1 * inch))

        trans_data = [['Date', 'Month', 'Amount', 'Late Fee', 'Total']]
        for s in savings_rows:
            trans_data.append([
                s['date'][:10] if s['date'] else '',
                s['month'],
                f"₦{s['amount'] - s['late_fee']:,.2f}",
                f"₦{s['late_fee']:,.2f}",
                f"₦{s['amount']:,.2f}",
            ])

        if len(trans_data) > 1:
            trans_table = Table(trans_data)
            trans_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('GRID', (0, 0), (-1, -1), 1, colors.black),
            ]))
            elements.append(trans_table)
        else:
            elements.append(Paragraph('No savings records found.', styles['Normal']))

        doc.build(elements)
        pdf = buffer.getvalue()
        buffer.close()

        response = make_response(pdf)
        response.headers['Content-Type'] = 'application/pdf'
        response.headers['Content-Disposition'] = f'attachment; filename=statement_{member_id}.pdf'
        return response

    except ImportError:
        flash('ReportLab not installed. Please run: pip install reportlab', 'warning')
        return redirect(url_for('members.member_details', member_id=member_id))
    except Exception as e:
        flash(f'Error generating statement: {str(e)}', 'danger')
        return redirect(url_for('members.member_details', member_id=member_id))
