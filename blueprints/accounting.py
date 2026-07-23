"""
Accounting blueprint — general-ledger views: chart of accounts, trial balance,
and the journal register. This is the auditable face of the double-entry ledger.
"""

import csv
import io
from datetime import datetime

from flask import Blueprint, render_template, request, redirect, url_for, flash, make_response, jsonify
from flask_login import login_required, current_user

from database import get_db
from utils import role_required, audit
from ledger import (get_accounts, trial_balance, backfill_from_transactions,
                    ledger_reconciliation, account_ledger, journal_entry_detail,
                    get_lock_date, reverse_journal_entry, PeriodLockedError,
                    get_default_cash_account)

accounting = Blueprint('accounting', __name__, url_prefix='/accounting')


def _today():
    return datetime.now().strftime('%Y-%m-%d')


def _year_start():
    return datetime.now().replace(month=1, day=1).strftime('%Y-%m-%d')


def _bank_account_rows(db):
    """Return active cash/bank GL accounts used for bank-position reporting."""
    rows = db.execute('''
        SELECT code, name, type, normal_balance, parent_code, is_active
        FROM accounts
        WHERE is_active = 1
          AND type = 'asset'
          AND (
                code = '1000'
             OR parent_code = '1000'
             OR LOWER(name) LIKE ?
             OR LOWER(name) LIKE ?
             OR LOWER(name) LIKE ?
          )
        ORDER BY
          CASE WHEN parent_code = '1000' THEN 0 WHEN code = '1000' THEN 1 ELSE 2 END,
          code
    ''', ('%bank%', '%cash%', '%wallet%')).fetchall()
    return [dict(r) for r in rows]


def _bank_positions(db, from_date, to_date):
    default_cash_account = get_default_cash_account(db)
    positions = []
    totals = {
        'opening_balance': 0.0,
        'cash_in': 0.0,
        'cash_out': 0.0,
        'closing_balance': 0.0,
        'entries': 0,
    }
    for account in _bank_account_rows(db):
        data = account_ledger(db, account['code'], from_date, to_date)
        if not data:
            continue
        row = {
            'code': account['code'],
            'name': account['name'],
            'parent_code': account.get('parent_code'),
            'is_default': account['code'] == default_cash_account,
            'opening_balance': data['opening_balance'],
            'cash_in': data['total_debit'],
            'cash_out': data['total_credit'],
            'closing_balance': data['closing_balance'],
            'entries': data['count'],
        }
        positions.append(row)
        totals['opening_balance'] += row['opening_balance']
        totals['cash_in'] += row['cash_in']
        totals['cash_out'] += row['cash_out']
        totals['closing_balance'] += row['closing_balance']
        totals['entries'] += row['entries']
    for key in ('opening_balance', 'cash_in', 'cash_out', 'closing_balance'):
        totals[key] = round(totals[key], 2)
    return positions, totals, default_cash_account


@accounting.route('/chart')
@login_required
@role_required('admin', 'treasurer')
def chart_of_accounts():
    db = get_db()
    accounts = get_accounts(db, active_only=False)
    default_cash_account = get_default_cash_account(db)
    account_list = [dict(a) for a in accounts]
    by_code = {a['code']: a for a in account_list}
    children = {}
    for a in account_list:
        if a.get('parent_code'):
            children.setdefault(a['parent_code'], []).append(a)
    for a in account_list:
        a['children'] = children.get(a['code'], [])
        a['is_parent'] = bool(a['children'])
    # Group by type for display
    groups = {}
    for a in account_list:
        groups.setdefault(a['type'], []).append(a)
    order = ['asset', 'liability', 'equity', 'income', 'expense']
    grouped = [(t, groups[t]) for t in order if t in groups]
    return render_template('accounting/chart.html', grouped=grouped,
                           all_accounts=account_list, accounts_by_code=by_code,
                           default_cash_account=default_cash_account)


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
    if parent:
        parent_row = db.execute(
            'SELECT code, type, normal_balance FROM accounts WHERE code = ? AND is_active = 1',
            (parent,)
        ).fetchone()
        if not parent_row:
            flash('Selected parent account does not exist or is inactive.', 'danger')
            return redirect(url_for('accounting.chart_of_accounts'))
        if not atype:
            atype = parent_row['type']
        if atype != parent_row['type']:
            flash('A detail account must use the same type as its parent account.', 'danger')
            return redirect(url_for('accounting.chart_of_accounts'))
        if not normal:
            normal = parent_row['normal_balance']
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


