"""
家計簿アプリ (Kakeibo - Japanese Household Budget App)
A complete household budget management application built with Streamlit.
"""

import streamlit as st
import sqlite3
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import json
import os
import io
import base64
import bcrypt
from datetime import date, datetime, timedelta
import calendar
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

# ─────────────────────────────────────────────
# Configuration & Constants
# ─────────────────────────────────────────────

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kakeibo.db")
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kakeibo_config.json")

DEFAULT_EXPENSE_CATEGORIES = [
    "食費", "交通費", "光熱費", "住居費", "医療費",
    "娯楽費", "衣服費", "通信費", "教育費", "外食費", "日用品", "その他"
]
DEFAULT_INCOME_CATEGORIES = [
    "給与", "副収入", "賞与", "投資・配当", "贈与", "その他収入"
]

QUICK_AMOUNTS = [500, 1000, 3000, 5000, 10000, 30000]

OCR_MODEL = "claude-haiku-4-5-20251001"

MEDIA_TYPE_MAP = {
    "jpg": "image/jpeg", "jpeg": "image/jpeg",
    "png": "image/png", "gif": "image/gif", "webp": "image/webp",
}

# ─────────────────────────────────────────────
# CSS Injection
# ─────────────────────────────────────────────

def inject_css():
    """Minimal mobile-friendly styling."""
    st.markdown("""
    <style>
        .stButton > button { min-height: 44px; }
        [data-testid="stForm"] .stButton > button { width: 100%; }
    </style>
    """, unsafe_allow_html=True)


# ─────────────────────────────────────────────
# Config (PIN) Management
# ─────────────────────────────────────────────

def load_config() -> dict:
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_config(config: dict):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


def has_pin() -> bool:
    config = load_config()
    return bool(config.get("pin_hash"))


def verify_pin(pin: str) -> bool:
    config = load_config()
    pin_hash = config.get("pin_hash", "")
    if not pin_hash:
        return True
    try:
        return bcrypt.checkpw(pin.encode(), pin_hash.encode())
    except Exception:
        return False


def set_pin(pin: str):
    config = load_config()
    hashed = bcrypt.hashpw(pin.encode(), bcrypt.gensalt()).decode()
    config["pin_hash"] = hashed
    save_config(config)


def remove_pin():
    config = load_config()
    config.pop("pin_hash", None)
    save_config(config)


# ─────────────────────────────────────────────
# Database Functions
# ─────────────────────────────────────────────

def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            type TEXT NOT NULL CHECK(type IN ('支出','収入')),
            category TEXT NOT NULL,
            amount INTEGER NOT NULL CHECK(amount > 0),
            memo TEXT DEFAULT '',
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
    # Seed default categories
    existing = {row[0] for row in c.execute("SELECT name FROM categories").fetchall()}
    for cat in DEFAULT_EXPENSE_CATEGORIES:
        if cat not in existing:
            c.execute("INSERT INTO categories (name, type) VALUES (?, '支出')", (cat,))
    for cat in DEFAULT_INCOME_CATEGORIES:
        if cat not in existing:
            c.execute("INSERT INTO categories (name, type) VALUES (?, '収入')", (cat,))
    conn.commit()
    conn.close()


def add_transaction(date_str: str, tx_type: str, category: str, amount: int, memo: str) -> bool:
    try:
        conn = get_connection()
        conn.execute(
            "INSERT INTO transactions (date, type, category, amount, memo) VALUES (?, ?, ?, ?, ?)",
            (date_str, tx_type, category, amount, memo)
        )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        st.error(f"登録エラー: {e}")
        return False


def delete_transaction(tx_id: int):
    conn = get_connection()
    conn.execute("DELETE FROM transactions WHERE id = ?", (tx_id,))
    conn.commit()
    conn.close()


def get_transactions(
    year: int = None,
    month: int = None,
    tx_type: str = None,
    categories: list = None,
    start_date: str = None,
    end_date: str = None,
) -> pd.DataFrame:
    conn = get_connection()
    query = "SELECT * FROM transactions WHERE 1=1"
    params = []
    if year and month:
        month_str = f"{year}-{month:02d}"
        query += " AND date LIKE ?"
        params.append(f"{month_str}%")
    elif year:
        query += " AND date LIKE ?"
        params.append(f"{year}%")
    if start_date:
        query += " AND date >= ?"
        params.append(start_date)
    if end_date:
        query += " AND date <= ?"
        params.append(end_date)
    if tx_type and tx_type != "すべて":
        query += " AND type = ?"
        params.append(tx_type)
    if categories:
        placeholders = ",".join("?" * len(categories))
        query += f" AND category IN ({placeholders})"
        params.extend(categories)
    query += " ORDER BY date DESC, id DESC"
    df = pd.read_sql_query(query, conn, params=params)
    conn.close()
    return df


def get_recent_transactions(limit: int = 5) -> pd.DataFrame:
    conn = get_connection()
    df = pd.read_sql_query(
        "SELECT * FROM transactions ORDER BY date DESC, id DESC LIMIT ?",
        conn, params=(limit,)
    )
    conn.close()
    return df


def get_monthly_summary(year: int, month: int) -> dict:
    df = get_transactions(year=year, month=month)
    if df.empty:
        return {"income": 0, "expense": 0, "balance": 0}
    income = int(df[df["type"] == "収入"]["amount"].sum())
    expense = int(df[df["type"] == "支出"]["amount"].sum())
    return {"income": income, "expense": expense, "balance": income - expense}


def get_categories(tx_type: str = None) -> list:
    conn = get_connection()
    if tx_type:
        rows = conn.execute(
            "SELECT name FROM categories WHERE type = ? ORDER BY id", (tx_type,)
        ).fetchall()
        conn.close()
        return [r[0] for r in rows]
    else:
        rows = conn.execute("SELECT name, type FROM categories ORDER BY type, id").fetchall()
        conn.close()
        return [dict(r) for r in rows]


def add_category(name: str, tx_type: str) -> bool:
    try:
        conn = get_connection()
        conn.execute("INSERT INTO categories (name, type) VALUES (?, ?)", (name, tx_type))
        conn.commit()
        conn.close()
        return True
    except sqlite3.IntegrityError:
        return False


def delete_category(name: str) -> bool:
    conn = get_connection()
    # Check if in use
    count = conn.execute(
        "SELECT COUNT(*) FROM transactions WHERE category = ?", (name,)
    ).fetchone()[0]
    if count > 0:
        conn.close()
        return False
    conn.execute("DELETE FROM categories WHERE name = ?", (name,))
    conn.commit()
    conn.close()
    return True


def get_total_record_count() -> int:
    conn = get_connection()
    count = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    conn.close()
    return count


def delete_month_data(year: int, month: int):
    conn = get_connection()
    month_str = f"{year}-{month:02d}"
    conn.execute("DELETE FROM transactions WHERE date LIKE ?", (f"{month_str}%",))
    conn.commit()
    conn.close()


# ── 予算 ──────────────────────────────────────

def set_budget(year: int, month: int, category: str, amount: int):
    ym = f"{year}-{month:02d}"
    conn = get_connection()
    conn.execute(
        "INSERT INTO budgets (year_month, category, amount) VALUES (?,?,?) "
        "ON CONFLICT(year_month, category) DO UPDATE SET amount=excluded.amount",
        (ym, category, amount),
    )
    conn.commit()
    conn.close()


