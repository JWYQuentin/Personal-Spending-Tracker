import os
import sqlite3
import json
import sys
from datetime import datetime, timedelta
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()
DB_PATH = "spending.db"
client = OpenAI()

# Non-spending categories — excluded by default from spending totals
NON_SPENDING = ("cc_payment", "internal_transfer", "investment", "income", "transfer")

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

# ============ TOOLS ============

def tool_query_spending(start_date=None, end_date=None, category=None, group_by="category", include_non_spending=False):
    """Aggregate spending. Returns category/month totals."""
    conn = db()
    where = ["amount < 0"]
    params = []
    if start_date:
        where.append("date(posted, 'unixepoch') >= ?")
        params.append(start_date)
    if end_date:
        where.append("date(posted, 'unixepoch') <= ?")
        params.append(end_date)
    if category:
        where.append("my_category = ?")
        params.append(category)
    if not include_non_spending:
        placeholders = ",".join("?" * len(NON_SPENDING))
        where.append(f"my_category NOT IN ({placeholders})")
        params.extend(NON_SPENDING)

    group_clauses = {
        "category": "my_category",
        "month": "strftime('%Y-%m', datetime(posted, 'unixepoch'))",
        "category_month": "my_category, strftime('%Y-%m', datetime(posted, 'unixepoch'))",
    }
    group_sql = group_clauses.get(group_by, "my_category")

    sql = f"""
        SELECT {group_sql} AS bucket,
               COUNT(*) AS n,
               ROUND(SUM(amount), 2) AS total
        FROM transactions
        WHERE {' AND '.join(where)}
        GROUP BY {group_sql}
        ORDER BY total ASC
    """
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    conn.close()
    return {"rows": rows, "filters": {"start_date": start_date, "end_date": end_date, "category": category}}

def tool_list_transactions(start_date=None, end_date=None, category=None, min_amount=None, max_amount=None, search=None, limit=50):
    """List individual transactions matching filters."""
    conn = db()
    where = []
    params = []
    if start_date:
        where.append("date(posted, 'unixepoch') >= ?")
        params.append(start_date)
    if end_date:
        where.append("date(posted, 'unixepoch') <= ?")
        params.append(end_date)
    if category:
        where.append("my_category = ?")
        params.append(category)
    if min_amount is not None:
        where.append("amount >= ?")
        params.append(min_amount)
    if max_amount is not None:
        where.append("amount <= ?")
        params.append(max_amount)
    if search:
        where.append("description LIKE ?")
        params.append(f"%{search}%")

    where_sql = "WHERE " + " AND ".join(where) if where else ""
    sql = f"""
        SELECT date(posted, 'unixepoch') AS date,
               ROUND(amount, 2) AS amount,
               description,
               my_category AS category
        FROM transactions
        {where_sql}
        ORDER BY posted DESC
        LIMIT ?
    """
    params.append(limit)
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    conn.close()
    return {"rows": rows, "count": len(rows)}

def tool_get_balances():
    """Current balance of each account."""
    conn = db()
    rows = conn.execute("""
        SELECT org, name, ROUND(balance, 2) AS balance, currency,
               date(balance_date, 'unixepoch') AS as_of
        FROM accounts ORDER BY org, name
    """).fetchall()
    conn.close()
    return {"accounts": [dict(r) for r in rows]}

def tool_run_sql(sql):
    """Read-only SQL escape hatch. Rejects anything other than SELECT."""
    stripped = sql.strip().rstrip(";")
    if not stripped.lower().startswith("select"):
        return {"error": "Only SELECT statements are allowed."}
    forbidden = ("insert", "update", "delete", "drop", "alter", "attach", "pragma")
    if any(word in stripped.lower() for word in forbidden):
        return {"error": "Query contains forbidden keyword."}
    try:
        conn = db()
        rows = [dict(r) for r in conn.execute(stripped).fetchall()]
        conn.close()
        return {"rows": rows[:200], "truncated": len(rows) > 200}
    except Exception as e:
        return {"error": str(e)}

TOOLS_IMPL = {
    "query_spending": tool_query_spending,
    "list_transactions": tool_list_transactions,
    "get_balances": tool_get_balances,
    "run_sql": tool_run_sql,
}

