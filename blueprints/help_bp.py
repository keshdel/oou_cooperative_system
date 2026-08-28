"""
help_bp.py — Contextual help system and knowledge base.

Two surfaces:
  1. GET /help                  — full searchable knowledge base
  2. GET /help/article/<slug>   — individual article (full page)
  3. GET /help/api/panel        — JSON for the floating help panel
                                  (returns article matching ?endpoint=)
"""
from flask import Blueprint, render_template, request, jsonify
from flask_login import login_required

help_bp = Blueprint('help_bp', __name__, url_prefix='/help')

# ══════════════════════════════════════════════════════════════════════════════
# KNOWLEDGE BASE CONTENT
# Each article:
#   slug        – URL-safe identifier
#   title       – article heading
#   category    – used for grouping (icon is set in CATEGORIES below)
#   summary     – one-sentence blurb shown on the KB index card
#   endpoints   – list of Flask endpoint strings this article is shown for
#   body        – list of sections; each section is:
#                   {'head': str, 'text': str}  or
#                   {'head': str, 'steps': [str, ...]}  or
#                   {'head': str, 'tips': [str, ...]}
# ══════════════════════════════════════════════════════════════════════════════

ARTICLES = [

    # ── Dashboard ────────────────────────────────────────────────────────────
    {
        'slug':      'dashboard',
        'title':     'Dashboard Overview',
        'category':  'Getting Started',
        'summary':   'Understand the key metrics and quick actions on your main dashboard.',
        'endpoints': ['main.dashboard'],
        'body': [
            {
                'head':  'What you see here',
                'text':  'The dashboard gives a real-time snapshot of your cooperative\'s financial health: total members, cumulative savings, active loan book, and investment portfolio - all updated live from the database.',
            },
            {
                'head':  'Summary cards',
                'steps': [
                    '<b>Total Members</b> — all registered members regardless of status.',
                    '<b>Total Savings</b> — sum of every savings deposit ever recorded.',
                    '<b>Active Loans</b> — outstanding principal on loans currently marked <em>active</em>.',
                    '<b>Investments</b> — total capital deployed in investments.',
                ],
            },
            {
                'head':  'Recent activity tables',
                'text':  'The two tables at the bottom show the 5 most recent savings payments and loan applications so you can act on them without navigating away.',
            },
            {
                'head':  'Tips',
                'tips':  [
                    'Use the sidebar links to drill into any section.',
                    'Figures update every time the page loads — no manual refresh needed.',
                ],
            },
        ],
    },

    # ── Members list ─────────────────────────────────────────────────────────
    {
        'slug':      'members-list',
        'title':     'Managing the Members List',
        'category':  'Members',
        'summary':   'Search, filter, add, and export your full membership register.',
        'endpoints': ['members.members_list'],
        'body': [
            {
                'head':  'Finding a member',
                'steps': [
                    'Use the search bar at the top to filter by name, number, phone, or email.',
                    'Click any row to open the full member profile.',
                ],
            },
            {
                'head':  'Adding a new member',
                'steps': [
                    'Click <b>Add Member</b> (top-right).',
                    'Fill in First Name, Last Name, and Phone (required).',
                    'Enter optional fields: email, address, occupation, date of birth, monthly savings target.',
                    'Click <b>Save Member</b>. A member number is auto-assigned (e.g. MEM/2025/0042).',
                ],
            },
            {
                'head':  'Bulk import',
                'text':  'To import many members at once go to <b>Data Migration → Import Members</b>. Download the template from that page, fill it in, and upload.',
            },
            {
                'head':  'Exporting',
                'text':  'Click <b>Export CSV</b> to download the full membership register for offline use or archiving.',
            },
            {
                'head':  'Tips',
                'tips':  [
                    'A member with an email address automatically gets a portal login account created during import.',
                    'You cannot delete a member who has savings or loan records — mark them Inactive instead.',
                ],
            },
        ],
    },

    # ── Member detail ─────────────────────────────────────────────────────────
    {
        'slug':      'member-details',
        'title':     'Member Profile & ID Card',
        'category':  'Members',
        'summary':   'View savings history, loans, and generate a printed ID card for any member.',
        'endpoints': ['members.member_details'],
        'body': [
            {
                'head':  'Profile sections',
                'steps': [
                    '<b>Summary cards</b> — total savings and active loan balance at a glance.',
                    '<b>Savings tab</b> — full payment history with amounts and receipts.',
                    '<b>Loans tab</b> — all loan applications, their status, and outstanding balance.',
                ],
            },
            {
                'head':  'Recording a savings payment',
                'steps': [
                    'Click <b>Record Savings</b>.',
                    'Enter the amount, month (YYYY-MM), and payment method.',
                    'Payments made after the 10th of the month automatically attract a 10 % late fee.',
                    'Click <b>Save</b> — a receipt number is generated and the member\'s total updates instantly.',
                ],
            },
            {
                'head':  'Printing a member ID card',
                'steps': [
                    'Click <b>Print Card</b> (top-right) — opens in a new tab.',
                    'The card shows the member\'s photo, member number, status, and a QR code.',
                    'Click <b>Print Card</b> on that page, select your printer, and choose paper size CR80 (85.6 × 54 mm) for a standard ID-card print.',
                ],
            },
            {
                'head':  'Tips',
                'tips':  [
                    'Upload a passport photo when editing the member to make the ID card look professional.',
                    'The QR code on the card encodes the member number — any QR scanner will read it.',
                ],
            },
        ],
    },

    # ── Savings list ──────────────────────────────────────────────────────────
    {
        'slug':      'savings',
        'title':     'Savings Management',
        'category':  'Savings',
        'summary':   'View, record, and report on all member savings contributions.',
        'endpoints': ['savings.savings_list'],
        'body': [
            {
                'head':  'Savings list',
                'text':  'Shows every savings payment across all members, newest first. The header shows the grand total of all savings ever recorded.',
            },
            {
                'head':  'Recording a payment',
                'steps': [
                    'Navigate to a member\'s profile page.',
                    'Click <b>Record Savings</b> and fill in the amount, month, and method.',
                    'Alternatively, use <b>Data Migration → Import Savings</b> to bulk-upload a CSV.',
                ],
            },
            {
                'head':  'Late fees',
                'text':  'A 10 % late fee is automatically calculated for monthly/salary savings recorded after the 10th of the month. The fee is stored separately so reports can distinguish gross savings from penalties.',
            },
            {
                'head':  'Tips',
                'tips':  [
                    'Members can see their own savings history and running balance in the Member Portal.',
                    'Each payment generates a unique receipt number (RCPT/YYYYMMDD/XXXX).',
                ],
            },
        ],
    },

    # ── Loans list ────────────────────────────────────────────────────────────
    {
        'slug':      'loans',
        'title':     'Loan Management',
        'category':  'Loans',
        'summary':   'Process applications, approve or reject loans, and track repayments.',
        'endpoints': ['loans.loans_list'],
        'body': [
            {
                'head':  'Loan statuses',
                'steps': [
                    '<b>Pending</b> — submitted, awaiting committee decision.',
                    '<b>Approved</b> — approved but not yet disbursed.',
                    '<b>Active</b> — disbursed and being repaid.',
                    '<b>Completed</b> — fully repaid.',
                    '<b>Rejected</b> — declined by the committee.',
                    '<b>Defaulted</b> — overdue and not recovering.',
                ],
            },
            {
                'head':  'Approving a loan',
                'steps': [
                    'Find the Pending loan in the list and click it to open details.',
                    'Click <b>Approve</b>. Insurance (1 %) and application fee (1 %) are deducted automatically; the net disbursement amount is calculated.',
                    'The member\'s first repayment date is set 30 days from today.',
                    'An email notification is sent to the member (if email is configured).',
                ],
            },
            {
                'head':  'Rejecting a loan',
                'steps': [
                    'Click <b>Reject</b> and provide a reason.',
                    'The reason is sent to the member by email and visible in their portal.',
                ],
            },
            {
                'head':  'Overdue loans',
                'text':  'Any active loan whose disbursement date + tenure months is in the past is flagged as <span style="color:#dc2626;font-weight:600">Overdue</span> in the list.',
            },
            {
                'head':  'Tips',
                'tips':  [
                    'Members must have been registered for at least 6 months and have ₦50,000+ in savings to qualify.',
                    'Maximum loan = 2 × total savings.',
                    'Only one active loan is allowed per member at a time.',
                ],
            },
        ],
    },

    # ── Apply loan (admin) ────────────────────────────────────────────────────
    {
        'slug':      'apply-loan',
        'title':     'Applying for a Loan (Staff)',
        'category':  'Loans',
        'summary':   'Submit a loan application on behalf of a member from the admin side.',
        'endpoints': ['loans.apply_loan'],
        'body': [
            {
                'head':  'Steps',
                'steps': [
                    'Select the member from the dropdown.',
                    'Choose a loan purpose — this sets the interest rate automatically (Regular 11 %, Housing 9 %, Emergency 10 %, Asset Purchase 10 %, School Fees 9 %).',
                    'Enter the loan amount and tenure (months).',
                    'The monthly repayment and total repayment are calculated live.',
                    'Click <b>Submit Application</b>. Status is set to Pending.',
                ],
            },
            {
                'head':  'Eligibility checks (automatic)',
                'steps': [
                    'Member must have been registered ≥ 6 months.',
                    'Total savings must be ≥ ₦50,000.',
                    'No existing active loan.',
                    'Amount must not exceed 2 × total savings.',
                ],
            },
            {
                'head':  'Tips',
                'tips':  [
                    'Members can also apply through the Member Portal — they see the same eligibility rules.',
                    'The interest method (flat or reducing balance) is set per loan purpose in Settings → Loans.',
                ],
            },
        ],
    },

    # ── Investments ───────────────────────────────────────────────────────────
    {
        'slug':      'investments',
        'title':     'Investments',
        'category':  'Investments',
        'summary':   'Track fixed deposits, government bonds, and other capital placements.',
        'endpoints': ['investments.investments_list'],
        'body': [
            {
                'head':  'Adding an investment',
                'steps': [
                    'Click <b>Add Investment</b>.',
                    'Enter the name, type (Fixed Deposit, Bond, etc.), institution, amount, interest rate, start and maturity dates.',
                    'Investments are approved immediately and appear in the portfolio total.',
                ],
            },
            {
                'head':  'Risk levels',
                'text':  'Set risk level as Low, Medium, or High. This appears in reports but does not affect calculations.',
            },
            {
                'head':  'Tips',
                'tips':  [
                    'Only Admins and Treasurers can add or edit investments.',
                    'Use Data Migration → Import Investments to bulk-load historical records.',
                ],
            },
        ],
    },

    # ── Reports ───────────────────────────────────────────────────────────────
    {
        'slug':      'reports',
        'title':     'Reports & Financial Summaries',
        'category':  'Reports',
        'summary':   'Generate period financial reports, export to PDF or CSV.',
        'endpoints': ['reports.reports_list'],
        'body': [
            {
                'head':  'Available reports',
                'steps': [
                    '<b>Financial Summary</b> — income, expenses, and net position for a date range.',
                    '<b>Savings Report</b> — total savings, late fees, and per-member breakdown.',
                    '<b>Loans Report</b> — disbursements, repayments, and outstanding balances.',
                    '<b>Membership Report</b> — member growth and status distribution.',
                    '<b>Investment Report</b> — portfolio value and expected returns.',
                ],
            },
            {
                'head':  'Selecting a date range',
                'text':  'Use the <b>From</b> and <b>To</b> date pickers at the top of the page. Click <b>Generate Report</b> to refresh the data.',
            },
            {
                'head':  'Printing / exporting',
                'text':  'Click <b>Print / Save PDF</b> to open the browser print dialog. The print stylesheet hides navigation and formats the report on A4 paper with a letterhead.',
            },
            {
                'head':  'Tips',
                'tips':  [
                    'Accessible to Admin, Treasurer, Secretary, and Exco roles.',
                    'For member-level statements, go to the member\'s profile → Statement tab.',
                ],
            },
        ],
    },

    # ── Settings ──────────────────────────────────────────────────────────────
    {
        'slug':      'settings',
        'title':     'System Settings',
        'category':  'Administration',
        'summary':   'Configure cooperative identity, savings rules, loan policies, payment gateways, and users.',
        'endpoints': ['admin_panel.settings'],
        'body': [
            {
                'head':  'General tab',
                'steps': [
                    '<b>Cooperative Identity</b> — name, short name, registration number, logo upload, address.',
                    '<b>Member Support Contact</b> — WhatsApp number, support phone/email, and office address shown on the Member Portal Support page.',
                ],
            },
            {
                'head':  'Savings tab',
                'text':  'Set minimum monthly savings, due day, late-fee percentage, deposit rates, and dividend rate.',
            },
            {
                'head':  'Loans tab',
                'text':  'Configure minimum membership months, minimum savings required, loan multiplier, maximum tenure, interest rates per purpose, insurance rate, and guarantor requirements.',
            },
            {
                'head':  'Payments tab',
                'text':  'Enter your Paystack Public and Secret keys to enable online savings collection and subscription renewal. The webhook URL shown here must be registered in your Paystack dashboard.',
            },
            {
                'head':  'Users tab',
                'steps': [
                    'Add new staff accounts with a role (Admin, Treasurer, Secretary, Exco).',
                    'Edit existing users\' name, email, or role.',
                    'Reset a user\'s password or enable/disable their account.',
                    'Use the search bar to find a specific user quickly.',
                ],
            },
            {
                'head':  'Uploading the logo',
                'steps': [
                    'Go to <b>General → Cooperative Identity</b>.',
                    'Click <b>Choose File</b> under Cooperative Logo.',
                    'Select a PNG, JPG, or WebP image (max 2 MB).',
                    'A live preview appears below the picker.',
                    'Click <b>Save General Settings</b>.',
                ],
            },
            {
                'head':  'Tips',
                'tips':  [
                    'Only the Admin role can access Settings.',
                    'Changes take effect immediately — no restart needed.',
                ],
            },
        ],
    },

    # ── Data migration ────────────────────────────────────────────────────────
    {
        'slug':      'data-migration',
        'title':     'Data Migration (Bulk Import)',
        'category':  'Administration',
        'summary':   'Import historical records from spreadsheets in 5 ordered steps.',
        'endpoints': ['migration.index', 'migration.import_members', 'migration.import_savings',
                      'migration.import_loans', 'migration.import_repayments'],
        'body': [
            {
                'head':  'Import order (important)',
                'steps': [
                    '1. <b>Members</b> — must be first; savings and loans reference member records.',
                    '2. <b>Savings</b> — historical deposits per member.',
                    '3. <b>Loans</b> — loan records, linked to members.',
                    '4. <b>Repayments</b> — loan repayment history, linked to loans.',
                    '5. <b>Expenses / Revenue / Investments</b> — independent, any order.',
                ],
            },
            {
                'head':  'How to import',
                'steps': [
                    'Click any import card (e.g. <b>Import Members</b>).',
                    'Click <b>Download Template</b> — this gives you the exact column headers required.',
                    'Fill in your data (do not change column names).',
                    'Upload the completed CSV file and click <b>Import</b>.',
                    'A summary shows how many records were imported, skipped (duplicates), or errored.',
                ],
            },
            {
                'head':  'Member accounts',
                'text':  'For every imported member who has an email address, a portal login is automatically created. Temporary passwords are displayed once after import — share them with members so they can log in and change them.',
            },
            {
                'head':  'Tips',
                'tips':  [
                    'All imports are atomic — if the file has a critical error the whole batch rolls back.',
                    'Duplicate member numbers and emails are silently skipped, not errored.',
                    'Purpose names and loan statuses are normalised automatically (e.g. "Education" → "School Fees").',
                ],
            },
        ],
    },

    # ── Subscription ──────────────────────────────────────────────────────────
    {
        'slug':      'subscription',
        'title':     'Subscription & Billing',
        'category':  'Administration',
        'summary':   'Understand per-member pricing and renew your annual subscription online.',
        'endpoints': ['admin_panel.subscription_page'],
        'body': [
            {
                'head':  'How pricing works',
                'text':  'The annual fee is calculated as: <b>active members × per-member fee</b>. The per-member fee is set in Billing Settings below. This means the cost scales with your cooperative\'s size.',
            },
            {
                'head':  'Renewing online',
                'steps': [
                    'Make sure a <b>Billing Contact Email</b> is set in Billing Settings.',
                    'Click <b>Pay Now</b> — a Paystack payment popup opens.',
                    'Complete payment with a card or bank transfer.',
                    'Your expiry date is automatically extended by 365 days.',
                    'A receipt is sent to the billing email.',
                ],
            },
            {
                'head':  'Manual date override',
                'text':  'Admin can manually enter an expiry date in the <b>Current Expiry Date</b> field and click Save — useful when a payment is made offline.',
            },
            {
                'head':  'What happens when it expires',
                'text':  'Members cannot log in to the portal. Staff with the Admin or Treasurer role can still reach the Subscription page to renew.',
            },
            {
                'head':  'Tips',
                'tips':  [
                    'The system sends a warning when fewer than 30 days remain.',
                    'Paystack keys are configured in Settings → Payments.',
                ],
            },
        ],
    },

    # ── Member portal ─────────────────────────────────────────────────────────
    {
        'slug':      'member-portal',
        'title':     'Member Portal — My Dashboard',
        'category':  'Member Portal',
        'summary':   'Overview of what members see when they log in to their self-service portal.',
        'endpoints': ['portal.member_portal'],
        'body': [
            {
                'head':  'What members can do',
                'steps': [
                    '<b>My Dashboard</b> — savings balance, active loan, recent transactions.',
                    '<b>My Savings</b> — full savings history, running balance, date filter, annual summary.',
                    '<b>My Loans</b> — all loans, status, repayment schedule.',
                    '<b>Transactions</b> — combined chronological list of savings and repayments.',
                    '<b>Statement</b> — accountant-grade statement with opening/closing balances.',
                    '<b>Apply for Loan</b> — submit a new loan application.',
                    '<b>Support</b> — contact the cooperative by WhatsApp, phone, or email.',
                ],
            },
            {
                'head':  'Changing password',
                'steps': [
                    'Click the username menu (top-right) → <b>Change Password</b>.',
                    'Enter current password and new password twice.',
                    'Click <b>Update Password</b>.',
                ],
            },
            {
                'head':  'Tips',
                'tips':  [
                    'Members only see their own data — they cannot access other members\' records.',
                    'Notifications (bell icon, top-right) show payment confirmations and loan status updates.',
                ],
            },
        ],
    },

    # ── My Savings (member) ───────────────────────────────────────────────────
    {
        'slug':      'my-savings',
        'title':     'My Savings',
        'category':  'Member Portal',
        'summary':   'View your full savings history, running balance, and annual summary.',
        'endpoints': ['portal.my_savings'],
        'body': [
            {
                'head':  'Reading the table',
                'steps': [
                    '<b>Date</b> — when the payment was recorded.',
                    '<b>Month</b> — the savings period this payment covers.',
                    '<b>Amount</b> — gross payment (includes any late fee).',
                    '<b>Late Fee</b> — penalty applied for payments after the 10th.',
                    '<b>Running Balance</b> — your cumulative savings up to that row.',
                ],
            },
            {
                'head':  'Filtering',
                'text':  'Use the <b>From</b> and <b>To</b> date pickers to narrow the view. The running balance always starts from your account open date so the numbers always reconcile.',
            },
            {
                'head':  'Annual summary',
                'text':  'The table at the bottom of the page shows gross savings, total late fees, and net savings per calendar year.',
            },
            {
                'head':  'Your personal account number',
                'text':  'If your cooperative has switched this on, your own account number is shown at the top of this page. Transfer to it from any bank app and the money is credited to you automatically — you do not need to quote a reference or send anyone proof. Save it in your bank app as a beneficiary and you can pay whenever you like.',
            },
            {
                'head':  'Choosing what your transfers pay for',
                'steps': [
                    '<b>My savings</b> — everything goes into your savings.',
                    '<b>My loan repayment, then savings</b> — clears what you owe first; anything left over is saved.',
                    '<b>My Target Advance contribution, then savings</b> — shown only if you are on a Target Advance cycle. Pays your due contributions oldest first; anything left over is saved.',
                    '<b>Let the cooperative decide</b> — follows whatever rule your cooperative has set.',
                ],
            },
            {
                'head':  'About that choice',
                'tips':  [
                    'You only set it once. Every transfer after that follows it until you change it.',
                    'Your choice overrides the cooperative\'s rule, because you know what you are paying for.',
                    'Money left over never gets stuck — it always ends up in your savings.',
                    'If your choice stops applying, for example your Target Advance cycle ends, transfers go to savings instead.',
                ],
            },
            {
                'head':  'Tips',
                'tips':  [
                    'Click <b>Print Statement</b> to get a print-ready version for personal records.',
                    'Contact your cooperative\'s secretary if you spot a discrepancy.',
                ],
            },
        ],
    },

    # ── My Loans (member) ─────────────────────────────────────────────────────
    {
        'slug':      'my-loans',
        'title':     'My Loans',
        'category':  'Member Portal',
        'summary':   'Track your loan applications, repayment progress, and outstanding balance.',
        'endpoints': ['portal.my_loans'],
        'body': [
            {
                'head':  'Loan statuses explained',
                'steps': [
                    '<b>Pending</b> — submitted, committee has not yet decided.',
                    '<b>Approved</b> — approved and awaiting disbursement.',
                    '<b>Active</b> — disbursed; repayments are due monthly.',
                    '<b>Completed</b> — fully repaid.',
                    '<b>Rejected</b> — not approved; reason is shown.',
                ],
            },
            {
                'head':  'Repayment progress bar',
                'text':  'Each active loan shows a progress bar: payments made out of total instalments, and the percentage of principal repaid.',
            },
            {
                'head':  'Tips',
                'tips':  [
                    'You cannot apply for a new loan while one is Active.',
                    'Contact the treasury if a repayment you made is not reflected here.',
                ],
            },
        ],
    },

    # ── Apply loan (member) ───────────────────────────────────────────────────
    {
        'slug':      'apply-loan-member',
        'title':     'Applying for a Loan',
        'category':  'Member Portal',
        'summary':   'Submit a loan application directly from your member portal.',
        'endpoints': ['portal.apply_loan_member'],
        'body': [
            {
                'head':  'Before you apply — eligibility',
                'steps': [
                    'You must have been a member for at least <b>6 months</b>.',
                    'Your total savings must be at least <b>₦50,000</b>.',
                    'You must have <b>no existing active loan</b>.',
                    'The amount you request cannot exceed <b>2 × your total savings</b>.',
                ],
            },
            {
                'head':  'Filling the form',
                'steps': [
                    'Choose a <b>Loan Purpose</b> — this sets your interest rate automatically.',
                    'Enter the <b>Amount</b> and <b>Tenure</b> (repayment months).',
                    'The estimated monthly repayment is calculated live as you type.',
                    'Click <b>Submit Application</b>.',
                ],
            },
            {
                'head':  'What happens next',
                'text':  'Your application status appears as Pending in My Loans. The committee will review and you will receive a notification when a decision is made.',
            },
            {
                'head':  'Interest rates by purpose',
                'steps': [
                    'Regular — 11 %',
                    'Housing — 9 %',
                    'Emergency — 10 %',
                    'Asset Purchase — 10 %',
                    'School Fees — 9 %',
                ],
            },
        ],
    },

    # ── Statement (member) ────────────────────────────────────────────────────
    {
        'slug':      'statements',
        'title':     'Account Statement',
        'category':  'Member Portal',
        'summary':   'Generate a certified account statement showing all savings and loan transactions.',
        'endpoints': ['portal.statements'],
        'body': [
            {
                'head':  'What the statement shows',
                'steps': [
                    '<b>Opening balance</b> — your savings and loan position at the start of the selected period.',
                    '<b>Savings deposits</b> — credited to your savings balance.',
                    '<b>Loan disbursements</b> — debited (money you received).',
                    '<b>Loan repayments</b> — credited (reduces your loan balance).',
                    '<b>Running balances</b> — savings, loan, and net position after every transaction.',
                    '<b>Closing balance</b> — your final position at the end of the period.',
                ],
            },
            {
                'head':  'Printing a certified statement',
                'steps': [
                    'Select your date range and click <b>Generate</b>.',
                    'Click <b>Print Statement</b>.',
                    'The printed copy includes a certification block with space for member and authorised officer signatures.',
                ],
            },
            {
                'head':  'Tips',
                'tips':  [
                    'Use <b>All Time</b> shortcut for a complete account history.',
                    'The Net Position column = Savings Balance − Loan Balance at each row.',
                ],
            },
        ],
    },

    # ── Support (member) ──────────────────────────────────────────────────────
    {
        'slug':      'support',
        'title':     'Support & Contact',
        'category':  'Member Portal',
        'summary':   'Contact your cooperative by WhatsApp, phone, email, or submit a query.',
        'endpoints': ['portal.support'],
        'body': [
            {
                'head':  'Contact options',
                'steps': [
                    '<b>WhatsApp</b> — click <em>Chat Now</em> to open a direct WhatsApp conversation.',
                    '<b>Phone</b> — displayed support number for direct calls.',
                    '<b>Email</b> — click to open your email client pre-addressed.',
                    '<b>Office Address</b> — physical location for in-person visits.',
                ],
            },
            {
                'head':  'Submitting a query',
                'text':  'Fill in the subject and message fields and click <b>Send Message</b>. A copy is sent to the cooperative\'s support email.',
            },
            {
                'head':  'Tips',
                'tips':  [
                    'All contact details are set by the administrator in Settings → General → Member Support Contact.',
                    'For urgent matters use WhatsApp — it is typically the fastest channel.',
                ],
            },
        ],
    },

    # ── Notifications ─────────────────────────────────────────────────────────
    {
        'slug':      'notifications',
        'title':     'Notifications',
        'category':  'Member Portal',
        'summary':   'Stay updated on savings confirmations, loan decisions, and system alerts.',
        'endpoints': ['portal.notifications'],
        'body': [
            {
                'head':  'Reading notifications',
                'text':  'Unread notifications show a red badge on the bell icon (top navigation bar). Click the bell or go to Notifications to read them.',
            },
            {
                'head':  'Notification types',
                'steps': [
                    '<b>Info (blue)</b> — savings payment confirmed, receipt number.',
                    '<b>Success (green)</b> — loan approved, welcome messages.',
                    '<b>Warning (yellow)</b> — loan decision updates, account notices.',
                    '<b>Danger (red)</b> — urgent alerts.',
                ],
            },
            {
                'head':  'Tips',
                'tips':  [
                    'Click any notification to mark it as read.',
                    'You can also access Notifications from the username dropdown (top-right).',
                ],
            },
        ],
    },

    {
        'slug':      'profile-readiness-and-setup-links',
        'title':     'Profile Readiness & Member Setup Links',
        'category':  'Administration',
        'summary':   'Track member readiness, send setup links, and help members complete their self-service profile.',
        'endpoints': ['admin_panel.settings', 'members.members_list', 'portal.profile', 'portal.edit_profile'],
        'body': [
            {
                'head':  'What readiness means',
                'steps': [
                    '<b>Profile completion</b> measures required personal, contact, bank, emergency, and nominee fields.',
                    '<b>Certified member</b> status is reached when the profile is 100 percent complete.',
                    '<b>Readiness to transact</b> helps admin see who can safely use portal services, statements, savings changes, and loan workflows.',
                ],
            },
            {
                'head':  'Sending setup links',
                'steps': [
                    'Go to <b>Settings - Users</b> or the member/user management area.',
                    'Use <b>Resend Setup Link</b> for one member, or <b>Bulk Send Setup Links</b> for all users who have not completed setup.',
                    'The member receives an email link to configure their password and profile.',
                    'Use revoke when an old setup link should no longer be valid.',
                ],
            },
            {
                'head':  'Admin and member view switching',
                'text':  'A staff user who also has a member profile can switch into the member portal to confirm the member experience, then return to the admin area from the account menu.',
            },
            {
                'head':  'Audit note',
                'tips':  [
                    'Keep email addresses clean before sending setup links.',
                    'Use Communications to remind members with incomplete profiles.',
                    'Do not share setup links publicly; each link is for the intended member account.',
                ],
            },
        ],
    },

    {
        'slug':      'chart-of-accounts-and-bank-accounts',
        'title':     'Chart of Accounts, Bank Accounts & Reconciliation',
        'category':  'Accounting',
        'summary':   'Create GL detail accounts, track bank positions, and reconcile savings posted to the right bank account.',
        'endpoints': ['accounting.chart_of_accounts', 'accounting.bank_accounts',
                      'accounting.bank_account_detail', 'accounting.reconciliation'],
        'body': [
            {
                'head':  'Chart of accounts',
                'steps': [
                    'Use <b>Accounting - Chart of Accounts</b> to review all GL accounts.',
                    'Create detail bank accounts under Cash and Bank, such as Zenith Bank, Access Bank, or petty cash.',
                    'Set the default cash/bank account used when new savings deposits are posted.',
                    'Inactive accounts are hidden from normal posting but retained for audit history.',
                ],
            },
            {
                'head':  'Bank account position',
                'steps': [
                    'Open <b>Accounting - Bank Accounts</b> to see each bank account balance by period.',
                    'Click a bank account to view opening balance, cash in, cash out, closing balance, and linked journal lines.',
                    'Use the period filter before reconciling against the bank statement.',
                ],
            },
            {
                'head':  'Savings bank reclassification',
                'text':  'If historical savings were posted to the general Cash and Bank GL instead of a detail bank account, use the savings reclassification tool to move those journal lines to the correct bank account without changing member savings balances.',
            },
            {
                'head':  'Control checks',
                'tips':  [
                    'Cash and Bank total should equal the sum of detail bank accounts.',
                    'Member Deposits should agree with member savings control reports.',
                    'Do not delete accounts with transactions; deactivate instead.',
                ],
            },
        ],
    },

    {
        'slug':      'journals-ledger-and-voids',
        'title':     'Journals, Ledger Drill-Down & Voids',
        'category':  'Accounting',
        'summary':   'Post manual journals, review debit/credit sides, drill into GL lines, and reverse entries safely.',
        'endpoints': ['accounting.new_journal', 'accounting.journal_register',
                      'accounting.journal_entry_view', 'accounting.journal_entry_quick_view',
                      'accounting.account_ledger_view', 'accounting.trial_balance_view',
                      'accounting.period_close'],
        'body': [
            {
                'head':  'Manual journal entry',
                'steps': [
                    'Go to <b>Accounting - New Journal</b>.',
                    'Enter the description, date, reference, and at least two lines.',
                    'Debit lines increase assets/expenses and reduce liabilities/equity/income.',
                    'Credit lines reduce assets/expenses and increase liabilities/equity/income.',
                    'The journal must balance before posting.',
                ],
            },
            {
                'head':  'Ledger drill-down',
                'steps': [
                    'Use the GL register or an account ledger to review journal lines by period.',
                    'Open a journal to see the full debit and credit split.',
                    'Use quick-view drawers where available for faster review without losing your place.',
                    'Export ledger lines for spreadsheet analysis and audit sampling.',
                ],
            },
            {
                'head':  'Voids and reversals',
                'text':  'Transactions are corrected by reversal entries, not hard deletion. For savings deposits and loan repayments, the reversal also updates the related subledger so member balances and GL remain aligned.',
            },
            {
                'head':  'Period close',
                'tips':  [
                    'Set a lock date after monthly review to prevent backdated posting into closed periods.',
                    'Use reversal entries for corrections after a period has already been reviewed.',
                    'Every journal keeps source module, reference, and audit metadata where available.',
                ],
            },
        ],
    },

    {
        'slug':      'dividends-and-patronage',
        'title':     'Dividends & Patronage',
        'category':  'Accounting',
        'summary':   'Compute surplus appropriation, preview member allocations, declare dividends, and export schedules.',
        'endpoints': ['accounting.dividends', 'accounting.declare_dividend',
                      'accounting.dividend_detail'],
        'body': [
            {
                'head':  'What the dividend page does',
                'steps': [
                    '<b>Preview</b> calculates the period net surplus and proposed appropriation.',
                    '<b>Surplus appropriation</b> splits net surplus into statutory reserve, honorarium, other allocation, and dividend pool.',
                    '<b>Member schedule</b> shows each member allocation before posting.',
                    '<b>Declared dividends</b> keeps a permanent history of posted dividend declarations.',
                ],
            },
            {
                'head':  'Computing a dividend',
                'steps': [
                    'Select the <b>From</b> and <b>To</b> dates for the surplus period.',
                    'Enter the percentage split for dividend, reserve, honorarium, and other allocation.',
                    'Set <b>Patronage split %</b> if part of the dividend pool should be distributed by loan-interest patronage.',
                    'Click <b>Compute</b> and review the surplus appropriation and member schedule.',
                ],
            },
            {
                'head':  'Declaring dividends',
                'steps': [
                    'Only an Admin can declare the computed dividend.',
                    'Review the member schedule carefully before clicking <b>Declare & credit members</b>.',
                    'Declaration credits member savings and posts to the ledger.',
                    'A declared dividend is part of the audit record and should not be treated like a draft preview.',
                ],
            },
            {
                'head':  'Review and export',
                'steps': [
                    'Open a declared dividend from the history table.',
                    'Review appropriation totals and member-level allocations.',
                    'Use PDF or Excel export for committee minutes, audit files, and member communication.',
                ],
            },
            {
                'head':  'Controls',
                'tips':  [
                    'Declare dividends only after financial statements and trial balance have been reviewed.',
                    'Confirm net surplus is positive before distribution.',
                    'Keep board/committee approval outside the system before posting the declaration.',
                    'If a declaration was wrong, correct it through controlled accounting adjustment rather than editing history.',
                ],
            },
        ],
    },

    {
        'slug':      'financial-reporting-center',
        'title':     'Financial Reporting Center',
        'category':  'Reports',
        'summary':   'Use financial statements, trial balance, cashbook, savings control, loan aging, and exports.',
        'endpoints': ['reports.reports_list', 'reports.financial_report', 'reports.cashbook_report',
                      'reports.member_savings_control', 'reports.loan_portfolio_report'],
        'body': [
            {
                'head':  'Core statements',
                'steps': [
                    '<b>Financial Statements</b> include income statement, balance sheet, cash flow, and surplus appropriation.',
                    '<b>Trial Balance</b> shows debit and credit balances for every chart-of-account line.',
                    '<b>General Ledger Register</b> exports journal lines for external analysis and audit working papers.',
                ],
            },
            {
                'head':  'Control reports',
                'steps': [
                    '<b>Cashbook</b> shows bank/cash movements with running balance.',
                    '<b>Member Savings Control</b> reconciles member-level savings to the Member Deposits control account.',
                    '<b>Loan Portfolio and Aging</b> shows outstanding loan book, repayments, due dates, and aging indicators.',
                ],
            },
            {
                'head':  'How to use reports',
                'steps': [
                    'Choose the report from <b>Reports</b>.',
                    'Set the period date range.',
                    'Review totals and status badges, especially balance-sheet balance and control differences.',
                    'Export CSV/Excel/PDF where available for committee packs or further analysis.',
                ],
            },
            {
                'head':  'Audit trail',
                'tips':  [
                    'Every report should be traceable back to journal entries and source modules.',
                    'Investigate any variance between member subledgers and GL control accounts before closing a period.',
                    'Use exports for external accountant review.',
                ],
            },
        ],
    },

    {
        'slug':      'member-communications-center',
        'title':     'Member Communications Center',
        'category':  'Communications',
        'summary':   'Send branded CoopMS emails for profile updates, savings reminders, loan repayments, and member notices.',
        'endpoints': ['communications.index', 'communications.new_campaign', 'communications.campaign_detail'],
        'body': [
            {
                'head':  'What can be sent',
                'steps': [
                    '<b>Profile update reminder</b> targets members with incomplete profiles.',
                    '<b>Monthly savings reminder</b> targets members with no savings recorded for the current month.',
                    '<b>Loan repayment reminder</b> targets members with active loan balances.',
                    '<b>Balance and statement notice</b> prompts members to review their portal account position.',
                    '<b>General cooperative notice</b> sends a standard administrative email.',
                ],
            },
            {
                'head':  'Sending a message',
                'steps': [
                    'Go to <b>Communications - Compose Email</b>.',
                    'Choose the message type; the title, subject, body, and audience are filled automatically.',
                    'Review the message and selected audience.',
                    'Send to a selected test member first before sending to a large audience.',
                ],
            },
            {
                'head':  'Merge tags',
                'text':  'Available tags include member name, member number, savings balance, monthly savings target, savings due day, loan balance, estimated monthly repayment, next repayment date, profile completion, and portal link.',
            },
            {
                'head':  'Delivery ledger',
                'tips':  [
                    'Every campaign records recipient count, sent, failed, and skipped totals.',
                    'A skipped row usually means the member has no email address.',
                    'WhatsApp should only be enabled after consent and approved template setup.',
                ],
            },
        ],
    },

    {
        'slug':      'loan-application-due-diligence',
        'title':     'Loan Application Due Diligence',
        'category':  'Loans',
        'summary':   'Understand member loan consent, repayment schedule acceptance, and staff vs non-staff documentation rules.',
        'endpoints': ['portal.apply_loan_member', 'portal.loan_detail', 'loans.loans_list',
                      'loans.apply_loan'],
        'body': [
            {
                'head':  'Before submission',
                'steps': [
                    'The applicant selects loan type, amount, and tenure.',
                    'CoopMS calculates the repayment schedule from the configured loan settings.',
                    'The applicant must accept the schedule before the application can proceed.',
                    'The applicant must accept terms and give express data-processing consent.',
                ],
            },
            {
                'head':  'Non-staff members',
                'steps': [
                    'Credit check consent and status must be reviewed.',
                    'Bank statement request/status must be tracked.',
                    'Payment collateral such as post-dated cheques or standing order can be recorded.',
                    'Final approval should not proceed until due diligence is complete.',
                ],
            },
            {
                'head':  'Staff cooperative members',
                'text':  'Staff-member applications can use HR affordability confirmation because repayments are salary-deducted. Bank statement and credit-check requirements may be marked not required according to the cooperative policy.',
            },
            {
                'head':  'Audit record',
                'tips':  [
                    'The accepted schedule and consent snapshot are retained with the loan.',
                    'Loan stage actions should be reviewed by the required cooperative officers.',
                    'Repayment emails and loan status notifications depend on configured outgoing email.',
                ],
            },
        ],
    },

    {
        'slug':      'system-settings-and-hardening',
        'title':     'System Settings & Hardening',
        'category':  'Administration',
        'summary':   'Configure identity, users, password policy, mail, accounting defaults, and readiness checks.',
        'endpoints': ['admin_panel.settings'],
        'body': [
            {
                'head':  'Key settings areas',
                'steps': [
                    '<b>Cooperative identity</b> controls name, short name, logo, address, and support details.',
                    '<b>Users</b> controls staff access, roles, activation, setup links, and super-admin status.',
                    '<b>Password policy</b> lets admin define the minimum strength expected for user passwords.',
                    '<b>Email</b> controls SMTP/provider settings used for setup links, notifications, and communications.',
                    '<b>Accounting defaults</b> control the bank/cash account used for new postings.',
                ],
            },
            {
                'head':  'Readiness checks',
                'text':  'Use the system readiness indicators to confirm critical operational items such as outgoing email, payment configuration, and accounting defaults before onboarding members at scale.',
            },
            {
                'head':  'Email setup',
                'steps': [
                    'Enable outgoing email.',
                    'Enter the sender name/address and SMTP or provider credentials.',
                    'Save settings before sending a test email.',
                    'Use a selected test member before sending a campaign to many members.',
                ],
            },
            {
                'head':  'Safety notes',
                'tips':  [
                    'Give admin access only to trusted officers.',
                    'Use setup links instead of sharing temporary passwords by hand.',
                    'Keep SMTP/API secrets out of screenshots and documents.',
                    'Review settings after every deployment or major configuration change.',
                ],
            },
        ],
    },

    # ── Target Advance (CTAS) ────────────────────────────────────────────────
    {
        'slug':      'target-advance-overview',
        'title':     'Target Advance: how the scheme works',
        'category':  'Target Advance',
        'summary':   'A rotating contribution scheme that gives each member a lump sum on their balloted position.',
        'endpoints': ['ctas.dashboard', 'ctas.overview'],
        'body': [
            {
                'head': 'The idea',
                'text': 'Target Advance is the traditional <em>ajo</em> or <em>esusu</em> run properly. Members contribute a fixed amount every period. Each period one member (or more, if you allow it) collects the full target amount. A ballot decides the order, so nobody has to recruit contributors and everybody gets a fair chance at an early position.',
            },
            {
                'head': 'Where the money comes from',
                'steps': [
                    'Every member contributes the same fixed amount each period — <b>including after they have collected</b>.',
                    'When a member is paid, their own contributions so far cover part of it and the cooperative advances the rest.',
                    'Their remaining contributions repay that advance until the cycle ends.',
                    'If the cycle is not full, contributions will not cover a whole payout — the cooperative bridges the gap. That is the <b>cooperative guarantee</b>, and the Setup tab shows exactly how much it costs.',
                ],
            },
            {
                'head': 'Everything posts to your books',
                'text': 'Nothing here sits outside your accounts. Contributions credit the CTAS Contribution Pool, payouts move money out of the pool and into CTAS Advances Receivable, and fees post to their own income accounts. You can trace every figure in the Trial Balance.',
            },
            {
                'head': 'The Overview screen',
                'text': 'Overview answers three questions at a glance: is the scheme profitable, is it funded, and is anything overdue. Money figures come straight from the ledger. Anything the system does not yet track is listed as such rather than shown as zero.',
            },
            {
                'head': 'Tips',
                'tips': [
                    'Target Advance is an optional module. If you cannot see it, ask your provider to switch it on.',
                    'Start with a short cycle and few members to learn the flow before running a real one.',
                    'Read <b>Running a Target Advance cycle</b> next.',
                ],
            },
        ],
    },
    {
        'slug':      'target-advance-cycle',
        'title':     'Running a Target Advance cycle',
        'category':  'Target Advance',
        'summary':   'From opening enrolment through approval, ballot, contributions and payouts.',
        'endpoints': ['ctas.cycle_detail'],
        'body': [
            {
                'head': 'The four tabs',
                'steps': [
                    '<b>Members</b> — approve applications and enrol members. This is the day-to-day work.',
                    '<b>Contributions</b> — export what is due each period and import what was collected.',
                    '<b>Payouts</b> — pay each member on their balloted position, and watch the cash projection.',
                    '<b>Setup</b> — liquidity, priority pricing and deleting the cycle. Set once, then leave alone.',
                ],
            },
            {
                'head': 'Start here every time',
                'text': 'The <b>Needs attention</b> bar at the top tells you what is blocking: members waiting on approval, members who have not accepted the terms, priority requests to decide, late contributions and any funding shortfall. Click any of them to jump straight to the right tab.',
            },
            {
                'head': 'Step by step',
                'steps': [
                    '<b>Open enrolment</b> — members can now apply, or you can add them on the Members tab.',
                    '<b>Approve</b> — each application passes three gates: confirm eligibility, finance review, committee approval. Then enrol.',
                    '<b>Close enrolment</b> once you have your members.',
                    '<b>Ready for ballot</b> — blocked if the projected cash falls short, unless you deliberately accept the gap.',
                    '<b>Run ballot</b> — assigns everyone a payout position. This cannot be undone.',
                    '<b>Collect and pay</b> — import contributions each period and pay out the member whose position it is.',
                ],
            },
            {
                'head': 'Approving several members at once',
                'text': 'Members sitting at the same gate can be moved together: tick <em>Select all</em> and press the button. Every check still applies to each member individually, so anyone who is not ready — for example someone who has not accepted the terms — is held back and named for you.',
            },
            {
                'head': 'Terms are required',
                'text': 'A member cannot be enrolled until they have accepted the scheme terms, which cover the contribution obligation, how the cooperative recovers it, credit checks and the handling of their personal information. If they signed on paper, use <em>record</em> beside their name to log it — your attestation is saved to the audit trail.',
            },
            {
                'head': 'Tips',
                'tips': [
                    'Separate the approval gates between officers under Settings → Task Assignment if your cooperative wants four-eyes control.',
                    'Set a cycle start date — the contribution due dates are calculated from it.',
                    'The ballot is recorded with the seed used, so its fairness can always be demonstrated.',
                ],
            },
        ],
    },
    {
        'slug':      'target-advance-setup',
        'title':     'Plans, pricing and liquidity',
        'category':  'Target Advance',
        'summary':   'Define reusable plans, price early positions, and check the cooperative can fund a cycle.',
        'endpoints': ['ctas.plans'],
        'body': [
            {
                'head': 'Plans and cycles',
                'text': 'A <b>plan</b> is the product — for example ₦50,000 monthly for 12 months to receive ₦600,000. A <b>cycle</b> is one run of that plan with real dates and real members. Define the plan once and reuse it every year.',
            },
            {
                'head': 'Building a plan',
                'steps': [
                    'Set the contribution per period and how many periods — the target is calculated for you.',
                    'Choose the frequency: weekly, fortnightly or monthly.',
                    'Set how many members can be paid each period.',
                    'Choose how affordability is checked (see below).',
                ],
            },
            {
                'head': 'Affordability',
                'steps': [
                    '<b>Savings</b> — a member may target up to a multiple of their savings balance. Works for any cooperative.',
                    '<b>Salary</b> — the contribution must fit within a share of their monthly salary. For staff cooperatives.',
                    '<b>Manual</b> — no automatic test; the committee decides.',
                ],
            },
            {
                'head': 'Priority positions',
                'text': 'Members who need money sooner can pay for an earlier position. Price each position on the Setup tab — earlier ones normally cost more. A member requests, an officer grants or declines, and if several want the same position the ballot decides between them. <b>The fee is only charged if the position is actually allocated</b>, so nobody needs a refund.',
            },
            {
                'head': 'Liquidity and the guarantee',
                'text': 'Before a cycle can go to ballot, the projection must show it can be funded. Record your CTAS cash reserve and the support the committee has approved, and the Setup tab shows the balance for every period in green, amber or red. A cycle with a projected shortfall is blocked until support is approved — or until an officer knowingly accepts the gap, which is recorded.',
            },
        ],
    },
    {
        'slug':      'target-advance-exceptions',
        'title':     'Arrears, missed contributions and member exit',
        'category':  'Target Advance',
        'summary':   'What happens when a contribution is missed and how to settle a member who leaves.',
        'endpoints': ['ctas.exceptions'],
        'body': [
            {
                'head': 'Missed and short contributions',
                'text': 'When you import a period and a member has paid nothing, or less than expected, the shortfall is added to their arrears, a case is raised here, and the member is notified. If they later pay more than expected, the extra reduces their arrears.',
            },
            {
                'head': 'Failed automatic payments',
                'text': 'If a member pays by saved card and the charge is declined, the system waits a few days and tries again. After the final attempt automatic payment is paused, a case is raised for you, and the member is asked to pay that contribution and add their card again. An expired card is never charged — it is detected first.',
            },
            {
                'head': 'When a member leaves',
                'steps': [
                    'Open the member on the cycle and choose <b>Exit / settle</b>.',
                    'What they still owe is recovered in order: savings, then share capital, then any other recovery you enter (dividends, terminal benefits, a cash payment).',
                    'Anything still left over is written off — a committee decision, recorded as such.',
                    'Everything posts to the ledger and the subscription is closed.',
                ],
            },
            {
                'head': 'Tips',
                'tips': [
                    'Work the open cases regularly — arrears are easiest to recover early.',
                    'Resolve a case with a note once you have acted, so the next officer sees what happened.',
                ],
            },
        ],
    },
    {
        'slug':      'my-target-advance',
        'title':     'My Target Advance',
        'category':  'Member Portal',
        'summary':   'Apply for a target advance, follow your ballot position and pay automatically.',
        'endpoints': ['ctas.my_ctas', 'ctas.autopay_setup'],
        'body': [
            {
                'head': 'What this is',
                'text': 'You contribute a fixed amount each period, and on your allocated position you receive the full target amount in one payment — often long before you would have saved it yourself. You keep contributing until the cycle ends.',
            },
            {
                'head': 'Applying',
                'steps': [
                    'Choose an open cycle and enter the amount you want to target and over how many periods.',
                    'Read the terms and accept them — they cover your contributions, how the cooperative recovers them, credit checks and how your information is used.',
                    'Type your full name as your signature and submit.',
                    'The cooperative reviews your application, then enrols you for the ballot.',
                ],
            },
            {
                'head': 'After the ballot',
                'text': 'You are told which period you will be paid in. Your contributions continue as normal both before and after you collect. The <em>My contributions</em> table shows every period, when it is due and whether it has been paid.',
            },
            {
                'head': 'Getting paid earlier',
                'text': 'If the cooperative offers priority positions, you can ask for an earlier one and accept the fee. If more members want that position than there are places, a ballot decides fairly between you — and if you miss out, <b>you pay nothing</b>.',
            },
            {
                'head': 'Paying automatically',
                'steps': [
                    'Choose <b>Set up automatic payment</b> on your subscription.',
                    'You pay your next contribution by card, and that card is then used for the rest automatically.',
                    'Your card number is entered on the payment provider\'s own secure page — the cooperative never sees or stores it.',
                    'You can cancel at any time from the same screen.',
                ],
            },
        ],
    },

    # ── Corrections and governance ───────────────────────────────────────────
    {
        'slug':      'reverse-savings-upload',
        'title':     'Correcting a savings upload',
        'category':  'Savings',
        'summary':   'Undo a whole upload that was posted with the wrong figures, without breaking your books.',
        'endpoints': ['savings.salary_batch_detail'],
        'body': [
            {
                'head': 'When to use this',
                'text': 'If a savings upload went in with the wrong amounts — for example the share-capital percentage was set incorrectly — you can reverse the whole batch from the batch page.',
            },
            {
                'head': 'It reverses, it does not delete',
                'text': 'Each row is cancelled by an opposite entry. Member balances and share capital are restored, the ledger stays balanced, and both the original and the correction remain visible. Deleting the rows instead would leave member balances and your accounts disagreeing.',
            },
            {
                'head': 'How to correct an upload',
                'steps': [
                    'Fix the cause first — for example correct the share-capital percentage in Settings.',
                    'Open the batch and use <b>Reverse this upload</b>, giving a reason. The reason is saved to the audit trail.',
                    'Re-upload the corrected file under a <b>new batch reference</b>.',
                    'Check one member\'s savings statement to confirm the figures now read correctly.',
                ],
            },
            {
                'head': 'Tips',
                'tips': [
                    'Reversed rows are hidden from the Savings Records list, so the totals stay easy to read.',
                    'Running the reversal twice is safe — rows already reversed are skipped.',
                ],
            },
        ],
    },
    {
        'slug':      'task-assignment',
        'title':     'Deciding what each officer can do',
        'category':  'Administration',
        'summary':   'Assign duties by office or to a named officer, without changing any code.',
        'endpoints': ['admin_panel.task_assignment', 'admin_panel.user_permissions'],
        'body': [
            {
                'head': 'Two levels',
                'steps': [
                    '<b>By office</b> — what a Treasurer, General Secretary or Exco member can do by default.',
                    '<b>By officer</b> — one named person allowed or denied a single duty regardless of their office.',
                ],
            },
            {
                'head': 'The President always has full access',
                'text': 'The President or Administrator holds every duty and cannot be edited, so somebody can always restore access after a mistake. To limit such an account, change its role first.',
            },
            {
                'head': 'Separating approval duties',
                'text': 'Some duties exist specifically so they can be split between officers — the three Target Advance approval gates, for example. A small cooperative can give all of them to one person; a larger one can require different officers at each gate.',
            },
            {
                'head': 'Tips',
                'tips': [
                    'Changes take effect on the officer\'s very next click — no sign-out needed.',
                    'Menus follow duties, so an officer is never shown a link that will refuse them.',
                    'Every change is written to the audit log.',
                ],
            },
        ],
    },

    # ── Member account numbers ───────────────────────────────────────────────
    {
        'slug':      'account-numbers-overview',
        'title':     'How member account numbers work',
        'category':  'Account Numbers',
        'summary':   'Give every member their own bank account number so transfers post themselves.',
        'endpoints': ['virtual_accounts.index'],
        'body': [
            {
                'head': 'The problem this solves',
                'text': 'Without it, a member transfers to the cooperative\'s account, forgets the reference, sends a screenshot, and somebody posts it by hand. Here every member gets a permanent account number in their own name, so the account the money landed in tells you whose it is. Nothing is matched by hand.',
            },
            {
                'head': 'Money is handled in two steps',
                'steps': [
                    '<b>It is banked.</b> The moment a transfer lands it is recorded — cash goes up, and the same amount is held under <b>Unallocated Member Receipts</b>. The cooperative owes the member that money from that second, whether or not anyone has decided what it is for.',
                    '<b>It is applied.</b> Separately, the money moves out of holding into the member\'s savings, loan or Target Advance. This is a decision, and it can be undone on its own.',
                ],
            },
            {
                'head': 'Who decides what a payment is for',
                'steps': [
                    '<b>The member, if they said.</b> On their My Savings page they choose savings, their loan, or their Target Advance. Their choice wins.',
                    '<b>Your rule, if they did not.</b> Set in Settings on this page: all to savings, clear loans first, or hold it for an officer.',
                    '<b>Anything left over becomes savings</b>, so a member who sends more than their target needs is simply saving the difference.',
                ],
            },
            {
                'head': 'What it will never do',
                'tips': [
                    'It never guesses whose money it is. A transfer it cannot match is held as <b>unidentified</b> for you to look at.',
                    'It never reverses the arrival itself. The money genuinely landed in the bank, so only the decision about it can be undone.',
                    'It never deletes anything. Undoing an allocation returns the amount to waiting, ready to apply elsewhere.',
                ],
            },
            {
                'head': 'Reading the page',
                'steps': [
                    '<b>Account numbers issued</b> — how many members have one, and how many still do not.',
                    '<b>Waiting to be applied</b> — money received that nobody has assigned yet.',
                    '<b>Holding account (2010)</b> — the same money as seen by the ledger. A green tick means the two agree.',
                    '<b>Unidentified transfers</b> — money that could not be tied to a member.',
                ],
            },
            {
                'head': 'Tips',
                'tips': [
                    'The red number beside <b>Account Numbers</b> in the menu counts payments needing a person. No number means nothing to do.',
                    'If the queue and the holding account ever disagree, contact your CoopMS provider — something needs looking at.',
                ],
            },
        ],
    },
    {
        'slug':      'account-numbers-setup',
        'title':     'Setting up member account numbers',
        'category':  'Account Numbers',
        'summary':   'The full sequence, from Paystack approval to the first live transfer.',
        'endpoints': [],
        'body': [
            {
                'head': 'Start the long part first',
                'text': 'Paystack has to approve your cooperative for dedicated accounts before a single number can be issued, and that takes a few days. Request it first and do everything else while you wait.',
            },
            {
                'head': 'Step by step',
                'steps': [
                    '<b>1. Ask Paystack for approval.</b> In your Paystack dashboard, request <b>Dedicated Virtual Accounts</b>. They will ask for business documents such as your CAC registration.',
                    '<b>2. Check every member has an email address.</b> The bank will not issue a number without one. Fix any blanks in the members list.',
                    '<b>3. Check the webhook in Paystack.</b> Under Settings, API Keys &amp; Webhooks, the URL must be your CoopMS address followed by <code>/webhooks/paystack</code>. This is how payments reach CoopMS — if it is wrong, transfers never appear.',
                    '<b>4. Open Account Numbers</b> in the left menu and click <b>Settings</b>.',
                    '<b>5. Set Status to On.</b> Do this once Paystack has approved you.',
                    '<b>6. Choose your fallback rule</b> — what a transfer pays for when the member has not said themselves.',
                    '<b>7. Save.</b> Your chart of accounts gains one line, Unallocated Member Receipts, which holds money that has arrived but not been applied.',
                    '<b>8. Create the numbers.</b> Click <b>Create for [number] members</b>. Anyone missing an email is listed on the <b>No account yet</b> tab.',
                    '<b>9. Test with your own money.</b> Transfer a small amount to your own number. It should appear within about a minute and be applied.',
                    '<b>10. Tell the members.</b> Each sees their number on My Savings with a Copy button, and chooses there what their transfers pay for.',
                ],
            },
            {
                'head': 'Do step 9 before step 10',
                'text': 'The first real transfer is the true test. If it arrives as <b>unidentified</b> rather than matched to you, stop and contact your CoopMS provider before telling members. Your money is safe and correctly recorded either way, but one small adjustment is needed first.',
            },
            {
                'head': 'Choosing the fallback rule',
                'steps': [
                    '<b>Put it all into savings</b> — simplest, and right for most cooperatives.',
                    '<b>Clear what they owe on their loans first, rest to savings</b> — if repayments should come before saving.',
                    '<b>Hold it and let an officer decide</b> — safest, but somebody must handle every payment.',
                ],
            },
            {
                'head': 'Tips',
                'tips': [
                    'A member who already has a number keeps it. Numbers are never reissued, because that would strand every standing transfer set up against the old one.',
                    'Changing the rule later only affects money that arrives after the change.',
                    'You are charged a small fee per payment received, set by Paystack — only when money actually comes in.',
                ],
            },
        ],
    },
    {
        'slug':      'account-numbers-money',
        'title':     'Handling money that comes in',
        'category':  'Account Numbers',
        'summary':   'Identify, apply, split and correct the transfers that land in member accounts.',
        'endpoints': ['virtual_accounts.receipt_detail'],
        'body': [
            {
                'head': 'Your daily check',
                'text': 'Open Account Numbers and look at two queues. Most days both are empty and there is nothing to do.',
            },
            {
                'head': 'Unidentified transfers',
                'steps': [
                    'Money arrived but could not be tied to a member — usually a transfer to the cooperative\'s own account rather than a member\'s number.',
                    'The money is safe and already in your books, held under Unallocated Member Receipts.',
                    'Click <b>Identify</b>, choose the member, and it moves to the waiting queue.',
                ],
            },
            {
                'head': 'Waiting to be applied',
                'steps': [
                    '<b>Apply by rule</b> — uses the member\'s own choice, or your fallback rule if they have not chosen.',
                    '<b>Split by hand</b> — open the transfer and divide it yourself between Target Advance, a loan and savings.',
                    'A part-applied transfer stays in the queue with the remainder shown, so nothing is lost track of.',
                ],
            },
            {
                'head': 'You cannot apply more than arrived',
                'text': 'The system refuses a split that adds up to more than the transfer, and tells you how much is actually left. This keeps the holding account from going negative, which would mean the books claimed money the cooperative never received.',
            },
            {
                'head': 'If you apply money to the wrong place',
                'steps': [
                    '<b>Savings or loan</b> — reverse its journal entry. The amount returns to waiting and can be applied somewhere else.',
                    '<b>Target Advance</b> — correct it from the Target Advance cycle page instead, because that module keeps its own contribution schedule.',
                    'The transfer itself is never undone. The money genuinely arrived, and pretending otherwise would put your books out of step with your bank statement.',
                ],
            },
            {
                'head': 'Tips',
                'tips': [
                    'Nothing is ever deleted — every correction leaves both the original and the reversal on record.',
                    'The <b>Applied to</b> list on a transfer shows exactly where every naira went, and what has since been reversed.',
                ],
            },
        ],
    },

    # ── Text messages ────────────────────────────────────────────────────────
    {
        'slug':      'sms-alerts',
        'title':     'Sending text messages to members',
        'category':  'Communications',
        'summary':   'Reach members who do not use the mobile app, on your own SMS account.',
        'endpoints': [],
        'body': [
            {
                'head': 'Who gets a text',
                'text': 'Only members with no mobile app installed. Members who have the app already get a free alert on their phone, and paying to tell the same person twice is waste — so the system checks first and texts only those it could not otherwise reach.',
            },
            {
                'head': 'You use your own SMS account',
                'text': 'The cooperative opens and funds its own provider account and is billed directly. Nothing is shared with other cooperatives, and you control your own spending.',
            },
            {
                'head': 'Setting it up',
                'steps': [
                    '<b>1. Open an account</b> at termii.com in the cooperative\'s name — use the cooperative\'s email, not a personal one, so it survives a change of officers.',
                    '<b>2. Fund it.</b> SMS is prepaid; credit runs down as messages go out.',
                    '<b>3. Register a sender name</b> — what members see instead of a phone number. Maximum 11 characters. Approval usually takes a day or two.',
                    '<b>4. Copy the API key</b> from the provider dashboard. Treat it like a password.',
                    '<b>5. In CoopMS go to Settings, then the SMS tab.</b>',
                    '<b>6. Tick "Send text messages"</b>, choose your provider, paste the API key and enter the sender name. Leave the country code as 234 unless you are outside Nigeria.',
                    '<b>7. Send a test</b> to your own phone using the box on the page.',
                ],
            },
            {
                'head': 'If the test fails',
                'steps': [
                    '<b>Sender name not approved</b> — the most common cause. Check your provider dashboard.',
                    '<b>No credit</b> — top up and try again.',
                    '<b>Wrong API key</b> — paste it again; leaving the field blank keeps the saved one.',
                ],
            },
            {
                'head': 'Keeping the bill down',
                'tips': [
                    'Encourage members onto the mobile app — those alerts cost nothing.',
                    'A message over 160 characters is billed as more than one.',
                    'A member can switch texts off on their own profile; their in-app alerts continue.',
                    'The log at the bottom of the SMS tab shows every message sent, skipped or failed, so you can see what your credit bought.',
                ],
            },
        ],
    },
]

