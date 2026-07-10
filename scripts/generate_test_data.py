"""Generate a coherent test dataset (CSV) matching the migration templates."""
import csv, os, random
random.seed(42)

OUT = r'C:\OOU_Accounting_System\test_data'
os.makedirs(OUT, exist_ok=True)

def w(name, header, rows):
    with open(os.path.join(OUT, name), 'w', newline='', encoding='utf-8') as f:
        wr = csv.writer(f); wr.writerow(header)
        for r in rows: wr.writerow(r)
    print(f"  {name}: {len(rows)} rows")

FIRST = ['Adebayo','Chidinma','Emeka','Folake','Gbenga','Halima','Ifeoma','Jide',
         'Kemi','Lanre','Maryam','Ngozi','Obinna','Patience','Rasheed','Segun',
         'Tunde','Uche','Yemi','Zainab']
LAST = ['Okafor','Adeyemi','Balogun','Chukwu','Danjuma','Eze','Fashola','Ganiyu',
        'Hassan','Ibrahim','Johnson','Kalu','Lawal','Musa','Nwosu','Oladipo',
        'Peters','Quadri','Suleiman','Umeh']
BANKS = ['GTBank','Access','Zenith','UBA','First Bank','Fidelity']

# ── Members ──────────────────────────────────────────────────────────────────
members = []
mem_rows = []
for i in range(1, 21):
    mn = f"OOU/2025/{i:04d}"
    fn, ln = FIRST[i-1], LAST[(i*3) % 20]
    email = f"{fn.lower()}.{ln.lower()}@example.com"
    phone = f"080{random.randint(10000000,99999999)}"
    joined = f"202{random.choice([2,3,3,3])}-{random.randint(1,12):02d}-{random.randint(1,28):02d}"
    monthly = random.choice([5000,5000,10000,10000,15000,20000])
    members.append({'mn':mn,'email':email,'monthly':monthly})
    mem_rows.append([fn, ln, email, phone, mn, joined, monthly,
                     f"{random.randint(1,50)} Lagos St", random.choice(['Teacher','Trader','Engineer','Nurse','Civil Servant']),
                     '', 'active', f"{random.choice(FIRST)} {ln}", 'Spouse',
                     f"080{random.randint(10000000,99999999)}", random.choice(BANKS),
                     f"{random.randint(1000000000,9999999999)}", f"{fn} {ln}", '', ''])
w('1_members.csv',
  ['first_name','last_name','email','phone','member_number','date_joined','monthly_savings',
   'address','occupation','date_of_birth','status','nominee_name','nominee_relationship',
   'nominee_phone','bank_name','account_number','account_name','emergency_contact_name','emergency_contact_phone'],
  mem_rows)

# ── Savings (10 months each) ─────────────────────────────────────────────────
sav_rows = []
rc = 1
for m in members:
    for mth in range(1, 11):
        month = f"2024-{mth:02d}"
        amt = m['monthly']
        late = 0
        pt = 'monthly'
        # occasional extra personal deposit + occasional late fee
        if random.random() < 0.15:
            late = round(amt*0.1, 2)
        sav_rows.append([m['mn'], '', amt, month, pt, late, 'transfer', f"RCPT/2024/{rc:05d}", f"2024-{mth:02d}-{random.randint(1,10):02d}", ''])
        rc += 1
        if random.random() < 0.2:
            sav_rows.append([m['mn'], '', random.choice([2000,3000,5000]), month, 'personal', 0, 'cash', f"RCPT/2024/{rc:05d}", f"2024-{mth:02d}-{random.randint(11,28):02d}", 'Personal top-up'])
            rc += 1
w('2_savings.csv',
  ['member_number','email','amount','month','payment_type','late_fee','payment_method','receipt_number','date','notes'],
  sav_rows)

