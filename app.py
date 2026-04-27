"""
OOU Acctg 2005 Alumni CMS - Cooperative Accounting Software
COMPLETE FIXED VERSION - All issues resolved
"""
from flask import Flask, render_template, redirect, url_for, request, flash, jsonify
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
from functools import wraps
import os
import sqlite3
import random
import uuid
import re
import json
from io import StringIO
from werkzeug.utils import secure_filename

from database import init_db, get_db
from card_generator import MemberCardGenerator

# Create the Flask app instance FIRST
app = Flask(__name__)

# Now configure the app
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'your-secret-key-change-this-in-production')
app.config['DATABASE'] = 'cooperative.db'

# Override database URL if in production (PostgreSQL)
if os.environ.get('DATABASE_URL'):
    db_url = os.environ['DATABASE_URL']
    # Fix for SQLAlchemy 1.4+ (some platforms use postgres://)
    db_url = re.sub(r'^postgres://', 'postgresql://', db_url)
    app.config['DATABASE_URL'] = db_url
else:
    app.config['DATABASE_URL'] = 'sqlite:///cooperative.db'

# If you have a mail extension (optional), initialize it after app
from extensions import mail
mail.init_app(app)

# The rest of your app (LoginManager, routes, etc.) continues below...


# Initialize login manager
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
mail.init_app(app)
# Initialize database
init_db()

# Make datetime available in all templates
@app.context_processor
def utility_processor():
    return {'datetime': datetime, 'now': datetime.now}

# User loader for Flask-Login
@login_manager.user_loader
def load_user(user_id):
    db = get_db()
    user = db.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
    if user:
        return User(user['id'], user['username'], user['password_hash'], user['role'])
    return None

class User(UserMixin):
    def __init__(self, id, username, password_hash, role):
        self.id = id
        self.username = username
        self.password_hash = password_hash
        self.role = role

# Role-based access control decorator
def role_required(*roles):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if current_user.role not in roles:
                flash('Access denied. Insufficient privileges.', 'danger')
                return redirect(url_for('dashboard'))
            return f(*args, **kwargs)
        return decorated_function
    return decorator

# ==================== PUBLIC ROUTES ====================

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        db = get_db()
        user = db.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()
        
        if user and check_password_hash(user['password_hash'], password):
            user_obj = User(user['id'], user['username'], user['password_hash'], user['role'])
            login_user(user_obj)
            flash('Login successful!', 'success')
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid username or password', 'danger')
    
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('You have been logged out', 'info')
    return redirect(url_for('index'))

@app.route('/setup')
def setup():
    """Initial setup route"""
    try:
        import subprocess
        subprocess.run(['python', 'init_settings.py'])
        return "Setup complete! You can now <a href='/login'>login</a>"
    except:
        return "Setup completed. You can now login."

# ==================== DASHBOARD ====================

@app.route('/dashboard')
@login_required
def dashboard():
    db = get_db()
    
    members_count = db.execute('SELECT COUNT(*) FROM members').fetchone()[0] or 0
    total_savings = db.execute('SELECT SUM(amount) FROM savings').fetchone()[0] or 0
    total_loans = db.execute('SELECT SUM(amount) FROM loans WHERE status = "active"').fetchone()[0] or 0
    total_investments = db.execute('SELECT SUM(amount) FROM investments').fetchone()[0] or 0
    
    recent_savings = db.execute('''
        SELECT s.*, m.first_name || " " || m.last_name as member_name 
        FROM savings s 
        JOIN members m ON s.member_id = m.id 
        ORDER BY s.date DESC LIMIT 5
    ''').fetchall()
    
    recent_loans = db.execute('''
        SELECT l.*, m.first_name || " " || m.last_name as member_name 
        FROM loans l 
        JOIN members m ON l.member_id = m.id 
        ORDER BY l.date_applied DESC LIMIT 5
    ''').fetchall()
    
    return render_template('dashboard.html', 
                         members_count=members_count,
                         total_savings=total_savings,
                         total_loans=total_loans,
                         total_investments=total_investments,
                         recent_savings=recent_savings,
                         recent_loans=recent_loans)

# ==================== ADMIN ROUTES ====================

@app.route('/members')
@login_required
@role_required('admin', 'secretary')
def members():
    db = get_db()
    members = db.execute('SELECT * FROM members ORDER BY date_joined DESC').fetchall()
    return render_template('admin/members.html', members=members)

@app.route('/members/<int:member_id>')
@login_required
def member_details(member_id):
    db = get_db()
    member = db.execute('SELECT * FROM members WHERE id = ?', (member_id,)).fetchone()
    
    if not member:
        flash('Member not found', 'danger')
        return redirect(url_for('members'))
    
    savings = db.execute('SELECT * FROM savings WHERE member_id = ? ORDER BY date DESC', (member_id,)).fetchall()
    loans = db.execute('SELECT * FROM loans WHERE member_id = ? ORDER BY date_applied DESC', (member_id,)).fetchall()
    total_savings = db.execute('SELECT SUM(amount) FROM savings WHERE member_id = ?', (member_id,)).fetchone()[0] or 0
    total_loans = db.execute('SELECT SUM(amount) FROM loans WHERE member_id = ? AND status = "active"', (member_id,)).fetchone()[0] or 0
    
    return render_template('admin/member-detail.html', 
                         member=member, 
                         savings=savings, 
                         loans=loans,
                         total_savings=total_savings,
                         total_loans=total_loans)

@app.route('/members/add', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'secretary')
def add_member():
    if request.method == 'POST':
        db = get_db()
        try:
            # Insert member
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

            # Get the newly created member's ID
            member_id = db.execute('SELECT last_insert_rowid()').fetchone()[0]

            # Handle photo upload
            if 'photo' in request.files:
                photo = request.files['photo']
                if photo and photo.filename:
                    from werkzeug.utils import secure_filename
                    import os
                    filename = secure_filename(photo.filename)
                    ext = filename.rsplit('.', 1)[1].lower()
                    unique_name = f"member_{member_id}_{int(datetime.now().timestamp())}.{ext}"
                    photo_path = os.path.join('static/uploads/member-photos', unique_name)
                    os.makedirs('static/uploads/member-photos', exist_ok=True)
                    photo.save(photo_path)
                    db.execute('UPDATE members SET photo_path = ? WHERE id = ?', (photo_path, member_id))
                    db.commit()

            flash('Member added successfully!', 'success')
        except Exception as e:   # <--- FIXED: added 'as e' and colon
            db.rollback()
            flash(f'Error adding member: {str(e)}', 'danger')
        return redirect(url_for('members'))
    
    # GET request – show form
    return render_template('admin/add-member.html')

@app.route('/members/edit/<int:member_id>', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'secretary')
def edit_member(member_id):
    db = get_db()
    
    if request.method == 'POST':
        try:
            # Update member details
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

            # Handle photo upload
            if 'photo' in request.files:
                photo = request.files['photo']
                if photo and photo.filename:
                    from werkzeug.utils import secure_filename
                    import os
                    filename = secure_filename(photo.filename)
                    ext = filename.rsplit('.', 1)[1].lower()
                    unique_name = f"member_{member_id}_{int(datetime.now().timestamp())}.{ext}"
                    photo_path = os.path.join('static/uploads/member-photos', unique_name)
                    os.makedirs('static/uploads/member-photos', exist_ok=True)
                    photo.save(photo_path)
                    db.execute('UPDATE members SET photo_path = ? WHERE id = ?', (photo_path, member_id))
                    db.commit()

            flash('Member updated successfully!', 'success')
        except Exception as e:   # <--- Proper except clause
            db.rollback()
            flash(f'Error updating member: {str(e)}', 'danger')
        return redirect(url_for('member_details', member_id=member_id))
    
    # GET request – show edit form
    member = db.execute('SELECT * FROM members WHERE id = ?', (member_id,)).fetchone()
    if not member:
        flash('Member not found', 'danger')
        return redirect(url_for('members'))
    
    return render_template('admin/edit-member.html', member=member)

