import os
import sqlite3
import hashlib
from datetime import datetime, timedelta
import requests
from dotenv import load_dotenv

load_dotenv()
ACCESS_URL = os.environ["SIMPLEFIN_ACCESS_URL"]
DB_PATH = "spending.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS accounts (
            id TEXT PRIMARY KEY,
            org TEXT,
            name TEXT,
            currency TEXT,
            balance REAL,
            balance_date INTEGER
        );
        CREATE TABLE IF NOT EXISTS transactions (
            id TEXT PRIMARY KEY,
            account_id TEXT,
            posted INTEGER,
            amount REAL,
            description TEXT,
            payee TEXT,
            memo TEXT,
            my_category TEXT,
            FOREIGN KEY(account_id) REFERENCES accounts(id)
        );
        CREATE INDEX IF NOT EXISTS idx_tx_posted ON transactions(posted);
        CREATE INDEX IF NOT EXISTS idx_tx_account ON transactions(account_id);
    """)
    conn.commit()
    return conn

def fetch_accounts(start_date=None):
    if start_date is None:
        start_date = int((datetime.now() - timedelta(days=90)).timestamp())
    url = f"{ACCESS_URL.rstrip('/')}/accounts?start-date={start_date}"
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    return r.json()

def tx_id(account_id, posted, amount, description):
    key = f"{account_id}|{posted}|{amount}|{description}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]

def sync():
    conn = init_db()
    data = fetch_accounts()

    if data.get("errors"):
        print("Errors from SimpleFIN:", data["errors"])

    for acct in data.get("accounts", []):
        conn.execute("""
            INSERT INTO accounts(id, org, name, currency, balance, balance_date)
            VALUES(?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                balance=excluded.balance,
                balance_date=excluded.balance_date
        """, (
            acct["id"],
            acct["org"]["name"],
            acct["name"],
            acct["currency"],
            float(acct["balance"]),
            acct["balance-date"],
        ))

        new_count = 0
        for t in acct.get("transactions", []):
            tid = tx_id(acct["id"], t["posted"], t["amount"], t["description"])
            cur = conn.execute("""
                INSERT OR IGNORE INTO transactions(
                    id, account_id, posted, amount, description, payee, memo, my_category
                ) VALUES(?, ?, ?, ?, ?, ?, ?, NULL)
            """, (
                tid,
                acct["id"],
                t["posted"],
                float(t["amount"]),
                t["description"],
                t.get("payee"),
                t.get("memo"),
            ))
            if cur.rowcount:
                new_count += 1

        print(f"{acct['org']['name']} - {acct['name']}: "
              f"balance ${float(acct['balance']):,.2f}, {new_count} new transactions")

    conn.commit()
    conn.close()

if __name__ == "__main__":
    sync()
