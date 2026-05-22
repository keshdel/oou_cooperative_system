import random
from datetime import datetime, timedelta

from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_required

from database import get_db
from utils import role_required

reports = Blueprint('reports', __name__)


@reports.route('/reports')
@login_required
def reports_list():
    db = get_db()

    def get_val(query, params=()):
        row = db.execute(query, params).fetchone()
        return row[0] if row and row[0] is not None else 0

    try:
        total_members = get_val('SELECT COUNT(*) FROM members')
        active_members = get_val("SELECT COUNT(*) FROM members WHERE status = 'active'")
        inactive_members = total_members - active_members
        members_with_loans = get_val("SELECT COUNT(DISTINCT member_id) FROM loans WHERE status = 'active'")

        thirty_days_ago = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')
        new_members_month = get_val(
            "SELECT COUNT(*) FROM members WHERE date_joined >= ?", (thirty_days_ago,)
        )

        total_savings_all = get_val('SELECT COALESCE(SUM(amount), 0) FROM savings')
        current_month = datetime.now().strftime('%Y-%m')
        this_month_savings = get_val(
            'SELECT COALESCE(SUM(amount), 0) FROM savings WHERE month = ?', (current_month,)
        )
        total_late_fees = get_val('SELECT COALESCE(SUM(late_fee), 0) FROM savings')
        avg_savings_per_member = total_savings_all / total_members if total_members > 0 else 0

        active_loans_total = get_val("SELECT COALESCE(SUM(amount), 0) FROM loans WHERE status = 'active'")
        total_disbursed = get_val(
            "SELECT COALESCE(SUM(amount), 0) FROM loans WHERE status IN ('active', 'completed')"
        )
        total_repaid = get_val('SELECT COALESCE(SUM(amount), 0) FROM repayments')
        total_interest = total_disbursed * 0.11 if total_disbursed else 0

        active_loans_count = get_val("SELECT COUNT(*) FROM loans WHERE status = 'active'")
        completed_loans_count = get_val("SELECT COUNT(*) FROM loans WHERE status = 'completed'")
        pending_loans_count = get_val("SELECT COUNT(*) FROM loans WHERE status = 'pending'")
        rejected_loans_count = get_val("SELECT COUNT(*) FROM loans WHERE status = 'rejected'")

        total_investments_value = get_val('SELECT COALESCE(SUM(amount), 0) FROM investments')

        savings_months = []
        monthly_savings_data = []
        for i in range(5, -1, -1):
            month_date = datetime.now().replace(day=1) - timedelta(days=30 * i)
            savings_months.append(month_date.strftime('%b'))
            month_str = month_date.strftime('%Y-%m')
            monthly_savings_data.append(
                get_val('SELECT COALESCE(SUM(amount), 0) FROM savings WHERE month = ?', (month_str,))
            )

        join_months = []
        new_members_data = []
        for i in range(5, -1, -1):
            month_start = (datetime.now().replace(day=1) - timedelta(days=30 * i)).strftime('%Y-%m-01')
            month_label = datetime.now().replace(day=1) - timedelta(days=30 * i)
            join_months.append(month_label.strftime('%b'))
            if i > 0:
                month_end = (datetime.now().replace(day=1) - timedelta(days=30 * (i - 1))).strftime('%Y-%m-01')
                count = get_val(
                    "SELECT COUNT(*) FROM members WHERE date_joined >= ? AND date_joined < ?",
                    (month_start, month_end)
                )
            else:
                count = get_val(
                    "SELECT COUNT(*) FROM members WHERE date_joined >= ?", (month_start,)
                )
            new_members_data.append(count)

        top_savers_rows = db.execute('''
            SELECT m.id, m.first_name, m.last_name, COALESCE(SUM(s.amount), 0) as total_savings
            FROM members m
            LEFT JOIN savings s ON m.id = s.member_id
            GROUP BY m.id
            ORDER BY total_savings DESC
            LIMIT 5
        ''').fetchall()
        top_savers = [
            {
                'id': ts['id'],
                'name': f"{ts['first_name']} {ts['last_name']}",
                'total_savings': float(ts['total_savings']),
                'monthly_avg': float(ts['total_savings']) / 6 if ts['total_savings'] > 0 else 0,
                'join_date': '',
            }
            for ts in top_savers_rows
        ]

        investment_type_labels = ['Fixed Deposit', 'Shares', 'Real Estate', 'Bonds', 'Other']
        investment_type_data = [random.randint(100000, 1000000) for _ in range(5)]

        dividend_amount = total_savings_all * 0.05
        reserve_amount = dividend_amount * 0.3
        honorarium_amount = dividend_amount * 0.1
        other_appropriations = dividend_amount * 0.1

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
            delinquent_loans=[],
            active_savings=total_savings_all,
            inactive_savings=0,
            loan_member_savings=total_savings_all * 0.6,
            total_income_year=total_savings_all,
            total_expenses_year=total_investments_value,
            net_surplus_year=total_savings_all - total_investments_value,
            member_dividends=[],
            suspended_members=0,
        )
    except Exception as e:
        import traceback
        traceback.print_exc()
        flash(f'Unable to load reports due to an internal error: {str(e)}', 'danger')
        zeros = {
            'total_members': 0, 'active_members': 0, 'inactive_members': 0,
            'members_with_loans': 0, 'new_members_month': 0, 'total_savings_all': 0,
            'this_month_savings': 0, 'total_late_fees': 0, 'avg_savings_per_member': 0,
            'active_loans_total': 0, 'total_disbursed': 0, 'total_repaid': 0,
            'total_interest': 0, 'active_loans_count': 0, 'completed_loans_count': 0,
            'pending_loans_count': 0, 'rejected_loans_count': 0, 'current_loans': 0,
            'days_30_loans': 0, 'days_60_loans': 0, 'days_90_loans': 0,
            'total_investments_value': 0, 'savings_months': [], 'monthly_savings_data': [],
            'join_months': [], 'new_members_data': [], 'investment_type_labels': [],
            'investment_type_data': [], 'dividend_amount': 0, 'reserve_amount': 0,
            'honorarium_amount': 0, 'other_appropriations': 0, 'top_savers': [],
            'delinquent_loans': [], 'active_savings': 0, 'inactive_savings': 0,
            'loan_member_savings': 0, 'total_income_year': 0, 'total_expenses_year': 0,
            'net_surplus_year': 0, 'member_dividends': [], 'suspended_members': 0,
        }
        return render_template('admin/reports.html', **zeros)