def get_budgets(year: int, month: int) -> dict:
    """Returns {category: budget_amount} for the given month."""
    ym = f"{year}-{month:02d}"
    conn = get_connection()
    rows = conn.execute("SELECT category, amount FROM budgets WHERE year_month=?", (ym,)).fetchall()
    conn.close()
    return {r[0]: r[1] for r in rows}


def delete_budget(year: int, month: int, category: str):
    ym = f"{year}-{month:02d}"
    conn = get_connection()
    conn.execute("DELETE FROM budgets WHERE year_month=? AND category=?", (ym, category))
    conn.commit()
    conn.close()


def get_budget_status(year: int, month: int) -> pd.DataFrame:
    """Returns DataFrame with category, budget, actual, usage_pct, over_budget."""
    budgets = get_budgets(year, month)
    if not budgets:
        return pd.DataFrame()
    df = get_transactions(year=year, month=month, tx_type="支出")
    actual_by_cat = df.groupby("category")["amount"].sum().to_dict() if not df.empty else {}
    rows = []
    for cat, budget in budgets.items():
        actual = actual_by_cat.get(cat, 0)
        rows.append({
            "category": cat,
            "budget": budget,
            "actual": actual,
            "usage_pct": round(actual / budget * 100, 1),
            "over_budget": actual > budget,
        })
    return pd.DataFrame(rows).sort_values("usage_pct", ascending=False)


# ── 定期取引 ──────────────────────────────────

def add_recurring(tx_type: str, category: str, amount: int, memo: str, day_of_month: int) -> bool:
    try:
        conn = get_connection()
        conn.execute(
            "INSERT INTO recurring (type, category, amount, memo, day_of_month) VALUES (?,?,?,?,?)",
            (tx_type, category, amount, memo, day_of_month),
        )
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False


def get_recurring(active_only: bool = True) -> pd.DataFrame:
    conn = get_connection()
    q = "SELECT * FROM recurring"
    if active_only:
        q += " WHERE active=1"
    q += " ORDER BY day_of_month, id"
    df = pd.read_sql_query(q, conn)
    conn.close()
    return df


def toggle_recurring(rec_id: int, active: bool):
    conn = get_connection()
    conn.execute("UPDATE recurring SET active=? WHERE id=?", (1 if active else 0, rec_id))
    conn.commit()
    conn.close()


def delete_recurring(rec_id: int):
    conn = get_connection()
    conn.execute("DELETE FROM recurring WHERE id=?", (rec_id,))
    conn.commit()
    conn.close()


def apply_recurring(year: int, month: int) -> int:
    """
    Register active recurring transactions for the given month.
    Skips entries whose (date, category, amount) already exists to prevent duplicates.
    Returns the number of newly registered transactions.
    """
    rec_df = get_recurring(active_only=True)
    if rec_df.empty:
        return 0

    existing = get_transactions(year=year, month=month)
    count = 0
    for _, r in rec_df.iterrows():
        day = min(int(r["day_of_month"]), calendar.monthrange(year, month)[1])
        tx_date = f"{year}-{month:02d}-{day:02d}"
        # Duplicate check: same date + category + amount
        if not existing.empty:
            dup = existing[
                (existing["date"] == tx_date) &
                (existing["category"] == r["category"]) &
                (existing["amount"] == int(r["amount"]))
            ]
            if not dup.empty:
                continue
        add_transaction(tx_date, r["type"], r["category"], int(r["amount"]), r["memo"])
        count += 1
    return count


# ─────────────────────────────────────────────
# Chart Functions
# ─────────────────────────────────────────────

def chart_expense_pie(year: int, month: int):
    df = get_transactions(year=year, month=month, tx_type="支出")
    if df.empty:
        st.info("支出データがありません。")
        return
    by_cat = df.groupby("category")["amount"].sum().reset_index()
    by_cat.columns = ["カテゴリ", "金額"]
    by_cat = by_cat.sort_values("金額", ascending=False)

    fig = px.pie(
        by_cat,
        names="カテゴリ",
        values="金額",
        title=f"{year}年{month}月 支出カテゴリ別",
        color_discrete_sequence=px.colors.qualitative.Set3,
        hole=0.35,
    )
    fig.update_traces(
        textposition="inside",
        textinfo="percent+label",
        hovertemplate="<b>%{label}</b><br>¥%{value:,}<br>%{percent}<extra></extra>"
    )
    fig.update_layout(
        showlegend=True,
        legend=dict(orientation="h", yanchor="bottom", y=-0.3, xanchor="center", x=0.5),
        margin=dict(t=60, b=80, l=20, r=20),
        height=420,
    )
    st.plotly_chart(fig, use_container_width=True)


def chart_daily_bar(year: int, month: int):
    df = get_transactions(year=year, month=month)
    if df.empty:
        st.info("データがありません。")
        return

    # Build a full date range for the month
    num_days = calendar.monthrange(year, month)[1]
    all_dates = [f"{year}-{month:02d}-{d:02d}" for d in range(1, num_days + 1)]

    income_by_day = df[df["type"] == "収入"].groupby("date")["amount"].sum()
    expense_by_day = df[df["type"] == "支出"].groupby("date")["amount"].sum()

    income_vals = [int(income_by_day.get(d, 0)) for d in all_dates]
    expense_vals = [int(expense_by_day.get(d, 0)) for d in all_dates]
    day_labels = [str(d) for d in range(1, num_days + 1)]

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=day_labels, y=income_vals, name="収入",
        marker_color="#00c851",
        hovertemplate="<b>%{x}日</b><br>収入: ¥%{y:,}<extra></extra>"
    ))
    fig.add_trace(go.Bar(
        x=day_labels, y=expense_vals, name="支出",
        marker_color="#ff4444",
        hovertemplate="<b>%{x}日</b><br>支出: ¥%{y:,}<extra></extra>"
    ))
    fig.update_layout(
        title=f"{year}年{month}月 日別収支",
        xaxis_title="日",
        yaxis_title="金額 (円)",
        barmode="group",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        hovermode="x unified",
        margin=dict(t=60, b=40, l=60, r=20),
        height=380,
    )
    st.plotly_chart(fig, use_container_width=True)


def chart_cumulative_line(year: int, month: int):
    df = get_transactions(year=year, month=month)
    if df.empty:
        st.info("データがありません。")
        return

    num_days = calendar.monthrange(year, month)[1]
    all_dates = [f"{year}-{month:02d}-{d:02d}" for d in range(1, num_days + 1)]

    daily_balance = {}
    for d in all_dates:
        rows = df[df["date"] == d]
        inc = int(rows[rows["type"] == "収入"]["amount"].sum())
        exp = int(rows[rows["type"] == "支出"]["amount"].sum())
        daily_balance[d] = inc - exp

    cumulative = []
    running = 0
    for d in all_dates:
        running += daily_balance.get(d, 0)
        cumulative.append(running)

    day_labels = list(range(1, num_days + 1))
    colors = ["#00c851" if v >= 0 else "#ff4444" for v in cumulative]

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=day_labels,
        y=cumulative,
        mode="lines+markers",
        name="累計収支",
        line=dict(color="#667eea", width=3),
        marker=dict(size=6, color=colors),
        hovertemplate="<b>%{x}日</b><br>累計: ¥%{y:,}<extra></extra>"
    ))
    fig.add_hline(y=0, line_dash="dash", line_color="gray", opacity=0.5)
    fig.update_layout(
        title=f"{year}年{month}月 累計収支推移",
        xaxis_title="日",
        yaxis_title="累計収支 (円)",
        hovermode="x unified",
        margin=dict(t=60, b=40, l=70, r=20),
        height=360,
    )
    st.plotly_chart(fig, use_container_width=True)