@accounting.route('/accounts/default-cash', methods=['POST'])
@login_required
@role_required('admin')
def set_default_cash_account():
    db = get_db()
    code = request.form.get('default_cash_account', '').strip()
    account = db.execute(
        "SELECT code, name FROM accounts WHERE code = ? AND type = 'asset' AND is_active = 1",
        (code,)
    ).fetchone()
    if not account:
        flash('Choose an active asset account for cash/bank posting.', 'danger')
        return redirect(url_for('accounting.chart_of_accounts'))
    try:
        existing = db.execute(
            "SELECT id FROM settings WHERE key = 'default_cash_account'"
        ).fetchone()
        if existing:
            db.execute(
                "UPDATE settings SET value = ? WHERE key = 'default_cash_account'",
                (code,)
            )
        else:
            db.execute(
                "INSERT INTO settings (key, value, description) VALUES (?, ?, ?)",
                ('default_cash_account', code, 'Default cash/bank GL account for receipts and disbursements')
            )
        db.commit()
        audit(db, 'SET_DEFAULT_CASH_ACCOUNT', 'accounting',
              f'Set default cash/bank posting account to {code} - {account["name"]}')
        flash(f'Default cash/bank posting account set to {code} - {account["name"]}.', 'success')
    except Exception as e:
        db.rollback()
        flash(f'Could not update default cash/bank account: {e}', 'danger')
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
        memos   = request.form.getlist('memo')
        lines = []
        for c, d, cr, memo in zip(codes, debits, credits, memos):
            c = (c or '').strip()
            if not c:
                continue
            try:
                d_v = float(d or 0); c_v = float(cr or 0)
            except ValueError:
                continue
            if d_v == 0 and c_v == 0:
                continue
            lines.append({'account': c, 'debit': d_v, 'credit': c_v, 'memo': (memo or '').strip()})
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


@accounting.route('/reconciliation')
@login_required
@role_required('admin', 'treasurer')
def reconciliation():
    db = get_db()
    rec = ledger_reconciliation(db)
    return render_template('accounting/reconciliation.html', rec=rec)


@accounting.route('/bank-accounts')
@login_required
@role_required('admin', 'treasurer')
def bank_accounts():
    db = get_db()
    from_date = request.args.get('from_date') or _year_start()
    to_date = request.args.get('to_date') or _today()
    positions, totals, default_cash_account = _bank_positions(db, from_date, to_date)

    if request.args.get('format') == 'csv':
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow([
            'account_code', 'account_name', 'opening_balance', 'cash_in',
            'cash_out', 'closing_balance', 'entries', 'is_default',
            'from_date', 'to_date',
        ])
        for row in positions:
            writer.writerow([
                row['code'], row['name'], f"{row['opening_balance']:.2f}",
                f"{row['cash_in']:.2f}", f"{row['cash_out']:.2f}",
                f"{row['closing_balance']:.2f}", row['entries'],
                'yes' if row['is_default'] else 'no', from_date, to_date,
            ])
        writer.writerow([])
        writer.writerow([
            'TOTAL', '', f"{totals['opening_balance']:.2f}",
            f"{totals['cash_in']:.2f}", f"{totals['cash_out']:.2f}",
            f"{totals['closing_balance']:.2f}", totals['entries'], '', from_date, to_date,
        ])
        resp = make_response(out.getvalue())
        resp.headers['Content-Type'] = 'text/csv; charset=utf-8'
        resp.headers['Content-Disposition'] = 'attachment; filename=bank_accounts_position.csv'
        return resp

    return render_template('accounting/bank-accounts.html',
                           positions=positions, totals=totals,
                           default_cash_account=default_cash_account,
                           from_date=from_date, to_date=to_date,
                           generated_on=datetime.now())


