"""
loan_pdf.py — render a member's loan application as a self-contained PDF.

The PDF is what an exco member reads on their phone: everything needed to make
a decision without logging in — applicant, eligibility, the exact terms the
member accepted, the repayment schedule, guarantors, consents, due-diligence
state and the approval trail so far.

    from loan_pdf import build_loan_application_pdf
    pdf_bytes, filename = build_loan_application_pdf(db, loan_id)

Money is written as "NGN 1,234.56" rather than with the naira sign: the PDF
uses reportlab's built-in Helvetica, whose standard encoding has no glyph for
U+20A6, so the symbol would render as a blank box in the exco's mail client.
"""

from datetime import datetime

from utils import compute_loan_schedule, METHOD_LABELS, member_savings_balance
import loan_workflow as lw


# ── Formatting helpers ────────────────────────────────────────────────────────

def money(value) -> str:
    try:
        return f"NGN {float(value or 0):,.2f}"
    except (TypeError, ValueError):
        return 'NGN 0.00'


def _text(value, dash='—') -> str:
    value = '' if value is None else str(value).strip()
    return value or dash


def _stamp(value, dash='—') -> str:
    """Trim a timestamp to 'YYYY-MM-DD HH:MM' for display."""
    if not value:
        return dash
    return str(value)[:16]


def _yes_no(value) -> str:
    return 'Yes' if value in (1, '1', True, 'true', 'True') else 'No'


def _has(row, column) -> bool:
    try:
        return column in row.keys()
    except AttributeError:
        return column in row


def _get(row, column, default=None):
    return row[column] if _has(row, column) and row[column] is not None else default


def _setting(db, key, default=''):
    try:
        row = db.execute('SELECT value FROM settings WHERE key = ?', (key,)).fetchone()
        return (row['value'] or default) if row else default
    except Exception:
        return default


# ── Data gathering ────────────────────────────────────────────────────────────

def loan_application_context(db, loan_id):
    """Everything the PDF (and the alert email summary) needs, as plain dicts."""
    loan = db.execute('SELECT * FROM loans WHERE id = ?', (loan_id,)).fetchone()
    if not loan:
        return None
    member = db.execute('SELECT * FROM members WHERE id = ?', (loan['member_id'],)).fetchone()
    guarantors = db.execute('''
        SELECT lg.status, lg.requested_at, lg.responded_at,
               m.first_name, m.last_name, m.member_number, m.phone
        FROM loan_guarantors lg JOIN members m ON m.id = lg.member_id
        WHERE lg.loan_id = ? ORDER BY lg.id
    ''', (loan_id,)).fetchall()
    history = db.execute(
        'SELECT * FROM loan_approvals WHERE loan_id = ? ORDER BY id', (loan_id,)
    ).fetchall()

    try:
        savings = member_savings_balance(db, loan['member_id'])
    except Exception:
        savings = 0.0

    amount = float(loan['amount'] or 0)
    tenure = max(int(loan['tenure'] or 1), 1)
    rate = float(loan['interest_rate'] or 0)
    method = loan['interest_method'] or 'reducing_annual'
    monthly_payment, total_repayment, schedule = compute_loan_schedule(amount, rate, tenure, method)

    stage = loan['approval_stage'] or lw.STAGE_SECRETARY
    return {
        'loan': loan,
        'member': member,
        'guarantors': guarantors,
        'history': history,
        'savings_balance': savings,
        'monthly_payment': monthly_payment,
        'total_repayment': float(loan['total_repayment'] or total_repayment),
        'schedule': schedule,
        'stage': stage,
        'stage_label': lw.STAGE_LABELS.get(stage, stage),
        'coop_name': _setting(db, 'coop_name', 'Cooperative'),
        'applicant_type': _get(loan, 'loan_applicant_type', 'non_staff'),
    }


def application_filename(loan) -> str:
    """Safe, human-readable file name for the attachment."""
    ref = str(loan['loan_number'] or f"loan-{loan['id']}")
    safe = ''.join(ch if ch.isalnum() else '-' for ch in ref).strip('-')
    while '--' in safe:
        safe = safe.replace('--', '-')
    return f'loan-application-{safe}.pdf'


# ── PDF rendering ─────────────────────────────────────────────────────────────

def build_loan_application_pdf(db, loan_id):
    """Return (pdf_bytes, filename), or (None, '') if the loan does not exist
    or reportlab is unavailable. Never raises."""
    try:
        ctx = loan_application_context(db, loan_id)
        if not ctx:
            return None, ''
        return _render(ctx), application_filename(ctx['loan'])
    except Exception:
        import logging
        logging.getLogger(__name__).exception('Loan application PDF failed for loan %s', loan_id)
        return None, ''