# ─────────────────────────────────────────────
# Export Functions
# ─────────────────────────────────────────────

def df_to_csv_bytes(df: pd.DataFrame) -> bytes:
    export_df = df[["date", "type", "category", "amount", "memo"]].copy()
    export_df.columns = ["日付", "種別", "カテゴリ", "金額", "メモ"]
    # UTF-8 BOM for Excel compatibility
    return b"\xef\xbb\xbf" + export_df.to_csv(index=False).encode("utf-8")


def df_to_excel_bytes(df: pd.DataFrame, period_label: str = "") -> bytes:
    export_df = df[["date", "type", "category", "amount", "memo"]].copy()
    export_df.columns = ["日付", "種別", "カテゴリ", "金額", "メモ"]

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "家計簿データ"

    # Header style
    header_fill = PatternFill(start_color="667EEA", end_color="667EEA", fill_type="solid")
    header_font = Font(color="FFFFFF", bold=True, size=12)
    header_align = Alignment(horizontal="center", vertical="center")
    thin_border = Border(
        left=Side(style="thin"), right=Side(style="thin"),
        top=Side(style="thin"), bottom=Side(style="thin")
    )

    # Title row
    ws.merge_cells("A1:E1")
    title_cell = ws["A1"]
    title_cell.value = f"家計簿データ {period_label}"
    title_cell.font = Font(bold=True, size=14)
    title_cell.alignment = Alignment(horizontal="center")
    ws.row_dimensions[1].height = 28

    # Column headers
    for col_idx, col_name in enumerate(export_df.columns, 1):
        cell = ws.cell(row=2, column=col_idx, value=col_name)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = header_align
        cell.border = thin_border
    ws.row_dimensions[2].height = 22

    # Data rows
    for row_idx, row in enumerate(export_df.itertuples(index=False), 3):
        for col_idx, value in enumerate(row, 1):
            cell = ws.cell(row=row_idx, column=col_idx, value=value)
            cell.border = thin_border
            cell.alignment = Alignment(vertical="center")
            if col_idx == 4:  # Amount column
                cell.number_format = "#,##0"
                if row[1] == "支出":
                    cell.font = Font(color="CC0000")
                else:
                    cell.font = Font(color="006600")
            if row_idx % 2 == 0:
                cell.fill = PatternFill(start_color="F8F9FA", end_color="F8F9FA", fill_type="solid")

    # Summary section
    summary_row = len(export_df) + 4
    ws.cell(row=summary_row, column=1, value="集計").font = Font(bold=True, size=12)
    inc_total = int(export_df[export_df["種別"] == "収入"]["金額"].sum())
    exp_total = int(export_df[export_df["種別"] == "支出"]["金額"].sum())
    balance = inc_total - exp_total

    items = [
        ("収入合計", inc_total, "006600"),
        ("支出合計", exp_total, "CC0000"),
        ("収支", balance, "0000CC" if balance >= 0 else "CC0000")
    ]
    for i, (label, val, color) in enumerate(items):
        r = summary_row + 1 + i
        ws.cell(row=r, column=1, value=label).font = Font(bold=True)
        cell = ws.cell(row=r, column=2, value=val)
        cell.number_format = "#,##0"
        cell.font = Font(color=color, bold=True)

    # Column widths
    col_widths = [14, 8, 14, 14, 30]
    for i, width in enumerate(col_widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = width

    output = io.BytesIO()
    wb.save(output)
    return output.getvalue()


# ─────────────────────────────────────────────
# OCR Functions
# ─────────────────────────────────────────────

def _get_api_key() -> str:
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        try:
            key = st.secrets.get("ANTHROPIC_API_KEY", "")
        except Exception:
            pass
    return key


def ocr_receipt_with_claude(image_bytes: bytes, media_type: str) -> dict:
    """Send receipt image to Claude Vision API and extract structured transaction data."""
    import anthropic

    api_key = _get_api_key()
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY が設定されていません")

    expense_cats = get_categories("支出")
    income_cats = get_categories("収入")
    today_str = date.today().isoformat()

    prompt = f"""以下のレシート・領収書画像を読み取り、JSONのみで返してください（コードブロック不要）。

出力形式:
{{
  "date": "YYYY-MM-DD",
  "type": "支出",
  "category": "食費",
  "amount": 1234,
  "store_name": "店名",
  "memo": "店名＋主な品目",
  "items": [{{"name": "品目", "price": 100}}]
}}

支出カテゴリ候補: {', '.join(expense_cats)}
収入カテゴリ候補: {', '.join(income_cats)}

- 日付不明の場合は {today_str} を使用
- 金額不明の場合は 0 を設定
- itemsは最大10件
- JSONのみ返すこと"""

    client = anthropic.Anthropic(api_key=api_key)
    image_data = base64.standard_b64encode(image_bytes).decode("utf-8")

    message = client.messages.create(
        model=OCR_MODEL,
        max_tokens=1024,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": image_data}},
                {"type": "text", "text": prompt},
            ],
        }],
    )

    raw = message.content[0].text.strip()
    # Strip markdown code fences if model added them
    if raw.startswith("```"):
        raw = "\n".join(raw.split("\n")[1:])
        raw = raw.rstrip("`").strip()

    return json.loads(raw)


_DUMMY_OCR_RESULT = {
    "date": date.today().isoformat(),
    "type": "支出",
    "category": "食費",
    "amount": 1580,
    "store_name": "サンプルスーパー",
    "memo": "サンプルスーパー 食料品",
    "items": [
        {"name": "野菜セット", "price": 580},
        {"name": "牛乳 1L", "price": 198},
        {"name": "食パン", "price": 298},
        {"name": "卵 10個", "price": 238},
        {"name": "ヨーグルト", "price": 266},
    ],
}


# ─────────────────────────────────────────────
# Machine Learning Functions
# ─────────────────────────────────────────────

def _ml_feature(text: str) -> str:
    """Combine memo and any extra context into a single feature string."""
    return (text or "").strip()


def train_category_classifier():
    """
    Train a TF-IDF + Logistic Regression pipeline on existing transaction memos.
    Returns the fitted pipeline or None if not enough data.
    """
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import Pipeline

    df = get_transactions()
    if df.empty or len(df) < 5 or df["category"].nunique() < 2:
        return None

    X = df["memo"].fillna("").astype(str)
    y = df["category"]

    pipeline = Pipeline([
        # char n-grams work well for Japanese without tokenization
        ("tfidf", TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4), min_df=1)),
        ("clf", LogisticRegression(max_iter=1000, C=1.0)),
    ])
    try:
        pipeline.fit(X, y)
        return pipeline
    except Exception:
        return None


def get_ml_model():
    """Return cached model from session_state, training if needed."""
    if "ml_model" not in st.session_state or st.session_state.get("ml_retrain"):
        st.session_state["ml_model"] = train_category_classifier()
        st.session_state["ml_retrain"] = False
    return st.session_state["ml_model"]