@accounting.route('/bank-accounts/<code>')
@login_required
@role_required('admin', 'treasurer')
def bank_account_detail(code):
    db = get_db()
    account = db.execute('''
        SELECT code FROM accounts
        WHERE code = ? AND is_active = 1 AND type = 'asset'
          AND (
                code = '1000'
             OR parent_code = '1000'
             OR LOWER(name) LIKE ?
             OR LOWER(name) LIKE ?
             OR LOWER(name) LIKE ?
          )
    ''', (code, '%bank%', '%cash%', '%wallet%')).fetchone()
    if not account:
        flash('Bank/cash account not found.', 'danger')
        return redirect(url_for('accounting.bank_accounts'))

    from_date = request.args.get('from_date') or _year_start()
    to_date = request.args.get('to_date') or _today()
    data = account_ledger(db, code, from_date, to_date)
    statement_balance_raw = request.args.get('statement_balance', '').strip()
    statement_balance = None
    variance = None
    variance_reconciled = None
    if statement_balance_raw:
        try:
            statement_balance = float(statement_balance_raw.replace(',', ''))
            variance = round(statement_balance - data['closing_balance'], 2)
            variance_reconciled = abs(variance) < 0.01
        except ValueError:
            flash('Statement balance must be a valid number.', 'warning')

    if request.args.get('format') == 'csv':
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(['account_code', data['account']['code']])
        writer.writerow(['account_name', data['account']['name']])
        writer.writerow(['from_date', from_date])
        writer.writerow(['to_date', to_date])
        writer.writerow(['opening_balance', f"{data['opening_balance']:.2f}"])
        writer.writerow(['cash_in', f"{data['total_debit']:.2f}"])
        writer.writerow(['cash_out', f"{data['total_credit']:.2f}"])
        writer.writerow(['gl_closing_balance', f"{data['closing_balance']:.2f}"])
        if statement_balance is not None:
            writer.writerow(['statement_balance', f"{statement_balance:.2f}"])
            writer.writerow(['variance', f"{variance:.2f}"])
        writer.writerow([])
        writer.writerow([
            'date', 'entry_number', 'description', 'reference', 'source_module',
            'memo', 'cash_in', 'cash_out', 'running_balance',
        ])
        for e in data['entries']:
            writer.writerow([
                str(e.get('date') or '')[:10],
                e.get('entry_number') or f"JE-{e.get('entry_id')}",
                e.get('description') or '',
                e.get('reference') or '',
                e.get('source_module') or '',
                e.get('memo') or '',
                f"{float(e.get('debit') or 0):.2f}",
                f"{float(e.get('credit') or 0):.2f}",
                f"{float(e.get('balance') or 0):.2f}",
            ])
        resp = make_response(out.getvalue())
        resp.headers['Content-Type'] = 'text/csv; charset=utf-8'
        resp.headers['Content-Disposition'] = f'attachment; filename=bank_account_{code}.csv'
        return resp

    return render_template('accounting/bank-account-detail.html',
                           data=data, account=data['account'],
                           from_date=from_date, to_date=to_date,
                           statement_balance=statement_balance,
                           statement_balance_raw=statement_balance_raw,
                           variance=variance,
                           variance_reconciled=variance_reconciled,
                           generated_on=datetime.now())


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