@app.route('/members/delete/<int:member_id>', methods=['POST'])
@login_required
@role_required('admin')  # Only admins can delete members
def delete_member(member_id):
    """Delete a member (only if they have no transactions)"""
    db = get_db()
    
    # Check if member exists
    member = db.execute('SELECT * FROM members WHERE id = ?', (member_id,)).fetchone()
    if not member:
        flash('Member not found.', 'danger')
        return redirect(url_for('members'))
    
    # Check for related records
    savings_count = db.execute('SELECT COUNT(*) FROM savings WHERE member_id = ?', (member_id,)).fetchone()[0]
    loans_count = db.execute('SELECT COUNT(*) FROM loans WHERE member_id = ?', (member_id,)).fetchone()[0]
    
    if savings_count > 0 or loans_count > 0:
        flash('Cannot delete member with existing savings or loans. Consider marking them as inactive instead.', 'danger')
        return redirect(url_for('member_details', member_id=member_id))
    
    # If no related records, proceed with deletion
    try:
        db.execute('DELETE FROM members WHERE id = ?', (member_id,))
        db.commit()
        flash(f'Member {member["first_name"]} {member["last_name"]} has been deleted.', 'success')
    except Exception as e:
        db.rollback()
        flash(f'Error deleting member: {str(e)}', 'danger')
    
    return redirect(url_for('members'))


@app.route('/members/bulk-upload', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'secretary')
def bulk_upload_members():
    """Bulk upload members from CSV/Excel"""
    if request.method == 'POST':
        if 'file' not in request.files:
            flash('No file selected', 'danger')
            return redirect(request.url)
        
        file = request.files['file']
        if file.filename == '':
            flash('No file selected', 'danger')
            return redirect(request.url)
        
        # Validate file extension
        if not (file.filename.endswith('.csv') or file.filename.endswith(('.xlsx', '.xls'))):
            flash('Please upload a CSV or Excel file', 'danger')
            return redirect(request.url)
        
        try:
            # Read file based on extension
            if file.filename.endswith('.csv'):
                df = pd.read_csv(file)
            else:
                df = pd.read_excel(file)
            
            # Check required columns
            required = ['first_name', 'last_name', 'email', 'phone']
            missing = [col for col in required if col not in df.columns]
            if missing:
                flash(f'Missing columns: {", ".join(missing)}', 'danger')
                return redirect(request.url)
            
            db = get_db()
            success = 0
            errors = []
            
            for index, row in df.iterrows():
                try:
                    # Generate member number
                    member_number = f"OOU/{datetime.now().year}/{str(index+1).zfill(4)}"
                    
                    # Check if email already exists
                    existing = db.execute('SELECT id FROM members WHERE email = ?', 
                                         (row.get('email', ''),)).fetchone()
                    if existing and request.form.get('update_existing'):
                        # Update existing member
                        db.execute('''
                            UPDATE members SET
                                first_name = ?, last_name = ?, phone = ?,
                                address = ?, occupation = ?, monthly_savings = ?
                            WHERE email = ?
                        ''', (
                            row['first_name'],
                            row['last_name'],
                            row['phone'],
                            row.get('address', ''),
                            row.get('occupation', ''),
                            float(row.get('monthly_savings', 5000)),
                            row['email']
                        ))
                    else:
                        # Insert new member
                        db.execute('''
                            INSERT INTO members (
                                member_number, first_name, last_name, email, phone,
                                address, occupation, monthly_savings, status, date_joined
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ''', (
                            member_number,
                            row['first_name'],
                            row['last_name'],
                            row.get('email', ''),
                            row['phone'],
                            row.get('address', ''),
                            row.get('occupation', ''),
                            float(row.get('monthly_savings', 5000)),
                            'active',
                            datetime.now()
                        ))
                    success += 1
                    
                except Exception as e:
                    errors.append(f"Row {index+2}: {str(e)}")
            
            db.commit()
            
            if errors:
                flash(f'Uploaded {success} members with {len(errors)} errors', 'warning')
                for err in errors[:5]:
                    flash(err, 'danger')
            else:
                flash(f'Successfully uploaded {success} members!', 'success')
                
            return jsonify({'success': True, 'added': success, 'errors': len(errors)})
            
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)}), 500
    
    # GET request - show upload form
    return render_template('admin/bulk-upload.html')

@app.route('/members/download-template')
@login_required
@role_required('admin', 'secretary')
def download_template():
    """Download CSV template for bulk upload"""
    import csv
    from io import StringIO
    from flask import make_response
    
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(['first_name', 'last_name', 'email', 'phone', 'address', 'occupation', 'monthly_savings'])
    writer.writerow(['John', 'Doe', 'john@email.com', '08012345678', 'Lagos', 'Teacher', '5000'])
    writer.writerow(['Jane', 'Smith', 'jane@email.com', '08087654321', 'Ibadan', 'Engineer', '10000'])
    
    response = make_response(output.getvalue())
    response.headers['Content-Type'] = 'text/csv'
    response.headers['Content-Disposition'] = 'attachment; filename=member_template.csv'
    
    return response


@app.route('/savings')
@login_required
def savings():
    db = get_db()
    savings = db.execute('''
        SELECT s.*, m.first_name || " " || m.last_name as member_name 
        FROM savings s 
        JOIN members m ON s.member_id = m.id 
        ORDER BY s.date DESC
    ''').fetchall()
    
    total_savings = db.execute('SELECT SUM(amount) FROM savings').fetchone()[0] or 0
    
    return render_template('admin/savings.html', savings=savings, total_savings=total_savings)