@reports.route('/reports/financial')
@login_required
def financial_report():
    db = get_db()
    from_date = request.args.get('from_date', (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d'))
    to_date = request.args.get('to_date', datetime.now().strftime('%Y-%m-%d'))

    try:
        total_savings = db.execute(
            'SELECT COALESCE(SUM(amount), 0) FROM savings WHERE date BETWEEN ? AND ?',
            (from_date, to_date)
        ).fetchone()[0]

        loan_interest = db.execute(
            "SELECT COALESCE(SUM(amount * ? / 100), 0) FROM loans "
            "WHERE status = 'active' AND date_applied BETWEEN ? AND ?",
            (11, from_date, to_date)
        ).fetchone()[0]

        late_fees = db.execute(
            'SELECT COALESCE(SUM(late_fee), 0) FROM savings WHERE date BETWEEN ? AND ?',
            (from_date, to_date)
        ).fetchone()[0]

        inv_total = db.execute(
            'SELECT COALESCE(SUM(amount), 0) FROM investments WHERE date BETWEEN ? AND ?',
            (from_date, to_date)
        ).fetchone()[0]

        honorarium = db.execute(
            'SELECT COALESCE(SUM(amount), 0) FROM honorarium WHERE date BETWEEN ? AND ?',
            (from_date, to_date)
        ).fetchone()[0]

        operating_expenses = db.execute(
            'SELECT COALESCE(SUM(amount), 0) FROM expenses WHERE date BETWEEN ? AND ?',
            (from_date, to_date)
        ).fetchone()[0]

        total_income = total_savings + loan_interest + late_fees
        total_expenses = inv_total + honorarium + operating_expenses
        net_surplus = total_income - total_expenses

        return render_template('admin/financial-report.html',
                               from_date=from_date,
                               to_date=to_date,
                               total_savings=total_savings,
                               loan_interest=loan_interest,
                               late_fees=late_fees,
                               total_income=total_income,
                               investments=inv_total,
                               honorarium=honorarium,
                               operating_expenses=operating_expenses,
                               total_expenses=total_expenses,
                               net_surplus=net_surplus)
    except Exception as e:
        flash(f'Error generating financial report: {str(e)}', 'danger')
        return redirect(url_for('reports.reports_list'))
