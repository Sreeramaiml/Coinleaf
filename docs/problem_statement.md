# Problem Statement: Personal Financial Assistant (AI Agent)

> **How to use this document:** Hand this entire file to an LLM / coding agent as the
> specification for the system to be built. It states the problem, constraints,
> required behaviour, data model, agent design, and acceptance criteria. Sections
> marked **[DECIDE]** are open choices the implementing agent should propose and justify.

---    

## 1. Context

I am an individual (salaried, India-based, primary currency **INR ₹**) whose money moves
through many disconnected places: a salary credit, two or three bank accounts, UPI apps,
credit cards, cash, EMIs, SIPs, rent, insurance premiums, and irregular side income.

Today the picture of "where my money actually goes" exists only in fragments — bank
statements in PDF, UPI history inside apps, a few screenshots, and memory. Consequences:

- I cannot answer simple questions: *How much did I spend on food last month? Am I saving more or less than six months ago? Which subscription did I forget to cancel?*
- I discover overspending only after it happened, never while it is happening.
- Recurring charges, duplicate payments, and silent price increases go unnoticed.
- Tax-relevant and reimbursable expenses are reconstructed painfully at year end.
- Existing apps either demand bank credentials (unacceptable), bury insight under ads, or force manual data entry that I abandon within two weeks.

## 2. Problem Statement (one paragraph)

**Build a privacy-preserving, conversational Personal Financial Assistant — an AI agent —
that ingests my financial records from the sources I already have (bank/credit-card
statements, UPI/SMS exports, CSV, manual entry, receipts), normalises them into a single
trustworthy ledger, classifies every transaction into meaningful categories, and then lets
me ask questions and receive proactive insights in plain language — so that at any moment I
know what I earned, what I spent, what is recurring, whether I am on budget, and what to do
differently.** The agent must be accurate (never invent a number), explainable (every answer
traceable to transactions), low-friction (ingest, don't interrogate), and safe (my financial
data never leaves my control without explicit consent).

## 3. Goals

**G1 — Single source of truth.** One deduplicated ledger of all income and expenses across accounts, with balances that reconcile to statements.

**G2 — Effortless capture.** Ingesting a month of activity should take under 2 minutes: drop a statement file, forward an SMS export, snap a receipt. Manual typing is the fallback, not the path.

**G3 — Trustworthy categorisation.** ≥90% of transactions auto-categorised correctly; corrections are learned permanently and never need repeating.

**G4 — Answers, not dashboards.** Natural-language Q&A over my own data: comparisons, trends, breakdowns, "what if", with figures and the transactions behind them.

**G5 — Proactive awareness.** The agent tells me things unasked: budget burn-rate, unusual spend, new or increased recurring charges, income not received, savings-rate drift.

**G6 — Decision support.** Monthly/annual summaries, savings rate, expense ratios, cash-flow forecast for the next 30–90 days, affordability checks for a planned purchase.

**G7 — Privacy by default.** Local-first storage; no bank login/scraping; no third-party data sharing; any cloud LLM usage is disclosed, minimised, and redactable.

### Non-goals (explicitly out of scope for v1)
- Automated bank-account aggregation via credentials or screen scraping.
- Executing payments, transfers, or trades of any kind.
- Personalised investment advice or securities recommendations (portfolio *tracking* is allowed; *advice* is not).
- Tax filing, GST returns, or anything presented as professional tax/legal advice.
- Multi-user / family accounts, shared wallets, bill splitting.
- Credit-score monitoring, loan marketplaces, affiliate product offers.

## 4. Users and primary scenarios

**Primary user:** me — single owner, moderately technical, reviews finances weekly, not daily.

Representative scenarios the system must handle end to end:

