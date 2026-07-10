"""
Accounting blueprint — general-ledger views: chart of accounts, trial balance,
and the journal register. This is the auditable face of the double-entry ledger.
"""

from datetime import datetime

from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user

from database import get_db
from utils import role_required, audit
from ledger import get_accounts, trial_balance, backfill_from_transactions

accounting = Blueprint('accounting', __name__, url_prefix='/accounting')


@accounting.route('/chart')
@login_required
@role_required('admin', 'treasurer')
def chart_of_accounts():
    db = get_db()
    accounts = get_accounts(db, active_only=False)
    # Group by type for display
    groups = {}
    for a in accounts:
        groups.setdefault(a['type'], []).append(a)
    order = ['asset', 'liability', 'equity', 'income', 'expense']
    grouped = [(t, groups[t]) for t in order if t in groups]
    return render_template('accounting/chart.html', grouped=grouped, all_accounts=accounts)


ACCOUNT_TYPES = ('asset', 'liability', 'equity', 'income', 'expense')


@accounting.route('/accounts/add', methods=['POST'])
@login_required
@role_required('admin')
def add_account():
    db = get_db()
    code   = request.form.get('code', '').strip()
    name   = request.form.get('name', '').strip()
    atype  = request.form.get('type', '').strip().lower()
    normal = request.form.get('normal_balance', '').strip().lower()
    parent = request.form.get('parent_code', '').strip() or None
    if not code or not name or atype not in ACCOUNT_TYPES:
        flash('Account code, name, and a valid type are required.', 'danger')
        return redirect(url_for('accounting.chart_of_accounts'))
    # Normal balance defaults from type if not specified
    if normal not in ('debit', 'credit'):
        normal = 'debit' if atype in ('asset', 'expense') else 'credit'
    try:
        db.execute(
            'INSERT INTO accounts (code, name, type, normal_balance, parent_code, is_active) '
            'VALUES (?, ?, ?, ?, ?, 1)', (code, name, atype, normal, parent))
        db.commit()
        audit(db, 'ADD_ACCOUNT', 'accounting', f'Added account {code} — {name} ({atype})')
        flash(f'Account {code} — {name} added.', 'success')
    except Exception as e:
        db.rollback()
        flash(f'Could not add account (the code may already exist): {e}', 'danger')
    return redirect(url_for('accounting.chart_of_accounts'))


@accounting.route('/accounts/<code>/toggle', methods=['POST'])
@login_required
@role_required('admin')
def toggle_account(code):
    db = get_db()
    a = db.execute('SELECT is_active FROM accounts WHERE code = ?', (code,)).fetchone()
    if not a:
        flash('Account not found.', 'danger')
        return redirect(url_for('accounting.chart_of_accounts'))
    new_val = 0 if a['is_active'] else 1
    db.execute('UPDATE accounts SET is_active = ? WHERE code = ?', (new_val, code))
    db.commit()
    audit(db, 'TOGGLE_ACCOUNT', 'accounting',
          f'Account {code} {"reactivated" if new_val else "deactivated"}')
    flash(f'Account {code} {"reactivated" if new_val else "deactivated"}.', 'success')
    return redirect(url_for('accounting.chart_of_accounts'))


@accounting.route('/journal/new', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'treasurer')
def new_journal():
    db = get_db()
    if request.method == 'POST':
        from ledger import post_journal
        desc = request.form.get('description', '').strip()
        date = request.form.get('date') or None
        ref  = request.form.get('reference', '').strip()
        codes   = request.form.getlist('account')
        debits  = request.form.getlist('debit')
        credits = request.form.getlist('credit')
        lines = []
        for c, d, cr in zip(codes, debits, credits):
            c = (c or '').strip()
            if not c:
                continue
            try:
                d_v = float(d or 0); c_v = float(cr or 0)
            except ValueError:
                continue
            if d_v == 0 and c_v == 0:
                continue
            lines.append({'account': c, 'debit': d_v, 'credit': c_v})
        try:
            if not desc:
                raise ValueError('A description is required.')
            eid = post_journal(db, desc, lines, date=date, reference=ref,
                               source_module='manual', created_by=current_user.id)
            if eid is None:
                raise ValueError('Enter at least one debit and one credit line.')
            db.commit()
            audit(db, 'MANUAL_JOURNAL', 'accounting', f'Posted manual journal: {desc}')
            flash('Journal entry posted.', 'success')
            return redirect(url_for('accounting.journal_register'))
        except ValueError as e:
            db.rollback()
            flash(str(e), 'danger')
        except Exception as e:
            db.rollback()
            flash(f'Error posting entry: {e}', 'danger')
    return render_template('accounting/journal_new.html',
                           accounts=get_accounts(db, active_only=True),
                           today=datetime.now().strftime('%Y-%m-%d'))


