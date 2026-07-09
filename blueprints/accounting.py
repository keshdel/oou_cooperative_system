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