def predict_category_ml(memo: str) -> tuple[str | None, float]:
    """Return (predicted_category, confidence) or (None, 0.0) if unavailable."""
    model = get_ml_model()
    if not model or not memo.strip():
        return None, 0.0
    proba = model.predict_proba([memo])[0]
    idx = int(proba.argmax())
    return model.classes_[idx], float(proba[idx])


def forecast_expenses_by_category() -> pd.DataFrame:
    """
    Predict next month's spend per category using per-category linear regression
    on monthly totals. Returns a DataFrame with columns [category, predicted_amount].
    """
    from sklearn.linear_model import LinearRegression
    import numpy as np

    df = get_transactions()
    exp = df[df["type"] == "支出"].copy()
    if exp.empty:
        return pd.DataFrame(columns=["カテゴリ", "予測金額"])

    exp["period"] = pd.to_datetime(exp["date"]).dt.to_period("M")
    exp["period_ord"] = exp["period"].apply(lambda p: p.ordinal)

    results = []
    for cat, grp in exp.groupby("category"):
        monthly = grp.groupby("period_ord")["amount"].sum().reset_index()
        if len(monthly) < 2:
            results.append({"カテゴリ": cat, "予測金額": int(grp["amount"].mean())})
            continue
        X = monthly["period_ord"].values.reshape(-1, 1)
        y = monthly["amount"].values
        reg = LinearRegression().fit(X, y)
        next_ord = int(monthly["period_ord"].max()) + 1
        pred = max(0, int(reg.predict([[next_ord]])[0]))
        results.append({"カテゴリ": cat, "予測金額": pred})

    return pd.DataFrame(results).sort_values("予測金額", ascending=False)


def detect_anomalies(year: int, month: int) -> pd.DataFrame:
    """
    Flag transactions whose amount is more than 2 standard deviations
    above the mean for that category (using all historical data as baseline).
    Returns the flagged rows with an extra 'z_score' column.
    """
    import numpy as np

    all_df = get_transactions()
    if all_df.empty:
        return pd.DataFrame()

    # Compute per-category mean/std from all historical data
    stats = all_df.groupby("category")["amount"].agg(["mean", "std"]).reset_index()
    stats.columns = ["category", "mean", "std"]
    stats["std"] = stats["std"].fillna(0)

    month_df = get_transactions(year=year, month=month)
    if month_df.empty:
        return pd.DataFrame()

    merged = month_df.merge(stats, on="category", how="left")
    merged["z_score"] = merged.apply(
        lambda r: (r["amount"] - r["mean"]) / r["std"] if r["std"] > 0 else 0, axis=1
    )
    flagged = merged[merged["z_score"] >= 2.0].copy()
    return flagged[["date", "category", "amount", "memo", "z_score"]].sort_values("z_score", ascending=False)


# ─────────────────────────────────────────────
# UI Sections
# ─────────────────────────────────────────────

def show_pin_lock_screen():
    inject_css()
    st.title("🔐 家計簿")
    st.caption("PINを入力してください")
    pin_input = st.text_input("PIN", type="password", max_chars=4, placeholder="4桁のPIN")
    if st.button("ロック解除", type="primary"):
        if verify_pin(pin_input):
            st.session_state.authenticated = True
            st.rerun()
        else:
            st.error("PINが違います。")


def show_input_tab(year: int, month: int):
    st.subheader("✏️ 収支を入力")

    if "quick_add_total" not in st.session_state:
        st.session_state.quick_add_total = 0

    # ── フォーム外: メモ入力でMLカテゴリ予測 ──
    memo_preview = st.text_input(
        "メモ（カテゴリをAIが予測します）",
        placeholder="例: スーパーで購入、電車代",
        key="memo_preview",
    )
    pred_cat, pred_conf = predict_category_ml(memo_preview)
    if pred_cat:
        st.caption(f"🤖 AI予測カテゴリ: **{pred_cat}** (信頼度 {pred_conf:.0%})")

    st.markdown("**クイック追加**")
    q_cols = st.columns(len(QUICK_AMOUNTS))
    for i, amt in enumerate(QUICK_AMOUNTS):
        with q_cols[i]:
            if st.button(f"+¥{amt:,}", key=f"quick_{amt}", use_container_width=True):
                st.session_state.quick_add_total += amt
                st.rerun()

    if st.session_state.quick_add_total > 0:
        st.info(f"クイック追加累計: ¥{st.session_state.quick_add_total:,}")

    with st.form("input_form", clear_on_submit=True):
        col1, col2 = st.columns(2)
        with col1:
            tx_type = st.radio("種別", ["支出", "収入"], horizontal=True, key="form_type")
        with col2:
            tx_date = st.date_input("日付", value=date.today(), key="form_date")

        categories = get_categories(tx_type)
        # AI予測カテゴリを初期選択に反映
        default_idx = categories.index(pred_cat) if pred_cat in categories else 0
        category = st.selectbox("カテゴリ", categories, index=default_idx, key="form_category")

        amount = st.number_input(
            "金額 (円)", min_value=0, step=100, format="%d", key="form_amount",
        )

        memo = st.text_input(
            "メモ（フォーム用）",
            value=memo_preview,
            placeholder="例: スーパーで購入",
            key="form_memo",
        )

        submitted = st.form_submit_button("💾 登録", type="primary", use_container_width=True)

        if submitted:
            final_amount = int(amount) + st.session_state.quick_add_total
            if final_amount <= 0:
                st.error("金額は1円以上入力してください。")
            else:
                if add_transaction(str(tx_date), tx_type, category, final_amount, memo):
                    st.success(f"✅ 登録完了: {tx_type} {category} ¥{final_amount:,}")
                    st.session_state.quick_add_total = 0
                    st.session_state["ml_retrain"] = True  # 新データで再学習フラグ
                    st.rerun()

    # Reset quick-add button
    if st.session_state.quick_add_total > 0:
        if st.button("クイック追加をリセット", key="reset_quick"):
            st.session_state.quick_add_total = 0
            st.rerun()

    # 定期取引の一括適用
    rec_df = get_recurring(active_only=True)
    if not rec_df.empty:
        st.markdown("---")
        if st.button(f"🔁 定期取引を今月に適用 ({len(rec_df)}件)", use_container_width=True):
            n = apply_recurring(year, month)
            if n > 0:
                st.success(f"✅ {n}件の定期取引を登録しました。")
                st.session_state["ml_retrain"] = True
                st.rerun()
            else:
                st.info("今月分はすでに登録済みです。")

    # Recent entries
    st.subheader("最近の登録 (5件)")
    recent = get_recent_transactions(5)
    if recent.empty:
        st.info("まだ登録がありません。")
    else:
        display = recent[["date", "type", "category", "amount", "memo"]].copy()
        display.columns = ["日付", "種別", "カテゴリ", "金額", "メモ"]
        display["金額"] = display["金額"].apply(lambda x: f"¥{int(x):,}")
        st.dataframe(display, use_container_width=True, hide_index=True)