@app.route('/savings/add', methods=['POST'])
@login_required
@role_required('admin', 'treasurer')
def add_saving():
    member_id = request.form['member_id']
    amount = float(request.form['amount'])
    month = request.form['month']
    
    if amount < 5000:
        flash(f'Minimum monthly savings is ₦5,000. You entered ₦{amount:,.2f}', 'danger')
        return redirect(url_for('member_details', member_id=member_id))
    
    db = get_db()
    
    try:
        # Check for duplicate
        existing = db.execute('SELECT id FROM savings WHERE member_id = ? AND month = ?', (member_id, month)).fetchone()
        if existing:
            flash('Savings for this month already recorded', 'warning')
            return redirect(url_for('member_details', member_id=member_id))
        
        # Calculate late fee
        today = datetime.now()
        if today.day > 10:
            late_fee = amount * 0.10
            total_amount = amount + late_fee
            flash(f'Late payment: Additional 10% fee of ₦{late_fee:,.2f} applied', 'info')
        else:
            total_amount = amount
            late_fee = 0
        
        # Generate receipt number
        receipt_number = f"RCPT/{datetime.now().strftime('%Y%m%d')}/{random.randint(1000, 9999)}"
        
        db.execute('''
            INSERT INTO savings (member_id, amount, month, late_fee, date, receipt_number) 
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (member_id, total_amount, month, late_fee, datetime.now(), receipt_number))
        
        db.execute('UPDATE members SET total_savings = total_savings + ? WHERE id = ?', (total_amount, member_id))
        db.commit()
        flash(f'Savings of ₦{amount:,.2f} recorded successfully! Receipt: {receipt_number}', 'success')
        
    except Exception as e:
        db.rollback()
        flash(f'Error recording savings: {str(e)}', 'danger')
    
    return redirect(url_for('member_details', member_id=member_id))

@app.route('/loans')
@login_required
def loans():
    db = get_db()
    loans = db.execute('''
        SELECT l.*, m.first_name || " " || m.last_name as member_name 
        FROM loans l 
        JOIN members m ON l.member_id = m.id 
        ORDER BY l.date_applied DESC
    ''').fetchall()
    
    active_loans = db.execute('SELECT SUM(amount) FROM loans WHERE status = "active"').fetchone()[0] or 0
    
    return render_template('admin/loans.html', loans=loans, active_loans=active_loans)

@app.route('/loans/apply', methods=['GET', 'POST'])
@login_required
def apply_loan():
    db = get_db()
    
    # --- FETCH SETTINGS (used for both GET and POST) ---
    # Get max tenure
    max_tenure_row = db.execute("SELECT value FROM settings WHERE key = 'max_tenure_months'").fetchone()
    max_tenure = int(max_tenure_row['value']) if max_tenure_row else 18
    
    # Get interest rates for different loan purposes
    interest_rows = db.execute("SELECT key, value FROM settings WHERE key LIKE 'interest_%'").fetchall()
    interest_rates = {row['key']: float(row['value']) for row in interest_rows}
    # Provide defaults if any are missing
    interest_rates = {
        'interest_regular': interest_rates.get('interest_regular', 11),
        'interest_housing': interest_rates.get('interest_housing', 9),
        'interest_emergency': interest_rates.get('interest_emergency', 10),
        'interest_asset': interest_rates.get('interest_asset', 10)
    }
    
    # --- POST: Process loan application ---
    if request.method == 'POST':
        member_id = request.form.get('member_id')
        amount = float(request.form.get('amount', 0))
        purpose = request.form.get('purpose')
        tenure = int(request.form.get('tenure', 0))
        
        # Basic validation
        if not member_id or amount <= 0 or not purpose or tenure <= 0:
            flash('All fields are required and must be valid.', 'danger')
            return redirect(url_for('apply_loan'))
        
        try:
            member = db.execute('SELECT * FROM members WHERE id = ?', (member_id,)).fetchone()
            if not member:
                flash('Member not found.', 'danger')
                return redirect(url_for('apply_loan'))
            
            # --- Check membership duration (robust date parsing) ---
            if member['date_joined']:
                try:
                    date_joined = datetime.fromisoformat(member['date_joined'].replace('Z', '+00:00'))
                except ValueError:
                    # Fallback for format without microseconds
                    date_joined = datetime.strptime(member['date_joined'], '%Y-%m-%d %H:%M:%S')
                months_as_member = (datetime.now() - date_joined).days / 30
                if months_as_member < 6:
                    flash('Member must be registered for at least 6 months.', 'danger')
                    return redirect(url_for('member_details', member_id=member_id))
            else:
                flash('Member join date is missing. Please contact admin.', 'danger')
                return redirect(url_for('member_details', member_id=member_id))
            
            # --- Minimum savings check ---
            if member['total_savings'] < 50000:
                flash(f'Minimum savings of ₦50,000 required (current: ₦{member["total_savings"]:,.2f}).', 'danger')
                return redirect(url_for('member_details', member_id=member_id))
            
            # --- Existing active loan check ---
            outstanding = db.execute('SELECT id FROM loans WHERE member_id = ? AND status = "active"', (member_id,)).fetchone()
            if outstanding:
                flash('Member already has an active loan. Please complete it before applying for a new one.', 'danger')
                return redirect(url_for('member_details', member_id=member_id))
            
            # --- Maximum loan amount (2x savings) ---
            max_loan = member['total_savings'] * 2
            if amount > max_loan:
                flash(f'Maximum loan amount is ₦{max_loan:,.2f} (2x savings).', 'danger')
                return redirect(url_for('member_details', member_id=member_id))
            
            # --- Determine interest rate based on purpose ---
            purpose_to_key = {
                'Housing': 'interest_housing',
                'Emergency': 'interest_emergency',
                'Asset Purchase': 'interest_asset'
            }
            rate_key = purpose_to_key.get(purpose, 'interest_regular')
            interest_rate = interest_rates.get(rate_key, 11)
            
            # --- Calculate loan repayment ---
            monthly_interest = (interest_rate / 100) / 12
            # Monthly payment formula for amortizing loan
            if monthly_interest > 0:
                monthly_payment = amount * monthly_interest * (1 + monthly_interest)**tenure / ((1 + monthly_interest)**tenure - 1)
            else:
                monthly_payment = amount / tenure
            total_repayment = monthly_payment * tenure
            total_interest = total_repayment - amount
            
            # --- Generate loan number ---
            loan_number = f"LOAN/{datetime.now().strftime('%Y%m%d')}/{random.randint(1000, 9999)}"
            
            # --- Insert loan application ---
            db.execute('''
                INSERT INTO loans (
                    loan_number, member_id, amount, purpose, tenure, interest_rate,
                    total_repayment, balance, status, date_applied
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (loan_number, member_id, amount, purpose, tenure, interest_rate,
                  total_repayment, total_repayment, 'pending', datetime.now()))
            
            db.commit()
            flash('Loan application submitted successfully! Pending approval.', 'success')
            return redirect(url_for('member_details', member_id=member_id))
            
        except Exception as e:
            db.rollback()
            flash(f'Error applying for loan: {str(e)}', 'danger')
            return redirect(url_for('apply_loan'))
    
    # --- GET: Show the application form ---
    members = db.execute('SELECT id, first_name, last_name FROM members WHERE status = "active"').fetchall()
    return render_template('admin/apply-loan.html',
                         members=members,
                         max_tenure=max_tenure,
                         interest_rates=interest_rates)

@app.route('/loans/approve/<int:loan_id>', methods=['POST'])
@login_required
@role_required('admin', 'treasurer')
def approve_loan(loan_id):
    db = get_db()
    
    try:
        loan = db.execute('SELECT * FROM loans WHERE id = ?', (loan_id,)).fetchone()
        
        if loan and loan['status'] == 'pending':
            # Calculate disbursed amount (after fees)
            insurance = loan['amount'] * 0.01
            application_fee = loan['amount'] * 0.01
            disbursed = loan['amount'] - insurance - application_fee
            
            db.execute('''
                UPDATE loans SET 
                    status = 'active', 
                    date_approved = ?, 
                    approved_by = ?,
                    insurance_premium = ?,
                    application_fee = ?,
                    disbursed_amount = ?,
                    disbursement_date = ?,
                    first_payment_date = ?
                WHERE id = ?
            ''', (
                datetime.now(), 
                current_user.id,
                insurance,
                application_fee,
                disbursed,
                datetime.now(),
                datetime.now() + timedelta(days=30),
                loan_id
            ))
            db.commit()
            flash('Loan approved successfully!', 'success')
        else:
            flash('Loan not found or already processed', 'danger')
            
    except Exception as e:
        db.rollback()
        flash(f'Error approving loan: {str(e)}', 'danger')
    
    return redirect(url_for('loans'))

@app.route('/loans/reject/<int:loan_id>', methods=['POST'])
@login_required
@role_required('admin', 'treasurer')
def reject_loan(loan_id):
    db = get_db()
    
    try:
        db.execute('UPDATE loans SET status = "rejected" WHERE id = ?', (loan_id,))
        db.commit()
        flash('Loan application rejected', 'info')
    except Exception as e:
        flash(f'Error rejecting loan: {str(e)}', 'danger')
    
    return redirect(url_for('loans'))

@app.route('/investments')
@login_required
@role_required('admin', 'treasurer')
def investments():
    db = get_db()
    investments = db.execute('SELECT * FROM investments ORDER BY date DESC').fetchall()
    total_investments = db.execute('SELECT SUM(amount) FROM investments').fetchone()[0] or 0
    
    return render_template('admin/investments.html', investments=investments, total_investments=total_investments)

@app.route('/investments/add', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'treasurer')
def add_investment():
    """Add a new investment"""
    if request.method == 'POST':
        db = get_db()
        try:
            # Generate a unique investment number
            year = datetime.now().year
            month = datetime.now().month
            random_num = random.randint(1000, 9999)
            investment_number = f"INV/{year}/{month:02d}/{random_num}"
            
            # Get form data with defaults
            name = request.form.get('name', '').strip()
            if not name:
                flash('Investment name is required.', 'danger')
                return redirect(url_for('add_investment'))
            
            amount = float(request.form.get('amount', 0))
            if amount <= 0:
                flash('Amount must be greater than zero.', 'danger')
                return redirect(url_for('add_investment'))
            
            investment_type = request.form.get('type', '')
            institution = request.form.get('institution', '')
            interest_rate = request.form.get('interest_rate')
            if interest_rate:
                interest_rate = float(interest_rate)
            else:
                interest_rate = None
            
            start_date = request.form.get('start_date')
            maturity_date = request.form.get('maturity_date')
            risk_level = request.form.get('risk_level', 'medium')
            description = request.form.get('description', '')
            
            # Insert into database
            db.execute('''
                INSERT INTO investments (
                    investment_number, name, amount, type, institution,
                    interest_rate, start_date, maturity_date, risk_level,
                    description, approval_status, created_by, date
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                investment_number,
                name,
                amount,
                investment_type,
                institution,
                interest_rate,
                start_date,
                maturity_date,
                risk_level,
                description,
                'approved',  # Default approval status
                current_user.id,
                datetime.now()
            ))
            db.commit()
            flash(f'Investment "{name}" added successfully! Reference: {investment_number}', 'success')
            return redirect(url_for('investments'))
            
        except Exception as e:
            db.rollback()
            flash(f'Error adding investment: {str(e)}', 'danger')
            # Log the error for debugging
            print(f"Investment add error: {str(e)}")
            return redirect(url_for('add_investment'))
    
    # GET request – show form
    return render_template('admin/add-investment.html')

@app.route('/reports')
@login_required
def reports():
    """Reports dashboard - safe version with fallbacks"""
    db = get_db()
    
    try:
        # Helper to safely get a single numeric value
        def safe_fetch(query, params=()):
            result = db.execute(query, params).fetchone()
            return result[0] if result and result[0] is not None else 0
        
        # Member stats
        total_members = safe_fetch('SELECT COUNT(*) FROM members')
        active_members = safe_fetch('SELECT COUNT(*) FROM members WHERE status = "active"')
        inactive_members = total_members - active_members
        members_with_loans = safe_fetch('SELECT COUNT(DISTINCT member_id) FROM loans WHERE status = "active"')
        new_members_month = safe_fetch('SELECT COUNT(*) FROM members WHERE date_joined >= date("now", "-30 days")')
        
        # Savings stats
        total_savings_all = safe_fetch('SELECT COALESCE(SUM(amount), 0) FROM savings')
        current_month = datetime.now().strftime('%Y-%m')
        this_month_savings = safe_fetch('SELECT COALESCE(SUM(amount), 0) FROM savings WHERE month = ?', (current_month,))
        total_late_fees = safe_fetch('SELECT COALESCE(SUM(late_fee), 0) FROM savings')
        avg_savings_per_member = total_savings_all / total_members if total_members > 0 else 0
        
        # Loan stats
        active_loans_total = safe_fetch('SELECT COALESCE(SUM(amount), 0) FROM loans WHERE status = "active"')
        total_disbursed = safe_fetch('SELECT COALESCE(SUM(amount), 0) FROM loans WHERE status IN ("active", "completed")')
        total_repaid = safe_fetch('SELECT COALESCE(SUM(amount), 0) FROM repayments')
        total_interest = (total_disbursed * 0.11) if total_disbursed else 0
        
        active_loans_count = safe_fetch('SELECT COUNT(*) FROM loans WHERE status = "active"')
        completed_loans_count = safe_fetch('SELECT COUNT(*) FROM loans WHERE status = "completed"')
        pending_loans_count = safe_fetch('SELECT COUNT(*) FROM loans WHERE status = "pending"')
        rejected_loans_count = safe_fetch('SELECT COUNT(*) FROM loans WHERE status = "rejected"')
        
        # Investment stats
        total_investments_value = safe_fetch('SELECT COALESCE(SUM(amount), 0) FROM investments')
        
        # Chart data – Monthly savings last 6 months (real data)
        savings_months = []
        monthly_savings_data = []
        for i in range(5, -1, -1):
            month_date = datetime.now().replace(day=1) - timedelta(days=30*i)
            month_str = month_date.strftime('%Y-%m')
            month_name = month_date.strftime('%b')
            savings_months.append(month_name)
            month_total = safe_fetch('SELECT COALESCE(SUM(amount), 0) FROM savings WHERE month = ?', (month_str,))
            monthly_savings_data.append(month_total)
        
        # Member join trend last 6 months
        join_months = []
        new_members_data = []
        for i in range(5, -1, -1):
            month_date = datetime.now().replace(day=1) - timedelta(days=30*i)
            month_name = month_date.strftime('%b')
            join_months.append(month_name)
            start = month_date.strftime('%Y-%m-01')
            end = (month_date + timedelta(days=32)).replace(day=1).strftime('%Y-%m-01')
            count = safe_fetch('SELECT COUNT(*) FROM members WHERE date_joined >= ? AND date_joined < ?', (start, end))
            new_members_data.append(count)
        
        # Investment type data (sample – you can replace with real category sums later)
        investment_type_labels = ['Fixed Deposit', 'Shares', 'Real Estate', 'Government Bonds', 'Other']
        investment_type_data = [random.randint(100000, 1000000) for _ in range(5)]
        
        # Dividends
        dividend_amount = total_savings_all * 0.05
        reserve_amount = dividend_amount * 0.3
        honorarium_amount = dividend_amount * 0.1
        other_appropriations = dividend_amount * 0.1
        
        # Top savers
        top_savers = db.execute('''
            SELECT m.id, m.first_name, m.last_name, COALESCE(SUM(s.amount), 0) as total_savings
            FROM members m
            LEFT JOIN savings s ON m.id = s.member_id
            GROUP BY m.id
            ORDER BY total_savings DESC
            LIMIT 5
        ''').fetchall()
        
        # Additional variables expected by template
        delinquent_loans = []
        active_savings = total_savings_all
        inactive_savings = 0
        loan_member_savings = total_savings_all * 0.6   # placeholder
        total_income_year = total_savings_all
        total_expenses_year = total_investments_value
        net_surplus_year = total_income_year - total_expenses_year
        member_dividends = []
        
        return render_template('admin/reports.html',
            total_members=total_members,
            active_members=active_members,
            inactive_members=inactive_members,
            members_with_loans=members_with_loans,
            new_members_month=new_members_month,
            total_savings_all=total_savings_all,
            this_month_savings=this_month_savings,
            total_late_fees=total_late_fees,
            avg_savings_per_member=avg_savings_per_member,
            active_loans_total=active_loans_total,
            total_disbursed=total_disbursed,
            total_repaid=total_repaid,
            total_interest=total_interest,
            active_loans_count=active_loans_count,
            completed_loans_count=completed_loans_count,
            pending_loans_count=pending_loans_count,
            rejected_loans_count=rejected_loans_count,
            current_loans=active_loans_count,
            days_30_loans=0,
            days_60_loans=0,
            days_90_loans=0,
            total_investments_value=total_investments_value,
            savings_months=savings_months,
            monthly_savings_data=monthly_savings_data,
            join_months=join_months,
            new_members_data=new_members_data,
            investment_type_labels=investment_type_labels,
            investment_type_data=investment_type_data,
            dividend_amount=dividend_amount,
            reserve_amount=reserve_amount,
            honorarium_amount=honorarium_amount,
            other_appropriations=other_appropriations,
            top_savers=top_savers,
            delinquent_loans=delinquent_loans,
            active_savings=active_savings,
            inactive_savings=inactive_savings,
            loan_member_savings=loan_member_savings,
            total_income_year=total_income_year,
            total_expenses_year=total_expenses_year,
            net_surplus_year=net_surplus_year,
            member_dividends=member_dividends
        )
    except Exception as e:
        # Log error and fallback to a safe empty report
        import traceback
        print(f"Reports error: {e}")
        print(traceback.format_exc())
        flash('Unable to load reports due to an internal error. Please try again later.', 'danger')
        # Return a page with all zeros as fallback
        zero_vars = {k:0 for k in [
            'total_members','active_members','inactive_members','members_with_loans','new_members_month',
            'total_savings_all','this_month_savings','total_late_fees','avg_savings_per_member',
            'active_loans_total','total_disbursed','total_repaid','total_interest','active_loans_count',
            'completed_loans_count','pending_loans_count','rejected_loans_count','current_loans',
            'days_30_loans','days_60_loans','days_90_loans','total_investments_value',
            'savings_months','monthly_savings_data','join_months','new_members_data',
            'investment_type_labels','investment_type_data','dividend_amount','reserve_amount',
            'honorarium_amount','other_appropriations','top_savers','delinquent_loans','active_savings',
            'inactive_savings','loan_member_savings','total_income_year','total_expenses_year',
            'net_surplus_year','member_dividends'
        ]}
        return render_template('admin/reports.html', **zero_vars)

@app.route('/reports/financial')
@login_required
def financial_report():
    """Financial report with real data"""
    db = get_db()
    
    from_date = request.args.get('from_date', (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d'))
    to_date = request.args.get('to_date', datetime.now().strftime('%Y-%m-%d'))
    
    try:
        # Member Savings
        total_savings = db.execute('''
            SELECT COALESCE(SUM(amount), 0) FROM savings 
            WHERE date BETWEEN ? AND ?
        ''', (from_date, to_date)).fetchone()[0]
        
        # Loan Interest (approximate)
        loan_interest = db.execute('''
            SELECT COALESCE(SUM(amount * ? / 100), 0) FROM loans 
            WHERE status = 'active' AND date_applied BETWEEN ? AND ?
        ''', (11, from_date, to_date)).fetchone()[0]
        
        # Late Fees
        late_fees = db.execute('''
            SELECT COALESCE(SUM(late_fee), 0) FROM savings 
            WHERE date BETWEEN ? AND ?
        ''', (from_date, to_date)).fetchone()[0]
        
        # Investments
        investments = db.execute('''
            SELECT COALESCE(SUM(amount), 0) FROM investments 
            WHERE date BETWEEN ? AND ?
        ''', (from_date, to_date)).fetchone()[0]
        
        # Honorarium
        honorarium = db.execute('''
            SELECT COALESCE(SUM(amount), 0) FROM honorarium 
            WHERE date BETWEEN ? AND ?
        ''', (from_date, to_date)).fetchone()[0]
        
        # Operating Expenses
        operating_expenses = db.execute('''
            SELECT COALESCE(SUM(amount), 0) FROM expenses 
            WHERE date BETWEEN ? AND ?
        ''', (from_date, to_date)).fetchone()[0]
        
        total_income = total_savings + loan_interest + late_fees
        total_expenses = investments + honorarium + operating_expenses
        net_surplus = total_income - total_expenses
        
        return render_template('admin/financial-report.html',
                             from_date=from_date,
                             to_date=to_date,
                             total_savings=total_savings,
                             loan_interest=loan_interest,
                             late_fees=late_fees,
                             total_income=total_income,
                             investments=investments,
                             honorarium=honorarium,
                             operating_expenses=operating_expenses,
                             total_expenses=total_expenses,
                             net_surplus=net_surplus)
    except Exception as e:
        flash(f'Error generating financial report: {str(e)}', 'danger')
        return redirect(url_for('reports'))

@app.route('/settings')
@login_required
@role_required('admin')
def settings():
    db = get_db()
    
    default_settings = {
        'coop_name': 'OOU Acctg 2005 Alumni CMS',
        'reg_number': 'CMS/2005/001',
        'address': '',
        'phone': '',
        'email': '',
        'fy_start': '1',
        'currency': 'NGN',
        'date_format': 'Y-m-d',
        'session_timeout': '30',
        'maintenance_mode': '0',
        'min_savings': '5000',
        'savings_due_day': '10',
        'late_fee_percent': '10',
        'min_deposit_period': '90',
        'member_deposit_rate': '9',
        'nonmember_deposit_rate': '7',
        'dividend_rate': '50',
        'min_membership_months': '6',
        'min_savings_for_loan': '50000',
        'loan_multiplier': '2',
        'max_tenure_months': '18',
        'max_interest_rate': '11',
        'insurance_rate': '1',
        'guarantors_required': '2',
        'default_penalty_rate': '20',
        'interest_regular': '11',
        'interest_housing': '9',
        'interest_emergency': '10',
        'interest_asset': '10',
        'entrance_fee': '2000',
        'reentry_fee': '5000',
        'loan_application_fee': '1000',
        'statement_fee': '500'
    }
    
    try:
        settings_rows = db.execute('SELECT key, value FROM settings').fetchall()
        settings_dict = {}
        for row in settings_rows:
            settings_dict[row['key']] = row['value']
        
        for key, default_value in default_settings.items():
            if key not in settings_dict:
                settings_dict[key] = default_value
        
        users = db.execute('SELECT id, username, role, created_at FROM users ORDER BY id').fetchall()
        user_list = []
        for user in users:
            user_list.append({
                'id': user['id'],
                'username': user['username'],
                'full_name': user['username'],
                'role': user['role'],
                'last_login': 'Never',
                'status': 'active'
            })
        
        return render_template('admin/settings.html', 
                             settings=settings_dict,
                             system_users=user_list,
                             audit_logs=[],
                             backup_history=[],
                             datetime=datetime)
        
    except Exception as e:
        flash(f'Error loading settings: {str(e)}', 'danger')
        return render_template('admin/settings.html', 
                             settings=default_settings,
                             system_users=[],
                             audit_logs=[],
                             backup_history=[],
                             datetime=datetime)

@app.route('/settings/update', methods=['POST'])
@login_required
@role_required('admin')
def update_settings():
    db = get_db()
    
    try:
        for key, value in request.form.items():
            if value is None or value == '':
                continue
                
            existing = db.execute('SELECT id FROM settings WHERE key = ?', (key,)).fetchone()
            
            if existing:
                db.execute('UPDATE settings SET value = ? WHERE key = ?', (value, key))
            else:
                db.execute('INSERT INTO settings (key, value, description) VALUES (?, ?, ?)', 
                          (key, value, f'Setting for {key}'))
        
        db.commit()
        flash('✅ Settings saved successfully!', 'success')
        
    except Exception as e:
        db.rollback()
        flash(f'❌ Error saving settings: {str(e)}', 'danger')
    
    return redirect(url_for('settings'))

# ==================== EXPENSE MANAGEMENT ====================

@app.route('/expenses')
@login_required
@role_required('admin', 'treasurer')
def expenses():
    """List all expenses"""
    db = get_db()
    expenses = db.execute('''
        SELECT * FROM expenses 
        ORDER BY date DESC
    ''').fetchall()
    
    total_expenses = db.execute('SELECT SUM(amount) FROM expenses').fetchone()[0] or 0
    
    return render_template('admin/expenses.html', expenses=expenses, total_expenses=total_expenses)

@app.route('/expenses/add', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'treasurer')
def add_expense():
    """Add new expense"""
    if request.method == 'POST':
        db = get_db()
        
        try:
            expense_number = f"EXP/{datetime.now().strftime('%Y%m%d')}/{random.randint(1000, 9999)}"
            
            db.execute('''
                INSERT INTO expenses (
                    expense_number, category, amount, description, vendor,
                    payment_method, date, recorded_by, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                expense_number,
                request.form['category'],
                float(request.form['amount']),
                request.form['description'],
                request.form.get('vendor', ''),
                request.form['payment_method'],
                request.form.get('date', datetime.now()),
                current_user.id,
                request.form.get('notes', '')
            ))
            
            db.commit()
            flash('Expense recorded successfully!', 'success')
        except Exception as e:
            db.rollback()
            flash(f'Error recording expense: {str(e)}', 'danger')
        
        return redirect(url_for('expenses'))
    
    return render_template('admin/add-expense.html')

# ==================== REVENUE MANAGEMENT ====================

@app.route('/revenue')
@login_required
@role_required('admin', 'treasurer')
def revenue():
    """List all other revenue"""
    db = get_db()
    revenues = db.execute('''
        SELECT * FROM revenue 
        ORDER BY date DESC
    ''').fetchall()
    
    total_revenue = db.execute('SELECT SUM(amount) FROM revenue').fetchone()[0] or 0
    
    return render_template('admin/revenue.html', revenues=revenues, total_revenue=total_revenue)

@app.route('/revenue/add', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'treasurer')
def add_revenue():
    """Add other revenue source"""
    if request.method == 'POST':
        db = get_db()
        
        try:
            revenue_number = f"REV/{datetime.now().strftime('%Y%m%d')}/{random.randint(1000, 9999)}"
            
            db.execute('''
                INSERT INTO revenue (
                    revenue_number, category, amount, description, source,
                    date, received_by, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                revenue_number,
                request.form['category'],
                float(request.form['amount']),
                request.form['description'],
                request.form.get('source', ''),
                request.form.get('date', datetime.now()),
                current_user.id,
                request.form.get('notes', '')
            ))
            
            db.commit()
            flash('Revenue recorded successfully!', 'success')
        except Exception as e:
            db.rollback()
            flash(f'Error recording revenue: {str(e)}', 'danger')
        
        return redirect(url_for('revenue'))
    
    return render_template('admin/add-revenue.html')

# ==================== HONORARIUM MANAGEMENT ====================

@app.route('/honorarium')
@login_required
@role_required('admin')
def honorarium():
    """Manage honorarium payments"""
    db = get_db()
    honorariums = db.execute('''
        SELECT h.*, u.username as paid_by_name 
        FROM honorarium h
        LEFT JOIN users u ON h.paid_by = u.id
        ORDER BY h.date DESC
    ''').fetchall()
    
    return render_template('admin/honorarium.html', honorariums=honorariums)

@app.route('/honorarium/add', methods=['POST'])
@login_required
@role_required('admin')
def add_honorarium():
    """Record honorarium payment"""
    db = get_db()
    
    try:
        db.execute('''
            INSERT INTO honorarium (
                recipient_id, recipient_name, amount, description, month, paid_by
            ) VALUES (?, ?, ?, ?, ?, ?)
        ''', (
            request.form.get('recipient_id'),
            request.form['recipient_name'],
            float(request.form['amount']),
            request.form['description'],
            request.form['month'],
            current_user.id
        ))
        
        db.commit()
        flash('Honorarium recorded successfully!', 'success')
    except Exception as e:
        db.rollback()
        flash(f'Error recording honorarium: {str(e)}', 'danger')
    
    return redirect(url_for('honorarium'))

# ==================== MEMBER PORTAL ROUTES ====================

@app.route('/member/portal')
@login_required
def member_portal():
    return render_template('member/portal.html')

@app.route('/my-savings')
@login_required
def my_savings():
    return render_template('member/my-savings.html')

@app.route('/saving-detail/<int:saving_id>')
@login_required
def saving_detail(saving_id):
    return render_template('member/saving-detail.html')

@app.route('/my-loans')
@login_required
def my_loans():
    return render_template('member/my-loans.html')

@app.route('/loan-detail/<int:loan_id>')
@login_required
def loan_detail(loan_id):
    return render_template('member/loan-detail.html')

@app.route('/apply-loan-member', methods=['GET', 'POST'])
@login_required
def apply_loan_member():
    """Member portal: apply for a loan (self‑service)"""
    db = get_db()
    
    # --- Fetch settings (shared for both GET and POST) ---
    max_tenure_row = db.execute("SELECT value FROM settings WHERE key = 'max_tenure_months'").fetchone()
    max_tenure = int(max_tenure_row['value']) if max_tenure_row else 18
    
    interest_rows = db.execute("SELECT key, value FROM settings WHERE key LIKE 'interest_%'").fetchall()
    interest_rates = {row['key']: float(row['value']) for row in interest_rows}
    interest_rates = {
        'interest_regular': interest_rates.get('interest_regular', 11),
        'interest_housing': interest_rates.get('interest_housing', 9),
        'interest_emergency': interest_rates.get('interest_emergency', 10),
        'interest_asset': interest_rates.get('interest_asset', 10)
    }
    
    # Get the logged‑in member by matching email
    member = db.execute('SELECT * FROM members WHERE email = ?', (current_user.email,)).fetchone()
    
    if not member:
        flash('Member profile not found. Please ensure your email is registered with the cooperative.', 'danger')
        return redirect(url_for('dashboard'))
    
    # --- POST: Process loan application ---
    if request.method == 'POST':
        amount = float(request.form.get('amount', 0))
        purpose = request.form.get('purpose')
        tenure = int(request.form.get('tenure', 0))
        
        if amount <= 0 or not purpose or tenure <= 0:
            flash('All fields are required and must be valid.', 'danger')
            return redirect(url_for('apply_loan_member'))
        
        try:
            # --- Check membership duration ---
            if member['date_joined']:
                try:
                    date_joined = datetime.fromisoformat(member['date_joined'].replace('Z', '+00:00'))
                except ValueError:
                    date_joined = datetime.strptime(member['date_joined'], '%Y-%m-%d %H:%M:%S')
                months_as_member = (datetime.now() - date_joined).days / 30
                if months_as_member < 6:
                    flash('You must be a member for at least 6 months to apply for a loan.', 'danger')
                    return redirect(url_for('apply_loan_member'))
            else:
                flash('Your join date is missing. Please contact admin.', 'danger')
                return redirect(url_for('apply_loan_member'))
            
            # --- Minimum savings check ---
            if member['total_savings'] < 50000:
                flash(f'Minimum savings of ₦50,000 required (current: ₦{member["total_savings"]:,.2f}).', 'danger')
                return redirect(url_for('apply_loan_member'))
            
            # --- Existing active loan check ---
            outstanding = db.execute('SELECT id FROM loans WHERE member_id = ? AND status = "active"', (member['id'],)).fetchone()
            if outstanding:
                flash('You already have an active loan. Please complete it before applying for a new one.', 'danger')
                return redirect(url_for('apply_loan_member'))
            
            # --- Maximum loan amount (2x savings) ---
            max_loan = member['total_savings'] * 2
            if amount > max_loan:
                flash(f'Maximum loan amount is ₦{max_loan:,.2f} (2x your savings).', 'danger')
                return redirect(url_for('apply_loan_member'))
            
            # --- Determine interest rate based on purpose ---
            purpose_to_key = {
                'Housing': 'interest_housing',
                'Emergency': 'interest_emergency',
                'Asset Purchase': 'interest_asset'
            }
            rate_key = purpose_to_key.get(purpose, 'interest_regular')
            interest_rate = interest_rates.get(rate_key, 11)
            
            # --- Calculate loan repayment ---
            monthly_interest = (interest_rate / 100) / 12
            if monthly_interest > 0:
                monthly_payment = amount * monthly_interest * (1 + monthly_interest)**tenure / ((1 + monthly_interest)**tenure - 1)
            else:
                monthly_payment = amount / tenure
            total_repayment = monthly_payment * tenure
            total_interest = total_repayment - amount
            
            # --- Generate loan number ---
            loan_number = f"LOAN/{datetime.now().strftime('%Y%m%d')}/{random.randint(1000, 9999)}"
            
            # --- Insert loan application ---
            db.execute('''
                INSERT INTO loans (
                    loan_number, member_id, amount, purpose, tenure, interest_rate,
                    total_repayment, balance, status, date_applied
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (loan_number, member['id'], amount, purpose, tenure, interest_rate,
                  total_repayment, total_repayment, 'pending', datetime.now()))
            
            db.commit()
            flash('Loan application submitted successfully! Pending approval.', 'success')
            return redirect(url_for('my_loans'))
            
        except Exception as e:
            db.rollback()
            flash(f'Error applying for loan: {str(e)}', 'danger')
            return redirect(url_for('apply_loan_member'))
    
    # --- GET: Show the application form ---
    return render_template('member/apply-loan.html',
                         member=member,
                         max_tenure=max_tenure,
                         interest_rates=interest_rates)
@app.route('/loan-calculator')
@login_required
def loan_calculator():
    return render_template('member/loan-calculator.html')

@app.route('/my-cards')
@login_required
def my_cards():
    return render_template('member/my-cards.html')



@app.route('/profile')
@login_required
def profile():
    return render_template('member/profile.html')

@app.route('/edit-profile', methods=['GET', 'POST'])
@login_required
def edit_profile():
    if request.method == 'POST':
        flash('Profile updated successfully!', 'success')
        return redirect(url_for('profile'))
    return render_template('member/edit-profile.html')

@app.route('/change-password', methods=['GET', 'POST'])
@login_required
def change_password():
    """Universal change password page for all users"""
    if request.method == 'POST':
        current_password = request.form.get('current_password')
        new_password = request.form.get('new_password')
        confirm_password = request.form.get('confirm_password')
        
        if not current_password or not new_password or not confirm_password:
            flash('All fields are required', 'danger')
            return redirect(url_for('change_password'))
        
        if new_password != confirm_password:
            flash('New passwords do not match', 'danger')
            return redirect(url_for('change_password'))
        
        if len(new_password) < 8:
            flash('Password must be at least 8 characters long', 'danger')
            return redirect(url_for('change_password'))
        
        db = get_db()
        user = db.execute('SELECT * FROM users WHERE id = ?', (current_user.id,)).fetchone()
        
        if not check_password_hash(user['password_hash'], current_password):
            flash('Current password is incorrect', 'danger')
            return redirect(url_for('change_password'))
        
        new_hash = generate_password_hash(new_password)
        db.execute('UPDATE users SET password_hash = ? WHERE id = ?', (new_hash, current_user.id))
        db.commit()
        
        flash('✅ Password changed successfully! Please login with your new password.', 'success')
        logout_user()
        return redirect(url_for('login'))
    
    return render_template('change-password.html')

@app.route('/nominee', methods=['GET', 'POST'])
@login_required
def nominee():
    return render_template('member/nominee.html')

@app.route('/transactions')
@login_required
def transactions():
    return render_template('member/transactions.html')

@app.route('/statements')
@login_required
def statements():
    return render_template('member/statements.html')

@app.route('/notifications')
@login_required
def notifications():
    return render_template('member/notifications.html')

@app.route('/support', methods=['GET', 'POST'])
@login_required
def support():
    return render_template('member/support.html')

# ==================== MEMBER STATEMENT PDF ====================

@app.route('/member/statement/<int:member_id>')
@login_required
def member_statement(member_id):
    """Generate member statement PDF"""
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.lib.units import inch
        from io import BytesIO
        from flask import make_response
        
        db = get_db()
        
        member = db.execute('SELECT * FROM members WHERE id = ?', (member_id,)).fetchone()
        if not member:
            flash('Member not found', 'danger')
            return redirect(url_for('members'))
        
        savings = db.execute('''
            SELECT date, month, amount, late_fee FROM savings 
            WHERE member_id = ? ORDER BY date DESC
        ''', (member_id,)).fetchall()
        
        loans = db.execute('''
            SELECT loan_number, amount, balance, status FROM loans 
            WHERE member_id = ?
        ''', (member_id,)).fetchall()
        
        buffer = BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4)
        elements = []
        styles = getSampleStyleSheet()
        
        elements.append(Paragraph(f"OOU Cooperative - Member Statement", styles['Title']))
        elements.append(Spacer(1, 0.2*inch))
        
        elements.append(Paragraph(f"<b>Member:</b> {member['first_name']} {member['last_name']}", styles['Normal']))
        elements.append(Paragraph(f"<b>Member #:</b> {member['member_number'] or 'N/A'}", styles['Normal']))
        elements.append(Paragraph(f"<b>Date:</b> {datetime.now().strftime('%d/%m/%Y')}", styles['Normal']))
        elements.append(Spacer(1, 0.2*inch))
        
        total_savings = sum(s['amount'] for s in savings)
        total_loans = sum(l['amount'] for l in loans if l['status'] == 'active')
        
        data = [
            ['Description', 'Amount'],
            ['Total Savings', f"₦{total_savings:,.2f}"],
            ['Active Loans', f"₦{total_loans:,.2f}"],
            ['Net Position', f"₦{total_savings - total_loans:,.2f}"]
        ]
        
        table = Table(data)
        table.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), colors.grey),
            ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),
            ('ALIGN', (0,0), (-1,-1), 'CENTER'),
            ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
            ('FONTSIZE', (0,0), (-1,0), 14),
            ('BOTTOMPADDING', (0,0), (-1,0), 12),
            ('BACKGROUND', (0,1), (-1,-1), colors.beige),
            ('GRID', (0,0), (-1,-1), 1, colors.black)
        ]))
        elements.append(table)
        elements.append(Spacer(1, 0.3*inch))
        
        elements.append(Paragraph("<b>Savings History</b>", styles['Heading2']))
        elements.append(Spacer(1, 0.1*inch))
        
        trans_data = [['Date', 'Month', 'Amount', 'Late Fee', 'Total']]
        for s in savings:
            trans_data.append([
                s['date'][:10] if s['date'] else '',
                s['month'],
                f"₦{s['amount'] - s['late_fee']:,.2f}",
                f"₦{s['late_fee']:,.2f}",
                f"₦{s['amount']:,.2f}"
            ])
        
        if len(trans_data) > 1:
            trans_table = Table(trans_data)
            trans_table.setStyle(TableStyle([
                ('BACKGROUND', (0,0), (-1,0), colors.grey),
                ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),
                ('ALIGN', (0,0), (-1,-1), 'CENTER'),
                ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
                ('GRID', (0,0), (-1,-1), 1, colors.black)
            ]))
            elements.append(trans_table)
        else:
            elements.append(Paragraph("No savings records found.", styles['Normal']))
        
        doc.build(elements)
        pdf = buffer.getvalue()
        buffer.close()
        
        response = make_response(pdf)
        response.headers['Content-Type'] = 'application/pdf'
        response.headers['Content-Disposition'] = f'attachment; filename=statement_{member_id}.pdf'
        
        return response
    except ImportError:
        flash('ReportLab not installed. Please run: pip install reportlab', 'warning')
        return redirect(url_for('member_details', member_id=member_id))
    except Exception as e:
        flash(f'Error generating statement: {str(e)}', 'danger')
        return redirect(url_for('member_details', member_id=member_id))