| # | Scenario |
|---|---|
| S1 | I drop last month's bank statement PDF and card statement CSV into a folder; the agent imports both, flags 3 duplicates and 2 uncategorised items, and asks me only about those. |
| S2 | I ask "How much did I spend on eating out in October vs September, and what pushed it up?" and get numbers plus the 5 largest contributing transactions. |
| S3 | I say "Groceries budget is ₹12,000 a month"; on the 19th the agent warns me I am at 84% with 11 days left. |
| S4 | A subscription renews at a higher price; the agent notices the amount change on a known recurring merchant and tells me. |
| S5 | My salary normally lands by the 2nd; on the 5th it hasn't — the agent flags the missing expected income. |
| S6 | I photograph a restaurant bill paid in cash; it becomes a categorised cash transaction with merchant and amount. |
| S7 | I ask "Can I afford a ₹85,000 laptop next month?" and get a cash-flow-based answer with assumptions stated. |
| S8 | Year end: I ask for an annual report — income, expenses by category, savings rate, top merchants, month-by-month trend — exported as PDF/Excel. |
| S9 | I correct a miscategorised transaction once ("Zomato is Food, not Shopping") and it is never wrong again, retroactively and going forward. |
| S10 | I ask "show me everything I can claim as reimbursement for Q3" and get a filtered, tagged, exportable list. |

## 5. Functional requirements

### 5.1 Data ingestion
- **FR-1** Import bank and credit-card statements: **CSV, XLS/XLSX, PDF (text-based), and PDF (password-protected, password supplied by me)**. Different banks ⇒ different column layouts; the importer must detect or be taught a mapping per source and remember it.
- **FR-2** Import UPI / wallet exports and bank transaction SMS (pasted text or exported file); parse amount, direction, counterparty/VPA, date, reference, and account last-4.
- **FR-3** Receipt / bill capture from image or PDF → extract merchant, date, total, tax, and line items where feasible; let me confirm before commit.
- **FR-4** Manual entry via conversation ("spent 450 cash on auto today") and via a simple form.
- **FR-5** Recurring/scheduled entries: salary, rent, EMI, SIP, insurance — defined once, auto-expected thereafter.
- **FR-6** **Deduplication**: the same transaction arriving from two sources (SMS + statement) must collapse into one, using a fuzzy key (date window + amount + account + normalised merchant) with a confidence score; ambiguous cases are queued for my review, never silently merged or silently duplicated.
- **FR-7** Idempotent re-import: re-importing the same file changes nothing.
- **FR-8** Every transaction retains provenance: source file, row/line, import timestamp, raw text.

### 5.2 Normalisation and classification
- **FR-9** Normalise every record to: date, posted/value date, amount (signed, minor units), currency, direction, account, raw description, cleaned merchant name, category, subcategory, tags, payment method, notes.
- **FR-10** **Merchant normalisation:** `POS 1234 SWIGGY*ORDER BLR` → `Swiggy`. Maintain a learned merchant alias table.
- **FR-11** **Category taxonomy** — two levels, editable by me. Starting set:
  - *Income:* Salary, Freelance/Business, Interest, Dividends, Rent received, Refunds/Cashback, Gifts, Other income
  - *Essentials:* Rent/Housing, Utilities, Groceries, Transport/Fuel, Healthcare, Insurance, Education, Childcare, Domestic help, Telecom/Internet
  - *Lifestyle:* Eating out, Shopping, Entertainment & Subscriptions, Travel, Fitness, Personal care, Gifts & Donations
  - *Obligations:* Loan EMI, Credit-card payment, Taxes, Fees & Charges
  - *Savings & Investments:* SIP/Mutual funds, Stocks, PPF/EPF, FD/RD, Gold, Crypto
  - *Other:* Cash withdrawal, Transfer between my accounts, Uncategorised
- **FR-12** **Transfers between my own accounts, credit-card bill payments, and investment contributions must NOT be counted as expenses.** This is the single most common source of wrong totals — treat it as a first-class rule, detected automatically (matching debit/credit pair across my accounts within a date window) and verifiable by me.
- **FR-13** Classification strategy: deterministic rules first (user rules → merchant table → regex/MCC heuristics), LLM only for the residue, with the chosen path recorded per transaction. Low-confidence items are marked `needs_review`, not guessed silently.
- **FR-14** **Learning from corrections:** one correction creates/updates a rule and offers retroactive re-application to matching past transactions.
- **FR-15** Splits: one transaction divisible across categories (₹3,000 supermarket bill = ₹2,400 groceries + ₹600 household).
- **FR-16** Refunds/reversals linked to the original charge so net spend is right.

