"""
家計簿アプリ (Household Budget App)
A mobile-friendly Streamlit app for tracking income and expenses.
"""

import streamlit as st
import pandas as pd
import sqlite3
import json
import os
import io
from datetime import date, datetime, timedelta
import plotly.express as px
import plotly.graph_objects as go
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
import bcrypt

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

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

# ---------------------------------------------------------------------------
# CSS
# ---------------------------------------------------------------------------

def inject_css():
    st.markdown("""
    <style>
    /* ── General layout ── */
    .main .block-container {
        padding-top: 1rem;
        padding-bottom: 2rem;
        max-width: 900px;
    }

    /* ── Larger tap targets for buttons ── */
    .stButton > button {
        height: 2.8rem;
        border-radius: 8px;
        font-size: 0.95rem;
        width: 100%;
    }

    /* ── Quick amount buttons ── */
    .quick-btn > button {
        background-color: #e8f4fd;
        border: 1px solid #90caf9;
        color: #1565c0;
        font-weight: 600;
        font-size: 0.85rem;
    }
    .quick-btn > button:hover {
        background-color: #bbdefb;
        border-color: #1565c0;
    }

    /* ── Primary action button ── */
    div[data-testid="stForm"] .stButton > button[kind="primary"] {
        background-color: #1976d2;
        color: white;
        font-size: 1.05rem;
        font-weight: 700;
        height: 3.2rem;
    }

    /* ── Card-style metrics ── */
    [data-testid="stMetric"] {
        background-color: #f8f9fa;
        border: 1px solid #e0e0e0;
        border-radius: 10px;
        padding: 0.75rem 1rem;
    }

    /* ── Tabs ── */
    .stTabs [data-baseweb="tab"] {
        font-size: 1rem;
        padding: 0.6rem 1rem;
    }

    /* ── Radio buttons (支出/収入 selector) ── */
    .stRadio [data-testid="stMarkdownContainer"] p {
        font-size: 1rem;
    }

    /* ── PIN entry screen ── */
    .pin-container {
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        min-height: 60vh;
        gap: 1rem;
    }
    .pin-title {
        font-size: 1.8rem;
        font-weight: 700;
        margin-bottom: 0.5rem;
    }
    .pin-subtitle {
        color: #666;
        margin-bottom: 1rem;
    }

    /* ── Danger zone ── */
    .danger-zone {
        border: 2px solid #f44336;
        border-radius: 8px;
        padding: 1rem;
        margin-top: 1rem;
    }

    /* ── Mobile: stack columns ── */
    @media (max-width: 640px) {
        .main .block-container {
            padding-left: 0.5rem;
            padding-right: 0.5rem;
        }
    }
    </style>
    """, unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Config helpers (PIN)
# ---------------------------------------------------------------------------

def load_config() -> dict:
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_config(cfg: dict):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def get_pin_hash() -> str | None:
    return load_config().get("pin_hash")


def set_pin(pin: str):
    cfg = load_config()
    hashed = bcrypt.hashpw(pin.encode(), bcrypt.gensalt()).decode()
    cfg["pin_hash"] = hashed
    save_config(cfg)


def remove_pin():
    cfg = load_config()
    cfg.pop("pin_hash", None)
    save_config(cfg)


def verify_pin(pin: str) -> bool:
    pin_hash = get_pin_hash()
    if pin_hash is None:
        return True
    try:
        return bcrypt.checkpw(pin.encode(), pin_hash.encode())
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    cur = conn.cursor()
    cur.executescript("""
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            type TEXT NOT NULL CHECK(type IN ('支出','収入')),
            category TEXT NOT NULL,
            amount INTEGER NOT NULL CHECK(amount > 0),
            memo TEXT DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            type TEXT NOT NULL CHECK(type IN ('支出','収入'))
        );
    """)
    # Insert default categories if empty
    cur.execute("SELECT COUNT(*) FROM categories")
    if cur.fetchone()[0] == 0:
        for cat in DEFAULT_EXPENSE_CATEGORIES:
            cur.execute(
                "INSERT OR IGNORE INTO categories (name, type) VALUES (?, '支出')", (cat,)
            )
        for cat in DEFAULT_INCOME_CATEGORIES:
            cur.execute(
                "INSERT OR IGNORE INTO categories (name, type) VALUES (?, '収入')", (cat,)
            )
    conn.commit()
    conn.close()


def get_categories(type_filter: str | None = None) -> list[str]:
    conn = get_conn()
    cur = conn.cursor()
    if type_filter:
        cur.execute(
            "SELECT name FROM categories WHERE type = ? ORDER BY id", (type_filter,)
        )
    else:
        cur.execute("SELECT name FROM categories ORDER BY type, id")
    rows = cur.fetchall()
    conn.close()
    return [r["name"] for r in rows]


def get_all_categories_with_type() -> pd.DataFrame:
    conn = get_conn()
    df = pd.read_sql_query(
        "SELECT id, name, type FROM categories ORDER BY type, id", conn
    )
    conn.close()
    return df


def add_category(name: str, type_: str) -> bool:
    try:
        conn = get_conn()
        conn.execute(
            "INSERT INTO categories (name, type) VALUES (?, ?)", (name, type_)
        )
        conn.commit()
        conn.close()
        return True
    except sqlite3.IntegrityError:
        return False


def delete_category(name: str) -> bool:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM transactions WHERE category = ?", (name,))
    count = cur.fetchone()[0]
    if count > 0:
        conn.close()
        return False  # Cannot delete used category
    cur.execute("DELETE FROM categories WHERE name = ?", (name,))
    conn.commit()
    conn.close()
    return True


def add_transaction(
    date_str: str, type_: str, category: str, amount: int, memo: str
) -> int:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO transactions (date, type, category, amount, memo)
        VALUES (?, ?, ?, ?, ?)
        """,
        (date_str, type_, category, amount, memo),
    )
    conn.commit()
    row_id = cur.lastrowid
    conn.close()
    return row_id


def delete_transaction(row_id: int):
    conn = get_conn()
    conn.execute("DELETE FROM transactions WHERE id = ?", (row_id,))
    conn.commit()
    conn.close()


def get_transactions(
    year: int | None = None,
    month: int | None = None,
    type_filter: str | None = None,
    categories: list[str] | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> pd.DataFrame:
    conn = get_conn()
    conditions = []
    params: list = []

    if year and month:
        conditions.append("date LIKE ?")
        params.append(f"{year:04d}-{month:02d}-%")
    elif year:
        conditions.append("date LIKE ?")
        params.append(f"{year:04d}-%")
    if date_from:
        conditions.append("date >= ?")
        params.append(date_from)
    if date_to:
        conditions.append("date <= ?")
        params.append(date_to)
    if type_filter and type_filter != "すべて":
        conditions.append("type = ?")
        params.append(type_filter)
    if categories:
        placeholders = ",".join("?" * len(categories))
        conditions.append(f"category IN ({placeholders})")
        params.extend(categories)

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    query = f"SELECT * FROM transactions {where} ORDER BY date DESC, id DESC"
    df = pd.read_sql_query(query, conn, params=params)
    conn.close()
    return df


def get_monthly_summary(year: int, month: int) -> dict:
    df = get_transactions(year=year, month=month)
    if df.empty:
        return {"income": 0, "expense": 0, "balance": 0}
    income = int(df[df["type"] == "収入"]["amount"].sum())
    expense = int(df[df["type"] == "支出"]["amount"].sum())
    return {"income": income, "expense": expense, "balance": income - expense}


def get_total_record_count() -> int:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM transactions")
    count = cur.fetchone()[0]
    conn.close()
    return count


def delete_month_data(year: int, month: int):
    conn = get_conn()
    conn.execute(
        "DELETE FROM transactions WHERE date LIKE ?",
        (f"{year:04d}-{month:02d}-%",),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Export helpers
# ---------------------------------------------------------------------------

def df_to_csv_bytes(df: pd.DataFrame) -> bytes:
    cols = ["date", "type", "category", "amount", "memo"]
    export_df = df[cols].copy() if all(c in df.columns for c in cols) else df.copy()
    export_df.columns = ["日付", "種別", "カテゴリ", "金額", "メモ"] if len(export_df.columns) == 5 else export_df.columns
    csv_str = export_df.to_csv(index=False)
    # UTF-8 BOM for Excel compatibility
    return ("﻿" + csv_str).encode("utf-8")


def df_to_excel_bytes(df: pd.DataFrame) -> bytes:
    cols = ["date", "type", "category", "amount", "memo"]
    export_df = df[cols].copy() if all(c in df.columns for c in cols) else df.copy()
    export_df.columns = ["日付", "種別", "カテゴリ", "金額", "メモ"]

    wb = Workbook()
    ws = wb.active
    ws.title = "家計簿"

    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="1976D2", end_color="1976D2", fill_type="solid")
    header_align = Alignment(horizontal="center", vertical="center")
    thin_border = Border(
        left=Side(style="thin"),
        right=Side(style="thin"),
        top=Side(style="thin"),
        bottom=Side(style="thin"),
    )

    # Header row
    for col_idx, col_name in enumerate(export_df.columns, start=1):
        cell = ws.cell(row=1, column=col_idx, value=col_name)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = header_align
        cell.border = thin_border

    # Data rows
    income_fill = PatternFill(start_color="E8F5E9", end_color="E8F5E9", fill_type="solid")
    expense_fill = PatternFill(start_color="FFEBEE", end_color="FFEBEE", fill_type="solid")

    for row_idx, row in enumerate(export_df.itertuples(index=False), start=2):
        row_fill = income_fill if row[1] == "収入" else expense_fill
        for col_idx, value in enumerate(row, start=1):
            cell = ws.cell(row=row_idx, column=col_idx, value=value)
            cell.fill = row_fill
            cell.border = thin_border
            if col_idx == 4:  # Amount column
                cell.number_format = "#,##0"
                cell.alignment = Alignment(horizontal="right")

    # Column widths
    ws.column_dimensions["A"].width = 14
    ws.column_dimensions["B"].width = 8
    ws.column_dimensions["C"].width = 14
    ws.column_dimensions["D"].width = 12
    ws.column_dimensions["E"].width = 30

    # Summary at the bottom
    if not export_df.empty:
        summary_row = len(export_df) + 3
        ws.cell(row=summary_row, column=1, value="集計").font = Font(bold=True)
        income_total = export_df[export_df["種別"] == "収入"]["金額"].sum()
        expense_total = export_df[export_df["種別"] == "支出"]["金額"].sum()
        ws.cell(row=summary_row + 1, column=1, value="収入合計")
        ws.cell(row=summary_row + 1, column=4, value=income_total).number_format = "#,##0"
        ws.cell(row=summary_row + 2, column=1, value="支出合計")
        ws.cell(row=summary_row + 2, column=4, value=expense_total).number_format = "#,##0"
        ws.cell(row=summary_row + 3, column=1, value="収支").font = Font(bold=True)
        balance_cell = ws.cell(row=summary_row + 3, column=4, value=income_total - expense_total)
        balance_cell.number_format = "#,##0"
        balance_cell.font = Font(
            bold=True,
            color="1565C0" if (income_total - expense_total) >= 0 else "C62828",
        )

    output = io.BytesIO()
    wb.save(output)
    return output.getvalue()


# ---------------------------------------------------------------------------
# Chart helpers
# ---------------------------------------------------------------------------

def chart_pie_expense_by_category(df: pd.DataFrame):
    expense_df = df[df["type"] == "支出"]
    if expense_df.empty:
        st.info("支出データがありません。")
        return
    cat_df = expense_df.groupby("category")["amount"].sum().reset_index()
    cat_df.columns = ["カテゴリ", "金額"]
    cat_df = cat_df.sort_values("金額", ascending=False)
    fig = px.pie(
        cat_df,
        names="カテゴリ",
        values="金額",
        title="支出カテゴリ別内訳",
        hole=0.35,
        color_discrete_sequence=px.colors.qualitative.Set3,
    )
    fig.update_traces(textposition="inside", textinfo="percent+label")
    fig.update_layout(
        legend=dict(orientation="h", yanchor="bottom", y=-0.3),
        margin=dict(t=50, b=80, l=0, r=0),
    )
    st.plotly_chart(fig, use_container_width=True)


def chart_bar_daily(df: pd.DataFrame):
    if df.empty:
        st.info("データがありません。")
        return
    pivot = (
        df.groupby(["date", "type"])["amount"]
        .sum()
        .reset_index()
        .pivot(index="date", columns="type", values="amount")
        .fillna(0)
        .reset_index()
    )
    pivot.columns.name = None
    fig = go.Figure()
    if "収入" in pivot.columns:
        fig.add_trace(
            go.Bar(
                x=pivot["date"],
                y=pivot["収入"],
                name="収入",
                marker_color="#43a047",
            )
        )
    if "支出" in pivot.columns:
        fig.add_trace(
            go.Bar(
                x=pivot["date"],
                y=pivot["支出"],
                name="支出",
                marker_color="#e53935",
            )
        )
    fig.update_layout(
        title="日別 収入・支出",
        barmode="group",
        xaxis_title="日付",
        yaxis_title="金額 (円)",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        margin=dict(t=60, b=40, l=0, r=0),
    )
    st.plotly_chart(fig, use_container_width=True)


def chart_line_cumulative_balance(df: pd.DataFrame):
    if df.empty:
        st.info("データがありません。")
        return
    df2 = df.copy()
    df2["signed"] = df2.apply(
        lambda r: r["amount"] if r["type"] == "収入" else -r["amount"], axis=1
    )
    daily = df2.groupby("date")["signed"].sum().reset_index().sort_values("date")
    daily["累計収支"] = daily["signed"].cumsum()

    fig = px.line(
        daily,
        x="date",
        y="累計収支",
        title="累計収支の推移",
        markers=True,
        color_discrete_sequence=["#1976d2"],
    )
    fig.add_hline(y=0, line_dash="dash", line_color="gray")
    fig.update_layout(
        xaxis_title="日付",
        yaxis_title="累計収支 (円)",
        margin=dict(t=60, b=40, l=0, r=0),
    )
    st.plotly_chart(fig, use_container_width=True)


# ---------------------------------------------------------------------------
# PIN lock screen
# ---------------------------------------------------------------------------

def render_pin_screen():
    st.markdown("""
    <div class="pin-container">
        <div class="pin-title">🔒 家計簿</div>
        <div class="pin-subtitle">PINコードを入力してください</div>
    </div>
    """, unsafe_allow_html=True)

    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        pin_input = st.text_input(
            "PINコード",
            type="password",
            max_chars=4,
            placeholder="4桁のPIN",
            label_visibility="collapsed",
        )
        if st.button("ロック解除", type="primary", use_container_width=True):
            if len(pin_input) == 4 and pin_input.isdigit():
                if verify_pin(pin_input):
                    st.session_state.authenticated = True
                    st.rerun()
                else:
                    st.error("PINコードが正しくありません。")
            else:
                st.warning("4桁の数字を入力してください。")


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

def render_sidebar():
    with st.sidebar:
        st.title("📒 家計簿")
        st.divider()

        today = date.today()
        year = st.selectbox(
            "年",
            options=list(range(today.year - 5, today.year + 2)),
            index=5,
            key="sidebar_year",
        )
        month = st.selectbox(
            "月",
            options=list(range(1, 13)),
            index=today.month - 1,
            key="sidebar_month",
        )

        st.divider()
        summary = get_monthly_summary(year, month)
        st.metric("収入合計", f"¥{summary['income']:,}")
        st.metric(
            "支出合計",
            f"¥{summary['expense']:,}",
        )
        balance = summary["balance"]
        st.metric(
            "収支",
            f"¥{balance:,}",
            delta=f"¥{balance:,}",
            delta_color="normal" if balance >= 0 else "inverse",
        )

        if summary["expense"] > 0:
            st.divider()
            st.caption("支出の進捗")
            # Show progress relative to income (budget usage)
            if summary["income"] > 0:
                ratio = min(summary["expense"] / summary["income"], 1.0)
                st.progress(ratio, text=f"{ratio*100:.1f}% 使用")
            else:
                st.info("収入を入力すると予算進捗が表示されます")

        st.divider()
        st.caption(f"DB: {DB_PATH}")

    return year, month


# ---------------------------------------------------------------------------
# Tab: 入力
# ---------------------------------------------------------------------------

def render_input_tab(year: int, month: int):
    st.subheader("✏️ 取引を入力")

    if "input_amount" not in st.session_state:
        st.session_state.input_amount = 0

    with st.form("input_form", clear_on_submit=True):
        type_col, date_col = st.columns([1, 1])
        with type_col:
            tx_type = st.radio(
                "種別",
                options=["支出", "収入"],
                horizontal=True,
                key="tx_type_radio",
            )
        with date_col:
            tx_date = st.date_input(
                "日付",
                value=date.today(),
                key="tx_date",
            )

        categories = get_categories(tx_type)
        tx_category = st.selectbox("カテゴリ", options=categories)

        tx_amount = st.number_input(
            "金額 (円)",
            min_value=0,
            max_value=100_000_000,
            value=st.session_state.input_amount,
            step=100,
            format="%d",
            key="tx_amount_input",
        )

        # Quick amount buttons
        st.caption("クイック金額ボタン（現在の金額に加算）")
        q_cols = st.columns(len(QUICK_AMOUNTS))
        quick_add = 0
        for i, amount in enumerate(QUICK_AMOUNTS):
            with q_cols[i]:
                label = f"+¥{amount:,}"
                if st.form_submit_button(label, use_container_width=True):
                    quick_add = amount

        tx_memo = st.text_input("メモ（任意）", max_chars=100, placeholder="例: スーパーで買い物")

        submitted = st.form_submit_button("登録する", type="primary", use_container_width=True)

    # Handle quick add outside the form to update session state
    if quick_add > 0:
        st.session_state.input_amount = int(tx_amount) + quick_add
        st.rerun()

    if submitted:
        final_amount = int(tx_amount)
        if final_amount <= 0:
            st.error("金額は1円以上を入力してください。")
        else:
            add_transaction(
                date_str=tx_date.strftime("%Y-%m-%d"),
                type_=tx_type,
                category=tx_category,
                amount=final_amount,
                memo=tx_memo.strip(),
            )
            st.session_state.input_amount = 0
            st.success(f"登録しました: {tx_type} {tx_category} ¥{final_amount:,}")
            st.rerun()

    # Show last 5 entries
    st.divider()
    st.subheader("直近5件")
    recent_df = get_transactions(year=year, month=month)
    if recent_df.empty:
        # Also try all time if month is empty
        recent_df = get_transactions()
    if recent_df.empty:
        st.info("まだデータがありません。")
    else:
        display_recent = recent_df.head(5)[["date", "type", "category", "amount", "memo"]].copy()
        display_recent.columns = ["日付", "種別", "カテゴリ", "金額", "メモ"]
        display_recent["金額"] = display_recent["金額"].apply(lambda x: f"¥{x:,}")
        st.dataframe(display_recent, use_container_width=True, hide_index=True)


# ---------------------------------------------------------------------------
# Tab: 一覧
# ---------------------------------------------------------------------------

def render_list_tab(year: int, month: int):
    st.subheader("📋 取引一覧")

    col1, col2 = st.columns([1, 2])
    with col1:
        type_filter = st.selectbox("種別フィルター", ["すべて", "支出", "収入"], key="list_type_filter")
    with col2:
        all_cats = get_categories()
        cat_filter = st.multiselect(
            "カテゴリフィルター",
            options=all_cats,
            default=[],
            placeholder="すべてのカテゴリ",
            key="list_cat_filter",
        )

    df = get_transactions(
        year=year,
        month=month,
        type_filter=type_filter if type_filter != "すべて" else None,
        categories=cat_filter if cat_filter else None,
    )

    if df.empty:
        st.info(f"{year}年{month}月のデータがありません。")
        return

    # Display
    display_df = df[["id", "date", "type", "category", "amount", "memo"]].copy()
    display_df.columns = ["ID", "日付", "種別", "カテゴリ", "金額", "メモ"]

    # Style helper: color type column
    def color_type(val):
        if val == "収入":
            return "color: #2e7d32; font-weight: bold"
        elif val == "支出":
            return "color: #c62828; font-weight: bold"
        return ""

    styled = display_df.drop(columns=["ID"]).style.applymap(color_type, subset=["種別"])
    styled = styled.format({"金額": lambda x: f"¥{int(x):,}"})
    st.dataframe(styled, use_container_width=True, hide_index=True)

    # Totals
    income_total = int(df[df["type"] == "収入"]["amount"].sum())
    expense_total = int(df[df["type"] == "支出"]["amount"].sum())
    t1, t2, t3 = st.columns(3)
    t1.metric("収入合計", f"¥{income_total:,}")
    t2.metric("支出合計", f"¥{expense_total:,}")
    t3.metric("収支", f"¥{income_total - expense_total:,}")

    # Delete section
    st.divider()
    st.subheader("削除")
    with st.expander("取引を削除する（クリックして展開）"):
        delete_id = st.number_input(
            "削除するID",
            min_value=1,
            step=1,
            value=int(df["id"].iloc[0]) if not df.empty else 1,
        )
        confirm = st.checkbox(f"ID {delete_id} を削除することを確認します")
        if st.button("削除する", type="primary", disabled=not confirm):
            delete_transaction(int(delete_id))
            st.success(f"ID {delete_id} を削除しました。")
            st.rerun()


# ---------------------------------------------------------------------------
# Tab: グラフ
# ---------------------------------------------------------------------------

def render_chart_tab(year: int, month: int):
    st.subheader("📊 グラフ")

    df = get_transactions(year=year, month=month)
    if df.empty:
        st.info(f"{year}年{month}月のデータがありません。")
        return

    st.markdown(f"#### {year}年{month}月")

    tab_pie, tab_bar, tab_line = st.tabs(["支出内訳", "日別収支", "累計収支"])
    with tab_pie:
        chart_pie_expense_by_category(df)
    with tab_bar:
        chart_bar_daily(df)
    with tab_line:
        chart_line_cumulative_balance(df)


# ---------------------------------------------------------------------------
# Tab: エクスポート
# ---------------------------------------------------------------------------

def render_export_tab(year: int, month: int):
    st.subheader("💾 データエクスポート")

    today = date.today()
    period_choice = st.selectbox(
        "期間選択",
        ["今月", "先月", "今年", "カスタム範囲"],
        key="export_period",
    )

    date_from = date_to = None

    if period_choice == "今月":
        date_from = date(today.year, today.month, 1).strftime("%Y-%m-%d")
        # Last day of this month
        if today.month == 12:
            date_to = date(today.year + 1, 1, 1) - timedelta(days=1)
        else:
            date_to = date(today.year, today.month + 1, 1) - timedelta(days=1)
        date_to = date_to.strftime("%Y-%m-%d")
        label = f"{today.year}年{today.month}月"

    elif period_choice == "先月":
        first_this = date(today.year, today.month, 1)
        last_last = first_this - timedelta(days=1)
        date_from = date(last_last.year, last_last.month, 1).strftime("%Y-%m-%d")
        date_to = last_last.strftime("%Y-%m-%d")
        label = f"{last_last.year}年{last_last.month}月"

    elif period_choice == "今年":
        date_from = date(today.year, 1, 1).strftime("%Y-%m-%d")
        date_to = date(today.year, 12, 31).strftime("%Y-%m-%d")
        label = f"{today.year}年"

    else:  # カスタム範囲
        c1, c2 = st.columns(2)
        with c1:
            custom_from = st.date_input("開始日", value=date(today.year, today.month, 1))
        with c2:
            custom_to = st.date_input("終了日", value=today)
        date_from = custom_from.strftime("%Y-%m-%d")
        date_to = custom_to.strftime("%Y-%m-%d")
        label = f"{date_from} ～ {date_to}"

    df = get_transactions(date_from=date_from, date_to=date_to)

    st.markdown(f"**期間: {label}**　|　**{len(df)}件**")

    if df.empty:
        st.info("該当期間のデータがありません。")
        return

    # Preview
    preview_df = df[["date", "type", "category", "amount", "memo"]].copy()
    preview_df.columns = ["日付", "種別", "カテゴリ", "金額", "メモ"]
    preview_df["金額"] = preview_df["金額"].apply(lambda x: f"¥{int(x):,}")
    st.dataframe(preview_df, use_container_width=True, hide_index=True)

    st.divider()
    c1, c2 = st.columns(2)
    with c1:
        csv_bytes = df_to_csv_bytes(df)
        filename_base = label.replace("年", "").replace("月", "").replace(" ", "").replace("～", "-")
        st.download_button(
            label="CSVダウンロード",
            data=csv_bytes,
            file_name=f"家計簿_{filename_base}.csv",
            mime="text/csv",
            use_container_width=True,
        )
    with c2:
        excel_bytes = df_to_excel_bytes(df)
        st.download_button(
            label="Excelダウンロード",
            data=excel_bytes,
            file_name=f"家計簿_{filename_base}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )


# ---------------------------------------------------------------------------
# Tab: 設定
# ---------------------------------------------------------------------------

def render_settings_tab():
    st.subheader("⚙️ 設定")

    # ── PIN lock ──
    with st.expander("🔒 PINロック設定", expanded=True):
        pin_exists = get_pin_hash() is not None
        if pin_exists:
            st.success("PINロックが設定されています。")
            with st.form("change_pin_form"):
                new_pin = st.text_input("新しいPIN（4桁）", type="password", max_chars=4)
                new_pin2 = st.text_input("新しいPIN（確認）", type="password", max_chars=4)
                col1, col2 = st.columns(2)
                with col1:
                    change_submitted = st.form_submit_button("PINを変更", use_container_width=True)
                with col2:
                    remove_submitted = st.form_submit_button(
                        "PINを削除", type="primary", use_container_width=True
                    )

            if change_submitted:
                if new_pin and new_pin == new_pin2 and new_pin.isdigit() and len(new_pin) == 4:
                    set_pin(new_pin)
                    st.success("PINを変更しました。")
                else:
                    st.error("4桁の数字で一致するPINを入力してください。")
            if remove_submitted:
                remove_pin()
                st.success("PINを削除しました。")
                st.rerun()
        else:
            st.info("PINロックは設定されていません。")
            with st.form("set_pin_form"):
                new_pin = st.text_input("新しいPIN（4桁）", type="password", max_chars=4)
                new_pin2 = st.text_input("新しいPIN（確認）", type="password", max_chars=4)
                set_submitted = st.form_submit_button("PINを設定", type="primary", use_container_width=True)
            if set_submitted:
                if new_pin and new_pin == new_pin2 and new_pin.isdigit() and len(new_pin) == 4:
                    set_pin(new_pin)
                    st.success("PINを設定しました。次回起動時から有効になります。")
                else:
                    st.error("4桁の数字で一致するPINを入力してください。")

    # ── Category management ──
    with st.expander("🏷️ カテゴリ管理"):
        st.markdown("**カテゴリ一覧**")
        cat_df = get_all_categories_with_type()
        if not cat_df.empty:
            col1, col2 = st.columns(2)
            with col1:
                st.markdown("**支出カテゴリ**")
                expense_cats = cat_df[cat_df["type"] == "支出"]["name"].tolist()
                for cat in expense_cats:
                    st.text(f"  • {cat}")
            with col2:
                st.markdown("**収入カテゴリ**")
                income_cats = cat_df[cat_df["type"] == "収入"]["name"].tolist()
                for cat in income_cats:
                    st.text(f"  • {cat}")

        st.divider()
        st.markdown("**新しいカテゴリを追加**")
        with st.form("add_cat_form"):
            new_cat_name = st.text_input("カテゴリ名", max_chars=20)
            new_cat_type = st.radio("種別", ["支出", "収入"], horizontal=True)
            add_cat_submitted = st.form_submit_button("追加", use_container_width=True)
        if add_cat_submitted:
            if new_cat_name.strip():
                if add_category(new_cat_name.strip(), new_cat_type):
                    st.success(f"カテゴリ「{new_cat_name}」を追加しました。")
                    st.rerun()
                else:
                    st.error("同名のカテゴリがすでに存在します。")
            else:
                st.error("カテゴリ名を入力してください。")

        st.divider()
        st.markdown("**カテゴリを削除**")
        all_cats = get_categories()
        if all_cats:
            with st.form("del_cat_form"):
                del_cat = st.selectbox("削除するカテゴリ", options=all_cats)
                del_cat_submitted = st.form_submit_button(
                    "削除", type="primary", use_container_width=True
                )
            if del_cat_submitted:
                if delete_category(del_cat):
                    st.success(f"カテゴリ「{del_cat}」を削除しました。")
                    st.rerun()
                else:
                    st.error(
                        f"カテゴリ「{del_cat}」は使用中のため削除できません。"
                        " このカテゴリの取引を先に削除または変更してください。"
                    )

    # ── Data info ──
    with st.expander("📂 データ情報"):
        st.markdown(f"**DBファイル:** `{DB_PATH}`")
        st.markdown(f"**設定ファイル:** `{CONFIG_PATH}`")
        total = get_total_record_count()
        st.markdown(f"**総取引件数:** {total:,} 件")

    # ── Danger zone ──
    with st.expander("⚠️ データ削除（危険）"):
        st.markdown(
            '<div class="danger-zone">',
            unsafe_allow_html=True,
        )
        st.warning("この操作は元に戻せません。")
        today = date.today()
        del_year = st.selectbox(
            "削除対象の年",
            options=list(range(today.year - 5, today.year + 1)),
            index=5,
            key="danger_year",
        )
        del_month = st.selectbox(
            "削除対象の月",
            options=list(range(1, 13)),
            index=today.month - 1,
            key="danger_month",
        )
        confirm_delete = st.checkbox(
            f"{del_year}年{del_month}月のデータをすべて削除することを確認します"
        )
        if st.button(
            f"{del_year}年{del_month}月のデータを削除",
            type="primary",
            disabled=not confirm_delete,
        ):
            delete_month_data(del_year, del_month)
            st.success(f"{del_year}年{del_month}月のデータを削除しました。")
            st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    st.set_page_config(
        page_title="家計簿",
        page_icon="📒",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    inject_css()
    init_db()

    # Session state defaults
    if "authenticated" not in st.session_state:
        st.session_state.authenticated = False

    # PIN lock gate
    pin_hash = get_pin_hash()
    if pin_hash is not None and not st.session_state.authenticated:
        render_pin_screen()
        return

    # Mark as authenticated if no PIN set
    if not st.session_state.authenticated:
        st.session_state.authenticated = True

    # Sidebar: year/month selector + summary
    year, month = render_sidebar()

    # Main tabs
    tab_input, tab_list, tab_chart, tab_export, tab_settings = st.tabs(
        ["✏️ 入力", "📋 一覧", "📊 グラフ", "💾 エクスポート", "⚙️ 設定"]
    )

    with tab_input:
        render_input_tab(year, month)

    with tab_list:
        render_list_tab(year, month)

    with tab_chart:
        render_chart_tab(year, month)

    with tab_export:
        render_export_tab(year, month)

    with tab_settings:
        render_settings_tab()


if __name__ == "__main__":
    main()
