# Architecture: Personal Financial Assistant

| Field | Value |
|---|---|
| Document type | Architecture and design decisions |
| Source | [problem_statement.md](problem_statement.md), cited as "PS §n" |
| Status | **Proposed. Awaiting owner review before any code is written (PS "What I want from you", step 1–3).** |
| Date | 2026-11-23 |
| Working package name | `finassist` (placeholder, rename freely) |

This document answers PS steps 1–3: it restates the problem, lists ambiguities, resolves every **[DECIDE]**, and proposes the architecture, data model, tool layer, and file layout. It also records rejected alternatives, which makes it the `ARCHITECTURE.md` deliverable in PS §10 item 9. Items marked **(verify in P0)** are assumptions to confirm on the real machine before the code depends on them.

---

## Contents

1. [The problem in my own words](#1-the-problem-in-my-own-words)
2. [Ambiguities and points I would decide differently](#2-ambiguities-and-points-i-would-decide-differently)
3. [Decisions](#3-decisions)
4. [System overview](#4-system-overview)
5. [Repository and module layout](#5-repository-and-module-layout)
6. [Data model](#6-data-model)
7. [Ingestion pipeline](#7-ingestion-pipeline)
8. [Normalisation and classification](#8-normalisation-and-classification)
9. [Analytics](#9-analytics)
10. [Agent](#10-agent)
11. [LLM usage, model tiers, and cost](#11-llm-usage-model-tiers-and-cost)
12. [Interaction surfaces](#12-interaction-surfaces)
13. [Reports and exports](#13-reports-and-exports)
14. [Privacy and security](#14-privacy-and-security)
15. [Configuration and data locations](#15-configuration-and-data-locations)
16. [Testing strategy](#16-testing-strategy)
17. [Performance](#17-performance)
18. [Build phases](#18-build-phases)
19. [Rejected alternatives](#19-rejected-alternatives)
20. [Known limitations and next three things](#20-known-limitations-and-next-three-things)
21. [Open questions for the owner](#21-open-questions-for-the-owner)
22. [Appendix: scenario traceability](#appendix-scenario-traceability)

---

## 1. The problem in my own words

The owner's money is spread across several bank accounts, a credit card, UPI apps, cash, and scheduled debits (EMI, SIP, rent, insurance). Each source shows only part of the picture, and nothing combines them. So simple questions ("what did I spend on food last month?") can't be answered, overspending is noticed only afterwards, and recurring or duplicate charges go unseen.

The system to build is a **local ledger with a conversational front end**:

1. **Ingest** the files the owner already has (statements, SMS/UPI exports, receipts, typed entries) with no bank login.
2. **Normalise** them into one deduplicated, reconciled ledger where every row traces back to its source line.
3. **Classify** every row, mostly with deterministic rules that learn from one correction, and with an LLM only for what remains.
4. **Answer and alert** in plain language. Every figure comes from code that queries the ledger, never from the model, and every figure can be expanded into the transactions behind it.

The quality bar is trust, not feature count. A wrong total, a double-counted card payment, or a merchant silently sent to a cloud API breaks the product. The design follows from that: a deterministic core, a thin LLM layer that can only relay tool output, and strict control over what leaves the machine.

---

## 2. Ambiguities and points I would decide differently

Each item gives the ambiguity, my proposed resolution, and whether the owner needs to confirm (see §21).

| # | Ambiguity in PS | Proposed resolution |
|---|---|---|
| A-1 | **"Credit-card payment" is listed as an Obligations category (§5.2 FR-11), but FR-12 says card bill payments must not count as expenses.** | A card bill payment is a **transfer** whenever the card account is tracked: the purchases are the expense. The "Credit-card payment" category exists only for an **untracked** card (no statement imported); then it counts as an expense so the spending isn't lost. The importer flags this case for review. |
| A-2 | **Refunds:** the taxonomy has Income → "Refunds/Cashback", but FR-16 wants refunds linked to the original charge "so net spend is right". | A refund **linked** to an original charge takes the original's category and reduces that category's expense (a positive amount in an expense category). Only **unlinked** cashback and rewards go to Income → Refunds/Cashback. |
| A-3 | **Cash:** if ATM withdrawals are expenses *and* cash receipts (S6) are expenses, cash spending is counted twice. | Model **Cash as an account**. A withdrawal is a transfer into Cash; cash spends are expenses from Cash. Unlogged cash shows up as a growing Cash balance, and the agent periodically asks to write it off as "Untracked cash spend" (one review item, one keystroke). |
| A-4 | **"Q3" (S10) and quarters generally:** calendar Q3 is Jul–Sep, Indian FY Q3 is Oct–Dec. | Config `periods.quarter_convention`, default **`fy`**. Every answer states the resolved dates ("Q3 FY2026-27: 1 Oct – 31 Dec 2026"), so a wrong guess is visible at once. |
| A-5 | **"Apparently-unused" recurrences (FR-19):** the ledger cannot see whether a subscription is used. | The system cannot detect this from transactions and will not pretend to. It (a) lists subscriptions with annualised cost and asks once a year "still using these?", and (b) lets the owner mark one `unused`, which then appears in briefs until it is cancelled. |
| A-6 | **`parent_txn_id` serves both splits and refunds (PS §7).** One field with two meanings makes queries error-prone. | Split into `split_parent_id` (on the child rows of a split) and `refund_of_txn_id` (on a refund row). |
| A-7 | **Array fields** (`tags[]`, `aliases[]`, `member_txn_ids[]`) in SQLite. | Use join tables (`transaction_tag`, `merchant_alias`, `recurring_member`). They can be indexed and queried, and the foreign keys keep them consistent. |
| A-8 | **`direction` and signed `amount_minor` are both stored (PS §7)**, so they can disagree. | `direction` is a **generated column** derived from the sign. It can be read but never written. |
| A-9 | **Loan EMI:** part of it is principal (paying down a liability), not consumption. | v1 counts the whole EMI as an Obligations expense, which is what cash-flow questions need. Splitting principal from interest is left out (it needs the loan schedule). Recorded in §20. |
| A-10 | **Savings rate vs net cash flow vs investments (FR-22)** are not defined. | Exact formulas in §9.2. In short: investments are **savings, not expense**; savings rate = (income − expense) / income. |
| A-11 | **FR-6 says ambiguous duplicates must never be merged silently.** Does that ban auto-merging *unambiguous* ones? | High-confidence matches (e.g. same UPI reference number) are merged automatically, **logged, shown in the import summary, and undoable**. Only the middle band goes to review. Without this, S1 ("asks me only about those") is not achievable. |
| A-12 | **Proactive insights (G5) need something to run.** No scheduler is specified. | v1 computes alerts **when the app is opened** ("since you were last here…"). An optional macOS `launchd` job for the weekly brief comes in P3. No always-on daemon. |
| A-13 | **"Never compute a figure in prose" vs comparisons** like "₹2,300 more than September". | Tools return the deltas and percentages themselves (`compare_periods` returns delta and % change). The model copies figures and never subtracts. Enforced by the grounding check (§10.5). |
| A-14 | **Weekly/month-end briefs (FR-27):** the PS implies the LLM writes them. | Briefs are produced **deterministically from templates**. That is cheaper and always correct, and works in local mode. The LLM can optionally rephrase a brief, but the template version is the source of truth. |
| A-15 | **A8 (decline when data can't support the question)** needs a definition of "can't". | Every query result reports **data coverage**: the dates for which each account in scope has data. A period with no or partial coverage is flagged in code, and the agent must say so. |
| A-16 | **Classification accuracy A4 measured on synthetic data** is circular if the same author writes both the merchant table and the fixtures. | The fixture generator draws part of its merchants and description templates from a **held-out set** that is not in the seed merchant table, so A4 measures the rules + LLM path rather than a lookup. |
| A-17 | **Python version:** PS says 3.12+; the repo pins 3.14 (`.python-version`). | `requires-python = ">=3.12"`, develop on 3.14. The native dependency that matters (`sqlcipher3` 0.6.5) ships macOS arm64 wheels for cp312–cp314 on PyPI (checked 2026-11-23). |

---

## 3. Decisions

### 3.1 The [DECIDE] points

| PS ref | Decision | One-line reason |
|---|---|---|
| FR-29 interface | **CLI chat first** (Typer + Rich + prompt_toolkit). **Local web UI** (FastAPI + Jinja2 + HTMX, bound to 127.0.0.1) added in P4 for the visual surface (FR-30). | CLI is the fastest to build, easiest to test, has no network surface, and suits a moderately technical weekly user. Budgets and the review queue read better in a browser, so that comes once the core is trusted. |
| §9 ORM | **SQLAlchemy Core** (expression language + `MetaData`) with **Alembic** migrations. **No ORM sessions.** | `query_transactions` builds filters and `GROUP BY` dynamically, and Core does that safely without string SQL. Plain ORM sessions would hide writes (unit-of-work, lazy loads), which conflicts with the audit-log and confirm-before-bulk-write rules. |
| §9 storage encryption | **SQLCipher** via `sqlcipher3`, 256-bit random key kept in the macOS Keychain. Fallback: encrypted APFS sparse-bundle volume for the data folder. | Encrypts the database file itself, not just the disk. FileVault alone protects only while the Mac is off. |
| §9 model tiers | **Owner's decision (2026-11-23): Google Gemini by default** — conversation `gemini-3.8-flash`, classification and receipts `gemini-3.5-flash-lite`; Anthropic (Claude Opus 5.5 / Haiku 5.5) remains selectable with `llm.provider`. **Local mode:** a tool-calling open-weights model served on localhost by Ollama, chosen in P2 by running the A7 benchmark. | The provider interface keeps the choice a config switch. Flash for conversation (stable, function calling and thinking); Flash-Lite for short, schema-constrained work. Expected cost **≈ US$3–7/month** at 500 transactions (§11.4). |
| §9 PDF parsing | **pdfplumber** for text PDFs (tables by position; `password=` handles protected PDFs). **ocrmypdf + Tesseract** (local, optional extra) as the scanned-PDF fallback. **openpyxl** reads `.xlsx`, **xlrd** reads legacy `.xls`, stdlib `csv` + **charset-normalizer** reads CSV. | pdfplumber gives character positions, which bank-statement layouts need. OCR stays local so a scanned statement never goes to the cloud. |
| §9 reports/charts | **Jinja2** → Markdown; **matplotlib** (Agg backend) → PNG/SVG charts; **fpdf2** → PDF with a bundled **Noto Sans** font (for the ₹ glyph); **XlsxWriter** → Excel with native charts and Indian number formats. | Everything is pure Python or bundled. Nothing needs the network or a system library like Pango or Ghostscript. |

### 3.2 Other decisions

| Topic | Decision | Reason |
|---|---|---|
| Config format | TOML (`tomllib`), validated with **pydantic** v2 | Readable, in the standard library, typed validation with clear errors. |
| Tool argument and result types | **pydantic** models. The JSON Schema sent to the model is generated from the same model that validates the arguments. | One definition gives both the schema and the validation. |
| Project tooling | **uv** (lockfile, exact pins), **ruff**, **mypy** (strict on `core/`, `db/`, `analytics/`), **pytest** + **hypothesis** | One-command setup (PS §9). Property tests suit money and dedupe logic. |
| Agent harness | **Hand-written tool loop** over the Anthropic Messages API, behind a provider interface | Each tool call has to pass through confirmation gates, redaction, and the grounding check (§10). The same loop must also drive a local model. |
| Fuzzy matching | **rapidfuzz** | Fast, MIT-licensed, no native dependency beyond its wheel. |
| Secrets | **keyring** → macOS Keychain | NFR-4. |
| Platform paths | **platformdirs** | Correct macOS locations without hard-coding. |
| Logging | **structlog** with a redaction processor | Structured logs. Amounts, descriptions, and account numbers are dropped before they reach a log. |

---

## 4. System overview

### 4.1 Principles

1. **Deterministic core, thin agent.** Parsing, dedupe, classification rules, every sum, and every forecast are plain Python and SQL with unit tests. The LLM chooses tools and phrases answers. It never does arithmetic or edits the ledger directly.
2. **Every figure has a receipt.** Every numeric tool result carries a `query_id`. That id leads back to the exact filter and the transaction list (FR-28).
3. **Writes are explicit.** Every change goes through one repository function that writes the audit log in the same database transaction. Bulk changes need the owner's confirmation, collected by the harness, not by the model.
4. **What leaves the machine is the minimum and is listed.** One module (`llm/`) talks to the network. It redacts what it sends, logs every call, and a runtime network guard enforces local mode.
5. **Fail closed.** A parse error rejects the whole file. A query over data that doesn't exist says so. A low-confidence label becomes `needs_review`, not a guess.

### 4.2 Component diagram

```
                       ┌───────────────────────────────────────────────────────────┐
  owner ── CLI chat ──▶│ agent/                                                    │
        ── review UI ─▶│  loop ─▶ tool registry ─▶ policy gate ─▶ execute          │
        ── web (P4) ──▶│   ▲          (pydantic)    (read / write / bulk-confirm)  │
                       │   │                                         │             │
                       │   └── grounding check ◀── results (for_model | for_user)  │
                       └──────┬────────────────────────────────────────┬───────────┘
                              │ llm/ (only network egress)             │ typed calls
                ┌─────────────┴─────────────┐                          ▼
                │ provider: anthropic|local │      ┌────────────────────────────────────────────┐
                │ redact ▸ netguard ▸ usage │      │ ingest/   classify/   analytics/   reports/│
                └─────────────┬─────────────┘      │ parsers   merchants   query        md/pdf  │
                              │                    │ profiles  rules       metrics      xlsx    │
                     api.anthropic.com             │ dedupe    heuristics  budgets      charts  │
                     (cloud mode only)             │ transfers llm-resid.  recurring    export/ │
                                                   │ reconcile splits      forecast     review/ │
                                                   └──────────────────┬─────────────────────────┘
                                                                      │ db/ repositories (audit in same txn)
                                                                      ▼
                                       SQLCipher DB  ·  encrypted originals  ·  backups  ·  Keychain
```

### 4.3 Request flow: "How much did I spend on eating out in October vs September?" (S2)

```
1. CLI → agent.loop: user message
2. LLM (Opus 5.5) → tool_use resolve_period("October") and resolve_period("September")   [parallel]
3. code resolves → 2026-10-01..31, 2026-09-01..30 (relative to today, Asia/Kolkata)
4. LLM → tool_use compare_periods(a, b, filters={category: "Eating out"}, top_contributors=5)
5. analytics.compare → two SQL aggregates + delta + top-5 contributing txns
      returns for_model: {a: "₹8,420", b: "₹6,110", delta: "+₹2,310", pct: "+37.8%",
                          top: [...5 rows, merchant names, display amounts], coverage: ok,
                          needs_review_in_scope: 2 (₹640), query_id: q_7f3a}
              for_user:  full contributing transaction table (rendered locally, not sent)
6. LLM → final text using only display strings from step 5
7. grounding check: every number in the text appears in step-5 output ✓
8. CLI renders answer + scope footer ("1–31 Oct vs 1–30 Sep 2026 · excl. transfers · 2 items unreviewed (₹640)")
   + handle "/how q_7f3a"
```

---

## 5. Repository and module layout

```
Financial-assistant/
├── pyproject.toml            # uv project, entry point `finassist`
├── uv.lock                   # exact pins (NFR-4)
├── config.example.toml       # every option, commented
├── README.md  PRIVACY.md     # PS §10 item 9
├── docs/
│   ├── problem_statement.md
│   ├── ARCHITECTURE.md       # this file
│   └── adding-a-bank.md      # NFR-8: how to add a statement format
├── src/finassist/
│   ├── __main__.py           # `python -m finassist`
│   ├── cli/
│   │   ├── app.py            # Typer commands: init, import, chat, review, report, export, backup, restore, undo, doctor, demo
│   │   ├── chat.py           # REPL, slash commands, confirmation prompts
│   │   ├── review.py         # one-keystroke review queue
│   │   └── render.py         # Rich tables, INR formatting, scope footers
│   ├── config/
│   │   ├── settings.py       # pydantic models for config.toml
│   │   └── paths.py          # platformdirs locations
│   ├── core/
│   │   ├── money.py          # Money type, parsing, rounding, INR formatting (no floats)
│   │   ├── periods.py        # month / quarter / FY / relative period resolution
│   │   ├── textnorm.py       # description cleaning primitives
│   │   ├── ids.py            # ULIDs, query ids
│   │   └── errors.py         # closed error set
│   ├── db/
│   │   ├── engine.py         # SQLCipher connection, PRAGMAs, key from Keychain
│   │   ├── schema.py         # SQLAlchemy Core Table definitions (single source)
│   │   ├── migrations/       # Alembic env + versions
│   │   ├── repo/             # one module per aggregate: accounts, transactions, merchants, rules, budgets, ...
│   │   ├── audit.py          # change sets + append-only audit log
│   │   ├── backup.py         # timestamped encrypted backups, restore
│   │   └── integrity.py      # invariant checks used by `doctor` and tests
│   ├── ingest/
│   │   ├── pipeline.py       # detect → parse → normalise → stage → preview → commit
│   │   ├── detect.py         # file type + mapping-profile matching
│   │   ├── profiles.py       # MappingProfile model + persistence
│   │   ├── parsers/          # base.py (Parser protocol), csv_table.py, xlsx_table.py, pdf_text.py, pdf_ocr.py,
│   │   │                     # sms.py, upi_export.py, receipt.py, manual.py
│   │   ├── banks/            # built-in mapping profiles (TOML), one file per layout
│   │   ├── fingerprint.py    # row fingerprints for idempotency
│   │   ├── dedupe.py         # cross-source duplicate scoring
│   │   └── originals.py      # encrypted copy of each imported file
│   ├── classify/
│   │   ├── pipeline.py       # ordered stages, records the path taken
│   │   ├── merchants.py      # merchant normalisation + alias table
│   │   ├── rules.py          # user rules: matchers, actions, priority
│   │   ├── heuristics.py     # regex / MCC / narration-pattern library
│   │   ├── llm_residue.py    # batched Haiku classification with structured output
│   │   ├── transfers.py      # own-account pair matching
│   │   ├── refunds.py        # refund ↔ original linking
│   │   └── splits.py
│   ├── analytics/
│   │   ├── query.py          # query_transactions: the single aggregation primitive
│   │   ├── receipts.py       # QueryReceipt storage for "show me how"
│   │   ├── coverage.py       # per-account data coverage
│   │   ├── metrics.py        # income, expense, savings rate, … (formulas in §9.2)
│   │   ├── compare.py        # period-over-period + contributors
│   │   ├── budgets.py  goals.py  recurring.py  income_watch.py
│   │   ├── forecast.py  affordability.py  anomalies.py
│   │   ├── reconcile.py      # statement balance reconciliation
│   │   └── briefs.py         # weekly / month-end templates
│   ├── review/
│   │   └── queue.py          # review item creation, ranking, resolution
│   ├── reports/
│   │   ├── builder.py        # report kinds → data → renderers
│   │   ├── charts.py         # matplotlib Agg
│   │   ├── markdown.py  pdf.py  xlsx.py
│   │   └── templates/
│   ├── export/
│   │   └── exporter.py       # CSV / JSON / XLSX full or filtered export (NFR-12)
│   ├── agent/
│   │   ├── loop.py           # turn loop, iteration cap, error handling
│   │   ├── tools/            # one module per tool group; each tool = pydantic in/out + effect tag
│   │   ├── registry.py       # tool list → provider schemas
│   │   ├── policy.py         # read / write / bulk-write gates, confirmation tokens
│   │   ├── grounding.py      # numeric grounding check
│   │   ├── session.py        # multi-turn state, last query spec, pseudonym map
│   │   └── prompts/system.md # behaviour contract (PS §8)
│   ├── llm/
│   │   ├── provider.py       # LLMProvider protocol
│   │   ├── anthropic_provider.py
│   │   ├── local_provider.py # Ollama on localhost
│   │   ├── scripted_provider.py # deterministic fake for tests
│   │   ├── redact.py         # outbound redaction + pseudonymisation
│   │   ├── netguard.py       # sys.addaudithook network guard
│   │   └── usage.py          # per-call token + cost log (no payloads)
│   ├── web/                  # P4: FastAPI + Jinja2 + vendored HTMX
│   └── demo/
│       └── generator.py      # seeded synthetic data + ground-truth labels
├── assets/fonts/NotoSans-*.ttf
└── tests/
    ├── unit/                 # mirrors src/ layout
    ├── property/             # hypothesis: money, dedupe, transfers, splits
    ├── fixtures/             # generated synthetic files (never real data)
    ├── scenarios/            # test_s01_import_dedupe.py … test_s10_reimbursement.py
    ├── acceptance/           # A1–A9 checks
    └── benchmark/            # 20 questions + expected SQL (live, opt-in)
```

**Dependency rule:** `core` ← `db` ← `ingest | classify | analytics | review` ← `reports | export` ← `agent` ← `cli | web`. Nothing below `agent` imports `llm`, with one exception: `classify/llm_residue.py` and `ingest/parsers/receipt.py` take an `LLMProvider` passed in as a parameter. This keeps the whole data layer testable without a network. An import-linter contract enforces the rule in CI.

---

## 6. Data model

### 6.1 Conventions (NFR-10, NFR-11)

| Concern | Rule |
|---|---|
| Money storage | `INTEGER` minor units (paise). Python type `Money(minor: int, currency: str)`, a frozen dataclass. Constructing it from a `float` raises `TypeError`. |
| Parsing amounts | String → `Decimal` → minor units. A value with more decimal places than the currency allows is a **parse error**, not a rounding. |
| Sign convention | **From the account holder's point of view: money into the account is positive, money out is negative.** On a credit card, a purchase is negative (more owed), a payment is positive. A card balance is negative while money is owed. |
| Rounding | Only derived values (averages, percentages, forecasts, FX conversion) are rounded, always `ROUND_HALF_EVEN`, in one helper. Prose shows whole rupees, tables show paise, percentages show one decimal place. |
| SQL averages | SQLite `AVG()` returns a float, so it is **never used**. Averages are `SUM` and `COUNT` from SQL, divided as `Decimal` in Python. |
| Dates | `txn_date` (when it happened) and `value_date` (when it posted) as ISO `DATE`. Timestamps are UTC. "Today" and relative periods use the configured zone (default `Asia/Kolkata`). |
| Currency | Each account has one currency. `amount_minor` is always in the account's currency, so a card statement's INR billed amount is the truth. The foreign original goes in `orig_amount_minor` and `orig_currency`. Converting between currencies uses an explicit `fx_rate` row (rate stored as a decimal string, with source and date) and is labelled in the output. |
| Indian formatting | `format_inr(minor)` → `₹1,23,456.78`, hand-written and unit-tested (lakh/crore grouping). |
| Financial year | `FY2026-27` = 1 Apr 2026 – 31 Mar 2027. Both FY and calendar periods are supported everywhere. |
| IDs | ULIDs as `TEXT`: they sort by time and are safe to show. |

### 6.2 Tables

Fields in PS §7 are kept. Additions are marked **+** and changes **Δ**, each with its reason.

**account**: `id, name, type (savings|current|credit_card|cash|wallet|loan|investment), institution, last4, currency, opening_balance_minor, opening_balance_date+, is_active, nickname+` (the label sent to the LLM in place of the institution and last4, §14.3).
*Current balance is **not** stored* (Δ). It is computed as opening balance + Σ amounts, so it can never drift.

**transaction**:

| Column | Notes |
|---|---|
| `id` | ULID |
| `account_id` | FK |
| `txn_date`, `value_date` | |
| `amount_minor` | signed, see §6.1 |
| `currency` | must equal the account's currency (CHECK via trigger) |
| `direction` Δ | `GENERATED ALWAYS AS (CASE WHEN amount_minor >= 0 THEN 'credit' ELSE 'debit' END)` |
| `orig_amount_minor`, `orig_currency` + | foreign-currency original |
| `raw_description` | exactly as in the source |
| `merchant_id` | FK, nullable |
| `category_id` | FK, nullable (= uncategorised) |
| `payment_method` | upi, card, netbanking, neft_imps_rtgs, nach, atm, cash, cheque, other |
| `is_transfer`, `transfer_group_id` | |
| `split_parent_id` Δ | set on the child rows of a split (A-6) |
| `is_split_parent` + | parent rows are excluded from aggregates; their children count |
| `refund_of_txn_id` Δ | A-6 |
| `confidence` | 0–1, of the current classification |
| `classification_source` Δ | `user | rule | merchant_map | heuristic | model | transfer_detector | split` |
| `classification_ref` + | id of the rule or heuristic that decided it, for explainability |
| `needs_review` | |
| `user_locked` + | set when the owner categorises this row by hand; rules never overwrite it |
| `fingerprint` + | idempotency key (§7.4), UNIQUE per account |
| `external_ref` | UTR / UPI ref / card auth code when present |
| `notes` | |
| `deleted_at` + | soft delete, so undo works (NFR-6) |
| `created_at`, `updated_at` | |

**source_row** + (provenance, FR-8): `id, import_source_id, row_index, page, raw_text, raw_fields_json, parsed_ok`.
**transaction_source** + : `txn_id, source_row_id, role (primary|merged_duplicate), merge_confidence`. One transaction can have several source rows (SMS + statement). This is how FR-6 merging keeps all provenance.
**import_source** (PS ImportSource): `id, file_name, sha256, source_type, account_id, mapping_profile_id, row_count, period_start, period_end, statement_opening_minor+, statement_closing_minor+, imported_at, status (staged|committed|rolled_back|failed)`.
**mapping_profile** +: `id, name, header_signature, file_kind, column_map_json, date_format, amount_mode (signed|debit_credit|amount_drcr), skip_rows, balance_column, default_account_id, version`. FR-1 "remember the mapping".
**merchant**: `id, canonical_name, default_category_id, is_subscription, vpa_handles_json`.
**merchant_alias** + : `merchant_id, alias_key (normalised), source (seed|user|confirmed_model)`, UNIQUE(alias_key).
**category**: `id, parent_id, name, kind (income|expense|transfer|investment), is_essential, is_system`. Seeded with the PS FR-11 taxonomy. Essentials and Obligations are `is_essential=1`.
**tag**, **transaction_tag** +: reimbursable, tax-80C, etc.
**rule**: `id, matcher_json, action_json, priority, created_from_correction, active, created_at, hit_count+`.
Matchers: `merchant`, `description_regex`, `amount_range`, `account`, `payment_method`, `direction`, combinable with AND. Actions: `set_category`, `add_tags`, `mark_transfer`, `set_merchant`, `ignore`.
**transfer_group** +: `id, confidence, status (auto|confirmed|rejected), created_by`.
**budget**: as PS, plus `alert_threshold_pct+`.
**goal**, **goal_contribution** + (contributions as rows, not a field).
**recurring_series**: as PS, plus `origin (user|detected)+`, `direction+`, `cadence_days+`, `expected_window_days+` (for expected income, FR-20), `status` extended to `active|new|increased|decreased|missing|cancelled|unused`.
**recurring_member** + : `series_id, txn_id`.
**statement_balance** + : `account_id, as_of_date, balance_minor, source_row_id`. Taken from statement summaries and running-balance columns, used by reconciliation.
**review_item**: `id, kind, txn_ids_json, payload_json, suggested_resolution_json, priority, created_at, resolved_at, resolution_json`.
**change_set** + : `id, actor (user|rule|agent|import|system), summary, created_at, undone_at`.
**audit_log**: `id, change_set_id, entity, entity_id, field, old_value, new_value, actor, reason, at`. **Append-only**: SQLite triggers `RAISE(ABORT)` on any UPDATE or DELETE.
**query_receipt** + : `query_id, created_at, spec_json, sql_hash, result_digest, txn_ids_blob`. Backs "show me how"; pruned after 90 days.
**fx_rate** + , **preference** + (key/value agent memory: "Q3 means FY", default accounts), **llm_call** + (§14.5).
**txn_fts** + : FTS5 virtual table over `raw_description`, merchant name, and notes, for text search (FR-21). **(verify in P0: the bundled SQLCipher build includes FTS5.)**

**Indexes:** `(account_id, txn_date)`, `(category_id, txn_date)`, `(merchant_id, txn_date)`, `(txn_date)`, `(transfer_group_id)`, `(needs_review) WHERE needs_review = 1`, UNIQUE `(account_id, fingerprint)`, UNIQUE `source_row(import_source_id, row_index)`, UNIQUE `transaction(account_id, external_ref) WHERE external_ref IS NOT NULL`.

### 6.3 Invariants and how each is enforced

| # | Invariant (PS §7) | Enforced by |
|---|---|---|
| 1 | Opening balance + Σ amounts reconciles to the statement's closing balance | `analytics/reconcile.py` runs on every commit for each statement period; a mismatch creates a `reconciliation_mismatch` review item with the difference and the suspect rows (§7.7). `doctor` re-runs it for everything. |
| 2 | Transfers net to zero and are excluded from income and expense | `transfers.create_group()` refuses a group whose amounts don't sum to zero (same currency). Every aggregate is built through `analytics/query.py`, which adds `is_transfer = 0` unless the caller asks for transfers explicitly. A dedicated test (A3). |
| 3 | No two transactions share `(source, external_ref)` | UNIQUE index on `(account_id, external_ref)` + UNIQUE `(import_source_id, row_index)` on `source_row` + UNIQUE `(account_id, fingerprint)`. |
| 4 | A split's children sum exactly to the parent | `splits.split()` is the only write path for splits; it checks the sum in integer paise inside the same transaction. A trigger blocks direct updates of a child's amount. Checked again by `doctor`. |
| 5+ | Every write has an audit record | Repository functions take a `ChangeSet` argument and write the audit rows in the same transaction. There is no write path without one (enforced by function signatures and a test that scans `repo/` for raw UPDATE/INSERT outside these helpers). |
| 6+ | Amounts are never floats | `Money` type; SQLAlchemy `TypeDecorator` that rejects non-`int`; a CI grep test that fails on `float(` in `core/`, `db/`, `analytics/`. |

### 6.4 Migrations

Alembic, with migrations generated from `db/schema.py` and reviewed by hand. On start-up, if the schema is behind: **back up first**, then migrate. A failed migration restores the backup automatically.

---

## 7. Ingestion pipeline

### 7.1 Stages

```
file ─▶ detect ─▶ parse ─▶ normalise ─▶ fingerprint ─▶ classify ─▶ dedupe ─▶ transfer-pair ─▶ reconcile ─▶ PREVIEW
                                                                                                           │
                                                 commit_import(import_id, decisions) ◀── owner / agent ───┘
                                                 (one SQLite transaction + backup before + audit)
```

`import_file` runs everything up to **PREVIEW** against a **staging area** (in-memory structures keyed by `import_id`, held in the session) and **writes nothing to the ledger**. `commit_import` applies the preview plus the owner's decisions in one `BEGIN IMMEDIATE … COMMIT`. Any exception rolls back everything (NFR-6), and the `import_source` row is marked `failed` in a separate small transaction so the failure is visible.

**Import preview (returned to the agent and CLI):**

```
bank_c_2026-10.pdf → "Savings (bank C)"            period 01-Oct-2026 – 31-Oct-2026
  142 rows parsed · 0 parse errors
  118 new · 21 already in ledger (skipped) · 3 merged with SMS records (auto, ≥0.95)
  3 possible duplicates → review · 2 uncategorised → review
  4 transfers paired (₹62,000) · 1 possible transfer to an untracked account → review
  reconciliation: opening ₹41,220.15 + net −₹8,915.40 = ₹32,304.75 ✓ matches closing balance
```

### 7.2 Detection and mapping profiles (FR-1, NFR-8)

1. **File kind** from magic bytes (not the extension): PDF, XLSX (zip + `xl/`), XLS (OLE2), text.
2. **Password-protected PDF** → ask for the password. The owner can save it in the Keychain under that profile (`finassist:statement:<profile>`). It is never written to disk or logs.
3. **Header signature**: the normalised header row (lowercase, collapsed whitespace, sorted column names) → SHA-256. A known signature selects its stored `mapping_profile`.
4. **Unknown layout**: a guided mapping step. The CLI shows the first rows with proposed roles for each column, guessed by heuristics (date-like, amount-like, "Dr/Cr", "balance", narration). The owner confirms or edits once, and the result is saved as a new profile. **Fallback only, after the heuristics and with the owner's consent:** send the **header row and three rows with digits masked** to the LLM to suggest a mapping.
5. **Text PDFs** are handled by profile-specific table extractors (`pdfplumber` table settings or column x-ranges stored in the profile), plus a generic "date … description … amount … balance" line parser.

**Adding a new bank** means adding one TOML file in `ingest/banks/` (simple layouts) or one `Parser` subclass (odd layouts), plus a synthetic fixture and a test. `docs/adding-a-bank.md` walks through it.

```python
class Parser(Protocol):
    kind: ClassVar[str]

    def sniff(self, file: SourceFile) -> float: ...  # 0–1 confidence this parser fits
    def parse(self, file: SourceFile, profile: MappingProfile) -> ParseResult: ...


# ParseResult = rows: list[ParsedRow], statement_meta: StatementMeta | None, errors: list[RowError]
```

A `ParseResult` with **any** row error rejects the file, reporting the row number and why (PS §13 "clear parse failures, never partial commits"). The owner can fix the profile and re-run.

### 7.3 SMS and UPI exports (FR-2)

A library of regex templates in `parsers/sms.py`, one per message family (debit/credit alert, UPI, card spend, ATM, NACH/ECS). Each template extracts amount, direction, account last4, counterparty/VPA, date, reference, and available balance. Last4 maps to an account; an unknown last4 goes to review. Unparseable messages are listed and skipped, never guessed. OTPs and promotional messages are detected and **dropped without being stored**.

### 7.4 Idempotency (FR-7, A2)

- **File level:** a file whose SHA-256 is already `committed` → "already imported, nothing to do".
- **Row level:** `fingerprint = sha256(account_id | txn_date | amount_minor | normalised raw_description | running_balance or '' | occurrence_index)`, where `occurrence_index` numbers identical `(date, amount, description)` rows *within the file*, so two genuine ₹20 tea purchases on the same day both survive. Overlapping statements (1–31 Oct and 15 Oct–15 Nov) produce identical fingerprints for the shared rows, which are skipped. *Assumption:* statements cover whole days. **(verify with real layouts in P1)**
- **Reference level:** UNIQUE `(account_id, external_ref)`.

### 7.5 Cross-source deduplication (FR-6)

This is a different problem from idempotency: the same real transaction arrives as an SMS and later as a statement row, with different text and possibly different dates.

**Candidates:** same account (or matching last4), same direction, **exactly equal `amount_minor`**, `|date diff| ≤ 3 days` (configurable), and not already linked to a row from the same kind of source.

**Score** (0–1):

| Signal | Effect |
|---|---|
| `external_ref` equal (UTR, UPI ref) | score = 0.99, decisive |
| date difference | 0 days +0.35, 1 day +0.25, 2–3 days +0.10 |
| merchant similarity (rapidfuzz `token_set_ratio` on normalised merchant) | up to +0.40 |
| payment method agrees | +0.10 |
| more than one candidate for the same row | −0.30 (ambiguous) |

**Bands:** ≥ 0.90 → auto-merge (statement row becomes primary, SMS row attached as `merged_duplicate`, logged, undoable). 0.60–0.90 → `possible_duplicate` review item. < 0.60 → separate transactions. Thresholds are in config and are tuned against the labelled fixture in P1.

**Same-day genuine repeats** (two identical charges at one merchant) are **not** deduplicated. The anomaly detector flags them as "possible double charge" instead (FR-26), because only the owner can tell.

### 7.6 Transfer detection (FR-12, A3)

Runs after dedupe, over the new rows and the existing unpaired rows of the last 10 days.

1. **Candidate pairs:** a debit on account A and a credit on account B (both the owner's), `amount_A = −amount_B` (same currency), `0 ≤ date_B − date_A ≤ 3` days.
2. **Hints raise the score:** narration contains `CC PAYMENT`, `CREDIT CARD`, the card's last4, the owner's own VPAs (config `self_vpas`), "SELF", the account holder's own name in a NEFT/IMPS narration, `NACH`/`BSE`/`CAMS`/`KFIN` (investment).
3. **One-to-one assignment:** pairs taken greedily by score so no row is paired twice.
4. **Outcome:** score ≥ 0.85 → linked as `transfer_group` (status `auto`); 0.5–0.85 → review. **A transfer-like debit with no partner** (card payment with no card statement imported, SIP to a fund platform) → review item *"possible transfer to an untracked account: mark as transfer / expense / investment"*. The answer is learned as a rule.
5. **Investment contributions** (SIP, stock purchases) are categorised with `kind = investment` and excluded from expense even without a matching investment account.

### 7.7 Reconciliation (invariant 1, A1)

For each account and each imported statement period:

- **Closing check:** `statement_opening + Σ(amount_minor of committed txns in period, incl. transfers, excl. split children since the parent carries the amount) == statement_closing`, exact to the paisa.
- **Running-balance chain** (when the statement has a balance column): every row should satisfy `balance_i = balance_{i−1} + amount_i`. The first row that breaks the chain points to the missing, duplicated, or misread row, so a mismatch says *where*, not only *how much*.
- **Mismatch** → `reconciliation_mismatch` review item with the difference and the suspect rows. The import still commits (the rows are real), but the account is marked **unreconciled** and every answer that touches it says so.
- Accounts fed only by SMS or manual entry can't be reconciled; they are labelled `unreconciled (no statement)` in coverage.

### 7.8 Receipts (FR-3, S6)

Image or PDF → `parsers/receipt.py`:

- **Cloud mode:** one Haiku 5.5 vision call with structured output `{merchant, date, total, tax, currency, line_items[], payment_hint}`. The image is sent **only after a per-receipt consent prompt**, or after a standing opt-in in config (§14). **(verify in P4 that Haiku 5.5 accepts image input, via the Models API `capabilities` field; otherwise fall back to Opus 5.5 for receipts.)**
- **Local mode:** Tesseract OCR + total/date/merchant heuristics, lower accuracy, always confirmed by hand.
- **Always:** the extraction is shown for confirmation ("₹1,240 · Café X · 21 Nov · Eating out · cash? [Y/n/edit]") before it is committed. The total is checked against the sum of line items + tax, and a mismatch is shown.
- If the receipt matches an existing card/UPI transaction (amount ±0, date ±1), it is **attached** to that transaction (adding line items and tax) instead of creating a duplicate.

### 7.9 Manual entry and scheduled entries (FR-4, FR-5)

- **Conversational:** "spent 450 cash on auto today" → the LLM calls `add_transaction` with structured fields (amount "450", account "cash", description "auto", date "today"). Code resolves the date and parses the amount, and the result is echoed back before it is saved.
- **Form:** `finassist add` (prompt_toolkit form) and the web form in P4.
- **Scheduled:** `recurring_series` rows with `origin = user` (salary on the 1st, rent on the 5th). They create *expectations*, not transactions. When the real row arrives it is matched to the series. A missed expectation triggers FR-20 (§9.7).

---

## 8. Normalisation and classification

### 8.1 Merchant normalisation (FR-10)

Deterministic cleaning, then lookup:

1. **Structural parse** of known narration formats: `UPI/<ref>/<payee name>/<vpa>/<bank>/…`, `POS <terminal> <merchant> <city>`, `NEFT-<utr>-<name>`, `ACH D- <mandate>`, `VIN/<merchant>/…`. The counterparty and VPA come out as separate fields.
2. **Cleaning:** uppercase → strip channel prefixes (`POS`, `UPI`, `ECOM`, `VIN`, `IMPS`, `NEFT`, `ACH`), digit runs of 4+, `*ORDER`-style suffixes, trailing city/country codes, punctuation → collapse whitespace → `alias_key`.
3. **Lookup:** exact `merchant_alias.alias_key` → VPA handle map (`swiggy@…` → Swiggy) → fuzzy (`rapidfuzz` ≥ 92 against canonical names and aliases). A fuzzy hit is used for the current row but saved as an alias only when the owner confirms.
4. **Seed table:** ~150 common Indian merchants and billers (food delivery, ride-hailing, e-commerce, telecom, utilities, OTT, fuel, pharmacies) with default categories. This is public information.
5. **No match:** the merchant stays null, the cleaned string is shown, and classification continues (§8.2).

`POS 1234 SWIGGY*ORDER BLR` → strip `POS`, `1234`, `*ORDER`, `BLR` → `SWIGGY` → alias → **Swiggy**.

### 8.2 Classification pipeline (FR-13)

The stages run in order, and the first that decides wins. The stage name is written to `classification_source` and the rule or heuristic id to `classification_ref`.

| Order | Stage | Confidence | Notes |
|---|---|---|---|
| 0 | `user_locked` row | 1.0 | Never touched by rules or re-runs. |
| 1 | Transfer detector (§7.6) | from score | Sets `is_transfer` and category "Transfer between my accounts". |
| 2 | **User rules** by priority (rules learned from corrections rank highest) | 1.0 | |
| 3 | **Merchant map** (merchant's default category) | 0.95 | |
| 4 | **Heuristics** (regex library + MCC for card rows + payment-method patterns, e.g. `SAL|SALARY` credit → Salary; `ATM WDL` → transfer to Cash; `NACH.*(BSE|CAMS|KFIN)` → SIP; `INT\.? ?(PD|CR)` → Interest) | 0.80–0.90 per heuristic | Each heuristic has a fixed confidence, tuned on the fixture. |
| 5 | **LLM residue** (Haiku 5.5, §8.3) | model-reported, capped at 0.85 | Only in cloud or local mode; skipped in `off` mode. |
| 6 | Nothing decided | — | `category = Uncategorised`, `needs_review = 1`. |

**`needs_review` threshold:** confidence < 0.75 → `needs_review = 1`, still showing the best guess as the suggestion. The threshold is tuned in P1 so the labelled fixture reaches **≥ 90% correct among auto-categorised rows, with the rest in review** (A4).

### 8.3 LLM residue classification

- **Batching:** up to 50 rows per request, rows that need it only.
- **Payload per row (minimised):** a short row id, cleaned description (redacted, §14.3), direction, amount **bucket** (`<500`, `500–2k`, `2k–10k`, `10k–50k`, `>50k`; enough to tell a ₹200 meal from a ₹20,000 rent), payment method. **No** dates, balances, account identifiers, or exact amounts.
- **Request:** frozen system prompt containing the taxonomy (cached), `output_config.format` with a JSON Schema whose `category` is an **enum of the leaf category ids**, effort `low`. Response per row: `{id, category_id, merchant_guess, confidence}`.
- **Use of the response:** the category becomes a suggestion with `classification_source = model`. `merchant_guess` is **never** saved as an alias automatically; it is offered in review, and confirming it creates the alias.
- **Calibration caveat:** a model's self-reported confidence is not calibrated. The cap (0.85) and the review threshold are tuned against the fixture, not trusted as-is.

### 8.4 Learning from corrections (FR-14, S9, A5)

"Zomato is Food, not Shopping" →

1. The agent calls `create_rule(matcher={merchant: "Zomato"}, action={set_category: "Lifestyle/Eating out"}, from_correction=true)`. Code resolves the names to ids and returns an error if a name is ambiguous.
2. The rule is saved with priority above the merchant map. The merchant's `default_category_id` is updated too, so other code paths agree.
3. `reclassify(scope={rule_id}, dry_run=true)` returns "38 past transactions match (₹14,220): 36 currently Shopping, 2 Uncategorised. `user_locked` rows excluded: 1".
4. This is a **bulk write**, so the harness asks the owner to confirm (§10.4). On yes: one `change_set`, audit rows for each change, undoable as a whole.
5. Future imports hit the rule at stage 2, so the owner is never asked again.

Rules that start overlapping or contradicting each other are reported by `doctor` and shown in `list_rules`.

### 8.5 Splits (FR-15) and refunds (FR-16)

- **Split:** `split_transaction(txn_id, parts=[{category, amount}…])`. Amounts are parsed to paise, and Σ parts must equal the parent exactly (else an error that names the difference). The parent becomes `is_split_parent = 1` and the children carry `split_parent_id`. Aggregates count the children only.
- **Refund linking:** for a credit at a merchant, look back 90 days for debits at the same merchant whose amount ≥ the refund. Exact amount match → linked automatically (confidence 0.9). Partial refunds or several candidates → review. A linked refund takes the original's category (A-2).

---

## 9. Analytics

### 9.1 `query_transactions`: the one aggregation primitive (FR-21)

Every numeric answer, report, budget, and brief goes through this function.

```python
class TxnFilter(BaseModel):
    period: ResolvedPeriod  # explicit start/end dates, always resolved by code
    accounts: list[AccountRef] | None = None
    categories: list[CategoryRef] | None = None  # a parent expands to all its children
    exclude_categories: list[CategoryRef] | None = None  # "exclude rent"
    kinds: list[Kind] | None = None  # income / expense / investment / transfer
    merchants: list[MerchantRef] | None = None
    tags: list[str] | None = None
    text: str | None = None  # FTS5
    amount_min: MoneyStr | None = None
    amount_max: MoneyStr | None = None
    direction: Literal["debit", "credit"] | None = None
    payment_methods: list[PaymentMethod] | None = None
    include_transfers: bool = False  # default excludes transfers (invariant 2)
    only_needs_review: bool = False


class QuerySpec(BaseModel):
    filter: TxnFilter
    group_by: list[
        Literal[
            "month",
            "week",
            "day",
            "fy",
            "category",
            "parent_category",
            "merchant",
            "account",
            "tag",
            "payment_method",
        ]
    ] = []
    metrics: list[Literal["sum", "count", "avg", "min", "max", "median"]] = ["sum"]
    order_by: Literal["value_desc", "value_asc", "key"] = "value_desc"
    limit: int | None = None  # top-N


class QueryResult(BaseModel):
    query_id: str
    rows: list[ResultRow]  # each value as {minor, display}
    totals: ResultRow
    coverage: CoverageReport  # per account: covered dates, gaps, reconciled?
    quality: QualityReport  # needs_review count and amount in scope; uncategorised amount
    scope_text: str  # "1–31 Oct 2026 · all accounts · excl. transfers"
```

Implementation notes:

- The SQL is built with SQLAlchemy Core from the spec. No string SQL, and no SQL ever comes from the model.
- `sum`, `count`, `min`, `max` are done in SQL. `avg` and `median` are computed in Python with `Decimal` and integers.
- Before returning, the result is saved as a `query_receipt` (spec, SQL hash, contributing txn ids), so "show me how" gives the exact same answer later.
- `coverage` makes A8 decidable: if any account in scope has no data for part of the period, `coverage.status` is `partial` or `none`, and the agent must say so (§10.3).

### 9.2 Metric definitions (FR-22)

All metrics exclude transfers, split parents, and soft-deleted rows. "Expense" includes linked refunds (which reduce it).

| Metric | Definition |
|---|---|
| Income | Σ amount where `category.kind = income` |
| Expense | −Σ amount where `category.kind = expense` |
| Investment contributions | −Σ amount where `category.kind = investment` |
| Savings | Income − Expense |
| **Savings rate** | (Income − Expense) / Income; "n/a" when income = 0 |
| Investment rate | Investment contributions / Income |
| **Net cash flow** | Income − Expense − Investment contributions (what is left in liquid accounts) |
| Essential / discretionary | Expense split by `category.is_essential` (Essentials + Obligations = essential; Lifestyle = discretionary) |
| Category share | category expense / total expense |
| Average daily spend | Expense / days in period (days **elapsed** for the current period) |
| Month-end projection | MTD expense + known recurring debits due before month end + (trailing-90-day median daily discretionary spend × days left) |
| Largest outliers | top-N expenses by amount, and rows flagged by §9.10 |

Uncategorised rows count as expense if they are debits, and the result reports how much of the total they make up (contract item 3).

### 9.3 Periods

`resolve_period(text | structured)` turns "October", "last month", "Q3", "FY24-25", "last 6 months" into explicit dates, using today's date in the configured zone. Anything it can't resolve (e.g. "since Diwali") returns an error, and the agent asks for dates. A month with no year means **the most recent past occurrence** ("October" asked in November 2026 → October 2026). The model never does date arithmetic; it passes text or structured fields and gets explicit dates back.

### 9.4 Comparisons and "where did the increase come from" (FR-21)

`compare_periods(a, b, filter, dimension="merchant"|"category", top_n=5)` returns both totals, the delta, % change, and the **decomposition by dimension**: for each merchant or category, its delta, sorted by contribution to the total change, plus new merchants (present in a, absent in b). For S2 it returns the five largest contributing transactions in period a.

### 9.5 Budgets (FR-17, S3)

- `set_budget(category | overall, amount, rollover?, start_month)`.
- `get_budget_status(month)` per budget: spent, remaining, % spent, % of month elapsed, **burn index = % spent / % elapsed**, projected month-end (same formula as §9.2, limited to the category), rollover carried in.
- **Alert rule:** % spent − % elapsed ≥ 15 points, or projected month-end > 100%, or % spent ≥ `alert_threshold_pct` (default 80%). S3: on day 19 of 30 (63% elapsed) at 84% spent, the difference is 21 points → *"Groceries: ₹10,080 of ₹12,000 (84%) with 11 days left; at this pace ≈ ₹15,900 by month end."*
- **Rollover:** when on, unspent budget (never overspend) carries into the next month only.

### 9.6 Goals (FR-18)

`required_monthly = (target − saved so far) / months left`, rounded up to the rupee. Progress = contributions (rows linked to the goal or the balance of a linked account). Projected hit date = remaining ÷ trailing-3-month average contribution. A goal with no contributions yet says "no projection" instead of a date.

### 9.7 Recurring detection (FR-19, S4) and expected income (FR-20, S5)

**Detection** (`detect_recurring`, re-run after each commit):

1. Group by merchant (or normalised counterparty) and direction.
2. Sort by date; compute the gaps between occurrences; match a cadence: weekly 7±1, monthly 30±3 (or same day of month ±3), quarterly 91±7, yearly 365±10.
3. Require ≥ 3 occurrences (yearly: ≥ 2, then confirmed by the owner). Amount stability: median absolute deviation / median ≤ 10%, or a fixed amount.
4. Upsert the `recurring_series`: `expected_amount_minor` = median of the last 3, `amount_tolerance` = max(₹10, 5%), `next_expected_date`.

**Status changes** (each produces a review item or alert; each fires once):

| Status | Trigger |
|---|---|
| `new` | series confirmed within the last 45 days |
| `increased` / `decreased` | the latest member's amount is outside `expected ± tolerance` (S4: "Netflix went from ₹649 to ₹799, +23%, +₹1,800 a year") |
| `missing` | today > `next_expected_date + grace` (grace = 3 days monthly, 7 days yearly) and no member arrived |
| `cancelled` | missing for 2 cycles, or the owner says so |
| `unused` | set by the owner only (A-5) |

`list_recurring` returns each series with cadence, amount, next date, and **annualised cost** (amount × cycles per year, in integer paise).

**Expected income** (S5) uses the same series with `direction = credit` and `expected_window_days` (e.g. salary 1st–2nd). On open, `income_watch.check(today)` flags any income series past `window_end + grace` (grace counts weekdays only; Indian bank holidays aren't modelled in v1). S5: expected by the 2nd, grace 2 weekdays, checked on the 5th → *"Salary (usually ₹1,48,000 by the 2nd) hasn't arrived as of 5 Nov."*

### 9.8 Cash-flow forecast (FR-23)

`forecast_cashflow(horizon_days ∈ {30, 60, 90})`:

1. **Start:** current balance of liquid accounts (savings, current, cash, wallet) minus card dues that fall due inside the horizon.
2. **Known flows:** every active `recurring_series` occurrence in the horizon (salary, rent, EMI, SIP, subscriptions, insurance), at expected amount.
3. **Variable spend:** discretionary and non-recurring essential spend modelled by **resampling whole historical months** (last 6–12 months, scaled to days). 1,000 simulated paths in integer paise → **P10 / P50 / P90** balance per day.
4. **Output:** a range for the end balance and the lowest balance with its date, plus an **assumption list** (which series were included, how many months of history, anything excluded or missing, e.g. "no salary series found"). Less than 3 months of history → the result is marked low-confidence and says why.

### 9.9 Affordability (FR-24, S7)

`check_affordability(amount, when, mode = one_off | emi{months, rate?})` adds the purchase to the forecast and reports:

- lowest projected balance (P10 and P50) with and without the purchase, against a **safety buffer** (config; default = one month of median essential expense);
- effect on the relevant category budget in that month;
- delay to each active goal's projected hit date.

**Verdict, by fixed rules:** `comfortable` if P10 minimum stays above the buffer; `tight` if P50 stays above the buffer but P10 doesn't; `not_advisable` if P50 falls below it. The verdict always comes with the numbers and assumptions. It is not investment advice; it is arithmetic about the owner's own cash flow.

### 9.10 Anomalies (FR-26)

| Detector | Rule |
|---|---|
| Amount outlier for merchant or category | robust z = (x − median) / (1.4826 × MAD) over 12 months, ≥ 5 data points, |z| > 3.5 |
| First-time large merchant | first transaction at a merchant and amount > P95 of all expenses |
| Possible double charge | same merchant, same amount, same day, distinct references |
| Category spike | month-to-date category spend > 1.5 × trailing-6-month median *at the same day of month* |
| Many small debits | count of debits < ₹200 in a week > trailing P90 weekly count |

Each anomaly produces a short, explained alert. They are suppressed once dismissed.

### 9.11 Briefs (FR-27)

`briefs.weekly()` and `briefs.month_end()` are **templates filled from the tools above** (A-14), at most 10 lines, ordered by what changed: budget alerts → recurring changes → missing income → anomalies → savings-rate change → one suggested action (e.g. "3 items in review, ~1 min").

---

## 10. Agent

### 10.1 Loop

```
turn(user_text):
  messages += user_text
  for i in 1..MAX_ITER (8):
      resp = provider.create(system=FROZEN_PROMPT, tools=REGISTRY, messages)
      if resp.stop_reason == "refusal": render tool-free fallback message; break
      if resp has tool_use blocks:
          results = []
          for call in tool_use blocks:                # run in parallel when all are read-only
              args = validate(call.input)             # pydantic; on failure → is_error result
              decision = policy.gate(tool, args)      # §10.4
              out = execute(tool, args) if allowed else decision.as_result()
              results.append(redact(out.for_model))   # for_user kept locally
          messages += all results in ONE user message
          continue
      text = resp.final_text
      ok, problems = grounding.check(text, results_this_turn)    # §10.5
      if not ok and not retried: messages += system note listing problems; retried = True; continue
      render(text, scope footer, /how handles, for_user tables, warnings)
      break
  if MAX_ITER reached: render "couldn't finish" + the last tool results as tables
```

The loop is written against the `LLMProvider` protocol (`create(system, tools, messages, model_role) → Response`). It has three implementations: Anthropic, local (Ollama), and a scripted fake for tests.

### 10.2 Tool surface

Each tool has typed pydantic input and output, an **effect** tag, and its own unit tests. Every result has two parts: **`for_model`** (compact, redacted, with display strings) and **`for_user`** (full tables, rendered by the CLI and never sent to the LLM). For example, the model sees "47 transactions, top 5 shown"; the owner sees all 47.

| Tool | Effect | Purpose / notes |
|---|---|---|
| `resolve_period(text)` | read | §9.3 |
| `list_accounts()` / `get_data_coverage()` | read | accounts, nicknames, coverage, reconciliation state |
| `query_transactions(spec)` | read | §9.1, the only aggregation primitive |
| `search_transactions(filter, limit)` | read | row-level listing (text, amount range, tags) |
| `get_summary(period, dimensions)` | read | income / expense / net / savings rate (built on `query_transactions`) |
| `compare_periods(a, b, filter, dimension, top_n)` | read | §9.4 |
| `explain(query_id)` | read | FR-28: filter + SQL in words + contributing transactions (in `for_user`) |
| `get_budget_status(month)` | read | §9.5 |
| `set_budget(...)` | write | single row |
| `get_goal_status()` / `set_goal(...)` | read / write | §9.6 |
| `list_recurring(status?)` / `detect_recurring()` | read / write | §9.7 (detection writes series) |
| `forecast_cashflow(horizon_days)` | read | §9.8 |
| `check_affordability(amount, when, mode)` | read | §9.9 |
| `find_anomalies(period)` | read | §9.10 |
| `get_brief(kind)` | read | §9.11 |
| `import_file(path, account?, options)` | read* | stages only; *writes nothing to the ledger* |
| `commit_import(import_id, decisions)` | **bulk** | always confirmed by the owner |
| `add_transaction(...)` | write | echo before save |
| `update_transaction(id, fields)` | write | sets `user_locked` when the category changes |
| `split_transaction(id, parts)` | write | §8.5 |
| `create_rule(...)` | write | §8.4 |
| `reclassify(scope, dry_run)` | **bulk** | dry run is free; applying is confirmed |
| `list_review_queue(limit)` / `resolve_review_item(id, resolution)` | read / write | §12.2 |
| `generate_report(kind, period, format)` | read (writes a file to the reports folder) | §13 |
| `export_data(format, filter)` | read (writes a file) | §13 |
| `undo(change_set_id?)` | **bulk** | reverses a change set |

That is about 25 tools. If tool-selection accuracy suffers on the benchmark, the tools are grouped and less-used ones are loaded on demand (tool search). That is a tuning step, not a design change.

### 10.3 Behaviour contract (PS §8): prompt + code

| # | Contract | In the system prompt | Enforced in code |
|---|---|---|---|
| 1 | Numbers only from tool results | "Copy figures from tool `display` fields; never calculate." | Grounding check (§10.5); tools return deltas and percentages ready-made (A-13). |
| 2 | State period and filters | "Say the period and filters." | The CLI appends the **scope footer** built from the `QueryResult.scope_text` of the queries used. It is always there, whatever the model wrote. |
| 3 | Quantify uncertainty | "Mention unreviewed or uncategorised amounts and coverage gaps." | `quality` and `coverage` are in every result. If coverage is `none` for the question's period, the harness **replaces** any figures with a coverage message (A8). `partial` adds a warning line. |
| 4 | Confirm before bulk writes | "Bulk changes are previewed; the owner confirms." | Policy gate (§10.4): the model cannot confirm. |
| 5 | No investment or product advice | Hard rule with redirect wording. | No tool exposes products or prices. A scenario eval checks for refusals. |
| 6 | Short answers | "One number + ≤ 2 lines of insight." | `max_tokens` cap for answer turns; the renderer folds long tool tables into `for_user`. |

### 10.4 Write policy and confirmation

| Effect | Behaviour |
|---|---|
| `read` | runs freely |
| `write` (one row, owner's explicit request) | runs; result echoed; one `change_set`; undoable |
| `bulk` | the tool runs in **dry-run** first and returns a preview + `confirmation_token = hash(planned change set)`. The harness **pauses the loop**, shows the preview, and asks the owner **in the terminal** (`Apply 38 changes? [y/N]`). The answer goes back to the model as the tool result. The model has no way to supply the confirmation. A token is single-use and is invalidated if the data changes before it is applied. |

Bulk thresholds: anything touching > 1 existing row, any import commit, any undo.

### 10.5 Numeric grounding check

After the model's final text:

1. Extract numeric tokens: ₹ amounts, bare numbers with Indian or Western grouping, percentages, "1.2 lakh"-style values.
2. Allowed set = every numeric value in this turn's tool results (their `display` strings and equivalent normalised values) + earlier turns' results the text refers to + harmless integers (days of month 1–31, years 2000–2100, counts ≤ 10 when they appear in results).
3. Any token outside the allowed set → one retry with a system note: *"These figures are not in tool output: ₹2,310.50, 38%. Call a tool or remove them."*
4. Second failure → the prose is dropped and the CLI shows the tool results as a table with "(the assistant's summary was withheld because it contained unverified numbers)".

This check is what makes A7 achievable: the benchmark compares the figures in the reply against direct SQL.

### 10.6 Multi-turn context (FR-33)

- The full conversation, including tool calls and their arguments, stays in the message history for the session, so "and the month before?" lets the model re-issue the last `query_transactions` with the period shifted.
- `session.py` keeps the **last query spec** and adds a short session-state note (*"last query: Eating out, 1–31 Oct 2026, by merchant"*) as a mid-conversation system message. It doesn't break the cached prefix, and it keeps follow-ups like "exclude rent" or "break that down by merchant" anchored.
- Preferences learned in conversation ("Q3 means FY", "my reimbursable tag is `office`") are saved to `preference` through a small write tool and loaded into the session note.

### 10.7 Clarifying questions (FR-32)

The system prompt gives the rule: ask only when the possible readings would give **materially different figures** (e.g. "food" = Groceries vs Eating out vs both). Otherwise state the assumption ("I've counted Groceries + Eating out as food") and answer. Defaults that are fixed in code (quarters, months without a year) are never asked about; they are stated.

### 10.8 Deterministic commands (no LLM)

Every common question has a slash command that calls the same tools directly: `/spend oct`, `/compare oct sep --category "Eating out"`, `/budget`, `/recurring`, `/review`, `/how q_7f3a`, `/brief`, `/undo`. They are how `off` mode works, how a model outage is survived, and how the scenario tests run cheaply.

---

## 11. LLM usage, model tiers, and cost

> **Updated 2026-11-23:** the owner chose **Gemini** as the default provider. §11.1–§11.3 describe the Anthropic option, which stays available (`llm.provider = "anthropic"`); §11.4 covers Gemini.

### 11.1 Roles

| Role | Model (cloud) | Settings | Local mode |
|---|---|---|---|
| Conversation / analysis | `claude-opus-5-5` | adaptive thinking (default; can't be disabled on this model), `output_config.effort = "medium"` to start, tuned per route on the benchmark; streaming; prompt caching on tools + system; `strict: true` tool schemas; `tool_choice: auto` (forced tool choice isn't supported on this model) | tool-calling model via Ollama, chosen by A7 score |
| Classification residue | `claude-haiku-5-5` | structured output (`output_config.format`, enum of category ids), effort `low`, batched 50 rows | same local model, or rules-only |
| Receipt extraction | `claude-haiku-5-5` (vision, verify in P4) | structured output | Tesseract OCR + heuristics |
| Unknown-layout mapping hint | `claude-haiku-5-5` | header + 3 digit-masked rows only | heuristics only |

The Opus 5.5 calls use server-side refusal fallbacks (`fallbacks: "default"` with the `server-side-fallback-2026-07-01` beta) and always check `stop_reason` before reading content. Refusals are unlikely in this domain, but a refusal must be handled, not crash the loop.

The model IDs live in config (`llm.models.chat`, `llm.models.classify`). Moving chat to `claude-sonnet-5-5` ($2 / $10 per million tokens, roughly half the chat cost) is a one-line change if the benchmark shows equal quality. That is the owner's call.

### 11.2 Prices used (Anthropic first-party, per million tokens)

| Model | Input | Output | Cache read |
|---|---|---|---|
| Opus 5.5 | $4.00 | $20.00 | $0.20 |
| Haiku 5.5 | $0.10 | $0.50 | — |

### 11.3 Monthly cost estimate (~500 transactions/month)

Assumptions: rules handle ~70% of rows in month 1 and ~95% once rules have been learned; 20 receipts a month; 120 questions a month (about 4 a day); about 3 model calls per question; ~7k-token frozen prefix (system prompt + tool schemas); cache writes priced at 1.25× input; half of questions start a fresh cache because the 5-minute cache has expired.

| Item | Calculation | Monthly |
|---|---|---|
| Chat: cached prefix reads | 120 × 3 × 7k × $0.20/M | $0.50 |
| Chat: cache writes | 60 × 7k × $5/M | $2.10 |
| Chat: uncached input (conversation + tool results) | 120 × ~7.5k × $4/M | $3.60 |
| Chat: output incl. thinking | 120 × ~1.5k × $20/M | $3.60 |
| Classification (steady state) | ~3 batches × (5k in, 2k out) | < $0.01 |
| Receipts | 20 × (~2k in, 0.6k out) | ≈ $0.01 |
| Briefs | templates, no LLM | $0 |
| **Total** | | **≈ $10 / month** (range $5–15 depending on how much the owner asks) |

One-off backfill of 24 months (~12,000 rows, ~40% to the LLM) ≈ 100 Haiku batches ≈ **$0.15**.

Every call's token counts are logged locally (§14.5). `finassist doctor --costs` prints the real monthly figure, so this estimate gets replaced by measurement after the first month.

---

### 11.4 Gemini (default provider)

| Role | Model | Settings |
|---|---|---|
| Conversation / analysis | `gemini-3.8-flash` (stable) | `models.generate_content`; tools declared with `parameters_json_schema`; **automatic function calling disabled**, so every call passes the policy gate, redaction and grounding check; `thinking_level` from `llm.effort` (medium); `store = false`; the model's own `Content` is kept unchanged in history, so thought signatures round-trip |
| Classification residue, receipts (P2/P4) | `gemini-3.5-flash-lite` (stable) | structured JSON output; minimal thinking |
| Refusals | — | finish reasons `SAFETY`, `PROHIBITED_CONTENT`, `BLOCKLIST`, `SPII`, `RECITATION` and a blocked prompt are reported as a refusal |

**Data-use gate.** On Gemini's free tier Google may use requests to improve its products, and human reviewers may read them. The real ledger is therefore sent only after the owner sets `llm.gemini_paid_tier = true`, confirming a billing-enabled project. The demo ledger (synthetic) is exempt. Details in PRIVACY.md.

**Prices** (paid tier, per million tokens, Google's pricing page read 2026-11-23):

| Model | Input | Output (incl. thinking) | Cache read |
|---|---|---|---|
| `gemini-3.8-flash` | $0.75 until 31 Dec 2026, then $1.50 | $3.75, then $7.50 | $0.075, then $0.15 |
| `gemini-3.5-flash-lite` | $0.30 | $2.50 | $0.03 |

**Monthly estimate** (same usage assumptions as §11.3; implicit caching ignored, so this is an upper bound):

| Item | Calculation | Monthly |
|---|---|---|
| Chat input | 120 questions × 3 calls × ~9.5k tokens × $0.75/M | $2.57 |
| Chat output incl. thinking | 120 × ~1.5k × $3.75/M | $0.68 |
| Classification (steady state, P2) | ~3 batches × (5k in, 2k out) on Flash-Lite | ≈ $0.02 |
| Receipts (P4) | 20 × (~2k in, 0.6k out) | ≈ $0.04 |
| **Total** | | **≈ $3.30 / month now; ≈ $6.60 after the January 2027 price change** |

Actual usage is logged per call (`llm_call`), so measurement replaces this estimate after the first month.

## 12. Interaction surfaces

### 12.1 CLI

```
finassist init                       # data folder, Keychain key, encrypted DB, seed taxonomy and merchants
finassist demo [--reset]             # separate demo DB seeded with synthetic data (PS §9 --demo)
finassist chat                       # default command; conversational REPL
finassist import <path…> [--account NAME] [--yes-if-clean]
finassist review                     # review queue
finassist add                        # manual entry form
finassist report <kind> --period P --format md|pdf|xlsx
finassist export --format csv|json|xlsx [--filter …]
finassist backup | restore <file> | undo [change_set] | doctor [--costs]
finassist config set-api-key         # stores the Anthropic API key in the Keychain
```

`finassist --demo <command>` runs any command against the demo database. On opening chat, the agent shows the "since last time" alerts (A-12) before the prompt.

### 12.2 Review queue (FR-31)

Item kinds: `uncategorised`, `low_confidence`, `possible_duplicate`, `possible_transfer`, `untracked_transfer`, `missing_income`, `recurring_change`, `reconciliation_mismatch`, `refund_link`, `untracked_cash`, `merchant_alias_suggestion`.

- **Ranking:** priority = amount weight (log scale) × uncertainty (1 − confidence) × kind weight (reconciliation and missing income first) + age bonus. Each session shows at most **15 items** (config), aiming for under 5 minutes a week (PS §14.5).
- **One keystroke:** `Enter` accepts the suggestion · `1–9` picks an alternative · `s` skips · `x` dismisses · `r` "accept and make a rule" (for categorisation, applies to all matching rows after the usual confirmation) · `?` shows the source rows.
- Each resolution is a `change_set`, undoable.

### 12.3 Web surface (P4, FR-30)

FastAPI + Jinja2 + HTMX (served from the package, no CDN), bound to `127.0.0.1` only, Host-header check against DNS rebinding, CSRF token on forms, random per-launch session token in the URL printed by `finassist web`. Pages: this month at a glance, budgets, recurring list, review queue. It calls the same `analytics/` and `review/` functions as the CLI; the web layer has no logic of its own. Chat in the browser is out of scope for v1 (the CLI is the chat).

---

## 13. Reports and exports

| Report (FR-25) | Contents |
|---|---|
| Monthly statement | income, expense, net, savings rate, category breakdown, budgets vs actual, recurring charges, anomalies, review status |
| Annual summary (S8) | the above per month + month-by-month trend, top merchants, FY and calendar totals |
| Category deep-dive | trend, merchants, largest transactions, recurring members |
| Merchant report | history, cadence, total and average spend |
| Tax / reimbursement export (S10) | filtered by tag and period, with notes and provenance, as an XLSX/CSV ready to send |

- All data comes from `query_transactions`. Each report footer lists the query ids used, so any figure in a report can be explained.
- Charts: trend line, category breakdown (horizontal bars, not pies), income vs expense bars. matplotlib Agg, rendered offline.
- **Markdown** via Jinja2; **PDF** via fpdf2 with bundled Noto Sans; **Excel** via XlsxWriter with an Indian number format (`[>=10000000]##\,##\,##\,##0.00;[>=100000]##\,##\,##0.00;##,##0.00`) and native charts, so the numbers stay numbers.
- Reports go to `~/Documents/finassist/reports/` (config). A report is **not** encrypted once written; the CLI says so on export.
- **Export (NFR-12):** whole ledger or filtered, as CSV (one file per table), JSON, or XLSX, with amounts as integer paise **and** a decimal string column, so nothing is lost and nothing needs re-parsing.

---

## 14. Privacy and security

### 14.1 Threat model, briefly

Protects against: a lost or stolen laptop, other local users, accidental sharing of files and logs, an LLM provider seeing more than needed, a dependency calling home, and the agent being talked into bulk changes. Does **not** protect against malware running as the owner while the app is unlocked (out of scope for a local app).

### 14.2 What leaves the machine (basis of `PRIVACY.md`, A9)

| Feature | `cloud` mode | `local` mode | `off` mode |
|---|---|---|---|
| Import, parse, dedupe, transfers, reconcile | nothing | nothing | nothing |
| Unknown-layout mapping hint (optional) | header row + 3 rows, digits masked → Anthropic | nothing | nothing |
| Classification residue | per row: cleaned and redacted description, direction, amount bucket, payment method → Anthropic | localhost only | nothing (→ review) |
| Receipt extraction | receipt image, after per-receipt consent → Anthropic | localhost only | manual entry |
| Chat | the question as typed; `for_model` tool results (merchant names, display amounts, dates, category names, account **nicknames**, pseudonymised people) → Anthropic | localhost only | slash commands only |
| Briefs, reports, exports, web UI | nothing | nothing | nothing |
| Anything else (telemetry, update checks, crash reports) | **nothing** | nothing | nothing |

The only remote host in the whole system is the configured provider's API host (`generativelanguage.googleapis.com` for Gemini, the default, or `api.anthropic.com`), and only in `cloud` mode. `PRIVACY.md` repeats this table, the exact fields per call, and a pointer to Anthropic's current data-retention terms (to be checked and quoted at the time of writing, not from memory).

### 14.3 Redaction and pseudonymisation (NFR-3)

Applied by `llm/redact.py` to **every** outbound payload, after the tool has built `for_model`:

| Data | Treatment |
|---|---|
| Account and card numbers (any 8+ digit run, masked forms like `XXXX1234`), IFSC codes | removed; accounts referred to by nickname ("Salary account") |
| UPI VPAs | known merchant VPA → merchant name; any other VPA → `PERSON_n` |
| Person names in UPI/NEFT/IMPS narrations | parsed out structurally (§8.1) → `PERSON_n` |
| Phone numbers, emails, PAN, Aadhaar-shaped numbers | removed |
| UTR / reference numbers | removed |
| Balances | sent only by tools whose answer is a balance (forecast, affordability) |

The `PERSON_n` mapping is kept per session in memory and reversed when rendering, so the owner sees "Ravi" while the API sees `PERSON_3`. A property test feeds generated narrations through the redactor and checks that no digit run of 8+ and no known name survives.

### 14.4 Network guard (A9)

`llm/netguard.py` installs a Python **audit hook** (`sys.addaudithook`, which can't be removed once installed) at start-up that inspects `socket.connect` and `socket.getaddrinfo` events:

- `local` and `off` modes: any non-loopback destination → `PermissionError` + log line.
- `cloud` mode: only the configured provider's API host is allowed.

This makes "zero network requests in local mode" a tested property: the acceptance test runs import → classify → 20 questions → reports in local mode with the guard in strict mode. Any attempted connection fails the test. A second check runs the same flow under macOS `nettop`/Little Snitch for the README's manual verification.

### 14.5 Other controls

- **Encryption at rest:** SQLCipher DB; original statement files stored AES-256-GCM encrypted in `originals/<sha256>.bin` with a key from the Keychain (kept for provenance and re-parsing); backups are SQLCipher copies with the same key.
- **Secrets:** DB key, original-file key, Anthropic API key, and statement passwords in the macOS Keychain via `keyring`. `.env` is supported for the API key only, must be mode `0600`, and is in `.gitignore`.
- **Logs:** structlog with a processor that drops amounts, descriptions, names, and account fields; logs carry ids and counts only. `llm_call` rows record time, role, model, input/output/cache tokens, estimated cost, number of redactions, and **no payload**. An opt-in `--debug-llm` writes redacted payloads to an encrypted local file for troubleshooting.
- **Backups (NFR-6):** before every import commit, bulk write, and migration, plus daily on first open; keep 30. `restore` backs up the current state first.
- **Undo (NFR-6):** every write belongs to a `change_set`; `undo` reverses it using the audit log's old values (soft-deleted rows come back).
- **Dependencies (NFR-4):** exact pins in `uv.lock`; `pip-audit` in CI; no dependency that phones home (one reason Streamlit was rejected, §19).
- **Test data (NFR-9):** fixtures are generated; a pre-commit hook refuses files under `tests/fixtures/` that weren't produced by the generator (checked by a header marker).

---

## 15. Configuration and data locations

| Item | Location (macOS) |
|---|---|
| Config | `~/Library/Application Support/finassist/config.toml` (override `FINASSIST_CONFIG`) |
| Database | `~/Library/Application Support/finassist/ledger.db` (SQLCipher) |
| Originals | `…/finassist/originals/` (encrypted) |
| Backups | `…/finassist/backups/ledger-YYYYMMDD-HHMMSS.db` |
| Demo data | `…/finassist/demo/` (separate DB and key) |
| Reports | `~/Documents/finassist/reports/` |
| Logs | `~/Library/Logs/finassist/` |
| Inbox (optional, S1) | `~/Documents/finassist/inbox/`; `finassist import --inbox` processes everything in it |

Config sections: `[general]` (zone, base currency, FY start month), `[periods]` (quarter convention), `[privacy]` (`llm_mode = cloud|local|off`, receipt consent), `[llm]` (models, effort, local endpoint), `[import]` (dedupe thresholds, date windows, self VPAs), `[classify]` (review threshold), `[budgets]` (alert threshold), `[forecast]` (buffer, history months), `[review]` (items per session). Every option has a default and is documented in `config.example.toml`. Invalid values stop start-up with a message naming the key.

---

## 16. Testing strategy

### 16.1 Layers

| Layer | What | How |
|---|---|---|
| Unit | every module in `core`, `db`, `ingest`, `classify`, `analytics`, `review`, `reports`, each tool | pytest, synthetic fixtures, in-memory SQLCipher DB |
| Property | money parsing/formatting round-trips; split sums; transfer groups net to zero; dedupe never merges rows with different amounts; fingerprint stability; redaction leaves no account-like digits | hypothesis |
| Harness | agent loop, policy gate, grounding check, redaction of tool results, refusal handling | `ScriptedProvider` replays fixed model responses: deterministic, free, no network |
| Scenario | S1–S10 end to end | scripted provider for the model's tool choices; the real tool layer and DB underneath |
| Acceptance | A1–A9 | see 16.3 |
| Live eval (opt-in, `-m live`) | the 20 benchmark questions and advice refusals against the real model | costs a few cents a run; run before each phase review, not in CI |

### 16.2 Synthetic data

`demo/generator.py`, seeded and reproducible, produces 14 months × ~130 transactions/month = **~1,800 transactions across 5 accounts** (2 savings, 1 salary/current, 1 credit card, cash), in **four source layouts**:

| Layout | Format | Quirks covered |
|---|---|---|
| Bank A | CSV | separate Debit/Credit columns, `DD/MM/YYYY`, UTF-8 BOM, footer summary rows |
| Bank B | XLSX | single signed amount, `DD-MMM-YY`, merged header cells, running balance |
| Bank C | text PDF (generated with fpdf2), one version password-protected | multi-line narrations, page breaks mid-table, opening/closing summary |
| Card | CSV | `Dr`/`Cr` suffixes, foreign-currency rows, MCC column, billing-cycle dates |
| SMS + UPI | text | overlapping with statements (dedupe), a few unparseable messages, OTPs to drop |

Plus a **ground-truth file** listing each transaction's true category, transfer pairs, duplicate pairs, refunds, and recurring series. Built-in traps: salary late in one month (S5), a subscription price rise (S4), same-day genuine repeats, a card payment, overlapping statement periods, a refund, a split-worthy supermarket bill, a held-out set of merchants (A-16).

### 16.3 Acceptance mapping

| PS | Test |
|---|---|
| A1 | import all fixtures → `reconcile` reports every statement matched to the paisa |
| A2 | import everything twice → row counts and a content hash of all tables are unchanged |
| A3 | Σ expense equals ground-truth expense; every card payment and own-account transfer is absent from expense queries |
| A4 | classification against ground truth: ≥ 90% correct among auto-categorised rows; all others `needs_review` |
| A5 | correct one merchant → past rows updated after confirmation; re-import a new month → that merchant categorised correctly, no review item |
| A6 | `tests/scenarios/test_s01…s10` |
| A7 | 20 benchmark questions: every figure in the reply equals the figure from an independent hand-written SQL query; `explain` returns the same transaction set |
| A8 | a question about a period with no data (e.g. 2019) → reply contains no figures and says data is missing |
| A9 | local-mode full run under strict network guard → zero connection attempts |
| A10 | manual: fresh macOS user account, README only, timed |

---

## 17. Performance

| Requirement | Approach |
|---|---|
| 1,000-row import < 30 s (NFR-5) | Parsing and rules take well under a second. The LLM residue (≤ ~300 rows at first, ≤ 50 later) is sent as up to 6 concurrent batches with a 20 s total budget. Rows not done within the budget are committed as `needs_review` and finished on the next open. |
| Question < 5 s | Aggregates are indexed SQL (milliseconds at 100k rows). Most time is the model; prompt caching, `medium`/`low` effort, and streaming keep the first visible text early. Measured per benchmark question and reported. |
| 100,000 transactions | Indexes in §6.2; FTS5 for text; a benchmark test with a generated 100k-row ledger asserting p95 query time < 200 ms. |

---

## 18. Build phases

The phases follow PS §11. Work stops after each phase for review (PS "What I want from you", step 4).

### P0: Skeleton (detailed)

Build order:

1. `pyproject.toml`, uv lock, ruff/mypy/pytest config, `.gitignore` for this project, `config.example.toml`.
2. `core/money.py`, `core/periods.py` (with an injectable clock, so tests and the demo pin "today") with property tests. **(verify: `sqlcipher3` wheel opens an encrypted DB on this Mac without Homebrew SQLCipher; `PRAGMA cipher_version` non-empty; FTS5 available.)**
3. `db/schema.py` (all tables from §6, so later phases don't need disruptive migrations), first Alembic migration, `db/audit.py`, Keychain key handling.
4. `finassist init` + seed taxonomy.
5. Accounts and manual entry (`add_transaction`).
6. CSV import for Bank A and Card layouts with mapping profiles, idempotent fingerprints, staging + `commit_import`.
7. `query_transactions` + `query_receipt` + `resolve_period` + coverage.
8. Agent loop with Anthropic provider and scripted provider; tools: `list_accounts`, `resolve_period`, `query_transactions`, `get_data_coverage`, `import_file`, `commit_import`, `add_transaction`, `explain`. Grounding check and scope footer from day one. Because P0 already sends chat to the API, the redaction baseline (account and card numbers, IFSC, phone, email, reference numbers), the cloud-mode network allow-list, and the payload-free usage log also land in P0, along with a PRIVACY.md draft.
9. `finassist demo` with the generator's first version.
10. README setup section.

**Done when** (PS): a CSV is imported and "total spend in October" returns the correct number, checked against SQL, shown with a **real session transcript against demo data**.

### P1: Trust

Cross-source dedupe, transfer detection, reconciliation (+ running-balance chain), XLSX and SMS parsers, review queue (CLI), rules + learning from corrections + `reclassify`, change sets + undo, backups. **Done when:** A1, A2, A3, A5 pass.

### P2: Intelligence

Merchant normalisation + seed table, heuristics, person-name pseudonymisation, LLM residue classifier, splits and refunds, full metrics (except month-end projection), `compare_periods`, `get_summary`, trends, search, multi-turn session state, slash commands, filtered export, local-model provider + strict network guard. **Done when:** S2, S9, S10, A4, A7, A8, A9 pass.

### P3: Foresight

User-defined scheduled entries (FR-5), recurring detection, expected income, month-end projection, budgets, goals, anomalies, forecast, affordability, briefs, on-open alerts, optional `launchd` brief. **Done when:** S3, S4, S5, S7 pass.

### P4: Output and polish

Text and password PDF statements, OCR fallback, receipts, reports (md/pdf/xlsx + charts), exports, web surface, `PRIVACY.md` final, packaging polish. **Done when:** S1, S6, S8, A6, A10 pass.

---

## 19. Rejected alternatives

| Alternative | Why rejected |
|---|---|
| Let the LLM write SQL (text-to-SQL) | Can't guarantee correct joins, transfer exclusion, or sign handling; injection risk; and every answer would need re-validation. A typed `QuerySpec` covers the question types in FR-21 and is testable. |
| LLM does arithmetic or summarises raw rows | Violates NFR-1 directly. |
| Managed or hosted agent platforms (Claude Managed Agents and similar) | The agent loop and session history would live off the machine, and tools would need to reach the local DB from outside. That conflicts with NFR-2. A local loop is a few hundred lines. |
| SDK Tool Runner instead of a hand-written loop | Workable, but the loop needs the policy gate, the split `for_model`/`for_user` results, redaction, and the grounding retry in exact positions, and must drive a local model with the same code. A hand-written loop is clearer. |
| Full SQLAlchemy ORM / SQLModel | Hidden writes and lazy loading make the audit and bulk-confirmation rules harder to guarantee. |
| Raw `sqlite3` with string SQL | Dynamic filters and grouping as strings are error-prone and an injection risk. |
| DuckDB | Excellent for analytics, but no mature at-rest encryption equivalent to SQLCipher, and 100k rows don't need it. |
| Plain SQLite on FileVault only | FileVault protects only while the Mac is off; a copied DB file or backup would be readable. |
| Streamlit for the UI | Heavy, re-runs the script on every interaction, and collects usage statistics by default. |
| Electron / React desktop app | Large surface and a second language for a single-user tool. |
| Camelot / tabula for PDFs | Ghostscript or Java dependencies. |
| PyMuPDF | AGPL licence. |
| WeasyPrint for PDF | Needs Pango and Cairo system libraries; harder single-command setup. |
| Cloud OCR for statements and receipts | Sends whole documents off the machine; local Tesseract is enough for statements. Receipts use the LLM only with consent. |
| Account-aggregator APIs / scraping | Explicit non-goal (PS §3). |
| Auto-applying LLM merchant guesses as aliases | One wrong guess would then miscategorise every future row silently; guesses wait for confirmation. |
| An always-running background daemon | More moving parts and attack surface for a weekly user; alerts on open + optional `launchd` meets G5. |

---

## 20. Known limitations and next three things

Known limitations (initial; updated at each phase review):

1. Scanned or image-only statements depend on OCR quality; complex layouts may need a hand-built profile.
2. Loan EMIs are counted whole as expense; principal vs interest isn't separated (A-9).
3. Indian bank holidays aren't modelled for expected-income grace periods.
4. "Unused subscription" relies on the owner (A-5).
5. Self-reported model confidence is not calibrated; thresholds are tuned on synthetic data and may need retuning on real data.
6. Local-model mode will answer complex questions less reliably than cloud mode; slash commands stay exact.
7. Generated reports and exports are not encrypted once written.

Next three things worth building after v1:

1. **Email-statement inbox watcher** (a local folder that the mail client drops attachments into, processed on open) to make capture almost zero-effort.
2. **Investment holdings tracking** (CAS statement import for mutual funds) for net worth, still tracking only, no advice.
3. **Tax-year pack**: 80C/80D/HRA-relevant tagging and a summary export for whoever files the return, explicitly not tax advice.

---

## 21. Open questions for the owner

Answers change the design; everything else has a default above.

| # | Question | Default if not answered |
|---|---|---|
| Q1 | Confirm A-1 to A-5 (card payment as transfer, refund handling, cash as an account, FY quarters, owner-marked unused subscriptions) | As proposed |
| Q2 | Which banks and card issuers, and which formats do you actually get (CSV, XLSX, PDF)? Fixtures will imitate their **layout** only, with synthetic data. | Generic layouts in §16.2 |
| Q3 | Cloud LLM for chat acceptable as described in §14.2, or start in local mode? | `cloud` with redaction |
| Q4 | Chat model: Opus 5.5 (≈ $10/month) or Sonnet 5.5 (≈ half)? | Opus 5.5, revisited after the P2 benchmark |
| Q5 | Receipt images to the cloud: ask per receipt, always allow, or never? | Ask per receipt |
| Q6 | Is Python 3.14 fine as the development version? | Yes |
| Q7 | Package / command name | `finassist` |

---

## Appendix: scenario traceability

| Scenario | Main components | Phase |
|---|---|---|
| S1 import + dedupe + ask only about exceptions | `ingest/pipeline`, `dedupe`, `transfers`, `reconcile`, `review/queue`, import preview | P1 (CSV/XLSX), P4 (PDF) |
| S2 eating out Oct vs Sep, what pushed it up | `resolve_period`, `compare_periods` (contributors), grounding check, scope footer | P2 |
| S3 groceries budget burn on the 19th | `budgets` burn index + alert rule, on-open alerts | P3 |
| S4 subscription price rise | `recurring` status `increased`, alert | P3 |
| S5 salary missing on the 5th | `income_watch` with expected window + grace | P3 |
| S6 photographed cash bill | `parsers/receipt`, consent, confirmation, Cash account | P4 |
| S7 can I afford ₹85,000 | `forecast` + `affordability` verdict with assumptions | P3 |
| S8 annual report as PDF/Excel | `reports/builder` (annual), charts, fpdf2, XlsxWriter | P4 |
| S9 one correction sticks | `create_rule` + `reclassify` (bulk confirm) + rule precedence | P1 (rules), P2 (agent flow) |
| S10 Q3 reimbursements | tags, `resolve_period` (FY quarter), `search_transactions`, `export_data` | P2 |

---

## 23. Changes made during the build

Decisions changed or refined while building, recorded per plan §1.2 item 4. Details and reasons are in the phase self-reviews.

### P0 (2026-11-23, [p0-self-review.md](reviews/p0-self-review.md))

- **§3.1 encryption:** the SQLCipher key is applied as a raw 256-bit key by a connection factory (`PRAGMA key = "x'…'"`), not through the `sqlite+pysqlcipher` URL, to avoid per-connection PBKDF2 and keep the key out of URLs.
- **§15 demo:** the demo ledger's key lives in a 0600 file in the demo folder (synthetic data only). `FINASSIST_KEYSTORE=file` does the same for tests and CI. The real ledger always uses the Keychain.
- **§10.2 tools:** `query_transactions` accepts the period as text and resolves it in code; `add_transaction` is a confirm-gated tool (it satisfies both §7.9's echo and §10.4's write rule).
- **§7.1 import preview:** adds a file check (statement opening + rows = closing) ahead of P1's reconciliation, and refuses a statement whose last-4 belongs to another account.
- **§10.5 grounding:** the small-integer and year exemption applies only to plain numbers, never to ₹ amounts, percentages or lakh/crore values.
- **§14.3 redaction:** only account nicknames reach the model, including in import results.
- **§16.2 demo data:** generator v1 omits transfers, card payments, ATM withdrawals, investments and refunds until P1's transfer detection exists; v2 adds them.
- **Versions in use:** Python 3.14, SQLCipher 4.20 (via `sqlcipher3` 0.6.5), SQLite 3.54, SQLAlchemy 2.1, pydantic 2.14, `anthropic` 2.12 (pinned in `uv.lock`).

### Provider change (2026-11-23, owner's decision)

- **§3.1 / §11:** default LLM provider switched from Anthropic to **Google Gemini** (`llm.provider = "gemini"`): chat `gemini-3.8-flash`, classification `gemini-3.5-flash-lite`. Anthropic stays available behind the same `LLMProvider` interface. New module `llm/gemini_provider.py` (google-genai 3.6.1, pinned in `uv.lock`); new §11.4.
- **§14:** new gate `llm.gemini_paid_tier` (default false): the real ledger is never sent to Gemini until the owner confirms a paid-tier key, because Google's free-tier terms allow product-improvement use and human review. Requests set `store = false`. The network guard's allowed host follows the provider.
- **§10.1 loop:** a model reply with neither text nor a tool call (e.g. Gemini's `MALFORMED_FUNCTION_CALL`) gets one nudge before the turn ends; empty assistant turns are not appended to history.
- **Tool schemas:** strict schemas apply only to Anthropic; Gemini receives the plain pydantic JSON Schema (titles removed).

### GUI brought forward (2026-11-23, owner's request)

- **§12.3 web surface** built now instead of in P4, and made the default (`finassist` opens it; `Finassist.command` launches it from Finder). The CLI remains for scripting and tests.
- **Stack:** FastAPI + Jinja2 (autoescaped) + a small hand-written script and stylesheet served by the app. HTMX was not needed; no third-party browser code and no CDN.
- **Security** (`web/security.py`): binds 127.0.0.1 only; exact Host check (DNS rebinding); a per-launch token in the launch URL exchanged for an HttpOnly SameSite=Strict cookie; CSRF header on every POST; strict Content-Security-Policy.
- **Same rules as chat:** pages call the same tools; writes go through `policy.prepare` (preview + token) and `policy.apply` (re-checked before writing). Chat turns run in a background thread and pause on a Yes/No card for confirm-gated tools.
- **Privacy:** conversations stay in memory; uploaded statements are written 0600 to `uploads/` in the data folder and deleted after import or cancel (and on next start).
- **Scope added beyond P0:** Overview (balances, coverage, quick figure, add account), Import (upload + demo inbox), Add transaction, Settings (API key, chat model, AI mode, Gemini paid-tier confirmation written to `config.toml` with tomlkit, mode 0600).

### GUI redesign: dashboard, no chat (2026-11-24, owner's request)

- **Chat removed from the browser app** at the owner's request. The agent, tools and grounding check are unchanged and remain available as `finassist chat` in the terminal (PS FR-29 is still met there). The GUI therefore uses no language model and runs under the **strict** (loopback-only) network guard.
- **Pages:** Dashboard (home), Transactions (filters, search, pagination, CSV export with formula-injection guarding), Import, Add transaction, Accounts, Settings (theme, data location, demo rebuild). An empty ledger opens on a getting-started checklist.
- **Dashboard data** (`analytics/dashboard.py`, `analytics/listing.py`) is built only on `run_query` / the shared filter builder, so it agrees with the CLI; tests check every figure against the generator's ground truth.
- **Charts** (`web/charts.py`, inline SVG, no library): line chart for the 12-month trend (2 series, legend, crosshair tooltip), columns for spending per day, ranked horizontal bars for payment methods, a tall segmented bar for "where your money is" (instead of a donut), sparklines on stat tiles. Mark specs and colours follow the data-viz method; series colours are the reference palette's slots 1–2 (and 1–8 for accounts), validated per the palette's published results. The validator script needs Node.js, which isn't installed here; to re-run it: `node <skill>/scripts/validate_palette.js`.
- **Top payees** are derived for display from statement narrations (`core/textnorm.payee_of`, deterministic, not stored). Real categories still come in P1/P2.

### PDF statements brought forward (2026-11-24, owner's request)

- **FR-1 PDF (text and password-protected)** built now instead of P4. `ingest/parsers/pdf_table.py` reads words with positions (pdfplumber), finds the header row by the profile's headings, splits each line into cells at clear gaps and gives each cell to the column it lines up with (same left edge for text, same right edge for amounts). Repeated page headers and footers are skipped; wrapped descriptions join onto their row.
- **One set of row rules for CSV and PDF** (`ingest/parsers/table.py`), so a PDF and its CSV twin give identical transactions and fingerprints: importing both changes nothing (tested).
- **Passwords** travel only in memory (`SourceFile.password`, excluded from repr); never staged, stored or logged. Saving passwords per bank in the Keychain (ARCH §7.2) is not built yet.
- **Refusals with a reason:** wrong or missing password, scanned PDFs without a text layer (OCR later), PDFs with no recognisable transaction table (the message lists the headings found).
- **Profiles:** `bank_a_pdf.toml`, `card_a_pdf.toml` (`file_kind = "pdf"`, plus `ignore_patterns` for footers). The demo now also writes the October statements as PDFs (the bank one protected with `demo4821`; AES-128 via fpdf2).
- **Automatic layout detection for other banks** (bug fix, 2026-11-24: the owner's own bank PDF was refused as "unknown layout"). `ingest/parsers/pdf_auto.py` finds the table's heading row by the column names Indian banks commonly print (Date / Txn Date / Value Date, Narration / Description / Particulars / Remarks, Chq/Ref No, Withdrawal / Debit, Deposit / Credit, Amount, Dr/Cr, Balance), including headings printed over two lines, and builds a mapping profile on the fly. Amount handling: separate Debit/Credit columns; one Amount column with a Dr/Cr column; or card style, where "Cr" means money in and everything else money out. Dates with a trailing time are read. Totals, "balance b/f", notes and disclaimers are skipped and counted. The preview says which heading became which column and asks the owner to check the totals before importing. A line that looks like a transaction but can't be read still rejects the whole file (PS §13). If no table is found, the refusal lists the headings it saw so a profile can be added. Built-in profiles are tried first.
- **Single-letter D/C markers** (bug fix, 2026-11-24: the owner's statement printed amounts as "512.46 D" / "12,000.00 C" and all 22 rows were refused). `parse_amount` now reads a trailing D/C as well as Dr/Cr (it must follow a digit, space or ")" so words never match), for amounts and balances, in CSV and PDF alike. Auto-detection picks `amount_suffix` when every sampled amount carries a marker (an unmarked amount then rejects the file) and says so in the preview.
- **Fine print is not a transaction** (bug fix, 2026-11-24: a credit-card PDF was refused over 8 fee notices and MCC footnotes that mention dates inside sentences). A line is a transaction only when its date cell is a date (optionally with a time) and nothing else, and the line has other content. A line is reported as an unreadable transaction only when its date cell is short (≤ 4 words) and starts with something shaped like a date; prose that merely mentions a date is skipped and counted.
- **Import page:** the Account box is now a dropdown ("Detect from the statement" or one of the owner's accounts).

### Runs on macOS, Windows and Linux (2026-11-24, owner's request)

- **One place for platform differences:** `config/system.py` (secret-store name, "this Mac" / "this computer" wording, file-privacy check, `O_BINARY`).
- **Secrets (NFR-4):** `KeychainStore` uses `keyring`, which reaches the macOS Keychain, the Windows Credential Manager, or the Linux Secret Service (GNOME Keyring / KWallet). If no secret store is usable, `KeyStoreUnavailable` (a `FinError`) explains what to do in the GUI and CLI instead of a traceback. On Linux the owner may opt in to `FINASSIST_KEYSTORE=file` (owner-only key file; weaker, documented in PRIVACY.md).
- **File privacy:** the "readable by other users" check uses POSIX mode bits on macOS/Linux. Windows has no such bits; the data folder under `%LOCALAPPDATA%` is private to the user by its access-control list, so the check is skipped there. Binary writes through `os.open` add `O_BINARY` so Windows never translates newlines.
- **Text:** every file is read and written as UTF-8 explicitly. On Windows the CLI switches piped/redirected output to UTF-8 so "₹" never fails.
- **Time zones:** `tzdata` is a dependency, because Windows (and minimal Linux images) have no system time-zone database for `zoneinfo`.
- **Install without a compiler:** every dependency has ready-built wheels for macOS (arm64, x86_64), Windows (x64, ARM64) and Linux (x86_64, aarch64; glibc and musl) on Python 3.12–3.14, checked with `uv pip compile --only-binary :all:` per platform. `cryptography` dropped Intel-Mac wheels after 48.x and Windows-ARM wheels after 46.0.3, so `[tool.uv] constraint-dependencies` pins those two platforms.
- **Launchers:** `Finassist.command` (macOS), `Finassist.bat` (Windows, CRLF), `finassist.sh` (Linux); `.gitattributes` keeps their line endings.
- **CI:** `.github/workflows/ci.yml` runs ruff, mypy, import contracts and pytest on Ubuntu, Windows, macOS arm64 and macOS Intel with Python 3.12 and 3.14.

### Named Coinleaf (2026-11-24, owner's request)

- **Name:** the app is called **Coinleaf** everywhere people see it: page titles, sidebar, onboarding, terminal messages, launchers (`Coinleaf.command`, `Coinleaf.bat`, `coinleaf.sh`) and user docs. (It was briefly named Rupavo the same day; that name was replaced and isn't kept as an alias.)
- **Logo** (`web/static/icon.svg`, also the browser-tab icon): the owner's design redrawn as a vector. It is a logo-green rounded square (#476c24) with a white coin (green inner ring, green ₹) and a white leaf with a green midrib growing from the coin's top right.
- **Colours follow the logo:** accent #476c24 (white text on it: 6.1:1), deep forest sidebar #263d12, green net-worth card, pale green-tinted page background. Dark mode is a forest-night theme with the logo's light green #a8c96e as the accent. Chart series keep the validated colour-blind-safe palette (ARCH dataviz rules); only the surrounding chrome changed.
- **Command:** `coinleaf`; `finassist` remains as an alias.
- **Kept on purpose:** the Python package (`finassist`), the data folder (`…/finassist/`), the secret-store service names (`finassist:…`), cookie and browser-storage keys, and the synthetic-data marker keep the internal name. Renaming them would orphan existing ledgers and their keys, sign people out, reset their theme choice and invalidate the fixtures. This design document keeps using "finassist" for the codebase.

### Sign-in page (2026-11-24, owner's request)

- **What changed:** the browser app now needs a password. Before, the only gate was the per-launch link from the terminal, which signed in any browser that opened it.
  - **First start:** the launch link leads to **Create your password**. Setup is only allowed in a browser holding the launch cookie, so another local user can't claim the app first.
  - **After that:** every visit goes to **Welcome back**, and a bookmark of `http://127.0.0.1:8765` works.
- **Storage:** `config/password.py` keeps a salted scrypt hash (N=2^15, r=8, p=1, 16-byte salt; parameters stored with the hash) in `app-password.json` in the data folder. The file is owner-only and written atomically. The password is never stored or logged.
- **Sessions** (`web/auth.py`): random ids in memory with a 15-minute idle timeout, sliding with use. They end on Lock (`POST /logout`), on password change (other sessions only), and when the app stops. A cookie is HttpOnly and SameSite=Strict. The page also locks itself after 15 minutes without mouse or keyboard input, so figures don't stay on screen.
- **Guessing:** 5 free tries, then 30 s doubling to 15 min per further wrong password, shared by sign-in and change-password.
- **Middleware** (`web/security.py`): `/static/` and the sign-in pages are public. A signed-out GET is redirected to sign-in, and a signed-out POST gets 401 with `X-Redirect`, which `app.js` follows. CSRF, the Host check and CSP are unchanged.
- **Recovery:** `coinleaf password reset` forgets the password; the ledger is untouched.
- **Scope, stated plainly:** the password guards the open app. The ledger's encryption key stays in the OS secret store, and the password is not used to derive it, so a forgotten password never means lost data. The flip side is that anyone signed in to the owner's computer account can reset the app password.