### 5.3 Budgets, goals, recurring detection
- **FR-17** Monthly budgets per category (and overall), with rollover optional; track actual vs budget vs days elapsed (burn rate), not just totals.
- **FR-18** Savings goals: target amount + date → required monthly contribution, progress, projected hit/miss date.
- **FR-19** **Recurring-charge detection** from history: identify subscriptions and fixed obligations by merchant+cadence+amount stability; list them with next expected date and annualised cost; detect *new*, *increased*, *missing*, and *apparently-unused* recurrences.
- **FR-20** Expected-income tracking: flag salary or invoice payments that did not arrive within the expected window.

### 5.4 Query, insight, and reporting
- **FR-21** **Natural-language Q&A** over the ledger, supporting at minimum: totals and averages by period/category/merchant/account; period-over-period comparison; top-N; trend over N months; "where did the increase come from"; search by text/amount range/tag; counts and frequency. **Every numeric answer must be computed by querying the data store — never estimated by the language model — and must be able to show the underlying transactions on request.**
- **FR-22** Derived metrics: total income, total expense, net cash flow, savings rate, essential vs discretionary split, category shares, average daily spend, largest outliers, month-end projection.
- **FR-23** **Cash-flow forecast** for 30/60/90 days from recurring items, historical averages, and known upcoming obligations; state assumptions and show a range, not false precision.
- **FR-24** **Affordability check:** given a planned purchase (amount, date, one-off or EMI), report the effect on projected balance, budget, and savings goals.
- **FR-25** Reports: monthly statement, annual summary, category deep-dive, merchant report, tax/reimbursement-tagged export. Output as **Markdown, PDF, and Excel/CSV**, with charts (trend line, category breakdown, income-vs-expense bars).
- **FR-26** Anomaly detection: amount far outside the merchant/category norm, first-time large merchant, duplicate charge on the same day, sudden category spike, unusually many small debits.
- **FR-27** Proactive briefs: weekly summary and month-end wrap, each ≤10 lines, led by what changed and what to do.
- **FR-28** **Explainability:** for any figure, "show me how you got this" returns the filter used and the contributing transaction list.

### 5.5 Interaction
- **FR-29** Primary interface: **conversational** (CLI chat and/or local web chat) — *[DECIDE]* which, with reasoning.
- **FR-30** A lightweight visual surface: current month at a glance, budgets, recurring list, review queue.
- **FR-31** A **review queue** as the only place I ever do chores: uncategorised, low-confidence, possible duplicates, possible transfers, missing expected income — each resolvable in one keystroke/click.
- **FR-32** The agent asks clarifying questions only when the answer materially changes the result; otherwise it states its assumption and proceeds.
- **FR-33** Multi-turn context: "and the month before?", "break that down by merchant", "exclude rent" must work without restating the question.

## 6. Non-functional requirements

