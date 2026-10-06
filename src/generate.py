"""Generate synthetic raw financial records for one persona.

Persona: software engineer in Hyderabad, 60 months (Oct 2021 - Sep 2026).
Output mimics what a person can actually export: bank and card statements,
payslips, mutual fund / REIT transactions, price history, EPF / PPF passbooks,
an FD register and loan sanction letters. Everything is fictional and seeded.
"""
import calendar
import datetime as dt
from pathlib import Path

import numpy as np
import pandas as pd

SEED = 7
ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"

N_MONTHS = 60
MONTHS = [(2021 + (9 + i) // 12, (9 + i) % 12 + 1) for i in range(N_MONTHS)]

OPENING_BANK = 200_000
OPENING_EPF = 140_000
OPENING_EQUITY_UNITS = 600.0          # bought before the window, at NAV 100
CC_LAST4 = "4321"

EMPLOYERS = [((2021, 10), "NIMBUS SOFTWARE PVT LTD"), ((2023, 7), "VERTEXA TECHNOLOGIES PVT LTD")]

LOANS = [
    # loan_id, lender, loan_type, principal, annual_rate, tenure_months, first_emi_date
    ("EDU-01", "SBI", "Education loan", 600_000, 0.105, 60, dt.date(2019, 10, 5)),
    ("CAR-01", "HDFC BANK", "Car loan", 650_000, 0.090, 60, dt.date(2023, 4, 5)),
]

# Income tax, new regime, by financial year start: (std deduction, rebate limit, slabs)
TAX = {
    2021: (0, 500_000, [(250_000, 0), (500_000, .05), (750_000, .10), (1_000_000, .15), (1_250_000, .20), (1_500_000, .25), (None, .30)]),
    2023: (50_000, 700_000, [(300_000, 0), (600_000, .05), (900_000, .10), (1_200_000, .15), (1_500_000, .20), (None, .30)]),
    2024: (75_000, 700_000, [(300_000, 0), (700_000, .05), (1_000_000, .10), (1_200_000, .15), (1_500_000, .20), (None, .30)]),
    2025: (75_000, 1_200_000, [(400_000, 0), (800_000, .05), (1_200_000, .10), (1_600_000, .15), (2_000_000, .20), (2_400_000, .25), (None, .30)]),
}

TRIPS = {(2021, 12): 38_000, (2022, 5): 45_000, (2022, 12): 30_000, (2023, 10): 62_000,
         (2024, 4): 48_000, (2024, 12): 70_000, (2025, 6): 55_000, (2026, 2): 85_000}

FDS = [("FD-01", (2022, 4), 100_000, dt.date(2023, 3, 15)), ("FD-02", (2024, 1), 100_000, None)]
REIT_BUYS = {(2024, 8): 150, (2025, 2): 120, (2025, 11): 120}
FLOOR = 25_000            # never let an optional investment take the account below this
BUFFER_MONTHS = 1.5       # cash kept after a lump-sum investment, in months of committed outflow

rng = np.random.default_rng(SEED)
bank, card, mf_txn, reit_txn = [], [], [], []
_ref = iter(range(410_000_000_000, 499_999_999_999, 7_919))


def ref():
    return str(next(_ref))


def day(y, m, d):
    return dt.date(y, m, min(max(int(d), 1), calendar.monthrange(y, m)[1]))


def rday(y, m, lo=1, hi=28):
    return day(y, m, rng.integers(lo, hi + 1))


def money(x):
    return float(round(x))


def emi(principal, rate, n):
    r = rate / 12
    return principal * r * (1 + r) ** n / ((1 + r) ** n - 1)


def annual_tax(gross_annual, fy):
    std, rebate, slabs = TAX[max(k for k in TAX if k <= fy)]
    taxable = max(gross_annual - std, 0)
    if taxable <= rebate:
        return 0.0
    tax, lower = 0.0, 0
    for upper, rate in slabs:
        top = taxable if upper is None else min(taxable, upper)
        if top > lower:
            tax += (top - lower) * rate
        if upper is None or taxable <= upper:
            break
        lower = upper
    return tax * 1.04


def debit(date, desc, amt):
    bank.append((date, desc, money(amt), 0.0))


def credit(date, desc, amt):
    bank.append((date, desc, 0.0, money(amt)))


def spend_card(date, desc, amt):
    card.append((date, desc, money(amt), "Dr"))


def upi(payee):
    return f"UPI/P2M/{ref()}/{payee}"


def split(total, n):
    w = rng.dirichlet(np.ones(n) * 3)
    return [max(total * x, 60) for x in w]


# ---------- market prices (synthetic, not real index data) ----------
eq_nav, debt_nav, reit_px = [100.0], [30.0], [330.0]
for _ in range(N_MONTHS - 1):
    eq_nav.append(eq_nav[-1] * (1 + rng.normal(0.011, 0.045)))
    debt_nav.append(debt_nav[-1] * (1 + rng.normal(0.0058, 0.002)))
    reit_px.append(reit_px[-1] * (1 + rng.normal(0.003, 0.03)))
SCHEMES = {"NIFTY 50 INDEX FUND": eq_nav, "SHORT TERM DEBT FUND": debt_nav}

loan_emi = {l[0]: round(emi(l[3], l[4], l[5])) for l in LOANS}

ctc = 1_450_000.0
rent = 15_000.0
payslips, epf_rows, ppf_rows, fd_rows = [], [], [], []
epf_bal, epf_accrued = float(OPENING_EPF), 0.0
ppf_bal = 0.0
reit_units = 0
equity_sip, debt_sip = 6_000.0, 0.0
bank_bal = float(OPENING_BANK)
last_bonus = 0.0

for i, (y, m) in enumerate(MONTHS):
    fy = y if m >= 4 else y - 1
    infl = 1.06 ** (i / 12)
    month_start = len(bank)
    employer = [e for (start, e) in EMPLOYERS if (y, m) >= start][-1]
    mon = calendar.month_abbr[m].upper()

    # ---- pay changes ----
    if m == 4 and i > 0:
        ctc *= 1.09 if y != 2023 else 1.08
        equity_sip = round(equity_sip * 1.10, -2)
    if (y, m) == (2023, 7):
        ctc *= 1.35
        equity_sip = 15_000.0
    if (y, m) == (2022, 4):
        debt_sip = 3_000.0

    # ---- payroll ----
    ctc_month = ctc / 12
    gross = ctc_month / (1 + 0.12 * 0.4)
    basic = gross * 0.4
    pf_emp = basic * 0.12
    pf_er = basic * 0.12
    tds = annual_tax(gross * 12, fy) / 12
    net = gross - pf_emp - 200 - tds
    payslips.append((f"{y}-{m:02d}", employer, money(gross), money(basic), money(pf_emp),
                     money(pf_er), 200.0, money(tds), money(net)))
    pay_day = day(y, m, calendar.monthrange(y, m)[1] - 1)
    credit(pay_day, f"NEFT/{employer}/SALARY {mon}{y % 100}", net)

    # EPF passbook: employer share net of pension (EPS) cap
    epf_contrib = pf_emp + (pf_er - 1_250)
    epf_bal += epf_contrib
    epf_rate = {2021: .081, 2022: .0815}.get(fy, .0825)
    epf_accrued += epf_bal * epf_rate / 12
    epf_int = 0.0
    if m == 3:
        epf_int, epf_accrued = epf_accrued, 0.0
        epf_bal += epf_int
    epf_rows.append((f"{y}-{m:02d}", money(epf_contrib), money(epf_int), money(epf_bal)))

    if m == 4 and i > 0:
        last_bonus = ctc / 1.09 * 0.08 if y != 2023 else ctc / 1.08 * 0.08
        credit(day(y, 4, 15), f"NEFT/{employer}/PERF BONUS FY{(y - 1) % 100}", last_bonus * 0.75)

    # ---- fixed costs ----
    if (y, m) == (2024, 3):
        rent = 24_000.0
    elif m == 3 and y > 2024 or (m == 10 and y in (2022, 2023)):
        rent = round(rent * 1.05, -2)
    landlord = "RAMESH K" if (y, m) < (2024, 3) else "SUNITA REDDY"
    debit(day(y, m, 3), f"UPI/P2P/{ref()}/{landlord}/Rent", rent)
    debit(day(y, m, 2), f"IMPS/{ref()}/TO FATHER/monthly support", round(6_000 * infl, -2))
    debit(day(y, m, 1), f"UPI/P2P/{ref()}/LAKSHMI/maid salary", round(3_000 * infl, -2))
    elec = (3_000 if m in (4, 5, 6) else 1_500) * infl * rng.uniform(0.85, 1.15)
    debit(rday(y, m, 10, 14), "BBPS/TGSPDCL/ELECTRICITY BILL", elec)
    debit(day(y, m, 5), "NACH/ACT FIBERNET/BROADBAND", 1_060 if y < 2025 else 1_178)
    debit(rday(y, m, 6, 9), upi("AIRTEL PREPAID"), 299 if (y, m) < (2024, 7) else 349)
    spend_card(day(y, m, 8), "NETFLIX.COM", 649)
    spend_card(day(y, m, 11), "SPOTIFY INDIA", 119)
    spend_card(day(y, m, 15), "GOOGLE YOUTUBE PREMIUM", 129 if y < 2025 else 149)
    spend_card(day(y, m, 1), "CULTFIT MEMBERSHIP", round(2_200 * infl, -1))
    if m == 1:
        spend_card(day(y, 1, 18), "STAR HEALTH INSURANCE PREMIUM", 14_500 * 1.08 ** (y - 2022))
    if m == 6:
        debit(day(y, 6, 10), "NACH/HDFC LIFE/TERM PREMIUM", 11_800)

    # ---- loans ----
    for loan_id, lender, ltype, principal, rate, n, first in LOANS:
        k = (y - first.year) * 12 + (m - first.month)
        if 0 <= k < n:
            debit(day(y, m, 5), f"NACH/{lender}/{ltype.upper()} EMI/{loan_id}", loan_emi[loan_id])

    # ---- car purchase ----
    if (y, m) == (2023, 3):
        fd1 = 100_000 * (1 + 0.07 / 4) ** 3  # 3 full quarters, premature closure
        credit(day(y, 3, 15), "FD CLOSURE/FD-01/PREMATURE", fd1)
        debit(day(y, 3, 20), f"RTGS/{ref()}/VARUN MOTORS/CAR DOWN PAYMENT", 150_000)
        debit(day(y, 3, 21), "NEFT/ICICI LOMBARD/MOTOR INSURANCE", 21_500)
    elif m == 3 and y > 2023:
        debit(day(y, 3, 21), "NEFT/ICICI LOMBARD/MOTOR INSURANCE", 21_500 * 0.9 ** (y - 2023))
    has_car = (y, m) >= (2023, 3)

    # ---- discretionary ----
    for amt in split(7_500 * infl * rng.uniform(0.85, 1.15), rng.integers(4, 8)):
        shop = rng.choice(["ZEPTO", "BLINKIT", "BIGBASKET", "DMART"])
        if shop == "DMART":
            spend_card(rday(y, m), "POS DMART AVENUE SUPERMARTS", amt)
        else:
            debit(rday(y, m), upi(shop), amt)
    for amt in split(5_500 * infl * rng.uniform(0.75, 1.3), rng.integers(6, 13)):
        place = rng.choice(["SWIGGY", "ZOMATO", "PARADISE BIRYANI", "CAFE NILOUFER", "TRUFFLES"],
                           p=[.35, .3, .15, .1, .1])
        (debit(rday(y, m), upi(place), amt) if place in ("SWIGGY", "ZOMATO")
         else spend_card(rday(y, m), f"POS {place} HYDERABAD", amt))
    ride = 1_200 if has_car else 2_600
    for amt in split(ride * infl * rng.uniform(0.8, 1.2), rng.integers(3, 8)):
        debit(rday(y, m), upi(rng.choice(["UBER INDIA", "RAPIDO", "OLA CABS", "HYD METRO RAIL"])), amt)
    if has_car:
        for _ in range(2):
            spend_card(rday(y, m), rng.choice(["HPCL FUEL", "IOCL FUEL"]), rng.uniform(1_500, 2_400) * infl)
        if m in (6, 12):
            spend_card(rday(y, m), "POS VARUN MOTORS SERVICE", rng.uniform(4_000, 9_000))
    sale = 2.6 if m in (10, 11) else 1.0
    for amt in split(4_000 * infl * sale * rng.lognormal(0, 0.35), rng.integers(2, 6)):
        spend_card(rday(y, m), rng.choice(["AMAZON PAY INDIA", "MYNTRA DESIGNS", "FLIPKART", "DECATHLON", "IKEA HYDERABAD"]), amt)
    if rng.random() < 0.18:
        card.append((rday(y, m), "AMAZON PAY INDIA REFUND", money(rng.uniform(400, 2_500)), "Cr"))
    for _ in range(rng.integers(1, 3)):
        spend_card(rday(y, m), rng.choice(["PVR INOX", "BOOKMYSHOW"]), rng.uniform(450, 1_400) * infl)
    debit(rday(y, m), upi("APOLLO PHARMACY"), rng.uniform(300, 900) * infl)
    if rng.random() < 0.15:
        spend_card(rday(y, m), "PRACTO CONSULTATION", rng.uniform(600, 2_500))
    debit(rday(y, m), f"ATM WDL/{ref()}/HITECH CITY", rng.choice([2_000, 2_500, 3_000]))
    if (y, m) in TRIPS:
        t = TRIPS[(y, m)]
        spend_card(rday(y, m, 1, 10), "MAKEMYTRIP FLIGHTS", t * 0.45)
        spend_card(rday(y, m, 10, 20), "MAKEMYTRIP HOTELS", t * 0.35)
        spend_card(rday(y, m, 15, 25), "POS LOCAL EXPENSES TRAVEL", t * 0.20)
    if m in (10, 11):
        debit(rday(y, m), upi("TANISHQ / DIWALI GIFTS"), rng.uniform(6_000, 12_000) * infl)
    if (y, m) in ((2024, 2), (2025, 9)):
        spend_card(rday(y, m), "COURSERA", 7_800 if y == 2024 else 14_200)

    # ---- card bill: everything charged last month, paid on the 3rd ----
    if i > 0:
        py, pm = MONTHS[i - 1]
        due = sum(a if t == "Dr" else -a for d, desc, a, t in card
                  if (d.year, d.month) == (py, pm) and not desc.startswith("PAYMENT RECEIVED"))
        debit(day(y, m, 3), f"CC BILL PAYMENT/HDFC CC XX{CC_LAST4}", due)
        card.append((day(y, m, 3), "PAYMENT RECEIVED - THANK YOU", money(due), "Cr"))
    if rng.random() < 0.35:
        card.append((rday(y, m), "CASHBACK CREDIT", money(rng.uniform(80, 400)), "Cr"))

    # ---- committed investments (SIPs run regardless) ----
    debit(day(y, m, 7), "NACH/BSE STAR MF/NIFTY 50 INDEX FUND SIP", equity_sip)
    mf_txn.append((day(y, m, 7), "NIFTY 50 INDEX FUND", "SIP", equity_sip, eq_nav[i]))
    if debt_sip:
        debit(day(y, m, 7), "NACH/BSE STAR MF/SHORT TERM DEBT FUND SIP", debt_sip)
        mf_txn.append((day(y, m, 7), "SHORT TERM DEBT FUND", "SIP", debt_sip, debt_nav[i]))
    if m in (2, 5, 8, 11) and reit_units:
        credit(day(y, m, 25), "ACH/OFFICE REIT/DISTRIBUTION", reit_units * reit_px[i] * 0.016)
    committed = sum(d for _, _, d, _ in bank[month_start:])

    # ---- optional investments: only from cash the month can spare ----
    def room(on):
        early_credits = sum(c for d, _, _, c in bank[month_start:] if d < on)
        spent = sum(d for _, _, d, _ in bank[month_start:])
        return bank_bal + early_credits - spent - FLOOR

    if m in (5, 11):
        # twice a year, invest whatever cash sits above BUFFER_MONTHS of committed spend
        lump = (room(day(y, m, 10)) + FLOOR - BUFFER_MONTHS * committed) // 10_000 * 10_000
        if lump >= 10_000:
            debit(day(y, m, 10), "BSE STAR MF/NIFTY 50 INDEX FUND LUMPSUM", lump)
            mf_txn.append((day(y, m, 10), "NIFTY 50 INDEX FUND", "LUMPSUM", lump, eq_nav[i]))
    ppf_dep = ppf_int = 0.0
    if m == 4 and y >= 2023:
        want = 40_000.0 if y < 2024 else 90_000.0
        ppf_dep = float(min(want, room(day(y, 4, 4)) // 1000 * 1000))
        if ppf_dep >= 10_000:
            debit(day(y, 4, 4), "TRF TO PPF A/C 3021XXXX88", ppf_dep)
            ppf_bal += ppf_dep
        else:
            ppf_dep = 0.0
    if m == 3 and ppf_bal:
        ppf_int = round(ppf_bal * 0.071)
        ppf_bal += ppf_int
    ppf_rows.append((f"{y}-{m:02d}", ppf_dep, float(ppf_int), ppf_bal))
    for fd_id, when, amt, closed in FDS:
        if (y, m) == when:
            assert room(day(y, m, 20)) >= amt, f"cannot fund {fd_id}"
            debit(day(y, m, 20), f"FD BOOKED/{fd_id}/AUTO RENEW", amt)
            fd_rows.append((fd_id, day(y, m, 20), float(amt), 0.07, closed))
    if (y, m) in REIT_BUYS:
        px = reit_px[i]
        units = int(min(REIT_BUYS[(y, m)], room(day(y, m, 16)) // px))
        if units >= 50:
            debit(day(y, m, 16), "NEFT/ZERODHA BROKING/FUNDS ADDED", units * px)
            reit_txn.append((day(y, m, 16), "OFFICE REIT", "BUY", units, round(px, 2)))
            reit_units += units

    bank_bal += sum(c - d for _, _, d, c in bank[month_start:])

# ---------- write raw files ----------
RAW.mkdir(parents=True, exist_ok=True)

b = pd.DataFrame(bank, columns=["txn_date", "description", "debit", "credit"])
b["_credit_first"] = (b["credit"] > 0).astype(int)
b = b.sort_values(["txn_date", "_credit_first"], ascending=[True, False], kind="stable").drop(columns="_credit_first")
b["balance"] = OPENING_BANK + (b["credit"] - b["debit"]).cumsum()
assert b["balance"].min() > 0, "bank balance went negative"
b.to_csv(RAW / "bank_statement.csv", index=False)

c = pd.DataFrame(card, columns=["txn_date", "description", "amount", "dr_cr"]).sort_values("txn_date", kind="stable")
c.to_csv(RAW / "credit_card_statement.csv", index=False)

pd.DataFrame(payslips, columns=["pay_month", "employer", "gross", "basic", "epf_employee",
                                "epf_employer", "professional_tax", "tds", "net_pay"]).to_csv(RAW / "payslips.csv", index=False)

mf = pd.DataFrame(mf_txn, columns=["txn_date", "scheme", "txn_type", "amount", "nav"])
mf["units"] = (mf["amount"] / mf["nav"]).round(4)
mf["nav"] = mf["nav"].round(4)
opening = pd.DataFrame([{"txn_date": dt.date(2021, 9, 30), "scheme": "NIFTY 50 INDEX FUND", "txn_type": "OPENING",
                         "amount": OPENING_EQUITY_UNITS * 100.0, "nav": 100.0, "units": OPENING_EQUITY_UNITS}])
pd.concat([opening, mf]).to_csv(RAW / "mf_transactions.csv", index=False)

month_ends = [day(y, m, 31) for y, m in MONTHS]
nav = pd.concat([pd.DataFrame({"month_end": month_ends, "instrument": k, "price": np.round(v, 4)}) for k, v in SCHEMES.items()]
                + [pd.DataFrame({"month_end": month_ends, "instrument": "OFFICE REIT", "price": np.round(reit_px, 2)})])
nav.to_csv(RAW / "price_history.csv", index=False)

pd.DataFrame(reit_txn, columns=["txn_date", "instrument", "txn_type", "units", "price"]).to_csv(RAW / "reit_transactions.csv", index=False)
pd.DataFrame(epf_rows, columns=["month", "contribution", "interest", "balance"]).to_csv(RAW / "epf_passbook.csv", index=False)
pd.DataFrame(ppf_rows, columns=["month", "deposit", "interest", "balance"]).to_csv(RAW / "ppf_passbook.csv", index=False)
pd.DataFrame(fd_rows, columns=["fd_id", "open_date", "principal", "annual_rate", "close_date"]).to_csv(RAW / "fd_register.csv", index=False)
pd.DataFrame([l[:6] + (l[6],) for l in LOANS],
             columns=["loan_id", "lender", "loan_type", "principal", "annual_rate", "tenure_months", "first_emi_date"]
             ).to_csv(RAW / "loans.csv", index=False)

print(f"bank rows {len(b)}, card rows {len(c)}, min balance {b['balance'].min():,.0f}, "
      f"closing balance {b['balance'].iloc[-1]:,.0f}")
