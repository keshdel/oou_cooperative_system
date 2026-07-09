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
    return render_template('accounting/chart.html', grouped=grouped)


@accounting.route('/trial-balance')
@login_required
@role_required('admin', 'treasurer')
def trial_balance_view():
    db = get_db()
    as_of = request.args.get('as_of', datetime.now().strftime('%Y-%m-%d'))
    tb = trial_balance(db, as_of=as_of)
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