def _source_link(db, module, source_id):
    """Resolve a journal entry's originating record to (label, url) for drill-down.

    Returns (label, None) when the source is known but has no dedicated page.
    """
    module = (module or '').lower()
    if not source_id:
        return (module.title() or 'Manual entry', None)
    try:
        if module == 'savings_deposit':
            # Precise linkage: source_id is the savings row.
            row = db.execute(
                'SELECT s.member_id, m.first_name, m.last_name, m.member_number '
                'FROM savings s LEFT JOIN members m ON m.id = s.member_id WHERE s.id = ?',
                (source_id,)).fetchone()
            if row and row['member_id']:
                return (f"Savings — {row['first_name']} {row['last_name']} (#{row['member_number']})",
                        url_for('members.member_savings_statement', member_id=row['member_id']))
            return ('Savings contribution', None)
        if module in ('savings', 'payments'):
            # Legacy coarse linkage: source_id was the member id.
            m = db.execute('SELECT id, first_name, last_name, member_number FROM members WHERE id = ?',
                           (source_id,)).fetchone()
            if m:
                return (f"Savings/payment — {m['first_name']} {m['last_name']} (#{m['member_number']})",
                        url_for('members.member_savings_statement', member_id=m['id']))
            return ('Member transaction', None)
        if module == 'loan_repayment':
            # Precise linkage: source_id is the repayment row.
            rep = db.execute(
                'SELECT r.loan_id, l.loan_number FROM repayments r '
                'LEFT JOIN loans l ON l.id = r.loan_id WHERE r.id = ?', (source_id,)).fetchone()
            if rep and rep['loan_id']:
                return (f"Loan repayment — {rep['loan_number'] or rep['loan_id']}",
                        url_for('loans.loan_detail', loan_id=rep['loan_id']))
            return ('Loan repayment', None)
        if module in ('loans', 'loan_disbursement'):
            row = db.execute(
                'SELECT id, loan_number FROM loans WHERE id = ?', (source_id,)).fetchone()
            if row:
                return (f"Loan {row['loan_number'] or row['id']}",
                        url_for('loans.loan_detail', loan_id=row['id']))
            return ('Loan transaction', None)
        if module == 'investments':
            return ('Investment', url_for('investments.investments_list'))
        if module == 'dividend':
            return ('Dividend declaration',
                    url_for('accounting.dividend_detail', decl_id=source_id))
        if module == 'opening':
            return ('Opening balance import', None)
    except Exception:
        pass
    return (module.title() or 'Manual entry', None)


@accounting.route('/ledger/<code>')
@login_required
@role_required('admin', 'treasurer')
def account_ledger_view(code):
    """Audit drill-down: every journal line that hit one account."""
    db = get_db()
    from_date = request.args.get('from_date', '')
    to_date   = request.args.get('to_date', '')
    data = account_ledger(db, code, from_date or None, to_date or None)
    if not data:
        flash(f'Account {code} not found.', 'danger')
        return redirect(url_for('accounting.chart_of_accounts'))
    return render_template('accounting/account-ledger.html',
                           data=data, account=data['account'],
                           from_date=from_date, to_date=to_date,
                           generated_on=datetime.now())


@accounting.route('/ledger/<code>/export')
@login_required
@role_required('admin', 'treasurer')
def account_ledger_export(code):
    """Export one GL account register as CSV for audit and spreadsheet analysis."""
    db = get_db()
    from_date = request.args.get('from_date', '')
    to_date   = request.args.get('to_date', '')
    data = account_ledger(db, code, from_date or None, to_date or None)
    if not data:
        flash(f'Account {code} not found.', 'danger')
        return redirect(url_for('accounting.chart_of_accounts'))

    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow([
        'account_code', 'account_name', 'account_type', 'normal_balance',
        'from_date', 'to_date', 'opening_balance',
    ])
    writer.writerow([
        data['account']['code'], data['account']['name'], data['account']['type'],
        data['account']['normal_balance'], from_date, to_date, data['opening_balance'],
    ])
    writer.writerow([])
    writer.writerow([
        'date', 'entry_number', 'description', 'reference', 'source_module',
        'source_id', 'line_memo', 'debit', 'credit', 'running_balance',
    ])
    for e in data['entries']:
        writer.writerow([
            str(e.get('date') or '')[:10],
            e.get('entry_number') or f"JE-{e.get('entry_id')}",
            e.get('description') or '',
            e.get('reference') or '',
            e.get('source_module') or '',
            e.get('source_id') or '',
            e.get('memo') or '',
            f"{float(e.get('debit') or 0):.2f}",
            f"{float(e.get('credit') or 0):.2f}",
            f"{float(e.get('balance') or 0):.2f}",
        ])
    writer.writerow([])
    writer.writerow(['totals', '', '', '', '', '', '', f"{data['total_debit']:.2f}", f"{data['total_credit']:.2f}", f"{data['closing_balance']:.2f}"])

    resp = make_response(out.getvalue())
    resp.headers['Content-Type'] = 'text/csv; charset=utf-8'
    resp.headers['Content-Disposition'] = f'attachment; filename=gl_register_{code}.csv'
    return resp