# ── Loans (first 8 eligible members) ─────────────────────────────────────────
PURPOSES = ['Regular','Housing','Emergency','Asset Purchase','School Fees']
loan_rows, repay_rows = [], []
loans = []
for i in range(8):
    m = members[i]
    principal = random.choice([100000,150000,200000,250000,300000])
    rate = random.choice([9,10,11])
    tenure = random.choice([6,12,18])
    interest = round(principal*rate/100*tenure/12, 2)
    total_rep = round(principal+interest, 2)
    ln_no = f"LOAN/2024/{i+1:04d}"
    completed = (i % 3 == 0)
    if completed:
        paid = total_rep; balance = 0.0; status='completed'
    else:
        paid = round(total_rep*random.choice([0.3,0.4,0.5,0.6]), 2); balance = round(total_rep-paid,2); status='active'
    disb_date = f"2024-{random.randint(1,4):02d}-{random.randint(1,28):02d}"
    loan_rows.append([m['mn'], '', ln_no, principal, PURPOSES[i%5], tenure, rate,
                      total_rep, balance, status, disb_date, disb_date, disb_date, principal, ''])
    loans.append({'ln':ln_no,'mn':m['mn'],'principal':principal,'interest':interest,'total_rep':total_rep,'paid':paid})

# Repayments (split principal/interest proportionally, matching app logic)
rr = 1
for L in loans:
    remaining = L['paid']
    n = random.randint(2,5)
    per = round(remaining/n, 2)
    for k in range(n):
        amt = per if k < n-1 else round(remaining - per*(n-1), 2)
        ip = round(amt * L['interest']/L['total_rep'], 2)
        pp = round(amt - ip, 2)
        repay_rows.append([L['ln'], '', '', amt, pp, ip, 0, 'transfer', f"REP/2024/{rr:05d}", f"2024-{random.randint(5,12):02d}-{random.randint(1,28):02d}", ''])
        rr += 1
w('3_loans.csv',
  ['member_number','email','loan_number','amount','purpose','tenure','interest_rate',
   'total_repayment','balance','status','date_applied','date_approved','disbursement_date','disbursed_amount','notes'],
  loan_rows)
w('4_repayments.csv',
  ['loan_number','member_number','email','amount','principal_paid','interest_paid','penalty_paid',
   'payment_method','receipt_number','date','notes'],
  repay_rows)

# ── Expenses ─────────────────────────────────────────────────────────────────
w('5_expenses.csv',
  ['category','amount','description','vendor','payment_method','date','notes'],
  [['Stationery',12000,'Office supplies','Balogun Market','cash','2024-02-05',''],
   ['Utilities',18000,'Electricity','IBEDC','transfer','2024-03-10',''],
   ['Bank Charges',6500,'Account maintenance','GTBank','transfer','2024-04-01',''],
   ['AGM Expenses',45000,'Annual general meeting','Venue Ltd','transfer','2024-06-15',''],
   ['Transport',9000,'Committee travel','','cash','2024-07-20',''],
   ['Printing',15000,'Passbooks & receipts','QuickPrint','cash','2024-08-12','']])

# ── Revenue ──────────────────────────────────────────────────────────────────
w('6_revenue.csv',
  ['category','amount','description','source','date','notes'],
  [['Entrance Fees',40000,'20 new members @ 2000','Members','2024-01-31',''],
   ['Form Fees',15000,'Loan application forms','Members','2024-03-31',''],
   ['Statement Fees',8000,'Statement requests','Members','2024-05-31',''],
   ['Commission',22000,'Cooperative services','Partners','2024-09-30','']])

# ── Investments ──────────────────────────────────────────────────────────────
w('7_investments.csv',
  ['name','type','amount','institution','interest_rate','risk_level','start_date','maturity_date','description','notes'],
  [['GTBank Fixed Deposit','Fixed Deposit',500000,'GTBank',12,'low','2024-01-15','2025-01-15','12-month FD',''],
   ['FGN Savings Bond','Government Bond',300000,'DMO',14,'low','2024-03-01','2026-03-01','2-year bond',''],
   ['Shop Rental','Real Estate',250000,'Coop Plaza',0,'medium','2024-02-01','','Rental unit','']])

# ── Honorarium ───────────────────────────────────────────────────────────────
w('8_honorarium.csv',
  ['recipient_name','member_number','email','amount','description','month','date'],
  [['Adebayo Okafor','OOU/2025/0001','',25000,'President allowance','2024-06','2024-06-30'],
   ['Chidinma Adeyemi','OOU/2025/0002','',15000,'Secretary allowance','2024-06','2024-06-30']])

print("\nDone. Files in:", OUT)
