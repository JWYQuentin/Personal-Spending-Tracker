# Launch with: streamlit run dashboard.py
import re
import sqlite3
from datetime import date, timedelta

import pandas as pd
import streamlit as st

DB_PATH = "spending.db"

# Categories that are not real spending (money between own accounts / earnings).
# Kept in sync with NON_SPENDING in agent.py; inlined to keep this file standalone.
NON_SPENDING = ("cc_payment", "internal_transfer", "investment", "income", "transfer")

# Maps the range selector to an inclusive lower-bound date (YYYY-MM-DD) or None for "All".
def _start_date(range_label):
    today = date.today()
    if range_label == "1 week":
        return (today - timedelta(days=7)).isoformat()
    if range_label == "2 weeks":
        return (today - timedelta(days=14)).isoformat()
    if range_label == "30 days":
        return (today - timedelta(days=30)).isoformat()
    if range_label == "90 days":
        return (today - timedelta(days=90)).isoformat()
    if range_label == "Year to date":
        return date(today.year, 1, 1).isoformat()
    return None  # "All"


def _connect():
    return sqlite3.connect(DB_PATH, detect_types=0)


@st.cache_data
def summary(range_label):
    """Returns total spending for the period as a positive dollar amount."""
    start = _start_date(range_label)
    placeholders = ",".join("?" * len(NON_SPENDING))
    date_clause = "AND date(posted, 'unixepoch') >= ?" if start else ""

    conn = _connect()
    try:
        spent_params = list(NON_SPENDING) + ([start] if start else [])
        spent = conn.execute(
            f"""SELECT COALESCE(SUM(amount), 0) FROM transactions
                WHERE amount < 0 AND my_category NOT IN ({placeholders}) {date_clause}""",
            spent_params,
        ).fetchone()[0]
    finally:
        conn.close()

    return -spent  # spending is stored negative; present as positive


@st.cache_data
def by_category(range_label):
    start = _start_date(range_label)
    placeholders = ",".join("?" * len(NON_SPENDING))
    date_clause = "AND date(posted, 'unixepoch') >= ?" if start else ""
    params = list(NON_SPENDING) + ([start] if start else [])

    conn = _connect()
    try:
        rows = conn.execute(
            f"""SELECT my_category AS category, -SUM(amount) AS amount
                FROM transactions
                WHERE amount < 0 AND my_category NOT IN ({placeholders}) {date_clause}
                GROUP BY my_category
                ORDER BY amount DESC""",
            params,
        ).fetchall()
    finally:
        conn.close()
    return pd.DataFrame(rows, columns=["category", "amount"])


@st.cache_data
def over_time(range_label):
    """Daily spending series; weekly buckets for long ranges (>60 days)."""
    start = _start_date(range_label)
    placeholders = ",".join("?" * len(NON_SPENDING))
    date_clause = "AND date(posted, 'unixepoch') >= ?" if start else ""
    params = list(NON_SPENDING) + ([start] if start else [])

    conn = _connect()
    try:
        rows = conn.execute(
            f"""SELECT date(posted, 'unixepoch') AS day, -SUM(amount) AS amount
                FROM transactions
                WHERE amount < 0 AND my_category NOT IN ({placeholders}) {date_clause}
                GROUP BY day
                ORDER BY day""",
            params,
        ).fetchall()
    finally:
        conn.close()

    df = pd.DataFrame(rows, columns=["day", "amount"])
    if df.empty:
        return df, "daily"

    df["day"] = pd.to_datetime(df["day"])
    df = df.set_index("day")
    span_days = (df.index.max() - df.index.min()).days

    if range_label in ("All", "Year to date") and span_days > 60:
        weekly = df.resample("W").sum()
        return weekly, "weekly"
    # Fill missing days with 0 so the line reads continuously.
    daily = df.resample("D").sum()
    return daily, "daily"


