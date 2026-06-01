# Personal Spending Tracker

A single-user, local-first personal finance tool. It pulls bank and credit-card
transactions via [SimpleFIN](https://www.simplefin.org/), categorizes them, and
lets you explore your spending through a chat agent or a minimalist dashboard.
All data stays on your machine in a local SQLite file.

## Stack

Python 3, SQLite (stdlib `sqlite3`), the OpenAI API, and Streamlit for the dashboard.

## Setup

```bash
# 1. Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 2. Install dependencies
pip install openai python-dotenv requests streamlit pandas openpyxl

# 3. Create a .env file with your credentials (this file is gitignored)
cat > .env <<'EOF'
SIMPLEFIN_ACCESS_URL=<your SimpleFIN access URL>
OPENAI_API_KEY=<your OpenAI API key>
EOF
```

The database (`spending.db`) is created automatically on first sync.

## Usage

```bash
python sync.py          # Pull the last 90 days of accounts and transactions into spending.db
python categorize.py    # Assign a category to every uncategorized transaction
python agent.py         # Chat REPL — ask questions about your spending
python agent.py "how much did I spend on dining last month?"   # one-shot question

python export_excel.py       # Write transactions + summaries to spending.xlsx
streamlit run dashboard.py   # Open the spending dashboard in your browser
```

A typical session is just sync → categorize → explore.

## How it works

- **`sync.py`** — Fetches accounts and transactions from SimpleFIN's `/accounts`
  endpoint. Idempotent: each transaction's primary key is a SHA-256 hash of
  `(account_id, posted, amount, description)`, so re-running never duplicates rows.
  Creates the schema if it's missing.
- **`categorize.py`** — Assigns `my_category` to uncategorized rows in two layers:
  a deterministic list of regex rules runs first (free), and anything unmatched
  goes to OpenAI `gpt-4o-mini` in batches. Non-spending categories
  (`cc_payment`, `internal_transfer`, `investment`, `income`, `transfer`) are
  tracked separately and excluded from spending totals.
- **`agent.py`** — A chat REPL that answers questions using OpenAI function-calling
  over four tools: `query_spending`, `list_transactions`, `get_balances`, and a
  read-only `run_sql`.
- **`dashboard.py`** — A read-only Streamlit dashboard: summary totals (spent /
  income / net), spending by category, spending over time, top transactions, and
  account balances, all filterable by time range.
- **`export_excel.py`** — A read-only export that regenerates `spending.xlsx` from
  the database: one tab of all transactions (with payment method), one of spending
  by category, and one of account balances. Runs automatically before the agent via
  the `track` launcher.

## Data

Two SQLite tables in `spending.db`:

- `accounts(id, org, name, currency, balance, balance_date)` — `balance_date` is unix epoch seconds.
- `transactions(id, account_id, posted, amount, description, payee, memo, my_category)` —
  `posted` is unix epoch seconds; spending is negative, income is positive.

Note: `spending.db` and `.env` are gitignored, so a fresh clone starts with no
data — run `sync.py` and `categorize.py` to populate it.
