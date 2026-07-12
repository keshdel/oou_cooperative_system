import csv
import os
import secrets
from datetime import datetime, timedelta
from io import StringIO, TextIOWrapper
from types import SimpleNamespace

from flask import Blueprint, render_template, redirect, url_for, request, flash, make_response
from flask_login import login_required
from werkzeug.security import generate_password_hash
from werkzeug.utils import secure_filename

from database import get_db, last_insert_id
from email_service import send_member_onboarding_email, send_welcome_email, send_email
from security import generate_account_setup_token
from utils import role_required, validate_image, audit, notify_member

members = Blueprint('members', __name__)


@members.route('/members')
@login_required
@role_required('admin', 'secretary')
def members_list():
    db  = get_db()
    all_members = db.execute('SELECT * FROM members ORDER BY date_joined DESC').fetchall()
    members_with_loans = db.execute(
        "SELECT COUNT(DISTINCT member_id) FROM loans WHERE status = 'active'"
    ).fetchone()[0] or 0
    return render_template('admin/members.html', members=all_members,
                           members_with_loans=members_with_loans)


@members.route('/members/<int:member_id>')
@login_required
@role_required('admin', 'secretary', 'treasurer', 'exco')
def member_details(member_id):
    db     = get_db()
    member = db.execute('SELECT * FROM members WHERE id = ?', (member_id,)).fetchone()
    if not member:
        flash('Member not found', 'danger')
        return redirect(url_for('members.members_list'))

    savings      = db.execute('SELECT * FROM savings WHERE member_id = ? ORDER BY date DESC', (member_id,)).fetchall()
    loans        = db.execute('SELECT * FROM loans WHERE member_id = ? ORDER BY date_applied DESC', (member_id,)).fetchall()
    total_savings = db.execute('SELECT SUM(amount) FROM savings WHERE member_id = ?', (member_id,)).fetchone()[0] or 0
    total_loans   = db.execute("SELECT SUM(amount) FROM loans WHERE member_id = ? AND status = 'active'", (member_id,)).fetchone()[0] or 0

    return render_template('admin/member-detail.html',
                           member=member, savings=savings, loans=loans,
                           total_savings=total_savings, total_loans=total_loans)


@members.route('/members/<int:member_id>/savings-statement')
@login_required
@role_required('admin', 'secretary', 'treasurer', 'exco')
def member_savings_statement(member_id):
    db = get_db()
    member = db.execute('SELECT * FROM members WHERE id = ?', (member_id,)).fetchone()
    if not member:
        flash('Member not found', 'danger')
        return redirect(url_for('members.members_list'))

    from_date_str = request.args.get('from_date', '')
    to_date_str = request.args.get('to_date', '')
    from_date = None
    to_date = None
    if from_date_str:
        try:
            from_date = datetime.strptime(from_date_str, '%Y-%m-%d')
        except ValueError:
            from_date_str = ''
    if to_date_str:
        try:
            to_date = datetime.strptime(to_date_str, '%Y-%m-%d').replace(hour=23, minute=59, second=59)
        except ValueError:
            to_date_str = ''

    rows = db.execute(
        'SELECT * FROM savings WHERE member_id = ? ORDER BY date ASC, id ASC',
        (member_id,)
    ).fetchall()

    running = 0.0
    statement = []
    opening_balance = 0.0
    for row in rows:
        amount = float(row['amount'] or 0)
        running += amount
        try:
            row_date = datetime.fromisoformat(str(row['date']).split('.')[0].replace('T', ' '))
        except Exception:
            row_date = datetime.now()

        if from_date and row_date < from_date:
            opening_balance = running
            continue
        if to_date and row_date > to_date:
            continue

        d = dict(row)
        d['date_parsed'] = row_date
        d['principal_amount'] = amount - float(row['late_fee'] or 0)
        d['running_balance'] = round(running, 2)
        statement.append(SimpleNamespace(**d))

    total_principal = sum(float(s.principal_amount or 0) for s in statement)
    total_late_fees = sum(float(s.late_fee or 0) for s in statement)
    total_paid = sum(float(s.amount or 0) for s in statement)
    closing_balance = statement[-1].running_balance if statement else opening_balance

    return render_template(
        'admin/member-savings-statement.html',
        member=member,
        statement=statement,
        from_date=from_date_str,
        to_date=to_date_str,
        opening_balance=opening_balance,
        closing_balance=closing_balance,
        total_principal=total_principal,
        total_late_fees=total_late_fees,
        total_paid=total_paid,
        generated_on=datetime.now(),
    )