@st.cache_data
def top_transactions(range_label):
    start = _start_date(range_label)
    placeholders = ",".join("?" * len(NON_SPENDING))
    date_clause = "AND date(posted, 'unixepoch') >= ?" if start else ""
    params = list(NON_SPENDING) + ([start] if start else [])

    conn = _connect()
    try:
        rows = conn.execute(
            f"""SELECT date(t.posted, 'unixepoch') AS date,
                       t.amount, t.description, t.my_category AS category,
                       a.org, a.name AS account_name
                FROM transactions t
                LEFT JOIN accounts a ON a.id = t.account_id
                WHERE t.amount < 0 AND t.my_category NOT IN ({placeholders}) {date_clause}
                ORDER BY t.amount ASC
                LIMIT 10""",
            params,
        ).fetchall()
    finally:
        conn.close()
    df = pd.DataFrame(rows, columns=["date", "amount", "description", "category", "org", "account_name"])
    df["method"] = df.apply(lambda r: _payment_method(r["org"], r["account_name"]), axis=1)
    return df


@st.cache_data
def accounts():
    conn = _connect()
    try:
        rows = conn.execute(
            """SELECT org, name, balance, currency,
                      date(balance_date, 'unixepoch') AS as_of
               FROM accounts ORDER BY org, name"""
        ).fetchall()
        through = conn.execute(
            "SELECT date(MAX(balance_date), 'unixepoch') FROM accounts"
        ).fetchone()[0]
    finally:
        conn.close()
    return pd.DataFrame(rows, columns=["org", "name", "balance", "currency", "as_of"]), through


def _money(x):
    return f"${x:,.2f}"


def _payment_method(org, account_name):
    """Readable payment-method label: account name with the trailing '(1234)' stripped,
    prefixed with 'BofA' for Bank of America so generic names aren't ambiguous."""
    if not account_name:
        return ""
    label = re.sub(r"\s*\(\d+\)\s*$", "", account_name)        # drop trailing "(1234)"
    label = re.sub(r"\s*-\s*\d+\s*$", "", label).strip()       # drop trailing "- 1234"
    if org and "bank of america" in org.lower() and not label.lower().startswith("bofa"):
        label = f"BofA {label}"
    return label


