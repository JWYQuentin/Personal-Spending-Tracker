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


Behavioral guidelines to reduce common LLM coding mistakes. Merge with project-specific instructions as needed.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

---

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.