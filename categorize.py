import os
import sqlite3
import json
import re
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()
DB_PATH = "spending.db"
client = OpenAI()

CATEGORIES = [
    "groceries", "dining", "transport", "shopping", "entertainment",
    "utilities", "rent", "health", "travel", "subscriptions",
    "education", "income", "transfer", "cc_payment",
    "internal_transfer", "investment", "other",
]

RULES = [
    # Non-spending — must come first
    (r"\bCAPITAL ONE\s+DES:MOBILE PMT\b", "cc_payment"),
    (r"\bCHASE CREDIT CRD\s+DES:EPAY\b", "cc_payment"),
    (r"\bAMEX\b.*\b(PMT|EPAYMENT|AUTOPAY)\b", "cc_payment"),
    (r"\bpayment thank you\b", "cc_payment"),
    (r"\bautomatic payment\s*-\s*thank\b", "cc_payment"),
    (r"\bOnline Banking transfer (to|from)\s+(SAV|CHK|CHECKING|SAVINGS)\b", "internal_transfer"),
    (r"\bROBINHOOD\b", "investment"),
    (r"\b(fidelity|vanguard|schwab|e\*?trade|coinbase|wealthfront|betterment)\b.*\b(invest|debit|transfer|ach)\b", "investment"),
    (r"\bWR-OP\s+DES:WEB PMTS\b", "rent"),
    (r"\bUCLA\b.*\b(ONLINEPYMT|TUITION)\b", "education"),
    (r"\bUCLA ONLINE\b", "education"),
    (r"\bUCLA-?\b", "education"),
    (r"\bzelle payment (to|from)\b", "transfer"),
    (r"\b(venmo|cash app|paypal)\b.*\b(payment|transfer)\b", "transfer"),

    # Spending categories
    (r"\b(whole foods|trader joe|safeway|kroger|wegmans|aldi|costco|stop ?& ?shop|ralphs|vons|albertsons)\b", "groceries"),
    (r"\b(starbucks|dunkin|peet's|blue bottle|philz)\b", "dining"),
    (r"\b(chipotle|mcdonald|chick-?fil-?a|panera|sweetgreen|doordash|grubhub|uber eats|seamless|tst\*|dlr pym|hudson st)\b", "dining"),
    (r"\b(uber|lyft|metro|mta|bart|caltrain|amtrak|ops\*westwood riviera|ctlp\*)\b", "transport"),
    (r"\b(shell|chevron|exxon|bp|mobil|valero|76 |arco)\b", "transport"),
    (r"\b(amazon|amzn|target|walmart|ebay|etsy|ao cabazon)\b", "shopping"),
    (r"\b(netflix|spotify|hulu|disney\+?|hbo|apple\.com/bill|youtube premium|riot\*)\b", "subscriptions"),
    (r"\b(comcast|xfinity|verizon|at&t|t-mobile|sprint|spectrum|qbe insurance)\b", "utilities"),
    (r"\b(con ?edison|pg&e|national grid|duke energy|socalgas|edison)\b", "utilities"),
    (r"\b(cvs|walgreens|rite aid|kaiser|one medical)\b", "health"),
    (r"\b(delta|united|american airlines|jetblue|southwest|airbnb|marriott|hilton|hyatt)\b", "travel"),
    (r"\b(payroll|direct deposit|salary|payco|adp|gusto)\b", "income"),
]

def apply_rules(description):
    if not description:
        return None
    for pattern, category in RULES:
        if re.search(pattern, description, re.IGNORECASE):
            return category
    return None

def categorize_with_openai(transactions):
    if not transactions:
        return {}
    items = [{"id": t["id"], "description": t["description"], "amount": t["amount"]} for t in transactions]
    system_msg = (
        f"You categorize bank/credit-card transactions into exactly one of these categories: "
        f"{', '.join(CATEGORIES)}. "
        "Negative amounts are spending, positive amounts are income/refunds. "
        "Be conservative — when unsure between two categories, prefer 'other'. "
        "Credit card payments between user's own accounts are 'cc_payment'. "
        "Money moving between user's checking and savings is 'internal_transfer'. "
        "Brokerage / investment transfers (Robinhood, Fidelity, etc.) are 'investment'. "
        "Zelle/Venmo/PayPal to people are 'transfer'. "
        "Respond with a JSON object containing a key 'results' whose value is an array of "
        "objects with 'id' and 'category' fields."
    )
    user_msg = f"Transactions:\n{json.dumps(items, indent=2)}"
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ],
    )
    parsed = json.loads(response.choices[0].message.content)
    results = parsed.get("results", [])
    return {r["id"]: r["category"] for r in results}

def categorize():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    uncategorized = conn.execute("""
        SELECT id, description, amount FROM transactions
        WHERE my_category IS NULL ORDER BY posted DESC
    """).fetchall()
    if not uncategorized:
        print("Nothing to categorize.")
        return
    print(f"Found {len(uncategorized)} uncategorized transactions.")
    rule_matched = 0
    needs_llm = []
    for t in uncategorized:
        cat = apply_rules(t["description"])
        if cat:
            conn.execute("UPDATE transactions SET my_category=? WHERE id=?", (cat, t["id"]))
            rule_matched += 1
        else:
            needs_llm.append({"id": t["id"], "description": t["description"], "amount": t["amount"]})
    conn.commit()
    print(f"  Rules matched: {rule_matched}")
    print(f"  Sending to OpenAI: {len(needs_llm)}")
    llm_matched = 0
    batch_size = 25
    for i in range(0, len(needs_llm), batch_size):
        batch = needs_llm[i:i+batch_size]
        try:
            results = categorize_with_openai(batch)
            for tid, cat in results.items():
                if cat not in CATEGORIES:
                    cat = "other"
                conn.execute("UPDATE transactions SET my_category=? WHERE id=?", (cat, tid))
                llm_matched += 1
            conn.commit()
            print(f"  Batch {i//batch_size + 1}: categorized {len(results)} transactions")
        except Exception as e:
            print(f"  Batch {i//batch_size + 1} failed: {e}")
    print(f"\nDone. Rules: {rule_matched}, OpenAI: {llm_matched}, Total: {rule_matched + llm_matched}")
    print("\nCategory totals (last 90 days):")
    rows = conn.execute("""
        SELECT my_category, COUNT(*) as n, printf('$%.2f', SUM(amount)) as total
        FROM transactions WHERE my_category IS NOT NULL
        GROUP BY my_category ORDER BY SUM(amount) ASC
    """).fetchall()
    for r in rows:
        print(f"  {r['my_category']:18s} {r['n']:4d} txns   {r['total']}")
    conn.close()

if __name__ == "__main__":
    categorize()