# ── Category metadata ─────────────────────────────────────────────────────────
CATEGORIES = {
    'Getting Started':  {'icon': 'fas fa-rocket',        'color': '#1a3a6c'},
    'Members':          {'icon': 'fas fa-users',          'color': '#0369a1'},
    'Savings':          {'icon': 'fas fa-piggy-bank',     'color': '#059669'},
    'Loans':            {'icon': 'fas fa-hand-holding-usd','color': '#d97706'},
    'Investments':      {'icon': 'fas fa-chart-line',     'color': '#7c3aed'},
    'Accounting':       {'icon': 'fas fa-balance-scale',   'color': '#0f766e'},
    'Reports':          {'icon': 'fas fa-file-alt',       'color': '#be185d'},
    'Communications':   {'icon': 'fas fa-paper-plane',    'color': '#2563eb'},
    'Administration':   {'icon': 'fas fa-cog',            'color': '#374151'},
    'Target Advance':   {'icon': 'fas fa-recycle',        'color': '#4338ca'},
    'Account Numbers':  {'icon': 'fas fa-building-columns', 'color': '#1d4ed8'},
    'Member Portal':    {'icon': 'fas fa-user-circle',    'color': '#0891b2'},
}

# Build endpoint → article lookup for the floating panel
_ENDPOINT_MAP: dict = {}
for _art in ARTICLES:
    for _ep in _art.get('endpoints', []):
        _ENDPOINT_MAP[_ep] = _art