- **NFR-1 Correctness over fluency.** A wrong number is a product failure. Aggregations are computed in code/SQL. If data is insufficient, say so. No fabricated transactions, merchants, or totals — ever.
- **NFR-2 Privacy & data residency.** Data at rest on my machine (or a store I control), encrypted. **No bank credentials collected, stored, or used. No scraping of banking portals.** No analytics, telemetry, or third-party sharing.
- **NFR-3 LLM data minimisation.** If a hosted model is used, send only what the task needs; support redaction of account numbers and a **fully local model mode** as a configurable option. Document exactly what leaves the machine, per feature.
- **NFR-4 Security.** Encrypted DB or encrypted volume; secrets (API keys, statement passwords) in OS keychain or a `.env` excluded from version control; nothing sensitive in logs; dependency pinning.
- **NFR-5 Performance.** Import of a 1,000-row statement < 30 s. A typical question answered in < 5 s. Ledger of 100,000 transactions stays responsive.
- **NFR-6 Reliability.** Imports are transactional — a failed import leaves no partial state. Automatic timestamped backups and a restore path. Every destructive operation is confirmed and undoable.
- **NFR-7 Auditability.** An append-only change log: what changed, when, by whom/what (me, a rule, or the agent), and the previous value.
- **NFR-8 Extensibility.** Adding a new statement format, category, metric, or report should be a small, isolated change. New bank ⇒ a parser/mapping plugin, not a rewrite.
- **NFR-9 Testability.** Deterministic logic unit-tested with synthetic fixtures covering multiple bank formats; agent behaviour covered by scenario tests S1–S10 in §4. **No real financial data in tests or fixtures.**
- **NFR-10 Internationalisation-ready.** INR default with Indian digit grouping (₹1,23,456.78) and the Indian financial year (Apr–Mar) alongside the calendar year; multi-currency amounts stored with currency and rate, not silently coerced.
- **NFR-11 Money handling.** Integer minor units or `Decimal` only — **never floats**. Explicit rounding rules. Signed amounts with a single documented convention.
- **NFR-12 Portability.** My data is exportable in open formats at any time; no lock-in.

## 7. Data model (minimum viable schema)

Entities and their essential fields — extend as needed, justify changes:

- **Account** — id, name, type (savings / current / credit_card / cash / wallet / loan / investment), institution, last4, currency, opening balance, current balance, is_active.
- **Transaction** — id, account_id, txn_date, value_date, amount_minor (signed), currency, direction, raw_description, merchant_id, category_id, subcategory, tags[], payment_method, is_transfer, transfer_group_id, parent_txn_id (for splits/refunds), confidence, classification_source (rule | merchant_map | model | user), needs_review, source_id, external_ref, notes, created_at, updated_at.
- **Merchant** — id, canonical_name, aliases[], default_category_id, is_subscription.
- **Category** — id, parent_id, name, kind (income | expense | transfer | investment), is_essential.
- **Rule** — id, matcher (merchant / regex / amount range / account), action (set category, set tags, mark transfer, ignore), priority, created_from_correction, active.
- **Budget** — id, category_id (nullable = overall), period (month), amount_minor, rollover, start_month, end_month.
- **Goal** — id, name, target_minor, target_date, linked_account_id, contributions.
- **RecurringSeries** — id, merchant_id, cadence, expected_amount_minor, amount_tolerance, next_expected_date, status (active | increased | missing | cancelled), member_txn_ids[].
- **ImportSource** — id, file name/hash, source type, account_id, mapping_profile, row_count, imported_at, status.
- **ReviewItem** — id, txn_id, reason, suggested_resolution, resolved_at.
- **AuditLog** — id, entity, entity_id, field, old_value, new_value, actor, reason, at.

Invariants to enforce in code, not by convention:
1. `sum(amounts)` per account + opening balance **must** reconcile to the statement closing balance; a mismatch surfaces as a reconciliation warning.
2. Transfers net to zero and are excluded from income and expense aggregates.
3. No two transactions share the same `(source_id, external_ref)`.
4. A split's children sum exactly to the parent amount.

## 8. Agent architecture (required shape)

Build this as a **tool-using agent over a deterministic data layer** — not as a model asked to do arithmetic.

```
          ┌──────────────────────────────────────────────┐
  chat →  │  Agent (LLM): intent → tool calls → answer   │
          │  memory: preferences, open questions, rules  │
          └───────────┬──────────────────────────────────┘
                      │ typed tool calls (validated args)
   ┌──────────────────┼───────────────────────────────────┐
   │  Ingestion   │  Ledger/Query  │  Analytics  │ Report │
   │  parsers,    │  SQL, filters, │ budgets,    │ md/pdf │
   │  dedupe, OCR │  aggregations  │ recurring,  │ xlsx,  │
   │              │                │ forecast,   │ charts │
   │              │                │ anomalies   │        │
   └──────────────────┬───────────────────────────────────┘
                      │
              Local encrypted database  +  original files
```

