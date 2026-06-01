# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A personal spending tracker. Three independent scripts share one SQLite database (`spending.db`) and form a pipeline: **sync → categorize → query via agent**. There is no package, no test suite, and no git history — each script is run directly.

## Commands

The project uses a local virtualenv at `.venv` (Python 3.12). Always invoke through it:

```bash
.venv/bin/python sync.py                      # 1. Pull last 90 days of accounts + transactions from SimpleFIN
.venv/bin/python categorize.py                # 2. Assign my_category to every uncategorized transaction
.venv/bin/python agent.py "how much on dining last month?"   # 3a. One-shot question
.venv/bin/python agent.py                      # 3b. Interactive REPL (type 'quit' to exit)
```

Secrets live in `.env` (gitignored, loaded via `python-dotenv`): `SIMPLEFIN_ACCESS_URL` (used by sync.py) and `OPENAI_API_KEY` (used by categorize.py and agent.py).

## Architecture

**`sync.py`** — `init_db()` creates the schema (idempotent, `CREATE TABLE IF NOT EXISTS`) and `sync()` fetches from the SimpleFIN `/accounts` endpoint. Transaction IDs are a SHA-256 hash of `account_id|posted|amount|description` (truncated to 16 chars), so re-syncing is safe — accounts upsert their balance, transactions use `INSERT OR IGNORE`. New transactions land with `my_category = NULL`.

**`categorize.py`** — two-stage categorization of NULL-category rows. First `apply_rules()` runs an ordered list of regexes (`RULES`); **order matters** — non-spending patterns (cc_payment, internal_transfer, investment, transfer) must precede merchant patterns so a credit-card payment isn't mistaken for shopping. Anything unmatched is batched (25 at a time) to OpenAI `gpt-4o-mini` with a forced JSON response. The canonical category list is `CATEGORIES`; LLM results outside that list are coerced to `other`.

**`agent.py`** — a tool-calling loop over `gpt-4o-mini`. It exposes four tools to the model (`query_spending`, `list_transactions`, `get_balances`, `run_sql`) and loops calling tools until the model returns a final text answer. `run_sql` is a read-only escape hatch that rejects non-SELECT statements and blacklisted keywords.

## Conventions that span files

- **Spending is stored as negative amounts; income/refunds positive.** Aggregations filter `amount < 0` for spending; the agent's system prompt instructs the model to present spending as positive dollars.
- **`NON_SPENDING` categories** (`cc_payment`, `internal_transfer`, `investment`, `income`, `transfer`) are money moving between the user's own accounts or earnings — not real spending. `query_spending` excludes them unless `include_non_spending=True`. The set of non-spending categories appears in both `agent.py` (`NON_SPENDING`) and `categorize.py` (`CATEGORIES`/rules) — keep them consistent when adding categories.
- **`posted` and `balance_date` are Unix-epoch integers.** SQL converts with `date(posted, 'unixepoch')` / `datetime(...)`. Date filters take `YYYY-MM-DD` strings.

## Schema

```
accounts(id TEXT PK, org, name, currency, balance REAL, balance_date INTEGER)
transactions(id TEXT PK, account_id FK, posted INTEGER, amount REAL,
             description TEXT, payee, memo, my_category TEXT)
```

`my_category` is NULL until `categorize.py` fills it.