def show_ocr_tab(year: int, month: int):
    st.subheader("📷 レシートOCR読取")
    st.caption("レシートや領収書の写真をアップロードすると、AIが自動で情報を読み取ります。")
    st.markdown("**処理フロー:** 画像アップロード → Claude AI 解析 → 確認フォーム → DB登録")

    has_key = bool(_get_api_key())

    col_upload, col_test = st.columns([3, 1])
    with col_test:
        st.markdown("&nbsp;", unsafe_allow_html=True)
        if st.button("🧪 ダミーでテスト", use_container_width=True, help="APIキーなしでフォームをテストできます"):
            import copy
            st.session_state["ocr_result"] = copy.deepcopy(_DUMMY_OCR_RESULT)
            st.session_state["ocr_source"] = "dummy"
            st.rerun()

    with col_upload:
        if not has_key:
            st.warning("⚠️ `ANTHROPIC_API_KEY` が未設定です。ダミーテストは右のボタンで実行できます。")
            with st.expander("APIキーの設定方法"):
                st.code("export ANTHROPIC_API_KEY='sk-ant-...'", language="bash")
                st.markdown("または `.streamlit/secrets.toml` に:")
                st.code('ANTHROPIC_API_KEY = "sk-ant-..."', language="toml")
        else:
            uploaded = st.file_uploader(
                "レシート画像をアップロード",
                type=["jpg", "jpeg", "png", "gif", "webp"],
                help="JPG / PNG / GIF / WebP 対応",
                key="ocr_uploader",
            )
            if uploaded:
                st.image(uploaded, caption="アップロード画像", use_column_width=True)
                if st.button("🔍 AIで読み取る", type="primary", use_container_width=True):
                    image_bytes = uploaded.read()
                    ext = uploaded.name.rsplit(".", 1)[-1].lower()
                    media_type = MEDIA_TYPE_MAP.get(ext, "image/jpeg")
                    with st.spinner("Claude AIがレシートを解析中..."):
                        try:
                            result = ocr_receipt_with_claude(image_bytes, media_type)
                            st.session_state["ocr_result"] = result
                            st.session_state["ocr_source"] = "api"
                            st.success("読み取り完了！内容を確認して登録してください。")
                            st.rerun()
                        except json.JSONDecodeError as e:
                            st.error(f"AI応答のパースに失敗しました: {e}")
                        except Exception as e:
                            st.error(f"OCRエラー: {e}")

    # ── 確認・登録フォーム ──
    if "ocr_result" not in st.session_state:
        return

    result = st.session_state["ocr_result"]
    source_label = "ダミーデータ" if st.session_state.get("ocr_source") == "dummy" else "AI読み取り結果"
    st.markdown("---")
    st.markdown(f"### {source_label}の確認・修正")

    # 品目一覧表示
    items = result.get("items") or []
    if items:
        items_df = pd.DataFrame(items)
        items_df.columns = ["品目", "金額(円)"]
        items_df["金額(円)"] = items_df["金額(円)"].apply(lambda x: f"¥{x:,}")
        with st.expander(f"品目一覧 ({len(items)}件)", expanded=True):
            st.dataframe(items_df, use_container_width=True, hide_index=True)

    with st.form("ocr_confirm_form", clear_on_submit=False):
        col1, col2 = st.columns(2)
        with col1:
            tx_type = st.radio(
                "種別", ["支出", "収入"],
                index=0 if result.get("type") == "支出" else 1,
                horizontal=True,
                key="ocr_type",
            )
        with col2:
            try:
                ocr_date = date.fromisoformat(result.get("date", date.today().isoformat()))
            except (ValueError, TypeError):
                ocr_date = date.today()
            tx_date = st.date_input("日付", value=ocr_date, key="ocr_date")

        # カテゴリ：OCR提案 → ML予測 → フォールバックの優先順
        cats = get_categories(tx_type)
        ocr_suggested = result.get("category", "")
        memo_for_ml = result.get("memo") or result.get("store_name") or ""
        ml_cat, ml_conf = predict_category_ml(memo_for_ml)

        if ocr_suggested in cats:
            default_idx = cats.index(ocr_suggested)
            cat_hint = f"Claude OCR提案: {ocr_suggested}"
        elif ml_cat and ml_cat in cats:
            default_idx = cats.index(ml_cat)
            cat_hint = f"🤖 ML予測: {ml_cat} ({ml_conf:.0%})"
        else:
            default_idx = 0
            cat_hint = ""

        category = st.selectbox(
            "カテゴリ",
            cats,
            index=default_idx,
            key="ocr_category",
            help=cat_hint,
        )

        amount = st.number_input(
            "金額（円）",
            value=max(0, int(result.get("amount", 0))),
            min_value=0,
            step=100,
            format="%d",
            key="ocr_amount",
        )

        memo_default = result.get("memo") or result.get("store_name") or ""
        memo = st.text_input("メモ", value=memo_default, key="ocr_memo")

        col_reg, col_clear = st.columns(2)
        with col_reg:
            submitted = st.form_submit_button("💾 登録", type="primary", use_container_width=True)
        with col_clear:
            cleared = st.form_submit_button("🗑️ クリア", use_container_width=True)

        if submitted:
            if amount <= 0:
                st.error("金額は1円以上入力してください。")
            elif add_transaction(str(tx_date), tx_type, category, int(amount), memo):
                st.success(f"✅ 登録完了: {tx_type} {category} ¥{int(amount):,}")
                st.session_state.pop("ocr_result", None)
                st.session_state.pop("ocr_source", None)
                st.rerun()

        if cleared:
            st.session_state.pop("ocr_result", None)
            st.session_state.pop("ocr_source", None)
            st.rerun()


def show_list_tab(year: int, month: int):
    st.subheader("📋 取引一覧")

    col1, col2 = st.columns(2)
    with col1:
        filter_type = st.radio("種別フィルター", ["すべて", "支出", "収入"], horizontal=True, key="list_type")
    with col2:
        # カテゴリ候補を種別フィルターに連動させる
        if filter_type == "すべて":
            cat_pool = [c["name"] for c in get_categories()]
        else:
            cat_pool = get_categories(filter_type)
        filter_cats = st.multiselect("カテゴリフィルター", cat_pool, key="list_cats")

    df = get_transactions(
        year=year, month=month,
        tx_type=filter_type if filter_type != "すべて" else None,
        categories=filter_cats if filter_cats else None
    )

    if df.empty:
        st.info("この月のデータがありません。")
        return

    # Display dataframe
    display_df = df[["date", "type", "category", "amount", "memo"]].copy()
    display_df.columns = ["日付", "種別", "カテゴリ", "金額", "メモ"]
    display_df["金額"] = display_df["金額"].apply(lambda x: f"¥{int(x):,}")

    st.dataframe(
        display_df,
        use_container_width=True,
        hide_index=True,
        height=min(400, max(200, len(display_df) * 36 + 40))
    )

    # Totals
    total_income = int(df[df["type"] == "収入"]["amount"].sum())
    total_expense = int(df[df["type"] == "支出"]["amount"].sum())
    balance = total_income - total_expense

    col1, col2, col3 = st.columns(3)
    col1.metric("収入合計", f"¥{total_income:,}")
    col2.metric("支出合計", f"¥{total_expense:,}")
    col3.metric("収支", f"¥{balance:,}", delta=f"{'黒字' if balance >= 0 else '赤字'}")

    # Delete section
    st.markdown("---")
    st.subheader("削除")
    st.caption("削除する取引を選択してください。")

    id_options = df["id"].tolist()
    id_labels = {
        row["id"]: f"ID:{row['id']} | {row['date']} | {row['category']} | ¥{int(row['amount']):,}"
        for _, row in df.iterrows()
    }
    del_col1, del_col2 = st.columns([3, 1])
    with del_col1:
        selected_id = st.selectbox(
            "削除する取引",
            options=id_options,
            format_func=lambda x: id_labels.get(x, str(x)),
            key="delete_select"
        )
    with del_col2:
        st.write("")
        st.write("")
        if st.button("🗑️ 削除", use_container_width=True, type="secondary"):
            st.session_state["confirm_delete_id"] = selected_id

    if "confirm_delete_id" in st.session_state:
        cid = st.session_state["confirm_delete_id"]
        st.warning(f"ID {cid} の取引を削除しますか？")
        c1, c2 = st.columns(2)
        with c1:
            if st.button("✅ はい、削除します", use_container_width=True, type="primary", key="confirm_del"):
                delete_transaction(cid)
                del st.session_state["confirm_delete_id"]
                st.success("削除しました。")
                st.rerun()
        with c2:
            if st.button("❌ キャンセル", use_container_width=True, key="cancel_del"):
                del st.session_state["confirm_delete_id"]
                st.rerun()


