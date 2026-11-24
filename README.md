# Coinleaf — personal financial assistant

A local, encrypted ledger of your bank, card and cash transactions with a conversational front end. You import the statement files you already have, and ask questions in plain words. Every figure is computed from the ledger by code, never estimated by the language model, and every figure can show the transactions behind it.

> **Status: Phase P0 (skeleton).** CSV import, manual entry, the query engine, and chat with the behaviour contract are in place. Dedupe, transfer detection and reconciliation come in P1; categorisation and full Q&A in P2; budgets and forecasts in P3; PDFs, receipts and reports in P4. See [docs/implementationplan.md](docs/implementationplan.md).

- Design: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- What leaves your machine: [PRIVACY.md](PRIVACY.md)
- Requirements: [docs/problem_statement.md](docs/problem_statement.md)

## Setup (macOS, Windows, Linux)

Runs on macOS (Apple Silicon and Intel), Windows 10/11 (x64 and ARM) and Linux (x64 and ARM; glibc 2.28+ or musl), with Python 3.12 to 3.14. Every dependency installs as a ready-built package, so no compiler is needed. You need [uv](https://docs.astral.sh/uv/), which also installs Python if needed:

| System | Install uv (once) |
|---|---|
| macOS, Linux | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| Windows (PowerShell) | `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 \| iex"` |

Then, in the project folder:

```sh
uv sync                 # installs exact pinned versions from uv.lock
uv run coinleaf demo   # builds a separate demo ledger from synthetic data
```

**Where the ledger key is kept:** in the operating system's own secret store: the macOS Keychain, the Windows Credential Manager, or on Linux the Secret Service keyring (GNOME Keyring or KWallet, which desktop Linux has by default). On a Linux machine with no keyring (e.g. a server), the app says so and stops. You can then install and unlock one, or set `FINASSIST_KEYSTORE=file` to keep the key in an owner-only file in the data folder instead. That is less protected, because anyone who can read your account's files could then open the ledger.

## Start the app

Double-click the launcher for your system in the project folder, or run the command below from any system:

| System | Launcher |
|---|---|
| macOS | **`Coinleaf.command`** (double-click in Finder) |
| Windows | **`Coinleaf.bat`** (double-click in File Explorer) |
| Linux | **`./coinleaf.sh`** in a terminal (most file managers won't run scripts on double-click) |


```sh
uv run coinleaf
```

Your browser opens the app at `http://127.0.0.1:…`. It runs only on your computer: nothing outside it can connect. Keep the terminal window open while you use it; close it (or press Ctrl+C) to stop.

**Signing in.** The first time, Coinleaf asks you to **create a password** (at least 8 characters). Only the browser opened by the link printed in the terminal can do this, so nobody else on the computer can claim the app first. After that, every visit starts at the **sign-in page**; `http://127.0.0.1:8765` works as a bookmark. Coinleaf **locks itself after 15 minutes** without mouse or keyboard use (the page clears, so figures don't stay on screen), and the **Lock** button in the sidebar locks it at once. Restarting Coinleaf also signs you out. After 5 wrong passwords each further try has to wait: 30 seconds, then 60, and so on up to 15 minutes. Change the password in **Settings**. If you forget it, run `uv run coinleaf password reset`, then start Coinleaf again and create a new one; your ledger isn't affected.

The password protects the open app. It doesn't encrypt anything: the ledger is already encrypted with a key in the OS secret store, and anyone signed in to your computer account can reset the app password.

The app has six pages:

| Page | What you see and do |
|---|---|
| **Dashboard** | Available balance, net worth, money in / out / saved with the change from the previous period, a 12-month income and spending chart, spending per day, top payees, how you paid, where your money is, and recent transactions. Pick any month or period and any account at the top: every number on the page follows. |
| **Transactions** | A searchable, filterable list (period, account, money in/out, text) with totals and **Export CSV**. |
| **Import statement** | Upload a CSV or text-PDF statement (password-protected PDFs too), check the preview (rows, new vs already imported, balance check), then click Import. |
| **Add transaction** | A short form for cash and anything not in a statement, with a preview before saving. |
| **Accounts** | Balances, which dates each account has data for, and adding accounts. |
| **Settings** | Light/dark theme, where your data lives, and rebuilding the demo. |

Every chart has hover and keyboard tooltips and a **Show as table** view. A new, empty ledger opens on a **Get started** checklist instead of empty charts. The browser app uses no AI and makes no internet connections; the AI chat remains available in the terminal (`uv run coinleaf chat`).

Switch between **Demo** (synthetic data) and **My ledger** at the top left. If you haven't created your own ledger yet, the app opens on the Demo.

The command line is still there for scripting and for the tests: `uv run coinleaf --help`.

### Optional: the language model

Plain-English questions use **Google Gemini** by default (`gemini-3.8-flash`); Anthropic Claude is available with `provider = "anthropic"` in the config. Without an API key, chat still works with slash commands (`/spend october`, `/how`, …).

1. Create a Gemini API key in Google AI Studio. **Use a key from a Google Cloud project with billing enabled (paid tier).** On the free tier, Google may use what you send to improve its products and human reviewers may read it; Google's terms say not to send personal information there. See [PRIVACY.md](PRIVACY.md).
2. Store the key:
   ```sh
   uv run coinleaf config set-api-key   # Gemini by default; stored in the OS secret store
   ```
3. Try it on the demo first (synthetic data; works with any key):
   ```sh
   uv run coinleaf --demo chat
   › total spend in October
   ```
4. For your real ledger, confirm the paid tier in `config.toml` in the data folder (see *Your own data* for where that is):
   ```toml
   [llm]
   gemini_paid_tier = true
   ```
   Until you do, chat on your real ledger stays on slash commands and nothing is sent to Gemini.

The app also reads `GEMINI_API_KEY` (or `GOOGLE_API_KEY`) from the environment, or from a `.env` file in the current folder that only you can read (`chmod 600 .env` on macOS/Linux; see `.env.example`). Set `llm_mode = "off"` in the config to never use a model.

## Your own data

```sh
uv run coinleaf init                         # encrypted ledger; key goes into the OS secret store
uv run coinleaf account add                  # e.g. your savings account, a card, Cash
uv run coinleaf import ~/Downloads/stmt.csv   # preview first, then confirm
uv run coinleaf                              # opens the app in your browser (default command)
```

Data lives in the standard per-user app-data folder (override with `FINASSIST_HOME`); the app's **Settings** page shows the exact path:

| System | Data folder |
|---|---|
| macOS | `~/Library/Application Support/finassist/` |
| Windows | `%LOCALAPPDATA%\finassist\` |
| Linux | `~/.local/share/finassist/` (or `$XDG_DATA_HOME/finassist/`) |

The database is encrypted with SQLCipher; the 256-bit key is kept in the OS secret store (see Setup). The folder, the Python package and the secret-store entries keep the project's original internal name, `finassist`, so ledgers and keys created before the app was named Coinleaf keep working. The `finassist` command also still works as an alias for `coinleaf`. Configuration: copy [config.example.toml](config.example.toml) to that folder as `config.toml`.

Two statement layouts are built in (Bank A savings, Card A credit card), each as CSV and as text PDF (`src/finassist/ingest/banks/*.toml`). Password-protected PDFs work: the password opens the file in memory and is never saved. Text PDFs from other banks are read by automatic layout detection: common column headings (Date, Narration/Description/Particulars, Withdrawal/Debit, Deposit/Credit, Amount, Balance) are recognised, and the preview says which heading became which column so you can check before importing. Scanned (image-only) PDFs are refused (OCR arrives later). Adding a layout means adding one TOML file there.

From the terminal: `uv run coinleaf import statement.pdf --ask-password`.

## Commands

| Command | What it does |
|---|---|
| `coinleaf` / `coinleaf gui` | Open the app in your browser (default) |
| `coinleaf chat` | Conversational assistant in the terminal |
| `coinleaf init` | Create the encrypted ledger |
| `coinleaf account add` / `account list` | Manage accounts |
| `coinleaf import FILE [--account NAME]` | Preview and import a statement (asks before committing) |
| `coinleaf add` | Add one transaction with a short form |
| `coinleaf spend PERIOD` / `income PERIOD` | Totals without the language model |
| `coinleaf demo [--reset]` | Build the demo ledger |
| `coinleaf password reset` | Forget the browser app's password so a new one can be created (data untouched) |
| `coinleaf config set-api-key [--provider gemini\|anthropic]` | Store an API key in the OS secret store (Keychain / Credential Manager / keyring) |

Add `--demo` before any command to use the demo ledger, e.g. `coinleaf --demo spend last month`.

In chat: `/spend <period>`, `/income <period>`, `/accounts`, `/coverage`, `/how [query_id]`, `/import <file> [account]`, `/help`, `/quit`.

Periods are written as words or dates: `october`, `oct 2025`, `last month`, `last 3 months`, `Q3` (Indian FY quarters by default), `FY2025-26`, `2026-10-01 to 2026-10-31`. Every answer states the exact dates it used.

## Example session (demo data, no API key)

From [docs/transcripts/p0-demo-session.md](docs/transcripts/p0-demo-session.md):

```
› /spend october
Money out October 2026: ₹1,79,510.11 (88 transactions)
Scope q_ca3201: 1–31 Oct 2026 · all accounts · kinds: expense · excl. transfers · money out shown as positive ·
88 transactions · 84 uncategorised (₹1,78,995.11) · /how q_ca3201

› /spend march 2019
No data for March 2019: Savings account has no statement for 1–31 Mar 2019; Credit card has no statement for 1–31 Mar 2019
```

Most rows show as uncategorised in P0: classification arrives in P1 (rules) and P2 (merchants, LLM residue).

## Development

```sh
uv run pytest            # 220+ tests, synthetic data only, no network, no OS secret store
uv run ruff check src tests scripts && uv run ruff format --check src tests scripts
uv run mypy              # strict on core, db, analytics
uv run lint-imports      # layer contracts (ARCH §5)
git config core.hooksPath .githooks   # refuse fixture files without the synthetic-data marker
```

CI (`.github/workflows/ci.yml`) runs all of these on Windows, Linux, macOS (Apple Silicon and Intel) with Python 3.12 and 3.14 once the project is pushed to GitHub.

Tests never use real financial data. Fixtures are produced by `src/finassist/demo/generator.py`, and every generated file carries a marker line that the pre-commit hook checks.

## Known limitations (P0)

- Demo data v1 has no card bill payments, transfers, ATM withdrawals or refunds, because P0 has no transfer detection; so the demo card balance keeps growing. Generator v2 (P1) adds those flows.
- CSV: only the two built-in layouts. PDF: built-in layouts plus automatic detection for common text-PDF tables. No scanned PDFs or Excel yet.
- Almost everything is uncategorised until P1/P2; spending totals count all money out.
- Local-model mode (`llm_mode = "local"`) arrives in P2; in P0 it falls back to slash commands.
- The chat path against a live model has not been exercised with a valid key yet; see the P0 self-review.
