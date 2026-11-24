# Implementation Plan: Personal Financial Assistant

| Field | Value |
|---|---|
| Document type | Implementation plan, phase by phase |
| Sources | [problem_statement.md](problem_statement.md) ("PS") and [ARCHITECTURE.md](ARCHITECTURE.md) ("ARCH") |
| Status | P0 built and self-reviewed ([docs/reviews/p0-self-review.md](reviews/p0-self-review.md)); waiting for owner review before P1. |
| Scope | What gets built in each phase, in what order, how it is tested, and what must be true before the next phase starts |

This plan adds no new requirements. Phases and their "done when" come from PS §11; module names come from ARCH §5; design details come from the ARCH section cited in each step. Items marked **verify** are checked on the owner's machine before code depends on them (§2.1).

---

## Contents

1. [How the work runs](#1-how-the-work-runs)
2. [Before P0: verification and owner input](#2-before-p0-verification-and-owner-input)
3. [P0: Skeleton](#3-p0-skeleton)
4. [P1: Trust](#4-p1-trust)
5. [P2: Intelligence](#5-p2-intelligence)
6. [P3: Foresight](#6-p3-foresight)
7. [P4: Output and polish](#7-p4-output-and-polish)
8. [Final acceptance](#8-final-acceptance)
9. [Self-review template](#9-self-review-template)
10. [Risks to the plan](#10-risks-to-the-plan)
- [Appendix A: requirement coverage by phase](#appendix-a-requirement-coverage-by-phase)
- [Appendix B: tool delivery by phase](#appendix-b-tool-delivery-by-phase)
- [Appendix C: scenarios and acceptance criteria by phase](#appendix-c-scenarios-and-acceptance-criteria-by-phase)

---

## 1. How the work runs

The order comes from PS ("What I want from you"): restate and design → resolve [DECIDE] points → build P0 completely → then one phase at a time, stopping after each for review. Steps 1 and 2 are done in ARCH §1–§3.

### 1.1 Phases at a glance

| Phase | PS §11 scope | Done when (PS §11 + this plan) | Depends on |
|---|---|---|---|
| **P0 Skeleton** | Schema, config, accounts, manual entry, CSV import, `query_transactions`, CLI chat | A CSV is imported and "total spend in October" returns the correct figure, shown in a real session against demo data; A2 (CSV) and A8 hold | ARCH approved; §2.2 questions due before P0 answered |
| **P1 Trust** | Dedupe, transfer detection, reconciliation, review queue, rules + learning from corrections | Totals reconcile to the statements; transfers excluded; one correction sticks: A1, A2, A3, A5 | P0 |
| **P2 Intelligence** | Full NL Q&A, comparisons, trends, derived metrics, explainability | S2, S9, S10 pass; A4, A7, A8, A9 | P1 |
| **P3 Foresight** | Budgets, recurring detection, anomalies, forecast, affordability, proactive briefs | S3, S4, S5, S7 pass | P2 |
| **P4 Output and polish** | PDF statement/receipt import, reports and charts, exports, visual surface, packaging | S1, S6, S8 pass; A6, A10; final acceptance (§8) | P3 |

### 1.2 Gate at the end of every phase

1. All tests for the phase pass, with the command and its output shown.
2. A real session transcript against the demo dataset, following the phase's transcript script.
3. A self-review (§9) listing every deviation from PS or ARCH and every assumption made.
4. ARCH updated wherever the build changed a decision, with the reason; PRIVACY.md updated for any new outbound call.
5. Work stops until the owner confirms. The next phase does not start early (PS "Do not jump ahead").

### 1.3 Working rules inside a phase

- Build bottom-up: `core` → `db` → domain modules → tools → agent → CLI. Each module lands with its unit tests.
- Tests are written **before** the implementation for the correctness-critical modules: `core/money.py`, `ingest/fingerprint.py`, `ingest/dedupe.py`, `classify/transfers.py`, `classify/splits.py`, `analytics/query.py`, `agent/grounding.py`, `agent/policy.py`, `llm/redact.py`, `llm/netguard.py`.
- **Privacy from the first outbound call.** P0 already sends chat to the Anthropic API, so redaction of account identifiers, the cloud-mode network allow-list, and the usage log land in P0, not later.
- **Time is injected.** "Today" comes from a clock provider in `core/periods.py`; tests and the demo pin it (`demo.today` in config), so S3 and S5 are deterministic.
- No real financial data anywhere in the repository. Fixtures come only from `demo/generator.py` (ARCH §16.2), which grows each phase.
- Every dependency is pinned to an exact version in `uv.lock` when it is added.
- One git commit per build step, so any step can be reviewed or reverted on its own.

---

## 2. Before P0: verification and owner input

### 2.1 Verification tasks

Each item is checked on the owner's Mac (or against official documentation) and recorded with the date and result in `docs/verification-log.md`. Items are checked at the start of the phase that needs them.

| # | Verify | Phase | Decision it unlocks |
|---|---|---|---|
| V1 | `sqlcipher3` installs from a wheel on this Mac (Python 3.14, arm64) without Homebrew SQLCipher; `PRAGMA cipher_version` returns a value | P0 | SQLCipher vs encrypted-volume fallback (ARCH §3.1) |
| V3 | SQLAlchemy Core + Alembic work through the SQLCipher driver (key set on connect) | P0 | Engine setup |
| V5 | `keyring` reads and writes the macOS Keychain from the venv without repeated prompts | P0 | Key storage |
| V6 | Python audit events `socket.connect` and `socket.getaddrinfo` fire for the Anthropic SDK's HTTP client on 3.14 | P0 | Network guard (ARCH §14.4) |
| V7a | The configured provider's model ids work with a real key (Gemini by default: `gemini-3.8-flash`, `gemini-3.5-flash-lite`; Anthropic: `claude-opus-5-5`, `claude-haiku-5-5`) | P0 | Model config |
| V4 | An encrypted backup can be made from Python (SQLite backup API or `sqlcipher_export`) | P1 | Backup method |
| V2 | The bundled SQLCipher build includes FTS5 | P2 | FTS5 vs `LIKE` fallback for text search |
| V11 | A local model served by Ollama makes valid tool calls with the P0 tool set | P2 | Local-mode model choice |
| V7b | The receipt model accepts image input (Gemini Flash-Lite by default) | P4 | Receipt model (ARCH §7.8) |
| V8 | `pdfplumber.open(path, password=...)` opens the password-protected fixture | P4 | PDF decrypt path |
| V9 | fpdf2 with bundled Noto Sans renders ₹ correctly | P4 | Report font |
| V10 | The XlsxWriter Indian number format displays correctly in Excel and Numbers | P4 | Excel export format |

### 2.2 Owner input (ARCH §21)

| # | Question | Needed before | Default if unanswered |
|---|---|---|---|
| Q1 | Confirm ARCH A-1 to A-5 (card payment as transfer, refunds, cash as an account, FY quarters, owner-marked unused subscriptions) | P0 (seed taxonomy kinds) | As proposed |
| Q3 | Cloud LLM for chat acceptable as described in ARCH §14.2? | P0 (first outbound call) | `cloud` with redaction; provider Gemini (owner's decision 2026-11-23), paid tier required for real data |
| Q6 | Python 3.14 as the development version | P0 | Yes |
| Q7 | Package / command name | P0 | `finassist` |
| Q2 | Which banks, card issuers, and formats (layouts only; data stays synthetic) | P1 (Bank B, SMS templates); useful in P0 | Generic layouts (ARCH §16.2) |
| Q4 | Chat model within the chosen provider (owner chose Gemini on 2026-11-23): `gemini-3.8-flash` or a Gemini 4 model | P2 exit (after measured cost) | `gemini-3.8-flash` |
| Q5 | Receipt images to the cloud: per receipt, always, or never | P4 | Ask per receipt |

---

## 3. P0: Skeleton

### 3.1 Goal

PS §11: *"I can import a CSV and ask 'total spend in October' and get a correct number."* Delivered with a real transcript against demo data (PS step 3).

### 3.2 Entry criteria

ARCH approved. Q1, Q3, Q6, Q7 answered or defaults accepted. V1, V3, V5, V6, V7a checked.

### 3.3 Requirements covered

| Requirement | P0 extent | ARCH |
|---|---|---|
| FR-1 | CSV only, two layouts (Bank A, Card) | §7.2 |
| FR-4 | Manual entry in chat and a simple CLI form | §7.9 |
| FR-7 | File-level and row-level idempotency | §7.4 |
| FR-8 | Provenance (`source_row`, `transaction_source`) | §6.2 |
| FR-9 | All normalised fields stored; merchant and category may be null until P1/P2 | §6.2 |
| FR-11 | Taxonomy seeded with kinds and `is_essential` | §6.2 |
| FR-21 | Totals by period, category, account; nothing more yet | §9.1 |
| FR-28 | `explain(query_id)` | §9.1, §10.2 |
| FR-29 | CLI chat | §3.1, §12.1 |
| NFR-1 | Grounding check, scope footer, coverage | §10.3–§10.5 |
| NFR-2, NFR-4 | SQLCipher, Keychain, redacting logs, cloud allow-list | §14 |
| NFR-3 | Redaction of account identifiers in outbound payloads (baseline) | §14.3 |
| NFR-6 | Transactional import commit | §7.1 |
| NFR-7 | Append-only audit log, change sets | §6.2 |
| NFR-10, NFR-11 | `Money`, INR formatting, FY periods, sign convention | §6.1 |

### 3.4 Tools delivered

`list_accounts`, `get_data_coverage`, `resolve_period`, `query_transactions`, `explain`, `import_file`, `commit_import`, `add_transaction`.

### 3.5 Build order

| Step | Work | Module (ARCH §5) | Tests landed with it |
|---|---|---|---|
| 0.1 | `git init`; `pyproject.toml` (requires-python ≥3.12, entry point `finassist`); uv lock; ruff, mypy (strict on `core`, `db`, `analytics`), pytest, hypothesis; import-linter contract for the dependency rule (ARCH §5); pre-commit hook rejecting fixture files without the generator marker | repo root | `uv run pytest` runs; import-linter passes |
| 0.2 | `Money` type: parse from strings (`1,23,456.78`, `450.00 Dr`, `(450.00)`, `-450`), integer paise, reject floats and excess decimals, `ROUND_HALF_EVEN` helper, `format_inr` | `core/money.py` | Property: parse ∘ format round-trips; constructing from a float raises; 3-decimal input rejected; lakh/crore grouping cases |
| 0.3 | Period resolution (months, "last month", month without year = most recent past, FY, FY and calendar quarters, last N months) with an injectable clock and configured zone | `core/periods.py` | FY boundaries (31 Mar / 1 Apr); "last month" on 1 Jan; "October" asked in Oct vs Nov; FY Q3 = Oct–Dec, calendar Q3 = Jul–Sep; pinned clock gives identical results |
| 0.4 | Config models and loader; platform paths | `config/settings.py`, `config/paths.py` | Each invalid value rejected with the key named; defaults load with no file |
| 0.5 | SQLCipher engine; DB key generated into the Keychain; PRAGMAs (`foreign_keys=ON`) | `db/engine.py` | Wrong key fails to open; file unreadable by plain `sqlite3`; key never in logs (V1, V5) |
| 0.6 | Full schema from ARCH §6.2 (all tables now, to avoid disruptive migrations later); first Alembic migration; triggers: audit log append-only, currency matches account, split-child amount lock | `db/schema.py`, `db/migrations/` | `direction` generated and not writable; UPDATE/DELETE on `audit_log` aborts; unique indexes reject duplicate fingerprints and `(account_id, external_ref)` (V3) |
| 0.7 | `ChangeSet` + audit writer; repository helpers that require a change set | `db/audit.py`, `db/repo/` | Every write produces audit rows in the same transaction; a test scans `db/repo/` for writes that bypass the helpers; CI grep fails on `float(` in `core`, `db`, `analytics` |
| 0.8 | `finassist init`: data folder, keys, DB, seed taxonomy (PS FR-11 with `kind`, `is_essential`, and the A-1 handling of "Credit-card payment") | `cli/app.py`, `db/repo/categories.py` | `init` twice changes nothing; every category has a kind; transfer and card-payment categories have the kinds in ARCH A-1 |
| 0.9 | Accounts (incl. a Cash account) and manual entry: repository, `add_transaction`, and `finassist add` prompt form | `db/repo/accounts.py`, `db/repo/transactions.py`, `cli/app.py` | Balance = opening + Σ amounts; wrong-currency entry refused; "450 cash on auto today" stores −45000 paise on Cash with today's pinned date |
| 0.10 | CSV ingest: `Parser` protocol, `csv_table` parser, profiles for Bank A and Card, fingerprints, staging, preview, `commit_import` in one transaction | `ingest/` | Re-import changes zero rows; overlapping statements import shared rows once; two identical same-day rows both survive; one bad row rejects the file with its row number; an exception during commit leaves no rows and marks the import `failed` |
| 0.11 | `query_transactions` (`QuerySpec`/`QueryResult`), query receipts, coverage report, minimal metrics (income, expense) | `analytics/query.py`, `receipts.py`, `coverage.py`, `metrics.py` | Results equal hand-written SQL for a table of specs; parent category expands to children; transfers excluded unless asked; averages are `Decimal`; a period with no data reports `coverage = none` |
| 0.12 | LLM provider protocol; Anthropic provider (streaming, prompt caching, refusal check); scripted provider; usage log without payloads | `llm/provider.py`, `anthropic_provider.py`, `scripted_provider.py`, `usage.py` | `llm_call` rows hold token counts and no message text; refusal stop reason handled without a crash (V7a) |
| 0.13 | Outbound redaction baseline (account and card numbers, IFSC, phone, email, UTR/reference numbers → removed; accounts → nicknames) and network guard in cloud mode (only `api.anthropic.com`) | `llm/redact.py`, `llm/netguard.py` | Property: no 8+ digit run survives; a connection to any other host raises (V6) |
| 0.14 | Agent: tool registry (pydantic → JSON schema), P0 tools, policy gate, grounding check, loop, system prompt with the behaviour contract | `agent/` | Scripted provider: unverified figure → one retry, then prose withheld; `commit_import` cannot run without terminal confirmation; invalid arguments → error result; loop stops at 8 iterations; scope footer always rendered; `for_user` data never appears in the payload sent to the provider |
| 0.15 | CLI: `chat` REPL, `import`, `init`, `add`, `demo`, `--demo` flag; rendering (INR formatting, scope footer, `/how <query_id>`) | `cli/` | Smoke tests with Typer's runner |
| 0.16 | Demo generator v1: Bank A CSV + Card CSV + Cash, 14 months, ground-truth file, fixed seed, pinned demo date | `demo/generator.py` | Same seed → byte-identical files; ground-truth totals equal ledger totals after import |
| 0.17 | README (setup, `init`, `demo`, first question); `config.example.toml`; PRIVACY.md draft listing the P0 outbound call (chat) and its fields | repo root | None |

### 3.6 Demo data after P0

Bank A savings (CSV), credit card (CSV), Cash (manual entries), 14 months, ~1,300 rows. No transfers detected yet (they arrive in P1), so transfer-like rows are labelled in ground truth but not used in P0 checks.

### 3.7 Transcript script

1. `uv sync` → `finassist init` → `finassist --demo chat`.
2. "What accounts do I have?"
3. Import the card CSV for one month: preview shown, confirmation asked, committed.
4. Re-import the same file: "already imported, nothing to do".
5. "Total spend in October" → figure + scope footer; the same figure from a direct SQL query in the session.
6. "And September?" (follow-up with history only; full multi-turn state comes in P2).
7. `/how` on the October answer → filter and contributing transactions.
8. "Spent 450 cash on auto today" → echoed, saved.
9. "What did I spend in March 2019?" → no figures, says the data doesn't cover that period.

### 3.8 Manual checks

- The DB file does not open with plain `sqlite3`.
- Log folder and `llm_call` rows contain no descriptions, amounts, or account numbers.
- With the network guard active, a deliberate request to another host fails.

### 3.9 Exit

Tests pass; transcript delivered; the October figure matches SQL; PRIVACY.md draft matches the code's outbound call; self-review; owner confirms.

---

## 4. P1: Trust

### 4.1 Goal

PS §11: *"Totals reconcile to the statement; transfers excluded; one correction sticks."* Acceptance A1, A2, A3, A5.

### 4.2 Entry criteria

P0 confirmed. Q2 answered or generic layouts accepted. V4 checked.

### 4.3 Requirements covered

| Requirement | P1 extent | ARCH |
|---|---|---|
| FR-1 | XLSX/XLS; guided mapping for unknown layouts, remembered | §7.2 |
| FR-2 | SMS and UPI exports | §7.3 |
| FR-6 | Cross-source dedupe with bands and review | §7.5 |
| FR-12 | Transfer detection, card payments, investments, untracked transfers | §7.6 |
| FR-14 | Rules from corrections, retroactive reclassify | §8.4 |
| FR-31 | Review queue in the CLI | §12.2 |
| Invariants 1–4 | Reconciliation, transfer groups, uniqueness, `doctor` | §6.3, §7.7 |
| NFR-6 | Backups, restore, undo | §14.5 |
| NFR-8 | `docs/adding-a-bank.md` | §7.2 |
| A-3 | Cash as an account; ATM withdrawals as transfers | §2 |

### 4.4 Tools delivered

`update_transaction`, `create_rule`, `reclassify`, `list_review_queue`, `resolve_review_item`, `undo`.

### 4.5 Build order

| Step | Work | Module | Tests landed with it |
|---|---|---|---|
| 1.1 | XLSX parser + Bank B profile; legacy XLS via xlrd | `ingest/parsers/xlsx_table.py` | Merged headers, `DD-MMM-YY` dates, signed amounts parse correctly |
| 1.2 | Guided mapping for unknown layouts; profile saved and reused | `ingest/detect.py`, `ingest/profiles.py` | Second file with the same headers uses the saved profile with no prompt |
| 1.3 | SMS and UPI parsers; OTP and promotional messages dropped without storing | `ingest/parsers/sms.py`, `upi_export.py` | Each template's fields extracted; unknown last4 → review; OTP text never reaches the DB |
| 1.4 | Cross-source dedupe with scoring and bands | `ingest/dedupe.py` | Property: different amounts never merge; same UPI ref → auto-merge; two candidates → review; merged rows keep both source rows |
| 1.5 | Transfer detection and one-to-one pairing; untracked-transfer review items; investment kind | `classify/transfers.py` | Property: every transfer group sums to zero; card payment with card tracked → transfer; without the card → review item |
| 1.6 | Cash account flows: ATM withdrawal → transfer into Cash; untracked-cash review item | `classify/transfers.py`, `review/queue.py` | Withdrawal + logged cash spend counted once |
| 1.7 | Reconciliation: closing check and running-balance chain; unreconciled state in coverage | `analytics/reconcile.py`, `analytics/coverage.py` | A removed fixture row is reported at the exact break point; matching statements report ✓ to the paisa |
| 1.8 | Review queue: item kinds, ranking, session cap, one-keystroke CLI | `review/queue.py`, `cli/review.py` | Ranking order for a fixed set; each resolution creates a change set |
| 1.9 | Rules (matchers, actions, priority, `user_locked`), `create_rule`, `reclassify` (dry run + confirmed apply), `update_transaction` | `classify/rules.py`, `agent/tools/` | Rule beats merchant map; `user_locked` rows never changed; reclassify without confirmation changes nothing |
| 1.10 | Undo of any change set; soft delete | `db/audit.py`, `cli/app.py` | Undo restores every field and soft-deleted rows; audit log only grows |
| 1.11 | Backups (before commits, bulk writes, migrations, daily) and restore | `db/backup.py` | Backup opens with the key and equals the source; restore backs up the current state first (V4) |
| 1.12 | `finassist doctor`: all invariants from ARCH §6.3 | `db/integrity.py` | Each invariant has a fixture that breaks it and is reported |
| 1.13 | Import timing check: 1,000-row CSV and XLSX (no LLM yet) | `tests/` | Under 30 s (NFR-5); expected well under 5 s |
| 1.14 | Demo generator v2 | `demo/generator.py` | Ground truth includes duplicate pairs, transfer pairs, refunds, same-day repeats |
| 1.15 | `docs/adding-a-bank.md`; README sections for import, review, undo, backups | `docs/`, repo root | None |

### 4.6 Demo data after P1

Adds: Bank B salary/current account (XLSX with running balance), SMS and UPI exports overlapping the statements, own-account transfers, card bill payments, ATM withdrawals, refunds, same-day genuine repeats, overlapping statement periods. Statement opening and closing balances in every file. Now 4 accounts + Cash, ~1,800 rows.

### 4.7 Transcript script

1. Import Bank B XLSX and an SMS export for the same month → preview with auto-merged duplicates, possible duplicates, paired transfers, reconciliation ✓.
2. `finassist review` → resolve each item with one keystroke.
3. "Total expense in October" → card bill payment and own-account transfers absent; same figure from SQL.
4. "Zomato is Eating out, not Shopping" → rule created → reclassify preview ("N past transactions") → confirm.
5. Import the next month → Zomato rows categorised with no review item.
6. `finassist undo` on the reclassify → restored; redo it.
7. `finassist doctor` → all statements reconciled.

### 4.8 Exit

A1, A2, A3, A5 pass; transcript; self-review; owner confirms.

---

## 5. P2: Intelligence

### 5.1 Goal

PS §11: *"Scenarios S2, S9, S10 pass."* Also A4 (classification), A7 (benchmark), A8 (out-of-range), A9 (local mode, zero network).

### 5.2 Entry criteria

P1 confirmed. V2, V11 checked.

### 5.3 Requirements covered

| Requirement | P2 extent | ARCH |
|---|---|---|
| FR-10 | Merchant normalisation, alias table, seed merchants | §8.1 |
| FR-13 | Full classification pipeline with LLM residue and `needs_review` | §8.2, §8.3 |
| FR-15, FR-16 | Splits; refund linking | §8.5 |
| FR-21 | Comparisons, top-N, trends over N months, "where did the increase come from", text/amount/tag search, counts | §9.1, §9.4 |
| FR-22 | All metrics except month-end projection (P3, needs recurring) | §9.2 |
| FR-28 | Explain on every numeric tool | §10.2 |
| FR-32, FR-33 | Clarifying-question rule; multi-turn session state, preferences | §10.6, §10.7 |
| NFR-3 | Full redaction with UPI/NEFT person pseudonyms; local-model mode | §14.3, §14.4 |
| NFR-5 | Question latency; 100k-row responsiveness | §17 |
| NFR-12 | Filtered export (CSV/XLSX) | §13 |

### 5.4 Tools delivered

`get_summary`, `compare_periods`, `search_transactions`, `split_transaction`, `export_data` (filtered), preference write. Existing tools gain tags, merchants, and full filters.

### 5.5 Build order

| Step | Work | Module | Tests landed with it |
|---|---|---|---|
| 2.1 | Merchant normalisation: structural narration parsing, cleaning, alias lookup, fuzzy match, seed table (~150 merchants, stored as TOML) | `classify/merchants.py` | Raw → canonical cases (`POS 1234 SWIGGY*ORDER BLR` → Swiggy); fuzzy hits not saved as aliases |
| 2.2 | Heuristics library with fixed confidences | `classify/heuristics.py` | Each heuristic has positive and negative cases |
| 2.3 | Redaction extended: VPAs, person names parsed from narrations → `PERSON_n`, reversed when rendering | `llm/redact.py` | Property: no VPA, phone, email, or parsed person name survives; pseudonyms reverse correctly |
| 2.4 | LLM residue classifier: batched Haiku 5.5, structured output with category enum, minimised payload | `classify/llm_residue.py` | Payload contains only the allowed fields (snapshot test); output outside the enum rejected; guesses are suggestions, not aliases |
| 2.5 | Classification pipeline order, `classification_source`/`classification_ref`; threshold tuning on the labelled fixture with held-out merchants | `classify/pipeline.py` | A4: ≥90% correct among auto-categorised rows; all others `needs_review` |
| 2.6 | Splits and refund linking | `classify/splits.py`, `classify/refunds.py` | Property: split children sum to the parent; linked refund reduces the original category |
| 2.7 | Full metrics (ARCH §9.2 except month-end projection), `get_summary`, `compare_periods` with contributors, trends (group by month over N months), `search_transactions` with FTS5, tags | `analytics/` | Each metric equals hand-written SQL; compare returns deltas and percentages so the model never subtracts (V2) |
| 2.8 | Multi-turn session state, preferences, mid-conversation state note; clarifying-question rule in the prompt | `agent/session.py`, `agent/prompts/system.md` | Scripted conversation: "and the month before?", "break that down by merchant", "exclude rent" produce the expected specs |
| 2.9 | Slash commands for every common question | `cli/chat.py` | Each command's output equals the corresponding tool result |
| 2.10 | Local provider (Ollama) and `llm_mode` switch (cloud / local / off); network guard strict mode for local and off | `llm/local_provider.py`, `llm/netguard.py` | A9: full flow in local mode makes zero non-loopback connection attempts (V11) |
| 2.11 | `export_data` (CSV/XLSX) for tagged and filtered rows | `export/exporter.py` | S10 export matches the query |
| 2.12 | Performance: 1,000-row import with LLM residue < 30 s; p95 query < 200 ms on a generated 100k-row ledger; question latency measured | `tests/` | NFR-5 |
| 2.13 | Benchmark: 20 questions with independent SQL answers; cost meter (`doctor --costs`) | `tests/benchmark/`, `llm/usage.py` | A7 (live, opt-in) and A8 |
| 2.14 | PRIVACY.md updated with classification calls and local mode | repo root | Fields listed match the payload snapshot test |

### 5.6 Demo data after P2

Adds: held-out merchants and description templates (ARCH A-16), split-worthy supermarket bills, reimbursable tags on some rows, a clear eating-out rise from September to October for S2.

### 5.7 Transcript script

1. "How much did I spend on eating out in October vs September, and what pushed it up?" → both figures, delta, five contributing transactions (S2).
2. "Break that down by merchant." → "Exclude Swiggy." → "And the month before?"
3. "Show me how you got this." → filter and transactions.
4. "Zomato is Food, not Shopping" on a fresh dataset → rule, reclassify, next import correct (S9).
5. "Show me everything I can claim as reimbursement for Q3" → FY Q3 dates stated, tagged list, export file (S10).
6. "Which mutual fund should I buy?" → declines and gives the general principle.
7. Switch to local mode and repeat steps 1 and 3 with the network guard in strict mode.
8. `finassist doctor --costs` → measured cost for the session.

### 5.8 Exit

S2, S9, S10, A4, A7, A8, A9 pass; transcript; measured cost per question compared with ARCH §11.3; owner chooses the chat model (Q4); self-review; owner confirms.

---

## 6. P3: Foresight

### 6.1 Goal

PS §11: *"Scenarios S3, S4, S5, S7 pass."*

### 6.2 Entry criteria

P2 confirmed; chat model chosen.

### 6.3 Requirements covered

| Requirement | P3 extent | ARCH |
|---|---|---|
| FR-5 | User-defined scheduled entries (salary, rent, EMI, SIP, insurance) as expectations | §7.9 |
| FR-17 | Budgets, burn index, alerts, rollover | §9.5 |
| FR-18 | Goals | §9.6 |
| FR-19 | Recurring detection; new, increased, decreased, missing, cancelled, owner-marked unused | §9.7 |
| FR-20 | Expected-income tracking | §9.7 |
| FR-22 | Month-end projection | §9.2 |
| FR-23, FR-24 | Forecast with range and assumptions; affordability verdict | §9.8, §9.9 |
| FR-26 | Anomaly detectors | §9.10 |
| FR-27, G5 | Weekly and month-end briefs; on-open alerts; optional `launchd` | §9.11, §2 A-12 |

### 6.4 Tools delivered

`set_budget`, `get_budget_status`, `set_goal`, `get_goal_status`, `detect_recurring`, `list_recurring`, `forecast_cashflow`, `check_affordability`, `find_anomalies`, `get_brief`.

### 6.5 Build order

| Step | Work | Module | Tests landed with it |
|---|---|---|---|
| 3.1 | Recurring series: user-defined schedules (FR-5) and detection; status changes; annualised cost | `analytics/recurring.py` | S4: price rise detected with % and annual effect; each status fires once; yearly series needs confirmation; a scheduled entry is matched when its transaction arrives |
| 3.2 | Expected income with weekday grace | `analytics/income_watch.py` | S5: salary expected by the 2nd is flagged on the 5th, not on the 3rd (pinned clock) |
| 3.3 | Month-end projection metric | `analytics/metrics.py` | Equals the ARCH §9.2 formula on a fixture |
| 3.4 | Budgets: set, status, burn index, alert rule, rollover | `analytics/budgets.py` | S3: 84% spent on day 19 of 30 triggers the alert; rollover never carries overspend |
| 3.5 | Goals: required monthly amount, progress, projected date | `analytics/goals.py` | No contributions → "no projection" |
| 3.6 | Anomaly detectors | `analytics/anomalies.py` | Each detector has a fixture that fires and one that doesn't |
| 3.7 | Cash-flow forecast with resampled months, P10/P50/P90, assumptions | `analytics/forecast.py` | Fixed seed → identical output; under 3 months of history → low-confidence flag; integer arithmetic throughout |
| 3.8 | Affordability verdict | `analytics/affordability.py` | S7: verdict moves from comfortable to tight to not advisable as the amount grows; assumptions listed |
| 3.9 | Weekly and month-end briefs (templates, ≤10 lines) | `analytics/briefs.py` | Line count ≤ 10; order follows ARCH §9.11 |
| 3.10 | On-open alerts in chat; optional `launchd` job for the weekly brief | `cli/chat.py`, `cli/app.py` | Each alert shown once per new event |
| 3.11 | README sections for budgets, goals, recurring, forecasts | repo root | None |

### 6.6 Demo data after P3

Adds: a subscription price rise, a salary late in the final month (demo date pinned to the 5th), a groceries budget at 84% on day 19 in a test fixture, a first-time large merchant, a double charge, and scheduled entries for rent, EMI, SIP, and insurance.

### 6.7 Transcript script

1. Open chat → on-open alerts: missing salary (S5), subscription increase (S4).
2. "Groceries budget is ₹12,000 a month" → on the pinned day 19, the budget warning (S3).
3. "What are my subscriptions and what do they cost a year?"
4. "Forecast the next 90 days" → range and assumptions.
5. "Can I afford a ₹85,000 laptop next month?" → verdict, numbers, assumptions (S7); then "on a 6-month EMI?".
6. "Anything unusual this month?"
7. `/brief` → weekly brief, ≤10 lines.

### 6.8 Exit

S3, S4, S5, S7 pass; transcript; self-review; owner confirms.

---

## 7. P4: Output and polish

### 7.1 Goal

PS §11: *"Scenarios S1, S6, S8 pass."* Then the final acceptance in §8.

### 7.2 Entry criteria

P3 confirmed. Q5 answered. V7b, V8, V9, V10 checked.

### 7.3 Requirements covered

| Requirement | P4 extent | ARCH |
|---|---|---|
| FR-1 | Text PDF and password-protected PDF; OCR fallback | §7.2 |
| FR-3 | Receipts with consent and confirmation | §7.8 |
| FR-25 | All report kinds in Markdown, PDF, Excel, with charts | §13 |
| FR-30 | Local web surface | §12.3 |
| NFR-4 | Encrypted originals store | §14.5 |
| NFR-12 | Full export in open formats | §13 |
| PS §10 items 9–10 | README, PRIVACY.md, ARCHITECTURE.md final; known limitations and next three things | §20 |

### 7.4 Tools delivered

`generate_report`; `import_file` extended to PDFs and receipts; `export_data` extended to the full ledger.

### 7.5 Build order

| Step | Work | Module | Tests landed with it |
|---|---|---|---|
| 4.1 | Text PDF parser + Bank C profile; password-protected PDFs with Keychain-saved passwords | `ingest/parsers/pdf_text.py` | Page breaks mid-table and multi-line narrations parse; password never logged (V8) |
| 4.2 | OCR fallback as an optional extra (ocrmypdf + Tesseract) | `ingest/parsers/pdf_ocr.py` | Skipped with a clear message when Tesseract is missing |
| 4.3 | Encrypted originals store | `ingest/originals.py` | Stored file unreadable without the key; re-parse from the stored original matches |
| 4.4 | Inbox import (`finassist import --inbox`) for S1 | `ingest/pipeline.py`, `cli/app.py` | Mixed PDF + CSV inbox imports both; only exceptions go to review |
| 4.5 | Receipts: consent prompt, extraction, confirmation, attach to an existing transaction when matched | `ingest/parsers/receipt.py` | S6; no image sent without consent; total vs line-item mismatch shown (V7b) |
| 4.6 | Reports: monthly, annual, category, merchant, tax/reimbursement; Markdown, PDF, Excel; charts | `reports/` | S8; each figure in a report equals its query; ₹ renders (V9, V10) |
| 4.7 | Full export (CSV per table, JSON, XLSX) | `export/exporter.py` | Export → re-import into an empty DB reproduces the same totals |
| 4.8 | Web surface: overview, budgets, recurring, review queue on 127.0.0.1 | `web/` | Wrong Host header rejected; missing CSRF token rejected; no external asset URLs in served pages |
| 4.9 | Final docs: README with example conversations, PRIVACY.md (every outbound call), ARCHITECTURE.md updated, known limitations and next three things | repo root, `docs/` | PRIVACY.md table checked against `llm/` call sites by a test |
| 4.10 | Timed fresh setup on a new macOS user account | — | A10 |

### 7.6 Demo data after P4

Adds: Bank C text PDFs (one password-protected), synthetic receipt images, and a month's inbox containing a PDF statement and a card CSV with 3 planted duplicates and 2 unknown merchants for S1. Final dataset: 14 months, ~1,800 transactions, 5 accounts including a credit card, 4 statement layouts plus SMS/UPI.

### 7.7 Transcript script

1. Drop last month's PDF statement and card CSV into the inbox → `finassist import --inbox` → 3 duplicates and 2 uncategorised items flagged, and only those asked about (S1).
2. Add a synthetic restaurant receipt → consent → extraction → confirmed as a Cash transaction (S6).
3. "Give me the annual report as PDF and Excel" → files written; figures spot-checked against SQL (S8).
4. `finassist web` → overview, budgets, recurring list, review queue.
5. Full export → re-import into an empty DB → totals equal.

### 7.8 Exit

S1, S6, S8, A6, A10 pass; final acceptance (§8); self-review; owner confirms.

---

## 8. Final acceptance

| # | Criterion (PS §12) | Established in | Final check |
|---|---|---|---|
| A1 | Per-account closing balances reconcile to the rupee | P1 | `doctor` on the full demo dataset |
| A2 | Re-importing every file changes zero rows | P0, extended P1 and P4 | Content hash of all tables before and after |
| A3 | No transfer or card payment counted as expense | P1 | Dedicated test |
| A4 | ≥90% auto-categorised correctly; rest `needs_review` | P2 | Labelled fixture with held-out merchants |
| A5 | One correction applies retroactively and on next import | P1, P2 | Scenario S9 |
| A6 | All ten scenarios demonstrable end to end | P2–P4 | `tests/scenarios/` + transcript per scenario |
| A7 | 20 benchmark figures match direct SQL; "show me how" works | P2 | Live benchmark run, output shown |
| A8 | Out-of-range question declined | P0, P2 | Test + transcript |
| A9 | PRIVACY.md complete; local mode makes zero network requests | P2, P4 | Strict-guard run + PRIVACY.md test |
| A10 | Fresh setup to first answer under 15 minutes from the README | P4 | Timed run, recorded |

Dataset: the demo generator's full output (≥12 months, ≥1,500 transactions, ≥3 accounts including a credit card, as PS §12 requires).

---

## 9. Self-review template

Used at the end of every phase:

```
Phase <n> self-review
1. Test results          command run and output
2. Transcript            scenarios covered, demo dataset seed, pinned date
3. Deviations            each difference from PS or ARCH, with the reason
4. Assumptions           each assumption made, and how to confirm it
5. Verification log      V-items checked this phase, with result and date
6. Privacy               outbound calls added or changed; PRIVACY.md updated?
7. Cost and performance  measured LLM cost, import time, question latency
8. Open risks            anything the owner should know before the next phase
```

---

## 10. Risks to the plan

| Risk | Phase | Effect on the plan | Response |
|---|---|---|---|
| `sqlcipher3` fails on the owner's Mac (V1) | P0 | Step 0.5 blocked | Encrypted APFS volume for the data folder (ARCH §3.1); record as a deviation |
| Real bank layouts differ from the synthetic ones | P1, P4 | Parsers fail on first real import | Ask for column headers and one masked sample line per bank (Q2); mapping profiles keep fixes small |
| Classification stays below 90% (A4) | P2 | P2 exit blocked | More heuristics and seed merchants first; then a stricter review threshold, which keeps "rest marked needs_review" true at the cost of more review items |
| Grounding check rejects harmless numbers | P0–P2 | Answers withheld too often | Tune the allow-list in `agent/grounding.py` against benchmark transcripts |
| Local model too weak at tool use (V11) | P2 | Local mode unreliable for chat | Slash commands remain exact; document the limitation; local mode still meets A9 |
| Chat cost above the ARCH §11.4 estimate | P2 | Monthly bill higher than expected | Measured at P2 exit; levers: lower thinking level, a cheaper model (owner's decision), fewer tools per request |
| Forecast too wide to be useful with little history | P3 | S7 answers are vague | State the low-confidence reason; the demo has 14 months, so acceptance is unaffected |
| The receipt model lacks image input (V7b) | P4 | Receipt step changes | Use the provider's chat model for receipts; cost impact is small at 20 receipts a month |
| Gemini free-tier key used with real data | All | Personal financial data used for product improvement and human review | `llm.gemini_paid_tier` gate: the real ledger is never sent until the owner confirms a paid-tier key (ARCH §11.4) |
| Scope creep into advice or new features | All | Phases slip | Anything outside PS goes to the "next three things" list, not the current phase |

---

## Appendix A: requirement coverage by phase

✓ = delivered in full in that phase · ◐ = partly (completed later) · blank = not touched.

| Requirement | P0 | P1 | P2 | P3 | P4 |
|---|---|---|---|---|---|
| FR-1 statement import | ◐ CSV | ◐ XLSX/XLS, guided mapping | | | ✓ PDF, password PDF |
| FR-2 SMS / UPI | | ✓ | | | |
| FR-3 receipts | | | | | ✓ |
| FR-4 manual entry | ✓ | | | | |
| FR-5 scheduled entries | | | | ✓ | |
| FR-6 dedupe | | ✓ | | | |
| FR-7 idempotent re-import | ✓ | ✓ overlaps across sources | | | ✓ PDFs |
| FR-8 provenance | ✓ | | | | |
| FR-9 normalised record | ◐ | ◐ | ✓ merchant | | |
| FR-10 merchant normalisation | | | ✓ | | |
| FR-11 taxonomy | ✓ | | | | |
| FR-12 transfers not expense | | ✓ | | | |
| FR-13 classification pipeline | | ◐ rules | ✓ | | |
| FR-14 learning from corrections | | ✓ | | | |
| FR-15 splits | | | ✓ | | |
| FR-16 refunds | | | ✓ | | |
| FR-17 budgets | | | | ✓ | |
| FR-18 goals | | | | ✓ | |
| FR-19 recurring detection | | | | ✓ | |
| FR-20 expected income | | | | ✓ | |
| FR-21 NL Q&A | ◐ totals | | ✓ | | |
| FR-22 derived metrics | ◐ income, expense | | ◐ | ✓ month-end projection | |
| FR-23 forecast | | | | ✓ | |
| FR-24 affordability | | | | ✓ | |
| FR-25 reports | | | | | ✓ |
| FR-26 anomalies | | | | ✓ | |
| FR-27 briefs | | | | ✓ | |
| FR-28 explainability | ✓ | | ✓ all tools | | |
| FR-29 conversational interface | ✓ CLI | | | | |
| FR-30 visual surface | | | | | ✓ |
| FR-31 review queue | | ✓ CLI | | | ✓ web |
| FR-32 clarifying questions | | | ✓ | | |
| FR-33 multi-turn | ◐ history | | ✓ | | |
| NFR-1 correctness | ✓ | ✓ | ✓ | ✓ | ✓ |
| NFR-2 privacy, residency | ✓ | | | | |
| NFR-3 LLM minimisation, local mode | ◐ redaction | | ✓ | | ✓ PRIVACY.md final |
| NFR-4 security | ✓ | | | | ✓ originals |
| NFR-5 performance | | ◐ import | ✓ | | ✓ PDF import |
| NFR-6 reliability | ◐ transactional | ✓ backups, undo | | | |
| NFR-7 audit log | ✓ | | | | |
| NFR-8 extensibility | ✓ parser protocol | ✓ adding-a-bank doc | | | |
| NFR-9 testability | ✓ | ✓ | ✓ | ✓ | ✓ |
| NFR-10 INR, FY | ✓ | | | | |
| NFR-11 money handling | ✓ | | | | |
| NFR-12 portability | | | ◐ filtered export | | ✓ full export |

---

## Appendix B: tool delivery by phase

| Tool (PS §8 / ARCH §10.2) | Phase |
|---|---|
| `list_accounts`, `get_data_coverage`, `resolve_period` | P0 |
| `query_transactions`, `explain` | P0 |
| `import_file`, `commit_import` | P0 (CSV), P1 (XLSX, SMS, UPI), P4 (PDF, receipts) |
| `add_transaction` | P0 |
| `update_transaction` | P1 |
| `create_rule`, `reclassify` | P1 |
| `list_review_queue`, `resolve_review_item` | P1 |
| `undo` | P1 |
| `get_summary`, `compare_periods`, `search_transactions` | P2 |
| `split_transaction` | P2 |
| `export_data` | P2 (filtered), P4 (full) |
| preference write | P2 |
| `set_budget`, `get_budget_status` | P3 |
| `set_goal`, `get_goal_status` | P3 |
| `detect_recurring`, `list_recurring` | P3 |
| `forecast_cashflow`, `check_affordability` | P3 |
| `find_anomalies`, `get_brief` | P3 |
| `generate_report` | P4 |

---

## Appendix C: scenarios and acceptance criteria by phase

| Phase | Scenarios (PS §4) | Acceptance (PS §12) |
|---|---|---|
| P0 | — (P0 "done when" check) | A2 (CSV), A8 |
| P1 | S9 rule mechanics, S1 partly (CSV/XLSX only) | A1, A2, A3, A5 |
| P2 | S2, S9, S10 | A4, A7, A8, A9 |
| P3 | S3, S4, S5, S7 | — |
| P4 | S1, S6, S8 | A6, A9 (final PRIVACY.md), A10; full re-run of A1–A10 |