def show_charts_tab(year: int, month: int):
    st.subheader("📊 グラフ分析")
    st.caption(f"{year}年{month}月のデータを表示しています")

    chart_tab1, chart_tab2, chart_tab3, chart_tab4 = st.tabs(["支出内訳", "日別収支", "累計収支", "予算 vs 実績"])
    with chart_tab1:
        chart_expense_pie(year, month)
    with chart_tab2:
        chart_daily_bar(year, month)
    with chart_tab3:
        chart_cumulative_line(year, month)
    with chart_tab4:
        bstatus = get_budget_status(year, month)
        if bstatus.empty:
            st.info("予算が設定されていません。⚙️設定タブから予算を登録してください。")
        else:
            fig = go.Figure()
            fig.add_trace(go.Bar(
                name="予算", x=bstatus["category"], y=bstatus["budget"],
                marker_color="#aab4f8", opacity=0.8,
            ))
            fig.add_trace(go.Bar(
                name="実績", x=bstatus["category"], y=bstatus["actual"],
                marker_color=["#ff6b6b" if v else "#51cf66" for v in bstatus["over_budget"]],
            ))
            fig.update_layout(
                barmode="group", title="予算 vs 実績",
                yaxis_title="金額（円）", height=380,
                margin=dict(t=50, b=40),
            )
            st.plotly_chart(fig, use_container_width=True)
            over = bstatus[bstatus["over_budget"]]
            if not over.empty:
                st.warning(f"⚠️ 予算超過: {', '.join(over['category'].tolist())}")


def show_export_tab(year: int, month: int):
    st.subheader("💾 データエクスポート")

    period_option = st.selectbox(
        "期間を選択",
        ["今月", "先月", "今年", "カスタム期間"],
        key="export_period"
    )

    today = date.today()
    start_date = None
    end_date = None
    period_label = ""

    if period_option == "今月":
        start_date = f"{year}-{month:02d}-01"
        last_day = calendar.monthrange(year, month)[1]
        end_date = f"{year}-{month:02d}-{last_day:02d}"
        period_label = f"{year}年{month}月"
    elif period_option == "先月":
        if month == 1:
            ly, lm = year - 1, 12
        else:
            ly, lm = year, month - 1
        start_date = f"{ly}-{lm:02d}-01"
        last_day = calendar.monthrange(ly, lm)[1]
        end_date = f"{ly}-{lm:02d}-{last_day:02d}"
        period_label = f"{ly}年{lm}月"
    elif period_option == "今年":
        start_date = f"{year}-01-01"
        end_date = f"{year}-12-31"
        period_label = f"{year}年"
    else:
        col1, col2 = st.columns(2)
        with col1:
            custom_start = st.date_input("開始日", value=date(year, month, 1), key="export_start")
        with col2:
            custom_end = st.date_input("終了日", value=today, key="export_end")
        start_date = str(custom_start)
        end_date = str(custom_end)
        period_label = f"{custom_start} 〜 {custom_end}"

    df = get_transactions(start_date=start_date, end_date=end_date)

    if df.empty:
        st.info("この期間のデータがありません。")
        return

    # Preview
    st.markdown(f"**プレビュー** ({len(df)}件)")
    preview_df = df[["date", "type", "category", "amount", "memo"]].copy()
    preview_df.columns = ["日付", "種別", "カテゴリ", "金額", "メモ"]
    preview_df["金額"] = preview_df["金額"].apply(lambda x: f"¥{int(x):,}")
    st.dataframe(preview_df, use_container_width=True, hide_index=True, height=280)

    # Summary
    total_income = int(df[df["type"] == "収入"]["amount"].sum())
    total_expense = int(df[df["type"] == "支出"]["amount"].sum())
    balance = total_income - total_expense
    col1, col2, col3 = st.columns(3)
    col1.metric("収入合計", f"¥{total_income:,}")
    col2.metric("支出合計", f"¥{total_expense:,}")
    col3.metric("収支", f"¥{balance:,}")

    st.markdown("---")
    col_csv, col_excel = st.columns(2)

    with col_csv:
        csv_data = df_to_csv_bytes(df)
        fname_base = period_label.replace(" ", "_").replace("〜", "to").replace("年", "").replace("月", "")
        st.download_button(
            label="📥 CSVダウンロード",
            data=csv_data,
            file_name=f"kakeibo_{fname_base}.csv",
            mime="text/csv",
            use_container_width=True,
            type="primary"
        )

    with col_excel:
        excel_data = df_to_excel_bytes(df, period_label)
        st.download_button(
            label="📊 Excelダウンロード",
            data=excel_data,
            file_name=f"kakeibo_{fname_base}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
            type="secondary"
        )


