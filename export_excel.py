# Export the spending database to a local Excel workbook (read-only on the DB).
# Run directly: .venv/bin/python export_excel.py
# Runs automatically before the agent via the `track` launcher.
import os
import re
import sqlite3
import sys
import tempfile

import pandas as pd

DB_PATH = "spending.db"
XLSX_PATH = "spending.xlsx"

# Categories that are not real spending (money between own accounts / earnings).
# Kept in sync with NON_SPENDING in agent.py / dashboard.py; inlined to stay standalone.
NON_SPENDING = ("cc_payment", "internal_transfer", "investment", "income", "transfer")


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


def _connect():
    return sqlite3.connect(DB_PATH, detect_types=0)


def load_frames():
    conn = _connect()
    try:
        tx = pd.DataFrame(
            conn.execute(
                """SELECT date(t.posted, 'unixepoch') AS date,
                          t.amount, t.description, t.my_category AS category,
                          a.org, a.name AS account_name
                   FROM transactions t
                   LEFT JOIN accounts a ON a.id = t.account_id
                   ORDER BY t.posted DESC"""
            ).fetchall(),
            columns=["date", "amount", "description", "category", "org", "account_name"],
        )

        placeholders = ",".join("?" * len(NON_SPENDING))
        by_cat = pd.DataFrame(
            conn.execute(
                f"""SELECT my_category AS category, -SUM(amount) AS spent, COUNT(*) AS transactions
                    FROM transactions
                    WHERE amount < 0 AND my_category NOT IN ({placeholders})
                    GROUP BY my_category
                    ORDER BY spent DESC""",
                NON_SPENDING,
            ).fetchall(),
            columns=["category", "spent", "transactions"],
        )

        balances = pd.DataFrame(
            conn.execute(
                """SELECT org, name, balance, currency,
                          date(balance_date, 'unixepoch') AS as_of
                   FROM accounts ORDER BY org, name"""
            ).fetchall(),
            columns=["org", "name", "balance", "currency", "as_of"],
        )
    finally:
        conn.close()

    # Derive the payment-method label, then drop the raw org/account_name columns.
    tx["payment_method"] = tx.apply(
        lambda r: _payment_method(r["org"], r["account_name"]), axis=1
    )
    tx = tx[["date", "amount", "description", "category", "payment_method"]]
    return tx, by_cat, balances


def _autosize(worksheet, df):
    """Widen columns to fit content and apply a $ number format to money columns."""
    for i, col in enumerate(df.columns, start=1):
        width = max([len(str(col))] + [len(str(v)) for v in df[col].values] + [8])
        worksheet.column_dimensions[
            worksheet.cell(row=1, column=i).column_letter
        ].width = min(width + 2, 60)
        if col in ("amount", "spent", "balance"):
            for row in range(2, len(df) + 2):
                worksheet.cell(row=row, column=i).number_format = '$#,##0.00'


def export():
    tx, by_cat, balances = load_frames()

    # Write to a temp file in the same dir, then atomically rename over the target,
    # so a crash mid-write can't corrupt an existing workbook.
    fd, tmp_path = tempfile.mkstemp(suffix=".xlsx", dir=os.path.dirname(os.path.abspath(XLSX_PATH)))
    os.close(fd)
    try:
        with pd.ExcelWriter(tmp_path, engine="openpyxl") as writer:
            tx.to_excel(writer, sheet_name="Transactions", index=False)
            by_cat.to_excel(writer, sheet_name="By category", index=False)
            balances.to_excel(writer, sheet_name="Account balances", index=False)
            _autosize(writer.sheets["Transactions"], tx)
            _autosize(writer.sheets["By category"], by_cat)
            _autosize(writer.sheets["Account balances"], balances)
        os.replace(tmp_path, XLSX_PATH)
    except PermissionError:
        # Excel/Numbers holds a lock on the open file on some platforms.
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        print(
            f"Could not write {XLSX_PATH} — it looks like it's open in another program. "
            "Close it and re-run to refresh.",
            file=sys.stderr,
        )
        return
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise

    print(f"Exported {len(tx)} transactions to {XLSX_PATH}")


if __name__ == "__main__":
    export()