# ============ TOOL SCHEMAS (for OpenAI) ============

TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "query_spending",
            "description": "Aggregate spending totals. Use for 'how much did I spend on X' or 'spending by category last month'. Excludes credit-card payments, transfers, investments, and income by default.",
            "parameters": {
                "type": "object",
                "properties": {
                    "start_date": {"type": "string", "description": "YYYY-MM-DD, inclusive"},
                    "end_date": {"type": "string", "description": "YYYY-MM-DD, inclusive"},
                    "category": {"type": "string", "description": "Filter to a single category"},
                    "group_by": {"type": "string", "enum": ["category", "month", "category_month"], "description": "How to group results"},
                    "include_non_spending": {"type": "boolean", "description": "Set true to include transfers, investments, cc payments"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_transactions",
            "description": "List individual transactions. Use when the user wants specifics like 'show me all Amazon purchases' or 'biggest charges last week'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "start_date": {"type": "string"},
                    "end_date": {"type": "string"},
                    "category": {"type": "string"},
                    "min_amount": {"type": "number", "description": "Note: spending is negative. -100 means $100 or more."},
                    "max_amount": {"type": "number"},
                    "search": {"type": "string", "description": "Match within description field"},
                    "limit": {"type": "integer", "default": 50},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_balances",
            "description": "Current balance of every account.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_sql",
            "description": "Read-only SELECT against the database. Schema: transactions(id, account_id, posted INTEGER unix, amount REAL, description TEXT, payee, memo, my_category). accounts(id, org, name, currency, balance, balance_date INTEGER unix). Use only when other tools don't fit.",
            "parameters": {
                "type": "object",
                "properties": {"sql": {"type": "string"}},
                "required": ["sql"],
            },
        },
    },
]

# ============ AGENT LOOP ============

SYSTEM_PROMPT = f"""You are a personal spending assistant. The user has connected bank accounts and credit cards. You answer questions about their spending by calling tools that query a local SQLite database.

Today's date is {datetime.now().strftime('%Y-%m-%d')}.

Guidelines:
- For relative dates ("last month", "this week"), compute actual YYYY-MM-DD ranges from today's date.
- Spending amounts are NEGATIVE in the data. When presenting to the user, show as positive dollar amounts ("$1,234.56 on dining") — only show negative if specifically distinguishing inflow vs outflow.
- The categories cc_payment, internal_transfer, and investment are NOT real spending — they are money moving between the user's own accounts. By default, query_spending excludes them.
- Be concise. Lead with the number. Add a one-line interpretation if useful.
- If a question is ambiguous (e.g. "last month" said on the 1st of a month), ask one clarifying question rather than guessing.
- Don't fabricate. If a tool returns no rows, say so."""

def run_agent(user_message, conversation=None):
    if conversation is None:
        conversation = [{"role": "system", "content": SYSTEM_PROMPT}]
    conversation.append({"role": "user", "content": user_message})

    while True:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=conversation,
            tools=TOOLS_SCHEMA,
            tool_choice="auto",
        )
        msg = response.choices[0].message
        conversation.append(msg.model_dump(exclude_none=True))

        if not msg.tool_calls:
            return msg.content, conversation

        for tc in msg.tool_calls:
            name = tc.function.name
            args = json.loads(tc.function.arguments or "{}")
            print(f"  → calling {name}({', '.join(f'{k}={v!r}' for k,v in args.items())})")
            try:
                result = TOOLS_IMPL[name](**args)
            except Exception as e:
                result = {"error": str(e)}
            conversation.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": json.dumps(result, default=str),
            })

def repl():
    print("Spending agent. Type 'quit' to exit.\n")
    conversation = None
    while True:
        try:
            q = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not q:
            continue
        if q.lower() in ("quit", "exit"):
            break
        try:
            answer, conversation = run_agent(q, conversation)
            print(f"\nagent> {answer}\n")
        except Exception as e:
            print(f"\nerror: {e}\n")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        # One-shot: python agent.py "your question"
        question = " ".join(sys.argv[1:])
        answer, _ = run_agent(question)
        print(answer)
    else:
        # Interactive REPL
        repl()