def show_settings_tab(year: int, month: int):
    st.subheader("⚙️ 設定")

    set_tab1, set_tab2, set_tab3, set_tab4, set_tab5 = st.tabs([
        "🔐 PINロック", "🏷️ カテゴリ管理", "📋 予算設定", "🔁 定期取引", "💿 データ管理"
    ])

    # ── PIN Tab ──
    with set_tab1:
        st.markdown("#### PINロック設定")
        if has_pin():
            st.success("✅ PINロックが有効です")
            st.markdown("---")
            st.markdown("**PINを変更する**")
            with st.form("change_pin_form"):
                current_pin = st.text_input("現在のPIN", type="password", max_chars=4)
                new_pin = st.text_input("新しいPIN (4桁)", type="password", max_chars=4)
                confirm_pin = st.text_input("新しいPINを確認", type="password", max_chars=4)
                if st.form_submit_button("PINを変更", type="primary"):
                    if not verify_pin(current_pin):
                        st.error("現在のPINが違います。")
                    elif len(new_pin) != 4 or not new_pin.isdigit():
                        st.error("新しいPINは4桁の数字にしてください。")
                    elif new_pin != confirm_pin:
                        st.error("新しいPINが一致しません。")
                    else:
                        set_pin(new_pin)
                        st.success("PINを変更しました。")

            st.markdown("---")
            st.markdown("**PINを削除する**")
            with st.form("remove_pin_form"):
                verify_for_remove = st.text_input("現在のPINを確認", type="password", max_chars=4)
                if st.form_submit_button("🔓 PINロックを解除", type="secondary"):
                    if verify_pin(verify_for_remove):
                        remove_pin()
                        st.success("PINロックを解除しました。")
                        st.rerun()
                    else:
                        st.error("PINが違います。")
        else:
            st.info("PINロックは無効です")
            st.markdown("---")
            st.markdown("**PINを設定する**")
            with st.form("set_pin_form"):
                new_pin = st.text_input("新しいPIN (4桁の数字)", type="password", max_chars=4)
                confirm_pin = st.text_input("確認", type="password", max_chars=4)
                if st.form_submit_button("🔐 PINを設定", type="primary"):
                    if len(new_pin) != 4 or not new_pin.isdigit():
                        st.error("PINは4桁の数字にしてください。")
                    elif new_pin != confirm_pin:
                        st.error("PINが一致しません。")
                    else:
                        set_pin(new_pin)
                        st.success("PINを設定しました。次回起動時から有効です。")

    # ── Category Tab ──
    with set_tab2:
        st.markdown("#### カテゴリ管理")

        # Add category
        with st.form("add_category_form"):
            col1, col2 = st.columns(2)
            with col1:
                new_cat_name = st.text_input("カテゴリ名", placeholder="新しいカテゴリ")
            with col2:
                new_cat_type = st.selectbox("種別", ["支出", "収入"])
            if st.form_submit_button("➕ 追加", type="primary"):
                if not new_cat_name.strip():
                    st.error("カテゴリ名を入力してください。")
                elif add_category(new_cat_name.strip(), new_cat_type):
                    st.success(f"「{new_cat_name}」を追加しました。")
                    st.rerun()
                else:
                    st.error("同名のカテゴリが既に存在します。")

        st.markdown("---")
        st.markdown("**現在のカテゴリ一覧**")

        all_cats = get_categories()
        expense_cats = [c for c in all_cats if c["type"] == "支出"]
        income_cats = [c for c in all_cats if c["type"] == "収入"]

        col1, col2 = st.columns(2)
        with col1:
            st.markdown("**支出カテゴリ**")
            for cat in expense_cats:
                c1, c2 = st.columns([3, 1])
                c1.write(cat["name"])
                if c2.button("🗑️", key=f"del_exp_{cat['name']}", help=f"{cat['name']}を削除"):
                    if delete_category(cat["name"]):
                        st.success(f"「{cat['name']}」を削除しました。")
                        st.rerun()
                    else:
                        st.error("このカテゴリは使用中のため削除できません。")

        with col2:
            st.markdown("**収入カテゴリ**")
            for cat in income_cats:
                c1, c2 = st.columns([3, 1])
                c1.write(cat["name"])
                if c2.button("🗑️", key=f"del_inc_{cat['name']}", help=f"{cat['name']}を削除"):
                    if delete_category(cat["name"]):
                        st.success(f"「{cat['name']}」を削除しました。")
                        st.rerun()
                    else:
                        st.error("このカテゴリは使用中のため削除できません。")

    # ── Budget Tab ──
    with set_tab3:
        st.markdown("#### 月別予算設定")
        st.caption(f"{year}年{month}月のカテゴリ別予算を設定します。")

        expense_cats = get_categories("支出")
        with st.form("budget_form"):
            col1, col2 = st.columns(2)
            with col1:
                b_cat = st.selectbox("カテゴリ", expense_cats, key="budget_cat")
            with col2:
                b_amount = st.number_input("予算（円）", min_value=100, step=1000, format="%d", key="budget_amount")
            if st.form_submit_button("💾 設定", type="primary"):
                set_budget(year, month, b_cat, int(b_amount))
                st.success(f"{b_cat} の予算を ¥{int(b_amount):,} に設定しました。")
                st.rerun()

        bstatus = get_budget_status(year, month)
        if not bstatus.empty:
            st.markdown("**現在の予算状況**")
            for _, row in bstatus.iterrows():
                c1, c2, c3 = st.columns([3, 2, 1])
                c1.write(f"**{row['category']}**")
                over = "⚠️ 超過" if row["over_budget"] else f"{row['usage_pct']}%"
                c2.write(f"¥{row['actual']:,} / ¥{row['budget']:,}  {over}")
                if c3.button("削除", key=f"del_budget_{row['category']}"):
                    delete_budget(year, month, row["category"])
                    st.rerun()
        else:
            st.info("この月の予算はまだ設定されていません。")

    # ── Recurring Tab ──
    with set_tab4:
        st.markdown("#### 定期取引")
        st.caption("毎月自動で登録したい取引を設定します（家賃・サブスクなど）。")

        with st.form("recurring_form"):
            col1, col2 = st.columns(2)
            with col1:
                r_type = st.radio("種別", ["支出", "収入"], horizontal=True, key="rec_type")
            with col2:
                r_day = st.number_input("引落日", min_value=1, max_value=28, value=1, step=1, key="rec_day")
            r_cats = get_categories(r_type)
            r_cat = st.selectbox("カテゴリ", r_cats, key="rec_cat")
            r_amount = st.number_input("金額（円）", min_value=1, step=100, format="%d", key="rec_amount")
            r_memo = st.text_input("メモ", placeholder="例: 家賃、Netflix", key="rec_memo")
            if st.form_submit_button("➕ 追加", type="primary"):
                if add_recurring(r_type, r_cat, int(r_amount), r_memo, int(r_day)):
                    st.success("定期取引を追加しました。")
                    st.rerun()

        st.markdown("---")
        rec_df = get_recurring(active_only=False)
        if rec_df.empty:
            st.info("定期取引が登録されていません。")
        else:
            st.markdown("**登録済み定期取引**")
            for _, r in rec_df.iterrows():
                c1, c2, c3, c4 = st.columns([3, 2, 1, 1])
                active = bool(r["active"])
                c1.write(f"{'✅' if active else '⏸️'} 毎月{r['day_of_month']}日 **{r['category']}** {r['memo']}")
                c2.write(f"{r['type']} ¥{int(r['amount']):,}")
                if c3.button("⏸️" if active else "▶️", key=f"tog_rec_{r['id']}",
                             help="無効化" if active else "有効化"):
                    toggle_recurring(int(r["id"]), not active)
                    st.rerun()
                if c4.button("🗑️", key=f"del_rec_{r['id']}", help="削除"):
                    delete_recurring(int(r["id"]))
                    st.rerun()

    # ── Data Management Tab ──
    with set_tab5:
        st.markdown("#### データ管理")
        st.text(f"DBファイル: {DB_PATH}")
        st.text(f"総レコード数: {get_total_record_count():,} 件")

        st.markdown("---")
        st.markdown("**⚠️ 危険ゾーン: データ削除**")
        with st.container():
            st.caption("選択した月のデータをすべて削除します。この操作は取り消せません。")

            col1, col2 = st.columns(2)
            with col1:
                del_year = st.number_input("年", value=year, min_value=2000, max_value=2100, key="del_year")
            with col2:
                del_month = st.number_input("月", value=month, min_value=1, max_value=12, key="del_month")

            if st.button("🗑️ この月のデータをすべて削除", type="secondary", use_container_width=True):
                st.session_state["confirm_month_delete"] = (int(del_year), int(del_month))

            if "confirm_month_delete" in st.session_state:
                dy, dm = st.session_state["confirm_month_delete"]
                st.error(f"⚠️ {dy}年{dm}月のデータをすべて削除しますか？この操作は取り消せません！")
                c1, c2 = st.columns(2)
                with c1:
                    if st.button("✅ 削除する", use_container_width=True, type="primary", key="confirm_month_del"):
                        delete_month_data(dy, dm)
                        del st.session_state["confirm_month_delete"]
                        st.success(f"{dy}年{dm}月のデータを削除しました。")
                        st.rerun()
                with c2:
                    if st.button("❌ キャンセル", use_container_width=True, key="cancel_month_del"):
                        del st.session_state["confirm_month_delete"]
                        st.rerun()