@accounting.route('/journal/<int:entry_id>')
@login_required
@role_required('admin', 'treasurer')
def journal_entry_view(entry_id):
    """Audit drill-down: one journal entry, its lines, and its source document."""
    db = get_db()
    data = journal_entry_detail(db, entry_id)
    if not data:
        flash('Journal entry not found.', 'danger')
        return redirect(url_for('accounting.journal_register'))
    src_label, src_url = _source_link(db, data['entry'].get('source_module'),
                                      data['entry'].get('source_id'))
    lock = get_lock_date(db)
    entry_locked = bool(lock) and str(data['entry'].get('date') or '')[:10] <= lock
    return render_template('accounting/journal-entry.html',
                           data=data, entry=data['entry'], lines=data['lines'],
                           source_label=src_label, source_url=src_url,
                           lock_date=lock, entry_locked=entry_locked)


@accounting.route('/journal/<int:entry_id>/quick-view')
@login_required
@role_required('admin', 'treasurer')
def journal_entry_quick_view(entry_id):
    """Compact journal detail for the in-app audit drawer."""
    db = get_db()
    data = journal_entry_detail(db, entry_id)
    if not data:
        return jsonify({'ok': False, 'message': 'Journal entry not found.'}), 404
    src_label, src_url = _source_link(db, data['entry'].get('source_module'),
                                      data['entry'].get('source_id'))
    lock = get_lock_date(db)
    entry_locked = bool(lock) and str(data['entry'].get('date') or '')[:10] <= lock
    html = render_template('accounting/_journal_quick_view.html',
                           data=data, entry=data['entry'], lines=data['lines'],
                           source_label=src_label, source_url=src_url,
                           lock_date=lock, entry_locked=entry_locked)
    return jsonify({
        'ok': True,
        'title': data['entry'].get('entry_number') or f"JE-{entry_id}",
        'html': html,
    })


@accounting.route('/journal/<int:entry_id>/reverse', methods=['POST'])
@login_required
@role_required('admin', 'treasurer')
def reverse_entry(entry_id):
    db = get_db()
    try:
        new_id, source_note = reverse_journal_entry(db, entry_id, created_by=current_user.id)
        db.commit()
        audit(db, 'REVERSE_JOURNAL', 'accounting',
              f'Reversed journal entry {entry_id} with new entry {new_id}'
              + (f' ({source_note})' if source_note else ''))
        msg = 'Entry reversed — a balanced offsetting entry has been posted.'
        if source_note:
            msg += ' ' + source_note
        flash(msg, 'success')
        return redirect(url_for('accounting.journal_entry_view', entry_id=new_id))
    except PeriodLockedError as e:
        db.rollback()
        flash(str(e), 'warning')
    except ValueError as e:
        db.rollback()
        flash(str(e), 'danger')
    except Exception as e:
        db.rollback()
        flash(f'Could not reverse entry: {e}', 'danger')
    return redirect(url_for('accounting.journal_entry_view', entry_id=entry_id))