def _empty(msg="No data for this period."):
    st.markdown(
        f"<span style='color:#999;font-size:0.85rem'>{msg}</span>",
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------- page

st.set_page_config(page_title="Spending", layout="centered")

# Minimal CSS: strip Streamlit chrome, neutralize table styling, set the big-number size.
st.markdown(
    """
    <style>
      #MainMenu, header, footer {visibility: hidden;}
      .block-container {padding-top: 2rem; max-width: 760px;}
      [data-testid="stMetricValue"] {font-size: 2rem; font-weight: 500;}
      [data-testid="stMetricLabel"] {font-size: 0.8rem; color: #555;}
      thead tr th {border-bottom: 1px solid #000 !important;}
      tbody tr td {border: none !important;}
      hr {border: none; border-top: 1px solid #e0e0e0; margin: 1.5rem 0;}
    </style>
    """,
    unsafe_allow_html=True,
)

st.markdown("<h1 style='font-weight:700;margin-bottom:0.5rem'>Spending</h1>", unsafe_allow_html=True)

range_label = st.radio(
    "time range",
    ["1 week", "2 weeks", "30 days", "90 days", "Year to date", "All"],
    index=2,
    horizontal=True,
    label_visibility="collapsed",
)

st.markdown("<hr>", unsafe_allow_html=True)

# --- summary
spent = summary(range_label)
st.metric("spent", _money(spent))

st.markdown("<hr>", unsafe_allow_html=True)

# --- spending by category
st.markdown("<div style='font-weight:500'>Spending by category</div>", unsafe_allow_html=True)
cat = by_category(range_label)
if cat.empty:
    _empty()
else:
    import altair as alt

    cat = cat.copy()
    cat["label"] = cat.apply(lambda r: f"{r['category']}  {_money(r['amount'])}", axis=1)
    chart = (
        alt.Chart(cat)
        .mark_bar(color="#888")
        .encode(
            x=alt.X("amount:Q", axis=alt.Axis(title=None, grid=False, labels=True, ticks=False)),
            y=alt.Y("category:N", sort="-x", axis=alt.Axis(title=None, labelLimit=200)),
        )
        .properties(height=max(120, 26 * len(cat)))
    )
    text = chart.mark_text(align="left", dx=4, color="#000").encode(
        text=alt.Text("amount:Q", format="$,.2f")
    )
    st.altair_chart(chart + text, width='stretch')

st.markdown("<hr>", unsafe_allow_html=True)

# --- spending over time
st.markdown("<div style='font-weight:500'>Spending over time</div>", unsafe_allow_html=True)
series, grain = over_time(range_label)
if series.empty:
    _empty()
else:
    import altair as alt

    plot_df = series.reset_index()
    plot_df.columns = ["day", "amount"]
    line = (
        alt.Chart(plot_df)
        .mark_line(color="#444")
        .encode(
            x=alt.X("day:T", axis=alt.Axis(title=None, grid=False)),
            y=alt.Y("amount:Q", axis=alt.Axis(title=None, grid=True)),
        )
        .properties(height=220)
    )
    st.altair_chart(line, width='stretch')
    if grain == "weekly":
        st.markdown(
            "<span style='color:#999;font-size:0.8rem'>Aggregated weekly.</span>",
            unsafe_allow_html=True,
        )

st.markdown("<hr>", unsafe_allow_html=True)

# --- top transactions
st.markdown("<div style='font-weight:500'>Top transactions</div>", unsafe_allow_html=True)
top = top_transactions(range_label)
if top.empty:
    _empty()
else:
    header = (
        "<tr>"
        "<th style='text-align:left;padding:4px 8px 4px 0'>Date</th>"
        "<th style='text-align:right;padding:4px 8px'>Amount</th>"
        "<th style='text-align:left;padding:4px 8px'>Description</th>"
        "<th style='text-align:left;padding:4px 0 4px 8px'>Category</th>"
        "</tr>"
    )
    body = []
    for _, r in top.iterrows():
        desc = (r["description"] or "")[:50]
        method = r["method"]
        method_line = (
            f"<div style='color:#999;font-size:0.78rem'>{method}</div>" if method else ""
        )
        body.append(
            "<tr>"
            f"<td style='padding:6px 8px 6px 0;vertical-align:top'>{r['date']}</td>"
            f"<td style='padding:6px 8px;text-align:right;vertical-align:top'>{_money(-r['amount'])}</td>"
            f"<td style='padding:6px 8px'>{desc}{method_line}</td>"
            f"<td style='padding:6px 0 6px 8px;vertical-align:top'>{r['category']}</td>"
            "</tr>"
        )
    st.markdown(
        "<table style='width:100%;border-collapse:collapse'>"
        + header + "".join(body) + "</table>",
        unsafe_allow_html=True,
    )

st.markdown("<hr>", unsafe_allow_html=True)

# --- account balances
st.markdown("<div style='font-weight:500'>Account balances</div>", unsafe_allow_html=True)
accts, through = accounts()
if accts.empty:
    _empty("No accounts.")
else:
    rows_html = []
    for _, r in accts.iterrows():
        rows_html.append(
            f"<tr>"
            f"<td style='padding:2px 0'>{r['org']} &middot; {r['name']}</td>"
            f"<td style='padding:2px 0;text-align:right'>{_money(r['balance'])}</td>"
            f"<td style='padding:2px 0;text-align:right;color:#999;font-size:0.8rem'>as of {r['as_of']}</td>"
            f"</tr>"
        )
    st.markdown(
        "<table style='width:100%;border-collapse:collapse'>" + "".join(rows_html) + "</table>",
        unsafe_allow_html=True,
    )

if through:
    st.markdown(
        f"<div style='color:#999;font-size:0.8rem;margin-top:1.5rem'>Data through {through}</div>",
        unsafe_allow_html=True,
    )