def show_ml_tab(year: int, month: int):
    st.subheader("🤖 ML分析")

    total = get_total_record_count()
    if total < 5:
        st.info(f"ML機能を使うには最低5件のデータが必要です（現在 {total} 件）。まず取引を登録してください。")
        return

    model = get_ml_model()

    # ── 1. カテゴリ予測テスト ──
    st.markdown("#### カテゴリ予測テスト")
    st.caption("メモを入力すると、過去のデータから最適なカテゴリを予測します。")
    test_memo = st.text_input("メモを入力", placeholder="例: コンビニ、ガス代、映画", key="ml_test_memo")
    if test_memo:
        if model:
            proba = model.predict_proba([test_memo])[0]
            top_n = min(3, len(model.classes_))
            top_idx = proba.argsort()[::-1][:top_n]
            for rank, idx in enumerate(top_idx, 1):
                bar = "█" * int(proba[idx] * 20)
                st.write(f"{rank}位 **{model.classes_[idx]}** {proba[idx]:.0%}  `{bar}`")
        else:
            st.warning("モデルを学習できませんでした（データのカテゴリが1種類のみの可能性があります）。")

    if st.button("🔄 モデルを再学習", key="ml_retrain_btn"):
        st.session_state["ml_retrain"] = True
        get_ml_model()
        st.success("再学習完了。")

    st.markdown("---")

    # ── 2. 来月の支出予測 ──
    st.markdown("#### 来月の支出予測")
    st.caption("カテゴリごとの月次推移から線形回帰で予測します。")
    forecast_df = forecast_expenses_by_category()
    if forecast_df.empty:
        st.info("予測に十分な支出データがありません。")
    else:
        total_forecast = int(forecast_df["予測金額"].sum())
        st.metric("来月 合計予測支出", f"¥{total_forecast:,}")
        fig = px.bar(
            forecast_df,
            x="カテゴリ", y="予測金額",
            labels={"予測金額": "予測金額（円）"},
            color="予測金額",
            color_continuous_scale="Blues",
        )
        fig.update_layout(showlegend=False, height=350, margin=dict(t=20, b=40))
        st.plotly_chart(fig, use_container_width=True)

    st.markdown("---")

    # ── 3. 異常支出検知 ──
    st.markdown(f"#### 異常支出検知（{year}年{month}月）")
    st.caption("過去の平均から2σ以上外れた取引をフラグします。")
    anomalies = detect_anomalies(year, month)
    if anomalies.empty:
        st.success("今月の異常支出は検出されませんでした。")
    else:
        display = anomalies.copy()
        display["金額"] = display["amount"].apply(lambda x: f"¥{int(x):,}")
        display["外れ度"] = display["z_score"].apply(lambda z: f"{z:.1f}σ")
        st.warning(f"{len(display)} 件の異常支出を検出しました。")
        st.dataframe(
            display[["date", "category", "金額", "memo", "外れ度"]].rename(
                columns={"date": "日付", "category": "カテゴリ", "memo": "メモ"}
            ),
            use_container_width=True, hide_index=True,
        )


def show_sidebar() -> tuple:
    with st.sidebar:
        st.markdown("# 🏠 家計簿")
        st.markdown("---")

        today = date.today()
        col1, col2 = st.columns(2)
        with col1:
            sel_year = st.number_input(
                "年", value=today.year, min_value=2000, max_value=2100,
                key="sel_year", step=1
            )
        with col2:
            sel_month = st.number_input(
                "月", value=today.month, min_value=1, max_value=12,
                key="sel_month", step=1
            )

        year = int(sel_year)
        month = int(sel_month)

        st.markdown(f"### {year}年{month}月")
        summary = get_monthly_summary(year, month)

        st.metric("💰 収入合計", f"¥{summary['income']:,}")
        st.metric("💸 支出合計", f"¥{summary['expense']:,}")

        balance = summary["balance"]
        delta_color = "normal" if balance >= 0 else "inverse"
        st.metric(
            "📊 収支",
            f"¥{balance:,}",
            delta=f"{'黒字' if balance >= 0 else '赤字'}",
            delta_color=delta_color
        )

        # 収支比率
        if summary["income"] > 0:
            usage_ratio = min(summary["expense"] / summary["income"], 1.0)
            st.markdown(f"**支出率: {usage_ratio*100:.1f}%**")
            st.progress(usage_ratio)
        elif summary["expense"] > 0:
            st.progress(1.0)
            st.caption("支出のみのデータです")

        # 予算進捗
        bstatus = get_budget_status(year, month)
        if not bstatus.empty:
            st.markdown("---")
            st.markdown("**📋 予算進捗**")
            for _, row in bstatus.iterrows():
                ratio = min(row["actual"] / row["budget"], 1.0)
                label = f"{row['category']} {row['usage_pct']}%"
                if row["over_budget"]:
                    label += " ⚠️"
                st.caption(label)
                st.progress(ratio)

        st.markdown("---")
        st.caption(f"DB: {os.path.basename(DB_PATH)}")
        st.caption(f"総件数: {get_total_record_count():,}件")

        # Lock button
        if has_pin() and st.session_state.get("authenticated"):
            if st.button("🔒 ロック", use_container_width=True):
                st.session_state.authenticated = False
                st.rerun()

    return year, month


# ─────────────────────────────────────────────
# Main App Entry Point
# ─────────────────────────────────────────────

def main():
    st.set_page_config(
        page_title="家計簿",
        page_icon="🏠",
        layout="wide",
        initial_sidebar_state="expanded",
        menu_items={
            "About": "家計簿アプリ - プライベートな家計管理ツール",
        }
    )

    inject_css()
    init_db()

    # Session state initialization
    if "authenticated" not in st.session_state:
        st.session_state.authenticated = False
    if "quick_add_total" not in st.session_state:
        st.session_state.quick_add_total = 0

    # PIN lock check
    if has_pin() and not st.session_state.authenticated:
        show_pin_lock_screen()
        return

    # Mark as authenticated if no PIN configured
    if not has_pin():
        st.session_state.authenticated = True

    # Sidebar returns selected year/month
    year, month = show_sidebar()

    # Main tab navigation
    tab1, tab2, tab3, tab4, tab5, tab6, tab7 = st.tabs([
        "✏️ 入力",
        "📷 OCR読取",
        "📋 一覧",
        "📊 グラフ",
        "🤖 ML分析",
        "💾 エクスポート",
        "⚙️ 設定",
    ])

    with tab1:
        show_input_tab(year, month)

    with tab2:
        show_ocr_tab(year, month)

    with tab3:
        show_list_tab(year, month)

    with tab4:
        show_charts_tab(year, month)

    with tab5:
        show_ml_tab(year, month)

    with tab6:
        show_export_tab(year, month)

    with tab7:
        show_settings_tab(year, month)


if __name__ == "__main__":
    main()