@accounting.route('/period-close', methods=['GET'])
@login_required
@role_required('admin', 'treasurer')
def period_close():
    db = get_db()
    lock = get_lock_date(db)
    # Recent lock-date changes for an audit trail.
    try:
        history = db.execute(
            "SELECT username, description, timestamp FROM audit_log "
            "WHERE action = 'PERIOD_LOCK' ORDER BY id DESC LIMIT 10").fetchall()
    except Exception:
        history = []
    return render_template('accounting/period-close.html',
                           lock_date=lock, history=history,
                           today=datetime.now().strftime('%Y-%m-%d'))


@accounting.route('/period-close/set', methods=['POST'])
@login_required
@role_required('admin')
def set_lock_date():
    db = get_db()
    new_date = request.form.get('lock_date', '').strip()
    action = request.form.get('action', 'set')

    if action == 'clear':
        new_date = ''
    elif new_date:
        # Validate the date format.
        try:
            datetime.strptime(new_date, '%Y-%m-%d')
        except ValueError:
            flash('Enter a valid date (YYYY-MM-DD).', 'danger')
            return redirect(url_for('accounting.period_close'))

    row = db.execute("SELECT value FROM settings WHERE key = 'books_lock_date'").fetchone()
    if row is None:
        db.execute("INSERT INTO settings (key, value, description) VALUES (?, ?, ?)",
                   ('books_lock_date', new_date, 'Books locked through this date'))
    else:
        db.execute("UPDATE settings SET value = ? WHERE key = 'books_lock_date'", (new_date,))
    db.commit()

    if new_date:
        audit(db, 'PERIOD_LOCK', 'accounting', f'Books locked through {new_date}')
        flash(f'Books are now locked through {new_date}. Entries on or before that date are blocked.', 'success')
    else:
        audit(db, 'PERIOD_LOCK', 'accounting', 'Books unlocked (lock date cleared)')
        flash('Books unlocked — no period lock is in effect.', 'info')
    return redirect(url_for('accounting.period_close'))


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


@accounting.route('/journal/export')
@login_required
@role_required('admin', 'treasurer')
def journal_register_export():
    """Export the journal register line-by-line as CSV."""
    db = get_db()
    from_date = request.args.get('from_date', '')
    to_date = request.args.get('to_date', '')
    where = []
    params = []
    if from_date:
        where.append('je.date >= ?')
        params.append(from_date)
    if to_date:
        where.append('je.date <= ?')
        params.append(f'{to_date} 23:59:59')
    where_sql = 'WHERE ' + ' AND '.join(where) if where else ''

    rows = db.execute(f'''
        SELECT je.entry_number, je.date, je.description, je.reference,
               je.source_module, je.source_id,
               jl.account_code, a.name AS account_name, jl.memo, jl.debit, jl.credit
        FROM journal_entries je
        JOIN journal_lines jl ON jl.entry_id = je.id
        LEFT JOIN accounts a ON a.code = jl.account_code
        {where_sql}
        ORDER BY je.date DESC, je.id DESC, jl.id ASC
    ''', tuple(params)).fetchall()

    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow([
        'entry_number', 'date', 'description', 'reference', 'source_module',
        'source_id', 'account_code', 'account_name', 'line_memo', 'debit', 'credit',
    ])
    for r in rows:
        writer.writerow([
            r['entry_number'], str(r['date'] or '')[:10], r['description'] or '',
            r['reference'] or '', r['source_module'] or '', r['source_id'] or '',
            r['account_code'], r['account_name'] or '', r['memo'] or '',
            f"{float(r['debit'] or 0):.2f}", f"{float(r['credit'] or 0):.2f}",
        ])

    resp = make_response(out.getvalue())
    resp.headers['Content-Type'] = 'text/csv; charset=utf-8'
    resp.headers['Content-Disposition'] = 'attachment; filename=journal_register.csv'
    return resp