@accounting.route('/trial-balance')
@login_required
@role_required('admin', 'treasurer')
def trial_balance_view():
    db = get_db()
    as_of = request.args.get('as_of', datetime.now().strftime('%Y-%m-%d'))
    tb = trial_balance(db, as_of=as_of)
    fmt = request.args.get('format')
    if fmt:
        from report_export import report_response
        rows = [{'cells': [r['code'], r['name'], r['type'].title(), r['debit'], r['credit']]}
                for r in tb['rows']]
        rows.append({'cells': ['', 'Totals', '', tb['total_debit'], tb['total_credit']], 'bold': True})
        report = {'title': 'Trial Balance', 'subtitle': f'As at {as_of}',
                  'sections': [{'columns': ['Code', 'Account', 'Type', 'Debit', 'Credit'], 'rows': rows}]}
        return report_response(report, fmt,
                               redirect_url=url_for('accounting.trial_balance_view', as_of=as_of))
    return render_template('accounting/trial_balance.html', tb=tb, as_of=as_of)


def _pct(source, name, default):
    try:
        return float(source.get(name, default))
    except (TypeError, ValueError):
        return default


@accounting.route('/dividends')
@login_required
@role_required('admin', 'treasurer')
def dividends():
    from dividends import compute_dividend_schedule
    db = get_db()
    today = datetime.now()
    from_date = request.args.get('from_date', today.replace(month=1, day=1).strftime('%Y-%m-%d'))
    to_date   = request.args.get('to_date', today.strftime('%Y-%m-%d'))
    rates = {
        'dividend_pct':    _pct(request.args, 'dividend_pct', 50),
        'reserve_pct':     _pct(request.args, 'reserve_pct', 25),
        'honorarium_pct':  _pct(request.args, 'honorarium_pct', 10),
        'other_pct':       _pct(request.args, 'other_pct', 15),
        'patronage_split': _pct(request.args, 'patronage_split', 0),
    }
    sched = None
    if request.args.get('preview'):
        sched = compute_dividend_schedule(db, from_date, to_date, **rates)
    declarations = db.execute(
        'SELECT * FROM dividend_declarations ORDER BY declared_at DESC'
    ).fetchall()
    return render_template('accounting/dividends.html',
                           from_date=from_date, to_date=to_date,
                           sched=sched, declarations=declarations, **rates)


@accounting.route('/dividends/declare', methods=['POST'])
@login_required
@role_required('admin')
def declare_dividend():
    from dividends import declare_dividends
    db = get_db()
    try:
        from_date = request.form['from_date']
        to_date   = request.form['to_date']
        decl_id = declare_dividends(
            db, from_date, to_date,
            dividend_pct=_pct(request.form, 'dividend_pct', 50),
            reserve_pct=_pct(request.form, 'reserve_pct', 25),
            honorarium_pct=_pct(request.form, 'honorarium_pct', 10),
            other_pct=_pct(request.form, 'other_pct', 15),
            patronage_split=_pct(request.form, 'patronage_split', 0),
            declared_by=current_user.id,
        )
        db.commit()
        audit(db, 'DECLARE_DIVIDEND', 'accounting',
              f'Declared dividend #{decl_id} for {from_date} to {to_date}')
        flash('Dividend declared and credited to members\' savings.', 'success')
        return redirect(url_for('accounting.dividend_detail', decl_id=decl_id))
    except ValueError as e:
        db.rollback()
        flash(str(e), 'warning')
        return redirect(url_for('accounting.dividends'))
    except Exception as e:
        db.rollback()
        flash(f'Error declaring dividend: {e}', 'danger')
        return redirect(url_for('accounting.dividends'))