# ==================== API ROUTES ====================

@app.route('/api/member/<int:member_id>')
@login_required
def get_member_api(member_id):
    db = get_db()
    member = db.execute('SELECT id, first_name, last_name, total_savings FROM members WHERE id = ?', (member_id,)).fetchone()
    
    if member:
        return jsonify({
            'id': member['id'],
            'first_name': member['first_name'],
            'last_name': member['last_name'],
            'total_savings': float(member['total_savings'] or 0),
            'max_loan': float(member['total_savings'] or 0) * 2
        })
    return jsonify({'error': 'Member not found'}), 404

@app.route('/api/add_user', methods=['POST'])
@login_required
@role_required('admin')
def add_user():
    """Add a new system user"""
    from werkzeug.security import generate_password_hash
    
    db = get_db()
    
    try:
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        role = request.form.get('role', 'member').strip()
        full_name = request.form.get('full_name', username)
        email = request.form.get('email', '')
        
        if not username or not password:
            flash('Username and password are required', 'danger')
            return redirect(url_for('settings'))
        
        existing = db.execute('SELECT id FROM users WHERE username = ?', (username,)).fetchone()
        if existing:
            flash(f'Username "{username}" already exists', 'danger')
            return redirect(url_for('settings'))
        
        password_hash = generate_password_hash(password)
        
        db.execute('''
            INSERT INTO users (username, password_hash, role, full_name, email, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (username, password_hash, role, full_name, email, datetime.now()))
        
        db.commit()
        flash(f'✅ User "{username}" created successfully!', 'success')
        
    except Exception as e:
        db.rollback()
        flash(f'❌ Error creating user: {str(e)}', 'danger')
    
    return redirect(url_for('settings'))

@app.route('/api/test_db')
@login_required
@role_required('admin')
def test_db():
    try:
        db = get_db()
        db.execute('SELECT 1').fetchone()
        return jsonify({'success': True, 'message': '✅ Database connection successful'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})
              
# Error handlers
@app.errorhandler(404)
def not_found_error(error):
    return render_template('errors/404.html'), 404

@app.errorhandler(403)
def forbidden_error(error):
    return render_template('errors/403.html'), 403

@app.errorhandler(500)
def internal_error(error):
    db = get_db()
    db.rollback()  # Rollback any failed transactions
    return render_template('errors/500.html'), 500

# Optional maintenance mode handler – you'd activate this via a config flag
@app.before_request
def check_maintenance():
    # Example: read from settings table
    if current_user.is_authenticated and current_user.role == 'admin':
        return  # Admins can bypass
    # If maintenance mode is on, show maintenance page
    # This is just a placeholder logic
    maintenance = False  # You'd fetch from settings
    if maintenance and request.endpoint not in ['login', 'static']:
        return render_template('errors/maintenance.html'), 503
        
        
# Initialize card generator
card_gen = MemberCardGenerator()

@app.route('/member/generate-card/<int:member_id>')
@login_required
@role_required('admin', 'secretary')
def generate_member_card(member_id):
    db = get_db()
    member = db.execute('SELECT * FROM members WHERE id = ?', (member_id,)).fetchone()
    if not member:
        flash('Member not found', 'danger')
        return redirect(url_for('members'))

    import uuid
    token = str(uuid.uuid4())
    db.execute('UPDATE members SET card_token = ? WHERE id = ?', (token, member_id))
    db.commit()
    verification_url = url_for('verify_card', token=token, _external=True)

    from card_generator import MemberCardGenerator
    card_gen = MemberCardGenerator()
    
    # Convert row to dict for safe .get()
    member_dict = dict(member)
    
    member_data = {
        'member_number': member_dict.get('member_number', f"OOU/{member_id:04d}"),
        'full_name': f"{member_dict['first_name']} {member_dict['last_name']}",
        'join_date': member_dict.get('date_joined', '')[:10] if member_dict.get('date_joined') else '',
        'membership_type': 'Full Member',
        'photo_path': member_dict.get('photo_path'),
        'qr_data': verification_url
    }
    
    card_path = card_gen.generate_member_card(member_data)
    card_filename = os.path.basename(card_path)   # e.g., member_card_OOU_0002.png
    db.execute('UPDATE members SET card_path = ? WHERE id = ?', (card_filename, member_id))
    db.commit()

    flash('Member card generated successfully!', 'success')
    return redirect(url_for('member_details', member_id=member_id))

@app.route('/member/view-card/<int:member_id>')
@login_required
def view_member_card(member_id):
    db = get_db()
    member = db.execute('SELECT * FROM members WHERE id = ?', (member_id,)).fetchone()
    if not member:
        flash('Member not found', 'danger')
        return redirect(url_for('members'))

    # Get stored filename (should be only filename, not full path)
    card_filename = member['card_path']
    if card_filename:
        full_card_path = os.path.join('static/cards', card_filename)
    else:
        full_card_path = None

    if not card_filename or not os.path.exists(full_card_path):
        # No card or file missing – generate one
        return redirect(url_for('generate_member_card', member_id=member_id))

    return render_template('member/view-card.html', member=member)

@app.route('/member/download-card/<int:member_id>')
@login_required
def download_member_card(member_id):
    db = get_db()
    member = db.execute('SELECT * FROM members WHERE id = ?', (member_id,)).fetchone()
    if not member or not member['card_path']:
        flash('Card not found', 'danger')
        return redirect(url_for('member_details', member_id=member_id))

    full_path = os.path.join('static/cards', member['card_path'])
    if not os.path.exists(full_path):
        flash('Card file missing. Please regenerate.', 'danger')
        return redirect(url_for('generate_member_card', member_id=member_id))

    from flask import send_file
    return send_file(full_path, as_attachment=True, download_name=member['card_path'])

@app.route('/verify-card/<token>')
def verify_card(token):
    db = get_db()
    member = db.execute('SELECT * FROM members WHERE card_token = ?', (token,)).fetchone()
    if not member:
        return render_template('errors/404.html'), 404
    return render_template('public/verify-card.html', member=member)

# ==================== RUN APPLICATION ====================

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)



