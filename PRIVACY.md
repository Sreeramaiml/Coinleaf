# Privacy: what data goes where

> **Draft for Phase P0.** Updated whenever an outbound call is added or changed (implementation plan §1.2). Final version in P4.

## Summary

- Your ledger stays on your computer, in an encrypted SQLCipher database. The key is in the operating system's secret store: the macOS Keychain, the Windows Credential Manager, or the Linux Secret Service keyring (GNOME Keyring / KWallet).
- No bank logins, no scraping, no telemetry, no analytics, no update checks, no crash reports.
- In P0 there is exactly **one** kind of outbound call: chat messages to the configured LLM provider. That is **Google's Gemini API** by default, or Anthropic's API if you choose it. Calls happen only when `privacy.llm_mode = "cloud"` (the default), an API key is available, and, for Gemini with your real ledger, you have confirmed a paid-tier key (below).
- Every other command (`import`, `add`, `spend`, `demo`, …) runs with networking limited to this machine, enforced in code.

## Outbound calls in P0

| Feature | Destination | When | What is sent | What is never sent |
|---|---|---|---|---|
| Chat (plain-English questions) | `generativelanguage.googleapis.com` (Gemini, default) or `api.anthropic.com` (if `provider = "anthropic"`) | `llm_mode = "cloud"` and you type a question (not a slash command) | see below | account numbers, last-4 digits, institution names, full transaction lists, statement files, the database key |
| Everything else | — | — | nothing | — |

Only one host is allowed per run: the configured provider's (`llm.api_host`).

## Gemini: free tier versus paid tier

Google's Gemini API terms (effective 23 March 2026, read 23 November 2026) treat the two tiers differently:

- **Free / unpaid tier:** Google "uses the content you submit to the Services and any generated responses to provide, improve, and develop" its products; "human reviewers may read, annotate, and process your API input and output"; and the terms say: *"Do not submit sensitive, confidential, or personal information to the Unpaid Services."*
- **Paid tier** (API key from a Google Cloud project with an active billing account): "Google doesn't use your prompts … or responses to improve our products", and processes them under its Data Processing Addendum. Google still "logs prompts and responses for a limited period of time, solely for detecting and preventing violations" of its Prohibited Use Policy. The data "may be stored transiently or cached in any country in which Google or its agents maintain facilities."

Your ledger is personal financial data, so Coinleaf enforces this:

- **Your real ledger is never sent to Gemini until you set `gemini_paid_tier = true`** under `[llm]` in `config.toml`. That setting is your confirmation that the key belongs to a billing-enabled project. Coinleaf cannot check this itself: the API doesn't report the tier.
- The **demo ledger** (synthetic data only) may use any Gemini key.
- Requests are sent with `store = false` (`llm.gemini_store`), which asks Google not to log the request. Google's abuse-monitoring logging on the paid tier still applies.

Check Google's current terms at https://ai.google.dev/gemini-api/terms before using cloud mode with real data. If you use Anthropic instead, check https://www.anthropic.com/legal.

## What a chat request contains

1. **The system prompt**: the assistant's rules, today's date, your time zone, currency and FY start month. No personal data.
2. **Tool definitions**: names and argument schemas. No personal data.
3. **Your messages** exactly as you typed them.
4. **Tool results, compact and redacted** (the `for_model` part of each result):
   - account **nicknames** you chose (e.g. "Savings account"), account type, currency, current balance;
   - the period and filters of each query; totals, counts, averages; coverage dates; how many rows are uncategorised;
   - when you ask for them, up to 10 of the largest matching transactions (date, account nickname, description, amount);
   - for "show me how", the filter and at most 5 example transactions (the full list is shown only on your screen);
   - for imports: the file name, account nickname, row counts, money in/out totals and the file balance check.
5. **The model's own earlier replies in the conversation**, sent back unchanged. Gemini requires its "thought signatures" to be returned in multi-turn tool use.

Before tool results are sent, every string is redacted (`src/finassist/llm/redact.py`):