**Required tool surface** (names indicative; typed inputs/outputs, each independently testable):

| Tool | Purpose |
|---|---|
| `import_file(path, account, options)` | Parse & stage a statement/receipt; return summary + issues |
| `commit_import(import_id, decisions)` | Apply a staged import after dedupe decisions |
| `add_transaction(...)` / `update_transaction(...)` | Manual entry and edits |
| `query_transactions(filters, group_by, metrics)` | The single aggregation primitive behind all numeric answers |
| `get_summary(period, dimensions)` | Income/expense/net/savings-rate roll-ups |
| `compare_periods(a, b, dimension)` | Period-over-period deltas with top contributors |
| `set_budget / get_budget_status` | Budgets and burn rate |
| `detect_recurring / list_recurring` | Subscription and obligation inventory |
| `forecast_cashflow(horizon_days)` | Projection with assumptions |
| `check_affordability(amount, when, mode)` | Purchase impact |
| `find_anomalies(period)` | Outliers, duplicates, spikes |
| `create_rule(...)` / `reclassify(scope)` | Learn a correction, apply retroactively |
| `list_review_queue / resolve_review_item` | Chore queue |
| `generate_report(kind, period, format)` | Markdown / PDF / Excel output |
| `export_data(format, filters)` | Open-format export |

**Agent behaviour contract:**
1. Numbers come only from tool results. Never compute or recall a figure in prose.
2. State the period and filters used in every answer ("Oct 1–31, excluding transfers").
3. Quantify uncertainty: say what is unclassified or missing and how it affects the answer.
4. Act on read-only queries freely; **confirm before any write** that edits many rows (bulk reclassify, delete, import commit affecting existing rows).
5. Never recommend specific investments or products; redirect to the general principle instead.
6. Prefer a short answer with the one number asked for, plus at most two lines of insight.

## 9. Technology constraints and choices

- **Language:** Python 3.12+ *(preferred — align with my existing projects)*.
- **Storage:** SQLite (encrypted at rest) for v1; schema migrations from day one. *[DECIDE]* whether an ORM is worth it.
- **LLM:** a tool-calling Claude model via the Anthropic API, with a pluggable provider interface and a local-model option for the privacy mode. *[DECIDE]* model tier per task (cheap model for classification, stronger for analysis/conversation) and a cost estimate per month at ~500 transactions.
- **PDF/statement parsing:** *[DECIDE]* libraries, with a fallback path for scanned statements (OCR).
- **Reports/charts:** *[DECIDE]* — must render without a network connection.
- **Packaging:** single-command setup (`uv`/venv + one entrypoint), config file, and a `--demo` mode seeded with synthetic data so the system can be demonstrated with zero real data.
- **Testing:** pytest, synthetic fixtures for ≥3 distinct bank statement layouts, scenario tests for S1–S10.

## 10. Deliverables

1. Working application: ingest → classify → query → report, runnable locally from a documented setup.
2. Database schema + migrations.
3. Statement parsers for CSV and text PDF, with a documented way to add a new bank.
4. The tool layer of §8, each tool unit-tested.
5. The agent loop with the behaviour contract of §8 encoded in the system prompt and enforced in code where possible.
6. Review queue + budget/recurring surfaces.
7. Report generation to Markdown, PDF, Excel.
8. Test suite with synthetic data, plus a seeded `--demo` dataset.
9. `README.md` (setup, usage, example conversations), `PRIVACY.md` (exactly what data goes where, including every LLM call), `ARCHITECTURE.md` (decisions and rejected alternatives).
10. A short list of known limitations and the next three things worth building.

## 11. Suggested build phases

