import csv
import os
from datetime import datetime
from io import StringIO, TextIOWrapper

from flask import Blueprint, render_template, redirect, url_for, request, flash, make_response
from flask_login import login_required
from werkzeug.utils import secure_filename

from database import get_db
from email_service import send_welcome_email
from utils import role_required, validate_image, audit, notify_member

members = Blueprint('members', __name__)


@members.route('/members')
@login_required
@role_required('admin', 'secretary')
def members_list():
    db  = get_db()
    all_members = db.execute('SELECT * FROM members ORDER BY date_joined DESC').fetchall()
    members_with_loans = db.execute(
        'SELECT COUNT(DISTINCT member_id) FROM loans WHERE status = "active"'
    ).fetchone()[0] or 0
    return render_template('admin/members.html', members=all_members,
                           members_with_loans=members_with_loans)


@members.route('/members/<int:member_id>')
@login_required
def member_details(member_id):
    db     = get_db()
    member = db.execute('SELECT * FROM members WHERE id = ?', (member_id,)).fetchone()
    if not member:
        flash('Member not found', 'danger')
        return redirect(url_for('members.members_list'))

    savings      = db.execute('SELECT * FROM savings WHERE member_id = ? ORDER BY date DESC', (member_id,)).fetchall()
    loans        = db.execute('SELECT * FROM loans WHERE member_id = ? ORDER BY date_applied DESC', (member_id,)).fetchall()
    total_savings = db.execute('SELECT SUM(amount) FROM savings WHERE member_id = ?', (member_id,)).fetchone()[0] or 0
    total_loans   = db.execute('SELECT SUM(amount) FROM loans WHERE member_id = ? AND status = "active"', (member_id,)).fetchone()[0] or 0

    return render_template('admin/member-detail.html',
                           member=member, savings=savings, loans=loans,
                           total_savings=total_savings, total_loans=total_loans)


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

            member_id     = db.execute('SELECT last_insert_rowid()').fetchone()[0]
            member_number = f"OOU/{datetime.now().year}/{member_id:04d}"
            db.execute('UPDATE members SET member_number = ? WHERE id = ?', (member_number, member_id))
            db.commit()

            member = db.execute('SELECT * FROM members WHERE id = ?', (member_id,)).fetchone()
            if member['email']:
                send_welcome_email(member['email'], {
                    'full_name':     f"{request.form['first_name']} {request.form['last_name']}",
                    'member_number': member_number,
                    'coop_name':     'OOU Cooperative',
                })
                notify_member(db, member['email'],
                              'Welcome to OOU Cooperative!',
                              f"Your member number is {member_number}. "
                              f"You can now log in to view your savings and loans.",
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

                    member_id     = db.execute('SELECT last_insert_rowid()').fetchone()[0]
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