| Pattern | Replaced with |
|---|---|
| any run of 8+ digits (account numbers, UTR/RRN/ARN references, phone numbers) | `[NUM]` |
| card-number shapes (`1234 5678 9012 3456`) | `[NUM]` |
| masked account numbers (`XXXXXXXX4821`, `XXXX XXXX XXXX 9013`) | `[ACCT]` |
| IFSC codes | `[IFSC]` |
| PAN-shaped values | `[PAN]` |
| email addresses | `[EMAIL]` |
| UPI VPAs (`name@bank`) | `[VPA]` |
| +91 phone numbers | `[PHONE]` |

Amounts are not redacted: they are the answer.

**Not yet redacted in P0:** names of people inside UPI/NEFT narrations (e.g. a landlord's name in a rent transfer) can appear in descriptions when you ask for individual transactions. Pseudonyms for these (`PERSON_1`, …) arrive in P2. If that matters to you before P2, set `llm_mode = "off"` and use slash commands.

## The browser app

`coinleaf` opens a page served by the app itself at `http://127.0.0.1:<port>`. It listens on your computer only (127.0.0.1), accepts only requests addressed to that exact local address, requires you to sign in with your Coinleaf password (first-time setup is only possible from the launch link printed in the terminal), and loads no scripts, fonts or images from the internet. **The browser app uses no AI and makes no outbound connections at all**: it runs under the strict, loopback-only network guard. Only the terminal command `coinleaf chat` contacts an AI provider, as described above. Your theme choice is stored in the browser; nothing else is.

## How "nothing else leaves" is enforced

`src/finassist/llm/netguard.py` installs a Python audit hook at start-up. It cannot be removed once installed. It checks every DNS lookup and socket connection:

- `chat` in cloud mode: lookups are allowed only for the configured provider's host, and connections only to the addresses that lookup returned (or to this machine).
- every other command, and `local` / `off` modes: only this machine (loopback).

A blocked attempt raises an error and is recorded. Tests run the guard in a separate process and confirm that other hosts are blocked (`tests/unit/test_llm.py`). It was also checked against both SDKs on 23 November 2026: with a dummy key, cloud mode reached only the provider's host, and strict mode blocked even that.

## What is stored locally, and where

| Data | Location | Protection |
|---|---|---|
| Ledger (transactions, provenance, audit log, query receipts) | `ledger.db` in the data folder: macOS `~/Library/Application Support/finassist/`, Windows `%LOCALAPPDATA%\finassist\`, Linux `~/.local/share/finassist/` | SQLCipher, 256-bit key in the OS secret store; owner-only file (mode 0600 on macOS/Linux; on Windows the per-user profile folder's access rules) |
| API keys | OS secret store (or `GEMINI_API_KEY` / `GOOGLE_API_KEY` / `ANTHROPIC_API_KEY`, or an owner-only `.env`) | OS secret store |
| LLM usage log | inside the encrypted ledger (`llm_call` table) | time, provider, model, token counts, cost estimate, number of redactions. **Never** the messages or results |
| Browser app password | `app-password.json` in the data folder: a salted scrypt hash only, never the password; owner-only file | resettable with `coinleaf password reset` by anyone signed in to your computer account |
| Sign-in sessions | memory only; end after 15 minutes without use, on Lock, and when Coinleaf stops | HttpOnly, SameSite=Strict cookie |
| Chat history (terminal and browser app) | memory only; not written to disk | — |
| Statements uploaded in the browser app | `…/finassist/uploads/`, mode 0600, deleted after import or cancel and on every start | data folder permissions |
| PDF statement passwords | never stored: used once to open the file in memory, then dropped; not logged, not saved in the browser | — |
| Demo ledger | `…/finassist/demo/` with its key in an owner-only file | synthetic data only |
| Statement files you import | stay where you put them; P0 does not copy them (encrypted copies arrive in P4) | your file system |

If you opt in with `FINASSIST_KEYSTORE=file` (for a Linux machine with no keyring), the ledger key is kept in an owner-only file in the data folder instead of the secret store. Anyone who can read your account's files can then open the ledger.

## Turning the model off

```toml
# config.toml in the data folder (macOS ~/Library/Application Support/finassist/, Windows %LOCALAPPDATA%\finassist\, Linux ~/.local/share/finassist/)
[privacy]
llm_mode = "off"
```

or `FINASSIST_LLM_MODE=off Coinleaf chat` for one session. Slash commands give exact figures without any model.