@members.route('/members/add', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'secretary')
def add_member():
    if request.method == 'POST':
        db = get_db()
        try:
            db.execute('''
                INSERT INTO members (
                    first_name, last_name, email, phone, address,
                    occupation, date_of_birth, nominee_name,
                    nominee_relationship, monthly_savings, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                request.form['first_name'],
                request.form['last_name'],
                request.form.get('email', ''),
                request.form['phone'],
                request.form.get('address', ''),
                request.form.get('occupation', ''),
                request.form.get('date_of_birth', None),
                request.form.get('nominee_name', ''),
                request.form.get('nominee_relationship', ''),
                float(request.form.get('monthly_savings', 5000)),
                'active'
            ))
            db.commit()

            member_id     = last_insert_id(db)
            member_number = f"OOU/{datetime.now().year}/{member_id:04d}"
            db.execute('UPDATE members SET member_number = ? WHERE id = ?', (member_number, member_id))
            db.commit()

            member = db.execute('SELECT * FROM members WHERE id = ?', (member_id,)).fetchone()
            onboarding = None
            if member['email']:
                username = member['email'].strip().lower()
                existing_user = db.execute(
                    'SELECT id FROM users WHERE username = ? OR email = ?',
                    (username, member['email'])
                ).fetchone()
                if not existing_user:
                    token, token_hash = generate_account_setup_token()
                    full_name = f"{request.form['first_name']} {request.form['last_name']}"
                    db.execute('''
                        INSERT INTO users
                            (username, password_hash, role, full_name, email,
                             is_active, must_change_password, created_at)
                        VALUES (?, ?, 'member', ?, ?, 1, 1, ?)
                    ''', (
                        username,
                        generate_password_hash(secrets.token_urlsafe(32)),
                        full_name,
                        member['email'],
                        datetime.now(),
                    ))
                    user_id = last_insert_id(db)
                    db.execute('''
                        INSERT INTO account_setup_tokens
                            (user_id, token_hash, purpose, expires_at)
                        VALUES (?, ?, 'member_onboarding', ?)
                    ''', (user_id, token_hash, datetime.now() + timedelta(hours=24)))
                    db.commit()
                    onboarding = {
                        'username': username,
                        'token': token,
                        'full_name': full_name,
                    }
                send_welcome_email(member['email'], {
                    'full_name':     f"{request.form['first_name']} {request.form['last_name']}",
                    'member_number': member_number,
                    'coop_name':     'OOU Cooperative',
                })
                if onboarding:
                    send_member_onboarding_email(
                        member['email'],
                        {
                            'full_name': onboarding['full_name'],
                            'member_number': member_number,
                        },
                        onboarding['username'],
                        url_for('auth.setup_password', token=onboarding['token'], _external=True),
                        url_for('portal.profile', _external=True),
                    )
                notify_member(db, member['email'],
                              'Welcome to OOU Cooperative!',
                              f"Your member number is {member_number}. "
                              f"Check your email to configure your portal password and profile.",
                              notification_type='success')

            if 'photo' in request.files:
                photo = request.files['photo']
                if photo and photo.filename:
                    ok, err = validate_image(photo)
                    if not ok:
                        flash(f'Photo not saved: {err}', 'warning')
                    else:
                        ext         = secure_filename(photo.filename).rsplit('.', 1)[1].lower()
                        unique_name = f"member_{member_id}_{int(datetime.now().timestamp())}.{ext}"
                        photo_path  = os.path.join('static/uploads/member-photos', unique_name)
                        os.makedirs('static/uploads/member-photos', exist_ok=True)
                        photo.save(photo_path)
                        db.execute('UPDATE members SET photo_path = ? WHERE id = ?', (photo_path, member_id))
                        db.commit()

            audit(db, 'ADD_MEMBER', 'members',
                  f"Added member {member_number} – {request.form['first_name']} {request.form['last_name']}")
            flash('Member added successfully!', 'success')
        except Exception as e:
            db.rollback()
            flash(f'Error adding member: {str(e)}', 'danger')
        return redirect(url_for('members.members_list'))

    return render_template('admin/add-member.html')


@members.route('/members/edit/<int:member_id>', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'secretary')
def edit_member(member_id):
    db = get_db()

    if request.method == 'POST':
        try:
            db.execute('''
                UPDATE members SET
                    first_name = ?, last_name = ?, email = ?, phone = ?,
                    address = ?, occupation = ?, date_of_birth = ?,
                    nominee_name = ?, nominee_relationship = ?, monthly_savings = ?,
                    status = ?
                WHERE id = ?
            ''', (
                request.form['first_name'],
                request.form['last_name'],
                request.form.get('email', ''),
                request.form['phone'],
                request.form.get('address', ''),
                request.form.get('occupation', ''),
                request.form.get('date_of_birth', None),
                request.form.get('nominee_name', ''),
                request.form.get('nominee_relationship', ''),
                float(request.form.get('monthly_savings', 5000)),
                request.form.get('status', 'active'),
                member_id
            ))
            db.commit()

            if 'photo' in request.files:
                photo = request.files['photo']
                if photo and photo.filename:
                    ok, err = validate_image(photo)
                    if not ok:
                        flash(f'Photo not saved: {err}', 'warning')
                    else:
                        ext         = secure_filename(photo.filename).rsplit('.', 1)[1].lower()
                        unique_name = f"member_{member_id}_{int(datetime.now().timestamp())}.{ext}"
                        photo_path  = os.path.join('static/uploads/member-photos', unique_name)
                        os.makedirs('static/uploads/member-photos', exist_ok=True)
                        photo.save(photo_path)
                        db.execute('UPDATE members SET photo_path = ? WHERE id = ?', (photo_path, member_id))
                        db.commit()

            audit(db, 'EDIT_MEMBER', 'members', f"Edited member ID {member_id}")
            flash('Member updated successfully!', 'success')
        except Exception as e:
            db.rollback()
            flash(f'Error updating member: {str(e)}', 'danger')
        return redirect(url_for('members.member_details', member_id=member_id))

    member = db.execute('SELECT * FROM members WHERE id = ?', (member_id,)).fetchone()
    if not member:
        flash('Member not found', 'danger')
        return redirect(url_for('members.members_list'))
    return render_template('admin/edit-member.html', member=member)


@members.route('/members/delete/<int:member_id>', methods=['POST'])
@login_required
@role_required('admin', 'secretary')
def delete_member(member_id):
    db     = get_db()
    member = db.execute('SELECT * FROM members WHERE id = ?', (member_id,)).fetchone()
    if not member:
        flash('Member not found.', 'danger')
        return redirect(url_for('members.members_list'))

    savings_count = db.execute('SELECT COUNT(*) FROM savings WHERE member_id = ?', (member_id,)).fetchone()[0]
    loans_count   = db.execute('SELECT COUNT(*) FROM loans WHERE member_id = ?', (member_id,)).fetchone()[0]
    if savings_count > 0 or loans_count > 0:
        flash('Cannot delete member with existing savings or loans. Mark them as inactive instead.', 'danger')
        return redirect(url_for('members.member_details', member_id=member_id))

    db.execute('DELETE FROM members WHERE id = ?', (member_id,))
    db.commit()
    audit(db, 'DELETE_MEMBER', 'members',
          f"Deleted member {member['member_number']} – {member['first_name']} {member['last_name']}")
    flash('Member deleted successfully.', 'success')
    return redirect(url_for('members.members_list'))


@members.route('/members/bulk-upload', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'secretary')
def bulk_upload_members():
    if request.method == 'POST':
        if 'file' not in request.files or request.files['file'].filename == '':
            flash('No file selected', 'danger')
            return redirect(request.url)

        file = request.files['file']
        if not file.filename.lower().endswith('.csv'):
            flash('Please upload a CSV file', 'danger')
            return redirect(request.url)

        try:
            stream  = TextIOWrapper(file.stream, encoding='utf-8')
            reader  = csv.DictReader(stream)
            required = {'first_name', 'last_name', 'email', 'phone'}
            if not required.issubset(reader.fieldnames or []):
                missing = required - set(reader.fieldnames or [])
                flash(f'Missing columns: {", ".join(missing)}', 'danger')
                return redirect(request.url)

            db      = get_db()
            success = 0
            errors  = []

            for row_num, row in enumerate(reader, start=2):
                try:
                    first_name = row.get('first_name', '').strip()
                    last_name  = row.get('last_name', '').strip()
                    email      = row.get('email', '').strip()
                    phone      = row.get('phone', '').strip()
                    if not all([first_name, last_name, email, phone]):
                        errors.append(f"Row {row_num}: Missing required field")
                        continue

                    address        = row.get('address', '').strip()
                    occupation     = row.get('occupation', '').strip()
                    monthly_savings = float(row.get('monthly_savings', 5000))
                    member_number  = f"OOU/{datetime.now().year}/{row_num:04d}"

                    db.execute('''
                        INSERT INTO members (
                            member_number, first_name, last_name, email, phone,
                            address, occupation, monthly_savings, status, date_joined
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (member_number, first_name, last_name, email, phone,
                          address, occupation, monthly_savings, 'active', datetime.now()))

                    member_id     = last_insert_id(db)
                    current_month = datetime.now().strftime('%Y-%m')
                    db.execute('INSERT INTO savings (member_id, amount, month, late_fee, date) VALUES (?, ?, ?, ?, ?)',
                               (member_id, monthly_savings, current_month, 0, datetime.now()))
                    db.execute('UPDATE members SET total_savings = total_savings + ? WHERE id = ?',
                               (monthly_savings, member_id))
                    success += 1
                except Exception as e:
                    errors.append(f"Row {row_num}: {str(e)}")

            db.commit()
            if errors:
                flash(f'Imported {success} members. {len(errors)} errors:', 'warning')
                for err in errors[:5]:
                    flash(err, 'danger')
            else:
                flash(f'Successfully imported {success} members!', 'success')
        except Exception as e:
            flash(f'Error processing file: {str(e)}', 'danger')

        return redirect(url_for('members.members_list'))

    return render_template('admin/bulk-upload.html')


@members.route('/members/download-template')
@login_required
@role_required('admin', 'secretary')
def download_template():
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(['first_name', 'last_name', 'email', 'phone', 'address', 'occupation', 'monthly_savings'])
    writer.writerow(['John', 'Doe', 'john@example.com', '08012345678', 'Lagos', 'Teacher', '5000'])
    writer.writerow(['Jane', 'Smith', 'jane@example.com', '08087654321', 'Ibadan', 'Engineer', '10000'])
    response = make_response(output.getvalue())
    response.headers['Content-Type'] = 'text/csv'
    response.headers['Content-Disposition'] = 'attachment; filename=member_template.csv'
    return response


@members.route('/members/export')
@login_required
@role_required('admin', 'secretary')
def export_members():
    db = get_db()
    all_members = db.execute('''
        SELECT m.member_number, m.first_name, m.last_name, m.email, m.phone,
               m.address, m.occupation, m.status, m.total_savings, m.date_joined
        FROM members m
        ORDER BY m.date_joined DESC
    ''').fetchall()

    output = StringIO()
    writer = csv.writer(output)
    writer.writerow([
        'Member Number', 'First Name', 'Last Name', 'Email', 'Phone',
        'Address', 'Occupation', 'Status', 'Total Savings', 'Date Joined'
    ])
    for m in all_members:
        writer.writerow([
            m['member_number'] or '',
            m['first_name'],
            m['last_name'],
            m['email'] or '',
            m['phone'] or '',
            m['address'] or '',
            m['occupation'] or '',
            m['status'],
            f"₦{m['total_savings']:,.2f}" if m['total_savings'] else '₦0.00',
            m['date_joined'][:10] if m['date_joined'] else '',
        ])

    response = make_response(output.getvalue())
    response.headers['Content-Type'] = 'text/csv'
    response.headers['Content-Disposition'] = 'attachment; filename=members_export.csv'
    return response


@members.route('/members/<int:member_id>/card')
@login_required
@role_required('admin', 'secretary', 'treasurer', 'exco')
def member_card(member_id):
    """Render a printable ID card with QR code for a member."""
    db = get_db()
    member = db.execute('SELECT * FROM members WHERE id = ?', (member_id,)).fetchone()
    if not member:
        flash('Member not found', 'danger')
        return redirect(url_for('members.members_list'))

    # Normalise photo path: strip leading 'static/' so url_for('static') works
    photo_static = None
    raw_photo = member['photo_path'] if member['photo_path'] else ''
    if raw_photo:
        photo_static = raw_photo[len('static/'):] if raw_photo.startswith('static/') else raw_photo

    # Pull coop identity settings for the card header
    rows = db.execute(
        "SELECT key, value FROM settings "
        "WHERE key IN ('coop_name','coop_short_name','coop_logo','reg_number','address')"
    ).fetchall()
    coop = {r['key']: r['value'] for r in rows}

    return render_template('admin/member_card.html',
                           member=member,
                           photo_static=photo_static,
                           coop=coop)


# ── Savings-amendment approval workflow ──────────────────────────────────────

@members.route('/savings-requests')
@login_required
@role_required('admin', 'secretary', 'treasurer')
def savings_requests():
    """Staff queue of member requests to change their monthly savings amount."""
    db = get_db()
    rows = db.execute('''
        SELECT r.*, m.first_name, m.last_name, m.member_number, m.email
        FROM savings_change_requests r
        JOIN members m ON m.id = r.member_id
        ORDER BY CASE WHEN r.status = 'pending' THEN 0 ELSE 1 END,
                 r.requested_at DESC, r.id DESC
    ''').fetchall()
    pending = [r for r in rows if r['status'] == 'pending']
    history = [r for r in rows if r['status'] != 'pending']
    return render_template('admin/savings-requests.html',
                           pending=pending, history=history)


@members.route('/savings-requests/<int:req_id>/act', methods=['POST'])
@login_required
@role_required('admin', 'secretary', 'treasurer')
def savings_request_act(req_id):
    from flask_login import current_user
    db = get_db()
    action = request.form.get('action', '')
    comment = request.form.get('comment', '').strip()

    req = db.execute('''
        SELECT r.*, m.first_name, m.last_name, m.member_number, m.email
        FROM savings_change_requests r
        JOIN members m ON m.id = r.member_id
        WHERE r.id = ?''', (req_id,)).fetchone()
    if not req:
        flash('Request not found.', 'danger')
        return redirect(url_for('members.savings_requests'))
    if req['status'] != 'pending':
        flash('This request has already been reviewed.', 'warning')
        return redirect(url_for('members.savings_requests'))

    reviewer = getattr(current_user, 'full_name', None) or getattr(current_user, 'username', 'Staff')
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    if action == 'approve':
        # Apply the new monthly savings amount to the member record.
        db.execute("UPDATE members SET monthly_savings = ? WHERE id = ?",
                   (req['requested_amount'], req['member_id']))
        db.execute('''UPDATE savings_change_requests
                      SET status = 'approved', reviewed_by = ?, reviewed_by_name = ?,
                          reviewed_at = ?, review_comment = ? WHERE id = ?''',
                   (getattr(current_user, 'id', None), reviewer, now, comment, req_id))
        db.commit()
        audit(db, 'SAVINGS_CHANGE_APPROVED', 'members',
              f"Member {req['member_id']} monthly savings set to ₦{req['requested_amount']:,.2f}")
        msg = (f"Your request to change your monthly savings to "
               f"₦{req['requested_amount']:,.2f} has been approved and now takes effect.")
        notify_member(db, req['email'], 'Savings Change Approved', msg,
                      notification_type='success', action_url=url_for('portal.member_portal'))
        if req['email']:
            send_email(req['email'], 'Savings Change Approved',
                       f"<p>Dear {req['first_name']},</p><p>{msg}</p>"
                       + (f"<p><em>Note from the office:</em> {comment}</p>" if comment else "")
                       + "<p>OOU Cooperative</p>")
        flash('Request approved — the member\'s monthly savings has been updated.', 'success')

    elif action == 'reject':
        db.execute('''UPDATE savings_change_requests
                      SET status = 'rejected', reviewed_by = ?, reviewed_by_name = ?,
                          reviewed_at = ?, review_comment = ? WHERE id = ?''',
                   (getattr(current_user, 'id', None), reviewer, now, comment, req_id))
        db.commit()
        audit(db, 'SAVINGS_CHANGE_REJECTED', 'members',
              f"Member {req['member_id']} savings-change request rejected")
        msg = (f"Your request to change your monthly savings to "
               f"₦{req['requested_amount']:,.2f} was not approved.")
        notify_member(db, req['email'], 'Savings Change Not Approved',
                      msg + (f" Reason: {comment}" if comment else ""),
                      notification_type='warning', action_url=url_for('portal.member_portal'))
        if req['email']:
            send_email(req['email'], 'Savings Change Not Approved',
                       f"<p>Dear {req['first_name']},</p><p>{msg}</p>"
                       + (f"<p><em>Reason:</em> {comment}</p>" if comment else "")
                       + "<p>OOU Cooperative</p>")
        flash('Request rejected and the member notified.', 'info')
    else:
        flash('Unknown action.', 'danger')

    return redirect(url_for('members.savings_requests'))
