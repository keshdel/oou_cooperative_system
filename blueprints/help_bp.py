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
]

# ── Category metadata ─────────────────────────────────────────────────────────
CATEGORIES = {
    'Getting Started':  {'icon': 'fas fa-rocket',        'color': '#1a3a6c'},
    'Members':          {'icon': 'fas fa-users',          'color': '#0369a1'},
    'Savings':          {'icon': 'fas fa-piggy-bank',     'color': '#059669'},
    'Loans':            {'icon': 'fas fa-hand-holding-usd','color': '#d97706'},
    'Investments':      {'icon': 'fas fa-chart-line',     'color': '#7c3aed'},
    'Reports':          {'icon': 'fas fa-file-alt',       'color': '#be185d'},
    'Administration':   {'icon': 'fas fa-cog',            'color': '#374151'},
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
