# P0 demo session transcript

Synthetic demo data only (generator seed 20261105, demo date pinned to 5 Nov 2026). Recorded 2026-11-23 in a scratch data folder, shown here as `<data folder>`. No API key was available, so this session uses slash commands; see [the P0 self-review](../reviews/p0-self-review.md).

```text
$ finassist demo
Demo ledger built at 

<data folder>/demo/ledger.db (synthetic data only).
  accounts: Bank A Savings, Card A, Cash · 1134 transactions generated
  imported 27 statement files; demo date pinned to 05 Nov 2026
  left for you to import: 

<data folder>/demo/inbox/card_a_2026-10.csv
Try: finassist --demo chat

$ finassist --demo chat
finassist · DEMO ledger (synthetic data) · today 05 Nov 2026 · no Anthropic API key found (environment, Keychain or 
./.env): slash commands only. Add one with `finassist config set-api-key`.
Type a question, /help for commands, /quit to leave.

› /accounts
Accounts                                                                                  
┏━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━┳━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━━━━━━┓
┃ Name            ┃ Account        ┃ Type        ┃ Institution ┃ Last 4 ┃        Balance ┃
┡━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━╇━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━━━━━━┩
│ Savings account │ Bank A Savings │ savings     │ Bank A      │ 4821   │  ₹13,66,868.08 │
│ Credit card     │ Card A         │ credit_card │ Card A      │ 9013   │ -₹11,62,485.89 │
│ Cash            │ Cash           │ cash        │             │        │      ₹2,436.00 │
└─────────────────┴────────────────┴─────────────┴─────────────┴────────┴────────────────┘

› /import 

<data folder>/demo/inbox/card_a_2026-10.csv
card_a_2026-10.csv → Card A   period 1–31 Oct 2026
  52 rows parsed · 0 parse errors · profile: Card A credit card CSV
  52 new · 0 already in ledger (skipped)
  money out ₹99,722.24 · money in ₹0.00 (new rows)
  file check: opening -₹11,62,485.89 + net -₹99,722.24 = -₹12,62,208.13 ✓ matches closing balance
Confirmation needed
  card_a_2026-10.csv → Card A   period 1–31 Oct 2026
    52 rows parsed · 0 parse errors · profile: Card A credit card CSV
    52 new · 0 already in ledger (skipped)
    money out ₹99,722.24 · money in ₹0.00 (new rows)
    file check: opening -₹11,62,485.89 + net -₹99,722.24 = -₹12,62,208.13 ✓ matches closing balance
  Commit 52 new transactions to Card A?
  answer: y
Imported 52 transactions into Card A.

› /import 

<data folder>/demo/inbox/card_a_2026-10.csv
card_a_2026-10.csv → Card A   period 1–31 Oct 2026
  already imported: nothing to do

› /spend september
Money out September 2026: ₹1,59,954.91 (76 transactions)
Scope q_cc2a0c: 1–30 Sep 2026 · all accounts · kinds: expense · excl. transfers · money out shown as positive · 76 
transactions · 72 uncategorised (₹1,59,187.91) · /how q_cc2a0c

› /spend october
Money out October 2026: ₹1,79,510.11 (88 transactions)
Scope q_ca3201: 1–31 Oct 2026 · all accounts · kinds: expense · excl. transfers · money out shown as positive · 88 
transactions · 84 uncategorised (₹1,78,995.11) · /how q_ca3201

› /how
Filter: 1–31 Oct 2026 · all accounts · kinds: expense · excl. transfers · money out shown as positive
How q_ca3201 was computed — 88 transactions                                                                           
┏━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━━━━━━━━┓
┃ Date       ┃ Account         ┃ Description                                            ┃     Amount ┃ Category      ┃
┡━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━━━━━━━━┩
│ 2026-10-01 │ Savings account │ UPI/098325259626/ZOMATO/zomato@hdfcbank/ICICI          │    ₹535.32 │ Uncategorised │
│            │                 │ Bank/Payment from Phone                                │            │               │
│ 2026-10-01 │ Credit card     │ TOIT BREWPUB BANGALORE IN                              │  ₹2,141.33 │ Uncategorised │
│ 2026-10-02 │ Savings account │ UPI/341333609984/SRI LAKSHMI                           │    ₹152.52 │ Uncategorised │
│            │                 │ STORES/srilakshmistores@okaxis/ICICI Bank/Payment from │            │               │
│            │                 │ Phone                                                  │            │               │
│ 2026-10-03 │ Savings account │ UPI/248844245260/RAPIDO/rapido@ybl/ICICI Bank/Payment  │    ₹177.93 │ Uncategorised │
│            │                 │ from Phone                                             │            │               │
│ 2026-10-03 │ Savings account │ UPI/844479863429/LAKSHMI                               │  ₹6,000.00 │ Uncategorised │
│            │                 │ DEVI/lakshmidevi97@okicici/SBI/house help oct          │            │               │
│ 2026-10-03 │ Credit card     │ NYKAA E RETAIL MUMBAI IN                               │  ₹2,324.65 │ Uncategorised │
│ 2026-10-04 │ Savings account │ UPI/709392355427/BIGBASKET/bigbasket@ybl/ICICI         │    ₹953.85 │ Uncategorised │
│            │                 │ Bank/Payment from Phone                                │            │               │
│ 2026-10-04 │ Credit card     │ FLIPKART INTERNET PVT LTD BANGALORE IN                 │  ₹2,374.00 │ Uncategorised │
│ 2026-10-04 │ Credit card     │ SWIGGY*ORDER BANGALORE IN                              │    ₹227.68 │ Uncategorised │
│ 2026-10-04 │ Credit card     │ AWS EMEA AWS.AMAZON.CO LU                              │    ₹888.70 │ Uncategorised │
│ 2026-10-05 │ Savings account │ IMPS/P2A/308389063350/RAMESH IYER/RENT OCT             │ ₹28,000.00 │ Uncategorised │
│ 2026-10-05 │ Credit card     │ DMART AVENUE SUPERMARTS BANGALORE IN                   │  ₹2,262.70 │ Uncategorised │
│ 2026-10-05 │ Credit card     │ TOIT BREWPUB BANGALORE IN                              │  ₹2,164.11 │ Uncategorised │
│ 2026-10-06 │ Credit card     │ BIGBASKET BANGALORE IN                                 │  ₹1,528.75 │ Uncategorised │
│ 2026-10-06 │ Credit card     │ UBER *TRIP HELP.UBER.COM IN                            │    ₹654.28 │ Uncategorised │
│ 2026-10-06 │ Credit card     │ ZOMATO*ORDER GURGAON IN                                │    ₹496.07 │ Uncategorised │
│ 2026-10-07 │ Credit card     │ NETFLIX.COM MUMBAI IN                                  │    ₹649.00 │ Uncategorised │
│ 2026-10-07 │ Credit card     │ FLIPKART INTERNET PVT LTD BANGALORE IN                 │  ₹7,673.81 │ Uncategorised │
│ 2026-10-07 │ Credit card     │ ZOMATO*ORDER GURGAON IN                                │    ₹234.41 │ Uncategorised │
│ 2026-10-08 │ Savings account │ UPI/SAME/CHAI POINT/chaipoint@paytm/ICICI Bank/tea     │     ₹20.00 │ Uncategorised │
│ 2026-10-08 │ Savings account │ BIL/ONL/455809922/STATE ELECTRICITY BOARD/CONS         │  ₹1,963.01 │ Uncategorised │
│            │                 │ 3453021762                                             │            │               │
│ 2026-10-08 │ Savings account │ UPI/SAME/CHAI POINT/chaipoint@paytm/ICICI Bank/tea     │     ₹20.00 │ Uncategorised │
│ 2026-10-08 │ Savings account │ UPI/353737525441/SWIGGY/swiggy@icici/ICICI             │    ₹692.32 │ Uncategorised │
│            │                 │ Bank/Payment from Phone                                │            │               │
│ 2026-10-08 │ Credit card     │ BIGBASKET BANGALORE IN                                 │  ₹3,446.06 │ Uncategorised │
│ 2026-10-08 │ Credit card     │ FLIPKART INTERNET PVT LTD BANGALORE IN                 │  ₹6,734.51 │ Uncategorised │
│ 2026-10-08 │ Credit card     │ BIGBASKET BANGALORE IN                                 │  ₹2,188.65 │ Uncategorised │
│ 2026-10-09 │ Credit card     │ DMART AVENUE SUPERMARTS BANGALORE IN                   │  ₹2,210.59 │ Uncategorised │
│ 2026-10-10 │ Cash            │ vegetables at market                                   │    ₹302.00 │ Groceries     │
│ 2026-10-10 │ Savings account │ ACH D- FIBERNET BROADBAND-24853137                     │    ₹799.00 │ Uncategorised │
│ 2026-10-10 │ Savings account │ UPI/141872732363/SWIGGY/swiggy@icici/ICICI             │    ₹690.53 │ Uncategorised │
│            │                 │ Bank/Payment from Phone                                │            │               │
│ 2026-10-10 │ Credit card     │ AMAZON PAY INDIA PRIVATE LIMITED BANGALORE IN          │  ₹5,100.88 │ Uncategorised │
│ 2026-10-10 │ Credit card     │ BIGBASKET BANGALORE IN                                 │    ₹854.53 │ Uncategorised │
│ 2026-10-12 │ Savings account │ UPI/038583471017/CHAI POINT/chaipoint@paytm/ICICI      │     ₹70.51 │ Uncategorised │
│            │                 │ Bank/Payment from Phone                                │            │               │
│ 2026-10-12 │ Savings account │ UPI/314434169252/SWIGGY/swiggy@icici/ICICI             │    ₹355.49 │ Uncategorised │
│            │                 │ Bank/Payment from Phone                                │            │               │
│ 2026-10-12 │ Credit card     │ SPOTIFY INDIA MUMBAI IN                                │    ₹119.00 │ Uncategorised │
│ 2026-10-12 │ Credit card     │ ZOMATO*ORDER GURGAON IN                                │    ₹624.20 │ Uncategorised │
│ 2026-10-13 │ Savings account │ UPI/300859281690/BIGBASKET/bigbasket@ybl/ICICI         │  ₹3,162.85 │ Uncategorised │
│            │                 │ Bank/Payment from Phone                                │            │               │
│ 2026-10-13 │ Credit card     │ AWS EMEA AWS.AMAZON.CO LU                              │  ₹1,935.34 │ Uncategorised │
│ 2026-10-14 │ Savings account │ UPI/707363782759/APOLLO                                │    ₹923.19 │ Uncategorised │
│            │                 │ PHARMACY/apollopharmacy@icici/ICICI Bank/Payment from  │            │               │
│            │                 │ Phone                                                  │            │               │
│ 2026-10-14 │ Credit card     │ FLIPKART INTERNET PVT LTD BANGALORE IN                 │  ₹1,189.65 │ Uncategorised │
│ 2026-10-15 │ Savings account │ UPI/827620118090/UBER INDIA/uber@axisbank/ICICI        │    ₹181.75 │ Uncategorised │
│            │                 │ Bank/Payment from Phone                                │            │               │
│ 2026-10-15 │ Savings account │ ACH D- LIFE INSURANCE CORP-PREMIUM 738098326           │  ₹6,500.00 │ Uncategorised │
│ 2026-10-15 │ Credit card     │ DMART AVENUE SUPERMARTS BANGALORE IN                   │  ₹4,124.74 │ Uncategorised │
│ 2026-10-16 │ Savings account │ UPI/243720470413/RAPIDO/rapido@ybl/ICICI Bank/Payment  │    ₹180.42 │ Uncategorised │
│            │                 │ from Phone                                             │            │               │
│ 2026-10-16 │ Credit card     │ AWS EMEA AWS.AMAZON.CO LU                              │  ₹1,210.82 │ Uncategorised │
│ 2026-10-17 │ Savings account │ UPI/675357187796/APOLLO                                │    ₹685.70 │ Uncategorised │
│            │                 │ PHARMACY/apollopharmacy@icici/ICICI Bank/Payment from  │            │               │
│            │                 │ Phone                                                  │            │               │
│ 2026-10-17 │ Credit card     │ UBER *TRIP HELP.UBER.COM IN                            │    ₹555.32 │ Uncategorised │
│ 2026-10-17 │ Credit card     │ AWS EMEA AWS.AMAZON.CO LU                              │    ₹702.15 │ Uncategorised │
│ 2026-10-17 │ Credit card     │ AMAZON PAY INDIA PRIVATE LIMITED BANGALORE IN          │  ₹4,096.70 │ Uncategorised │
│ 2026-10-18 │ Credit card     │ TOIT BREWPUB BANGALORE IN                              │  ₹1,790.41 │ Uncategorised │
│ 2026-10-18 │ Credit card     │ BOOKMYSHOW MUMBAI IN                                   │  ₹1,118.91 │ Uncategorised │
│ 2026-10-19 │ Savings account │ UPI/749743283992/CHAI POINT/chaipoint@paytm/ICICI      │     ₹93.30 │ Uncategorised │
│            │                 │ Bank/Payment from Phone                                │            │               │
│ 2026-10-19 │ Savings account │ UPI/543566711267/RAPIDO/rapido@ybl/ICICI Bank/Payment  │    ₹162.96 │ Uncategorised │
│            │                 │ from Phone                                             │            │               │
│ 2026-10-19 │ Savings account │ UPI/345725290922/RAPIDO/rapido@ybl/ICICI Bank/Payment  │    ₹106.65 │ Uncategorised │
│            │                 │ from Phone                                             │            │               │
│ 2026-10-19 │ Credit card     │ AWS EMEA AWS.AMAZON.CO LU                              │  ₹1,007.11 │ Uncategorised │
│ 2026-10-19 │ Credit card     │ SWIGGY*ORDER BANGALORE IN                              │    ₹485.99 │ Uncategorised │
│ 2026-10-19 │ Credit card     │ UBER *TRIP HELP.UBER.COM IN                            │    ₹382.53 │ Uncategorised │
│ 2026-10-19 │ Credit card     │ DMART AVENUE SUPERMARTS BANGALORE IN                   │  ₹3,235.64 │ Uncategorised │
│ 2026-10-19 │ Credit card     │ BOOKMYSHOW MUMBAI IN                                   │    ₹482.19 │ Uncategorised │
│ 2026-10-19 │ Credit card     │ SWIGGY*ORDER BANGALORE IN                              │    ₹919.72 │ Uncategorised │
└────────────┴─────────────────┴────────────────────────────────────────────────────────┴────────────┴───────────────┘
… 28 more rows (export arrives in P2)
1–31 Oct 2026 · all accounts · kinds: expense · excl. transfers · money out shown as positive

› /spend march 2019
No data for March 2019: Savings account has no statement for 1–31 Mar 2019; Credit card has no statement for 1–31 Mar 
2019
! No imported data covers this period; figures are not meaningful.
Scope q_ebe4b8: 1–31 Mar 2019 · all accounts · kinds: expense · excl. transfers · money out shown as positive · 0 
transactions · /how q_ebe4b8

› /coverage
Data coverage (today 2026-11-05):
  Savings account    statements  1 Sep 2025 – 31 Oct 2026
  Credit card        statements  1 Sep 2025 – 31 Oct 2026
  Cash               manual      2025-09-01 to 2026-10-31

› /quit

$ finassist --demo add
Account: Amount (₹): Money out or in? (out/in) [out]: Description: Date (today, yesterday or YYYY-MM-DD) [today]: Category (optional, e.g. Groceries) []: Confirmation needed
  Add: 05 Nov 2026 · Cash · -₹450.00 · auto · Transport/Fuel · cash
  Save this transaction?
  answer: y
Saved. Cash balance is now ₹1,986.00.

--- independent check: direct SQL on the encrypted demo DB, and the generator ground truth
September: SQL 15995491 paise over 76 rows; ground truth 15995491 paise; match=True
October: SQL 17951011 paise over 88 rows; ground truth 17951011 paise; match=True
```