@accounting.route('/dividends/<int:decl_id>')
@login_required
@role_required('admin', 'treasurer')
def dividend_detail(decl_id):
    db = get_db()
    decl = db.execute('SELECT * FROM dividend_declarations WHERE id = ?', (decl_id,)).fetchone()
    if not decl:
        flash('Dividend declaration not found.', 'danger')
        return redirect(url_for('accounting.dividends'))
    allocs = db.execute('''
        SELECT da.*, m.member_number, m.first_name, m.last_name
        FROM dividend_allocations da JOIN members m ON m.id = da.member_id
        WHERE da.declaration_id = ? ORDER BY da.total DESC
    ''', (decl_id,)).fetchall()
    fmt = request.args.get('format')
    if fmt:
        from report_export import report_response
        appro = [
            {'cells': ['Net surplus', decl['net_surplus']], 'bold': True},
            {'cells': ['Statutory reserve', decl['reserve_amount']]},
            {'cells': ['Honorarium', decl['honorarium_amount']]},
            {'cells': ['Other', decl['other_amount']]},
            {'cells': ['Dividend pool', decl['dividend_pool']], 'bold': True},
        ]
        alloc_rows = [{'cells': [a['member_number'], f"{a['first_name']} {a['last_name']}",
                                 a['savings_base'], a['dividend_savings'],
                                 a['dividend_patronage'], a['total']]} for a in allocs]
        report = {
            'title': 'Dividend Declaration',
            'subtitle': f"{decl['period_from']} to {decl['period_to']}",
            'sections': [
                {'heading': 'Appropriation', 'columns': ['', 'Amount'], 'rows': appro},
                {'heading': 'Member Allocations',
                 'columns': ['Member No', 'Name', 'Savings', 'On savings', 'Patronage', 'Total'],
                 'rows': alloc_rows},
            ],
        }
        return report_response(report, fmt,
                               redirect_url=url_for('accounting.dividend_detail', decl_id=decl_id))
    return render_template('accounting/dividend_detail.html', decl=decl, allocs=allocs)


@accounting.route('/backfill', methods=['POST'])
@login_required
@role_required('admin')
def backfill():
    """Post journal entries for existing transactions not yet in the ledger."""
    db = get_db()
    try:
        n = backfill_from_transactions(db, created_by=current_user.id)
        db.commit()
        audit(db, 'GL_BACKFILL', 'accounting',
              f'Backfilled {n} transactions into the general ledger')
        if n:
            flash(f'Posted {n} historical transaction(s) to the general ledger.', 'success')
        else:
            flash('The ledger is already up to date — nothing to backfill.', 'info')
    except Exception as e:
        db.rollback()
        flash(f'Error backfilling ledger: {e}', 'danger')
    return redirect(url_for('accounting.journal_register'))


@accounting.route('/journal')
@login_required
@role_required('admin', 'treasurer')
def journal_register():
    db = get_db()
    entries = db.execute('''
        SELECT id, entry_number, date, description, reference, source_module
        FROM journal_entries
        ORDER BY date DESC, id DESC
        LIMIT 200
    ''').fetchall()
    # Fetch lines for the listed entries
    lines_by_entry = {}
    for e in entries:
        rows = db.execute('''
            SELECT jl.account_code, a.name AS account_name, jl.debit, jl.credit, jl.memo
            FROM journal_lines jl
            LEFT JOIN accounts a ON a.code = jl.account_code
            WHERE jl.entry_id = ?
            ORDER BY jl.debit DESC, jl.id
        ''', (e['id'],)).fetchall()
        lines_by_entry[e['id']] = rows
    return render_template('accounting/journal.html',
                           entries=entries, lines_by_entry=lines_by_entry)
