"""
Pure database layer — no Streamlit dependency.
All functions raise exceptions on error instead of calling st.error().
"""

import sqlite3
import calendar
import os
from datetime import date
from typing import Optional

import pandas as pd

DB_PATH = os.environ.get(
    "KAKEIBO_DB_PATH",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "kakeibo.db"),
)

DEFAULT_EXPENSE_CATEGORIES = [
    "食費", "交通費", "光熱費", "住居費", "医療費",
    "娯楽費", "衣服費", "通信費", "教育費", "外食費", "日用品", "その他",
]
DEFAULT_INCOME_CATEGORIES = [
    "給与", "副収入", "賞与", "投資・配当", "贈与", "その他収入",
]


def get_connection(db_path: str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: str = DB_PATH):
    conn = get_connection(db_path)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            type TEXT NOT NULL CHECK(type IN ('支出','収入')),
            category TEXT NOT NULL,
            amount INTEGER NOT NULL CHECK(amount > 0),
            memo TEXT DEFAULT '',
            account TEXT NOT NULL DEFAULT '現金',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            type TEXT NOT NULL CHECK(type IN ('支出','収入'))
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS budgets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            year_month TEXT NOT NULL,
            category TEXT NOT NULL,
            amount INTEGER NOT NULL CHECK(amount > 0),
            UNIQUE(year_month, category)
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS recurring (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type TEXT NOT NULL CHECK(type IN ('支出','収入')),
            category TEXT NOT NULL,
            amount INTEGER NOT NULL CHECK(amount > 0),
            memo TEXT DEFAULT '',
            day_of_month INTEGER NOT NULL DEFAULT 1 CHECK(day_of_month BETWEEN 1 AND 28),
            active INTEGER NOT NULL DEFAULT 1
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            type TEXT NOT NULL DEFAULT '現金',
            initial_balance INTEGER NOT NULL DEFAULT 0,
            note TEXT DEFAULT ''
        )
    """)
    # Seed defaults
    if c.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 0:
        for name, atype in [("現金", "現金"), ("銀行口座", "銀行"), ("クレジットカード", "クレジット")]:
            c.execute("INSERT OR IGNORE INTO accounts (name, type) VALUES (?,?)", (name, atype))
    cols = {row[1] for row in c.execute("PRAGMA table_info(transactions)").fetchall()}
    if "account" not in cols:
        c.execute("ALTER TABLE transactions ADD COLUMN account TEXT NOT NULL DEFAULT '現金'")
    existing = {row[0] for row in c.execute("SELECT name FROM categories").fetchall()}
    for cat in DEFAULT_EXPENSE_CATEGORIES:
        if cat not in existing:
            c.execute("INSERT INTO categories (name, type) VALUES (?, '支出')", (cat,))
    for cat in DEFAULT_INCOME_CATEGORIES:
        if cat not in existing:
            c.execute("INSERT INTO categories (name, type) VALUES (?, '収入')", (cat,))
    conn.commit()
    conn.close()


# ── Transactions ─────────────────────────────

def add_transaction(date_str: str, tx_type: str, category: str, amount: int,
                    memo: str = "", account: str = "現金", db_path: str = DB_PATH) -> int:
    conn = get_connection(db_path)
    cur = conn.execute(
        "INSERT INTO transactions (date, type, category, amount, memo, account) VALUES (?,?,?,?,?,?)",
        (date_str, tx_type, category, amount, memo, account),
    )
    conn.commit()
    row_id = cur.lastrowid
    conn.close()
    return row_id


def delete_transaction(tx_id: int, db_path: str = DB_PATH):
    conn = get_connection(db_path)
    conn.execute("DELETE FROM transactions WHERE id=?", (tx_id,))
    conn.commit()
    conn.close()


def get_transactions(
    year: int = None, month: int = None,
    tx_type: str = None, categories: list = None,
    start_date: str = None, end_date: str = None,
    account: str = None, keyword: str = None,
    amount_min: int = None, amount_max: int = None,
    db_path: str = DB_PATH,
) -> pd.DataFrame:
    conn = get_connection(db_path)
    q = "SELECT * FROM transactions WHERE 1=1"
    p = []
    if year and month:
        q += " AND date LIKE ?"; p.append(f"{year}-{month:02d}%")
    elif year:
        q += " AND date LIKE ?"; p.append(f"{year}%")
    if start_date:
        q += " AND date >= ?"; p.append(start_date)
    if end_date:
        q += " AND date <= ?"; p.append(end_date)
    if tx_type and tx_type != "すべて":
        q += " AND type = ?"; p.append(tx_type)
    if categories:
        q += f" AND category IN ({','.join('?'*len(categories))})"; p.extend(categories)
    if account and account != "すべて":
        q += " AND account = ?"; p.append(account)
    if keyword:
        q += " AND (memo LIKE ? OR category LIKE ?)"; p.extend([f"%{keyword}%"]*2)
    if amount_min is not None:
        q += " AND amount >= ?"; p.append(amount_min)
    if amount_max is not None:
        q += " AND amount <= ?"; p.append(amount_max)
    q += " ORDER BY date DESC, id DESC"
    df = pd.read_sql_query(q, conn, params=p)
    conn.close()
    return df


def get_recent_transactions(limit: int = 5, db_path: str = DB_PATH) -> pd.DataFrame:
    conn = get_connection(db_path)
    df = pd.read_sql_query(
        "SELECT * FROM transactions ORDER BY date DESC, id DESC LIMIT ?", conn, params=(limit,))
    conn.close()
    return df


def get_monthly_summary(year: int, month: int, db_path: str = DB_PATH) -> dict:
    df = get_transactions(year=year, month=month, db_path=db_path)
    if df.empty:
        return {"income": 0, "expense": 0, "balance": 0}
    income = int(df[df["type"] == "収入"]["amount"].sum())
    expense = int(df[df["type"] == "支出"]["amount"].sum())
    return {"income": income, "expense": expense, "balance": income - expense}


def get_total_record_count(db_path: str = DB_PATH) -> int:
    conn = get_connection(db_path)
    n = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    conn.close()
    return n


def delete_month_data(year: int, month: int, db_path: str = DB_PATH):
    conn = get_connection(db_path)
    conn.execute("DELETE FROM transactions WHERE date LIKE ?", (f"{year}-{month:02d}%",))
    conn.commit()
    conn.close()


# ── Categories ───────────────────────────────

def get_categories(tx_type: str = None, db_path: str = DB_PATH):
    conn = get_connection(db_path)
    if tx_type:
        rows = conn.execute(
            "SELECT name FROM categories WHERE type=? ORDER BY id", (tx_type,)).fetchall()
        conn.close()
        return [r[0] for r in rows]
    rows = conn.execute("SELECT name, type FROM categories ORDER BY type, id").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def add_category(name: str, tx_type: str, db_path: str = DB_PATH) -> bool:
    conn = get_connection(db_path)
    try:
        conn.execute("INSERT INTO categories (name, type) VALUES (?,?)", (name, tx_type))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()


def delete_category(name: str, db_path: str = DB_PATH) -> bool:
    conn = get_connection(db_path)
    if conn.execute("SELECT COUNT(*) FROM transactions WHERE category=?", (name,)).fetchone()[0]:
        conn.close()
        return False
    conn.execute("DELETE FROM categories WHERE name=?", (name,))
    conn.commit()
    conn.close()
    return True


# ── Budgets ──────────────────────────────────

def set_budget(year: int, month: int, category: str, amount: int, db_path: str = DB_PATH):
    ym = f"{year}-{month:02d}"
    conn = get_connection(db_path)
    conn.execute(
        "INSERT INTO budgets (year_month,category,amount) VALUES (?,?,?) "
        "ON CONFLICT(year_month,category) DO UPDATE SET amount=excluded.amount",
        (ym, category, amount))
    conn.commit()
    conn.close()


def get_budgets(year: int, month: int, db_path: str = DB_PATH) -> dict:
    ym = f"{year}-{month:02d}"
    conn = get_connection(db_path)
    rows = conn.execute("SELECT category,amount FROM budgets WHERE year_month=?", (ym,)).fetchall()
    conn.close()
    return {r[0]: r[1] for r in rows}


def delete_budget(year: int, month: int, category: str, db_path: str = DB_PATH):
    ym = f"{year}-{month:02d}"
    conn = get_connection(db_path)
    conn.execute("DELETE FROM budgets WHERE year_month=? AND category=?", (ym, category))
    conn.commit()
    conn.close()


def get_budget_status(year: int, month: int, db_path: str = DB_PATH) -> pd.DataFrame:
    budgets = get_budgets(year, month, db_path)
    if not budgets:
        return pd.DataFrame()
    df = get_transactions(year=year, month=month, tx_type="支出", db_path=db_path)
    actual = df.groupby("category")["amount"].sum().to_dict() if not df.empty else {}
    rows = []
    for cat, budget in budgets.items():
        act = actual.get(cat, 0)
        rows.append({"category": cat, "budget": budget, "actual": act,
                     "usage_pct": round(act / budget * 100, 1), "over_budget": act > budget})
    return pd.DataFrame(rows).sort_values("usage_pct", ascending=False)


# ── Recurring ────────────────────────────────

def add_recurring(tx_type: str, category: str, amount: int, memo: str,
                  day_of_month: int, db_path: str = DB_PATH) -> int:
    conn = get_connection(db_path)
    cur = conn.execute(
        "INSERT INTO recurring (type,category,amount,memo,day_of_month) VALUES (?,?,?,?,?)",
        (tx_type, category, amount, memo, day_of_month))
    conn.commit()
    row_id = cur.lastrowid
    conn.close()
    return row_id


def get_recurring(active_only: bool = True, db_path: str = DB_PATH) -> pd.DataFrame:
    conn = get_connection(db_path)
    q = "SELECT * FROM recurring" + (" WHERE active=1" if active_only else "") + " ORDER BY day_of_month, id"
    df = pd.read_sql_query(q, conn)
    conn.close()
    return df


def toggle_recurring(rec_id: int, active: bool, db_path: str = DB_PATH):
    conn = get_connection(db_path)
    conn.execute("UPDATE recurring SET active=? WHERE id=?", (1 if active else 0, rec_id))
    conn.commit()
    conn.close()


def delete_recurring(rec_id: int, db_path: str = DB_PATH):
    conn = get_connection(db_path)
    conn.execute("DELETE FROM recurring WHERE id=?", (rec_id,))
    conn.commit()
    conn.close()


def apply_recurring(year: int, month: int, db_path: str = DB_PATH) -> int:
    rec_df = get_recurring(active_only=True, db_path=db_path)
    if rec_df.empty:
        return 0
    existing = get_transactions(year=year, month=month, db_path=db_path)
    count = 0
    for _, r in rec_df.iterrows():
        day = min(int(r["day_of_month"]), calendar.monthrange(year, month)[1])
        tx_date = f"{year}-{month:02d}-{day:02d}"
        if not existing.empty:
            dup = existing[
                (existing["date"] == tx_date) &
                (existing["category"] == r["category"]) &
                (existing["amount"] == int(r["amount"]))
            ]
            if not dup.empty:
                continue
        add_transaction(tx_date, r["type"], r["category"], int(r["amount"]), r["memo"],
                        db_path=db_path)
        count += 1
    return count


# ── Accounts ─────────────────────────────────

def get_accounts(db_path: str = DB_PATH) -> list:
    conn = get_connection(db_path)
    rows = conn.execute("SELECT * FROM accounts ORDER BY id").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_account_names(db_path: str = DB_PATH) -> list:
    return [a["name"] for a in get_accounts(db_path)]


def add_account(name: str, atype: str, initial_balance: int = 0,
                note: str = "", db_path: str = DB_PATH) -> bool:
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO accounts (name,type,initial_balance,note) VALUES (?,?,?,?)",
            (name, atype, initial_balance, note))
        conn.commit()
        return True
    except Exception:
        return False
    finally:
        conn.close()


def delete_account(name: str, db_path: str = DB_PATH) -> bool:
    conn = get_connection(db_path)
    if conn.execute("SELECT COUNT(*) FROM transactions WHERE account=?", (name,)).fetchone()[0]:
        conn.close()
        return False
    conn.execute("DELETE FROM accounts WHERE name=?", (name,))
    conn.commit()
    conn.close()
    return True


def get_account_balance(name: str, db_path: str = DB_PATH) -> int:
    conn = get_connection(db_path)
    row = conn.execute("SELECT initial_balance FROM accounts WHERE name=?", (name,)).fetchone()
    initial = row[0] if row else 0
    conn.close()
    df = get_transactions(account=name, db_path=db_path)
    if df.empty:
        return initial
    return initial + int(df[df["type"] == "収入"]["amount"].sum()) - int(df[df["type"] == "支出"]["amount"].sum())