# ── Routes ────────────────────────────────────────────────────────────────────

@help_bp.route('/')
@login_required
def knowledge_base():
    """Full searchable knowledge base."""
    # Group articles by category, preserve CATEGORIES order
    grouped: dict = {cat: [] for cat in CATEGORIES}
    for art in ARTICLES:
        cat = art.get('category', 'Getting Started')
        grouped.setdefault(cat, []).append(art)
    # Drop empty categories
    grouped = {k: v for k, v in grouped.items() if v}
    return render_template('help/knowledge_base.html',
                           grouped=grouped,
                           categories=CATEGORIES,
                           total=len(ARTICLES))


@help_bp.route('/article/<slug>')
@login_required
def article(slug):
    """Full article page."""
    art = next((a for a in ARTICLES if a['slug'] == slug), None)
    if not art:
        from flask import abort
        abort(404)
    cat_meta = CATEGORIES.get(art.get('category', ''), {})
    # Collect sibling articles for the sidebar
    siblings = [a for a in ARTICLES
                if a['category'] == art['category'] and a['slug'] != slug]
    return render_template('help/article.html',
                           article=art,
                           cat_meta=cat_meta,
                           siblings=siblings)


@help_bp.route('/api/panel')
@login_required
def panel_api():
    """JSON endpoint for the floating help panel.
    ?endpoint=main.dashboard  →  article for that page (or a generic fallback).
    """
    endpoint = request.args.get('endpoint', '')
    art = _ENDPOINT_MAP.get(endpoint)
    if not art:
        # Generic fallback
        return jsonify({
            'found':   False,
            'title':   'Help Centre',
            'summary': 'No specific guide exists for this page yet.',
            'slug':    None,
            'body':    [],
        })
    return jsonify({
        'found':   True,
        'title':   art['title'],
        'summary': art['summary'],
        'slug':    art['slug'],
        'body':    art['body'],
    })