| Phase | Scope | Done when |
|---|---|---|
| **P0 — Skeleton** | Schema, config, accounts, manual entry, CSV import, `query_transactions`, CLI chat | I can import a CSV and ask "total spend in October" and get a correct number |
| **P1 — Trust** | Dedupe, transfer detection, reconciliation, review queue, rules + learning from corrections | Totals reconcile to the statement; transfers excluded; one correction sticks |
| **P2 — Intelligence** | Full NL Q&A, comparisons, trends, derived metrics, explainability | Scenarios S2, S9, S10 pass |
| **P3 — Foresight** | Budgets, recurring detection, anomalies, forecast, affordability, proactive briefs | Scenarios S3, S4, S5, S7 pass |
| **P4 — Output & polish** | PDF statement/receipt import, reports & charts, exports, visual surface, packaging | Scenarios S1, S6, S8 pass |

## 12. Acceptance criteria

The build is acceptable when all of the following hold on a synthetic dataset of ≥12 months and ≥1,500 transactions across ≥3 accounts including a credit card:

- **A1** Importing the provided statements produces a ledger whose per-account closing balances reconcile to the statements, to the rupee.
- **A2** Re-importing every file changes zero rows.
- **A3** No inter-account transfer or credit-card bill payment is counted in total expenses; verified by an explicit test.
- **A4** ≥90% of transactions are auto-categorised correctly against a labelled fixture; the remainder are marked `needs_review`, not mis-stated.
- **A5** A correction applied once is reflected retroactively and on the next import, with no repeat prompt.
- **A6** All ten scenarios in §4 are demonstrable end to end.
- **A7** For 20 benchmark questions, every figure the agent reports matches a direct SQL query over the same data, and "show me how" returns the contributing transactions.
- **A8** The agent declines to answer, or states the limitation, when the data cannot support the question — verified with a deliberately out-of-range question.
- **A9** `PRIVACY.md` accurately enumerates every outbound call; running in local-model mode makes zero network requests (verifiable).
- **A10** Fresh-machine setup to first answer in under 15 minutes following the README alone.

## 13. Risks and required mitigations

| Risk | Mitigation the build must include |
|---|---|
| LLM hallucinating figures | All arithmetic in the data layer; the model may only relay tool output; answers carry their filters |
| Transfers inflating expenses | First-class transfer detection + reconciliation test (A3) |
| Duplicate records from overlapping sources | Fuzzy dedupe with confidence, review queue for ambiguity, idempotent imports |
| Bank-format drift breaking imports | Mapping profiles per source, clear parse failures, never partial commits |
| Mis-categorisation eroding trust | Confidence thresholds, `needs_review` instead of guessing, one-shot learning |
| Privacy exposure via LLM calls | Data minimisation, redaction, local-model mode, documented in `PRIVACY.md` |
| Float rounding errors in money | Integer minor units / `Decimal` enforced at the type level |
| Chore fatigue killing adoption | Review queue capped and ranked; agent asks only when it matters |
| Scope creep into advice/trading | Hard product boundary: tracking and analysis only; no execution, no recommendations |

## 14. Assumptions (correct these if wrong)

1. Single user, India, primary currency INR; occasional foreign-currency transactions.
2. No bank API/account-aggregator access in v1 — statement files and SMS/manual input only.
3. Data volume ~300–600 transactions/month, history imported for 12–24 months.
4. Local-first on macOS; a hosted LLM is acceptable for non-sensitive text when disclosed, with a local option available.
5. I will tolerate a short weekly review ritual if it stays under five minutes.

---

### What I want from you (the implementing LLM/agent)

1. Restate the problem in your own words and list anything in this document that is ambiguous or that you would decide differently — **before writing code**.
2. Resolve every **[DECIDE]** point with a recommendation and a one-line reason.
3. Propose the architecture and file layout, then build **Phase P0** completely — working, tested, runnable — and show me a real session transcript against demo data.
4. Then proceed phase by phase, stopping after each for review. Do not jump ahead.
5. Keep correctness, privacy, and explainability above feature count. A smaller system I trust beats a larger one I don't.