def _render(ctx) -> bytes:
    import io
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer,
                                    Table, TableStyle, KeepTogether)

    loan = ctx['loan']
    member = ctx['member']
    navy = colors.HexColor('#082b66')
    grey = colors.HexColor('#6c757d')
    light = colors.HexColor('#f1f4f9')

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle('CoopTitle', parent=styles['Title'], fontSize=16,
                                 textColor=navy, spaceAfter=2, alignment=1)
    sub_style = ParagraphStyle('CoopSub', parent=styles['Normal'], fontSize=9,
                               textColor=grey, alignment=1, spaceAfter=10)
    head_style = ParagraphStyle('CoopHead', parent=styles['Heading3'], fontSize=11,
                                textColor=navy, spaceBefore=10, spaceAfter=4)
    body_style = ParagraphStyle('CoopBody', parent=styles['Normal'], fontSize=8.5, leading=11)
    note_style = ParagraphStyle('CoopNote', parent=styles['Normal'], fontSize=7.5,
                                textColor=grey, leading=10)

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4, title=f"Loan Application {loan['loan_number'] or loan['id']}",
        author=ctx['coop_name'], topMargin=15 * mm, bottomMargin=14 * mm,
        leftMargin=14 * mm, rightMargin=14 * mm,
    )

    def kv_table(rows):
        table = Table([[Paragraph(f'<b>{k}</b>', body_style), Paragraph(str(v), body_style)]
                       for k, v in rows], colWidths=[52 * mm, 130 * mm])
        table.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('BACKGROUND', (0, 0), (0, -1), light),
            ('GRID', (0, 0), (-1, -1), 0.4, colors.HexColor('#dee2e6')),
            ('LEFTPADDING', (0, 0), (-1, -1), 5),
            ('RIGHTPADDING', (0, 0), (-1, -1), 5),
            ('TOPPADDING', (0, 0), (-1, -1), 3),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ]))
        return table

    def grid_table(header, rows, col_widths, aligns=None):
        data = [[Paragraph(f'<b>{h}</b>', body_style) for h in header]]
        for row in rows:
            data.append([Paragraph(str(c), body_style) for c in row])
        table = Table(data, colWidths=col_widths, repeatRows=1)
        style = [
            ('BACKGROUND', (0, 0), (-1, 0), navy),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('GRID', (0, 0), (-1, -1), 0.4, colors.HexColor('#dee2e6')),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, light]),
            ('LEFTPADDING', (0, 0), (-1, -1), 4),
            ('RIGHTPADDING', (0, 0), (-1, -1), 4),
            ('TOPPADDING', (0, 0), (-1, -1), 2.5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 2.5),
        ]
        for col in (aligns or []):
            style.append(('ALIGN', (col, 0), (col, -1), 'RIGHT'))
        table.setStyle(TableStyle(style))
        return table

    story = [
        Paragraph(ctx['coop_name'], title_style),
        Paragraph('LOAN APPLICATION — FOR MANAGEMENT COMMITTEE REVIEW', sub_style),
    ]

    # ── Summary strip ────────────────────────────────────────────────────────
    summary = Table([[
        Paragraph(f"<b>Reference</b><br/>{_text(loan['loan_number'])}", body_style),
        Paragraph(f"<b>Amount requested</b><br/>{money(loan['amount'])}", body_style),
        Paragraph(f"<b>Submitted</b><br/>{_stamp(loan['date_applied'])}", body_style),
        Paragraph(f"<b>Current stage</b><br/>{ctx['stage_label']}", body_style),
    ]], colWidths=[45.5 * mm] * 4)
    summary.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), light),
        ('BOX', (0, 0), (-1, -1), 0.6, navy),
        ('INNERGRID', (0, 0), (-1, -1), 0.4, colors.HexColor('#dee2e6')),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
    ]))
    story += [summary, Spacer(1, 4)]

    # ── Applicant ────────────────────────────────────────────────────────────
    if member:
        full_name = f"{_get(member, 'first_name', '')} {_get(member, 'last_name', '')}".strip()
        applicant_rows = [
            ('Member name', _text(full_name)),
            ('Member number', _text(_get(member, 'member_number'))),
            ('Applicant type', 'Staff cooperator (payroll)' if ctx['applicant_type'] == 'staff'
                               else 'Non-staff cooperator'),
            ('Employee ID', _text(_get(member, 'employee_id'))),
            ('Phone', _text(_get(member, 'phone'))),
            ('Email', _text(_get(member, 'email'))),
            ('Date joined', _stamp(_get(member, 'date_joined'))),
            ('Savings balance (ledger)', money(ctx['savings_balance'])),
            ('Maximum eligible (2x savings)', money(ctx['savings_balance'] * 2)),
        ]
    else:
        applicant_rows = [('Member', 'Member record not found')]
    story += [Paragraph('1. Applicant', head_style), kv_table(applicant_rows)]

    # ── Requested facility ───────────────────────────────────────────────────
    method = loan['interest_method'] or 'reducing_annual'
    story += [
        Paragraph('2. Facility requested', head_style),
        kv_table([
            ('Amount requested', money(loan['amount'])),
            ('Purpose', _text(loan['purpose'])),
            ('Tenure', f"{_text(loan['tenure'], '0')} months"),
            ('Interest rate', f"{float(loan['interest_rate'] or 0):g}%"),
            ('Interest method', METHOD_LABELS.get(method, method)),
            ('Monthly repayment', money(ctx['monthly_payment'])),
            ('Total repayable', money(ctx['total_repayment'])),
            ('Repayment collateral', _text(_get(loan, 'payment_collateral_type', '')).replace('_', ' ').title()),
        ]),
    ]

    # ── Consents & declaration ───────────────────────────────────────────────
    consent_rows = [
        ('Terms & conditions accepted', _yes_no(_get(loan, 'terms_accepted'))),
        ('Data processing consent', _yes_no(_get(loan, 'data_processing_consent'))),
        ('Repayment schedule accepted', _yes_no(_get(loan, 'repayment_schedule_accepted'))),
    ]
    if ctx['applicant_type'] == 'staff':
        consent_rows.append(('HR/payroll affordability consent', _yes_no(_get(loan, 'hr_affordability_consent'))))
    else:
        consent_rows.append(('Credit/affordability check consent', _yes_no(_get(loan, 'credit_check_consent'))))
    consent_rows += [
        ('Signed by (typed signature)', _text(_get(loan, 'signature_name'))),
        ('Signed at', _stamp(_get(loan, 'signed_at'))),
        ('Submitted from IP', _text(_get(loan, 'consent_ip'))),
        ('Submission channel', _text(_get(loan, 'submission_channel'), 'not recorded')),
    ]
    story += [Paragraph('3. Declaration &amp; consents', head_style), kv_table(consent_rows)]

    # ── Due diligence ────────────────────────────────────────────────────────
    dd_rows = []
    if ctx['applicant_type'] == 'staff':
        dd_rows.append(['HR/payroll affordability', _text(_get(loan, 'hr_affordability_status', 'pending'))])
    else:
        dd_rows.append(['Bank statement', _text(_get(loan, 'bank_statement_status', 'requested'))])
        dd_rows.append(['Credit/affordability check', _text(_get(loan, 'credit_check_status', 'pending'))])
    dd_rows.append(['Repayment collateral', _text(_get(loan, 'payment_collateral_status', 'pending'))])
    story += [
        Paragraph('4. Pre-disbursement due diligence', head_style),
        grid_table(['Check', 'Status'], [[c, str(s).replace('_', ' ').title()] for c, s in dd_rows],
                   [120 * mm, 62 * mm]),
    ]

    # ── Guarantors ───────────────────────────────────────────────────────────
    if ctx['guarantors']:
        g_rows = [[
            f"{_text(g['first_name'], '')} {_text(g['last_name'], '')}".strip(),
            _text(g['member_number']),
            _text(g['phone']),
            str(g['status'] or 'pending').title(),
            _stamp(g['responded_at']),
        ] for g in ctx['guarantors']]
    else:
        g_rows = [['No guarantors recorded', '', '', '', '']]
    story += [
        Paragraph('5. Guarantors', head_style),
        grid_table(['Guarantor', 'Member no.', 'Phone', 'Consent', 'Responded'],
                   g_rows, [58 * mm, 32 * mm, 32 * mm, 28 * mm, 32 * mm]),
    ]

    # ── Repayment schedule ───────────────────────────────────────────────────
    schedule = ctx['schedule'] or []
    if schedule:
        s_rows = [[
            str(row['month']), money(row['payment']), money(row['principal']),
            money(row['interest']), money(row['balance']),
        ] for row in schedule]
        totals = [
            'Total',
            money(sum(r['payment'] for r in schedule)),
            money(sum(r['principal'] for r in schedule)),
            money(sum(r['interest'] for r in schedule)),
            '',
        ]
        s_rows.append(totals)
        story += [
            Paragraph('6. Repayment schedule accepted by the applicant', head_style),
            grid_table(['Month', 'Instalment', 'Principal', 'Interest', 'Balance'],
                       s_rows, [20 * mm, 42 * mm, 40 * mm, 40 * mm, 40 * mm],
                       aligns=[1, 2, 3, 4]),
        ]

    # ── Approval trail ───────────────────────────────────────────────────────
    if ctx['history']:
        h_rows = [[
            _stamp(h['acted_at']),
            str(h['stage'] or '').title(),
            str(h['action'] or '').title(),
            _text(h['acted_by_name']),
            _text(h['comment'], ''),
        ] for h in ctx['history']]
        story += [
            Paragraph('7. Approval trail to date', head_style),
            grid_table(['When', 'Stage', 'Action', 'By', 'Comment'],
                       h_rows, [30 * mm, 28 * mm, 24 * mm, 32 * mm, 68 * mm]),
        ]

    story += [
        Spacer(1, 8),
        KeepTogether([Paragraph(
            f"Generated automatically by the cooperative management system on "
            f"{datetime.now().strftime('%Y-%m-%d %H:%M')}. This document reflects the "
            f"application exactly as the member submitted it and is provided to the "
            f"management committee for review. It is not an approval or an offer of credit.",
            note_style)]),
    ]

    doc.build(story)
    return buf.getvalue()
