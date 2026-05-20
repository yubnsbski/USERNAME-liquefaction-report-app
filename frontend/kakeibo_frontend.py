"""
家計簿フロントエンド — FastAPI バックエンドを KakeiboClient 経由で操作する Streamlit アプリ
Run backend first:  uvicorn backend.main:app --reload --port 8000
Run frontend:       streamlit run frontend/kakeibo_frontend.py
"""

import base64
import os
from datetime import date, timedelta

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from frontend.api_client import KakeiboClient

# ── Page config ──────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="家計簿",
    page_icon="💴",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ── Client ────────────────────────────────────────────────────────────────────

@st.cache_resource
def get_client() -> KakeiboClient:
    return KakeiboClient()

api = get_client()

# ── Helper: check backend availability ───────────────────────────────────────

@st.cache_data(ttl=5)
def backend_ok() -> bool:
    try:
        api.health()
        return True
    except Exception:
        return False

if not backend_ok():
    st.error("バックエンドに接続できません。`uvicorn backend.main:app --reload --port 8000` を起動してください。")
    st.stop()

# ── Session defaults ─────────────────────────────────────────────────────────

today = date.today()
if "sel_year" not in st.session_state:
    st.session_state.sel_year = today.year
if "sel_month" not in st.session_state:
    st.session_state.sel_month = today.month

# ── Tabs ─────────────────────────────────────────────────────────────────────

tabs = st.tabs(["🏠 ダッシュボード", "✏️ 入力", "📷 OCR読取", "🔍 検索",
                "📋 一覧", "📊 グラフ", "🤖 ML分析", "⚙️ 設定"])

# ════════════════════════════════════════════════════════════════════════════
# TAB 0 — ダッシュボード
# ════════════════════════════════════════════════════════════════════════════

with tabs[0]:
    st.subheader("ダッシュボード")

    c1, c2 = st.columns([1, 5])
    with c1:
        year = st.selectbox("年", list(range(today.year - 3, today.year + 2)),
                            index=today.year - (today.year - 3), key="dash_year")
        month = st.selectbox("月", list(range(1, 13)), index=today.month - 1, key="dash_month")

    try:
        data = api.get_dashboard(year, month)
    except Exception as e:
        st.error(f"取得失敗: {e}")
        st.stop()

    summary = data.get("summary", {})
    inc = summary.get("income", 0)
    exp = summary.get("expense", 0)
    bal = summary.get("balance", 0)

    m1, m2, m3 = st.columns(3)
    m1.metric("収入", f"¥{inc:,}")
    m2.metric("支出", f"¥{exp:,}", delta=f"-¥{exp:,}", delta_color="inverse")
    m3.metric("収支", f"¥{bal:,}", delta_color="normal" if bal >= 0 else "inverse")

    col_l, col_r = st.columns(2)

    # Budget status bar chart
    with col_l:
        st.markdown("#### 予算使用状況")
        budget_items = data.get("budget_status", [])
        if budget_items:
            bdf = pd.DataFrame(budget_items)
            colors = ["#e74c3c" if v else "#2ecc71" for v in bdf["over_budget"]]
            fig = go.Figure(go.Bar(
                x=bdf["category"], y=bdf["usage_pct"],
                marker_color=colors, text=bdf["usage_pct"].apply(lambda v: f"{v:.0f}%"),
                textposition="outside",
            ))
            fig.add_hline(y=100, line_dash="dot", line_color="gray")
            fig.update_layout(yaxis_title="使用率(%)", margin=dict(t=10, b=10), height=260)
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("予算が設定されていません")

    # Account balances
    with col_r:
        st.markdown("#### 口座残高")
        acc_items = data.get("account_balances", [])
        if acc_items:
            adf = pd.DataFrame(acc_items)
            fig2 = px.bar(adf, x="name", y="balance", text="balance",
                          color_discrete_sequence=["#3498db"])
            fig2.update_traces(texttemplate="¥%{text:,}", textposition="outside")
            fig2.update_layout(xaxis_title="", yaxis_title="残高(円)", margin=dict(t=10, b=10), height=260)
            st.plotly_chart(fig2, use_container_width=True)
        else:
            st.info("口座情報がありません")

    # Recent transactions
    st.markdown("#### 最近の取引")
    recent = data.get("top_expenses", [])
    if recent:
        rdf = pd.DataFrame(recent)[["date", "type", "category", "amount", "memo", "account"]]
        rdf.columns = ["日付", "種類", "カテゴリ", "金額", "メモ", "口座"]
        st.dataframe(rdf, use_container_width=True, hide_index=True)
    else:
        st.info("データなし")

    # Monthly trend
    st.markdown("#### 月次推移(6ヶ月)")
    trend = data.get("monthly_trend", [])
    if trend:
        tdf = pd.DataFrame(trend)
        fig3 = go.Figure()
        fig3.add_trace(go.Bar(name="収入", x=tdf["month"], y=tdf["income"],
                               marker_color="#2ecc71"))
        fig3.add_trace(go.Bar(name="支出", x=tdf["month"], y=tdf["expense"],
                               marker_color="#e74c3c"))
        fig3.update_layout(barmode="group", margin=dict(t=10, b=10), height=240)
        st.plotly_chart(fig3, use_container_width=True)


# ════════════════════════════════════════════════════════════════════════════
# TAB 1 — 入力
# ════════════════════════════════════════════════════════════════════════════

with tabs[1]:
    st.subheader("取引入力")

    try:
        categories = api.get_categories()
        accounts = api.get_account_names()
    except Exception as e:
        st.error(f"カテゴリ/口座の取得失敗: {e}")
        categories, accounts = [], ["現金"]

    with st.form("add_tx", clear_on_submit=True):
        r1c1, r1c2, r1c3 = st.columns(3)
        tx_date = r1c1.date_input("日付", value=today)
        tx_type = r1c2.selectbox("種類", ["支出", "収入"])
        tx_account = r1c3.selectbox("口座", accounts if accounts else ["現金"])

        filtered_cats = [c["name"] for c in categories if c["type"] == tx_type]
        r2c1, r2c2 = st.columns(2)
        tx_cat = r2c1.selectbox("カテゴリ", filtered_cats if filtered_cats else ["その他"])
        tx_amount = r2c2.number_input("金額 (円)", min_value=1, step=100, value=1000)

        tx_memo = st.text_input("メモ")
        submitted = st.form_submit_button("追加", type="primary", use_container_width=True)

    if submitted:
        try:
            api.add_transaction(
                date=str(tx_date), type=tx_type, category=tx_cat,
                amount=int(tx_amount), memo=tx_memo, account=tx_account,
            )
            st.success("追加しました ✓")
            st.cache_data.clear()
        except Exception as e:
            st.error(f"追加失敗: {e}")

    # Recent 5
    st.markdown("---")
    st.markdown("#### 直近5件")
    try:
        recent5 = api.get_recent_transactions(5)
        if recent5:
            rdf5 = pd.DataFrame(recent5)
            for _, row in rdf5.iterrows():
                cols = st.columns([2, 1, 2, 2, 3, 1])
                cols[0].write(row["date"])
                cols[1].write(row["type"])
                cols[2].write(row["category"])
                cols[3].write(f"¥{row['amount']:,}")
                cols[4].write(row.get("memo", ""))
                if cols[5].button("削除", key=f"del_{row['id']}"):
                    try:
                        api.delete_transaction(row["id"])
                        st.success("削除しました")
                        st.cache_data.clear()
                        st.rerun()
                    except Exception as e:
                        st.error(f"削除失敗: {e}")
    except Exception as e:
        st.error(f"取得失敗: {e}")


# ════════════════════════════════════════════════════════════════════════════
# TAB 2 — OCR読取
# ════════════════════════════════════════════════════════════════════════════

with tabs[2]:
    st.subheader("レシートOCR読取")
    st.info("画像をアップロードするとAIがレシートを解析し、取引候補を提示します。")

    uploaded = st.file_uploader("レシート画像", type=["png", "jpg", "jpeg", "webp"])
    dummy_mode = st.checkbox("ダミーモード（APIキー不要）")

    if uploaded:
        st.image(uploaded, caption="アップロード画像", width=300)
        if st.button("OCR実行", type="primary"):
            with st.spinner("AIが解析中..."):
                try:
                    if dummy_mode:
                        ocr_result = {
                            "store": "サンプルストア",
                            "date": str(today),
                            "total": 1580,
                            "items": [
                                {"name": "牛乳", "amount": 198},
                                {"name": "パン", "amount": 150},
                                {"name": "卵", "amount": 298},
                                {"name": "野菜セット", "amount": 534},
                                {"name": "お菓子", "amount": 400},
                            ],
                        }
                    else:
                        ocr_result = api.ocr_receipt(uploaded.read(), uploaded.name)
                    st.session_state["ocr_result"] = ocr_result
                    st.success("解析完了")
                except Exception as e:
                    st.error(f"OCR失敗: {e}")

    if "ocr_result" in st.session_state:
        res = st.session_state["ocr_result"]
        st.markdown(f"**店舗**: {res.get('store','不明')} / **日付**: {res.get('date', str(today))} / **合計**: ¥{res.get('total', 0):,}")

        items = res.get("items", [])
        if items:
            idf = pd.DataFrame(items)
            st.dataframe(idf, use_container_width=True, hide_index=True)

        st.markdown("#### 取引として登録")
        try:
            categories = api.get_categories()
            accounts = api.get_account_names()
        except Exception:
            categories, accounts = [], ["現金"]

        expense_cats = [c["name"] for c in categories if c["type"] == "支出"]
        with st.form("ocr_confirm"):
            oc1, oc2, oc3 = st.columns(3)
            ocr_date = oc1.date_input("日付", value=date.fromisoformat(res.get("date", str(today))))
            ocr_cat = oc2.selectbox("カテゴリ", expense_cats if expense_cats else ["食費"])
            ocr_account = oc3.selectbox("口座", accounts if accounts else ["現金"])
            ocr_amount = st.number_input("金額", value=res.get("total", 0), min_value=1)
            ocr_memo = st.text_input("メモ", value=res.get("store", ""))
            if st.form_submit_button("登録", type="primary"):
                try:
                    api.add_transaction(
                        date=str(ocr_date), type="支出", category=ocr_cat,
                        amount=int(ocr_amount), memo=ocr_memo, account=ocr_account,
                    )
                    del st.session_state["ocr_result"]
                    st.success("登録しました ✓")
                    st.cache_data.clear()
                except Exception as e:
                    st.error(f"登録失敗: {e}")


# ════════════════════════════════════════════════════════════════════════════
# TAB 3 — 検索
# ════════════════════════════════════════════════════════════════════════════

with tabs[3]:
    st.subheader("検索・絞り込み")

    with st.expander("フィルター", expanded=True):
        fc1, fc2, fc3 = st.columns(3)
        f_year = fc1.selectbox("年", [None] + list(range(today.year - 3, today.year + 2)),
                               format_func=lambda x: "全て" if x is None else str(x), key="f_year")
        f_month = fc2.selectbox("月", [None] + list(range(1, 13)),
                                format_func=lambda x: "全て" if x is None else f"{x}月", key="f_month")
        f_type = fc3.selectbox("種類", [None, "支出", "収入"],
                               format_func=lambda x: "全て" if x is None else x, key="f_type")

        fc4, fc5 = st.columns(2)
        f_keyword = fc4.text_input("キーワード（メモ・カテゴリ）")
        try:
            all_cats = api.get_categories(type=f_type)
            cat_names = [c["name"] for c in all_cats]
        except Exception:
            cat_names = []
        f_cats = fc5.multiselect("カテゴリ", cat_names)

        fa1, fa2 = st.columns(2)
        f_amin = fa1.number_input("金額(下限)", min_value=0, value=0, step=100)
        f_amax = fa2.number_input("金額(上限)", min_value=0, value=0, step=100,
                                  help="0の場合は上限なし")

    if st.button("検索", type="primary"):
        try:
            results = api.get_transactions(
                year=f_year, month=f_month, type=f_type,
                keyword=f_keyword if f_keyword else None,
                categories=f_cats if f_cats else None,
                amount_min=f_amin if f_amin > 0 else None,
                amount_max=f_amax if f_amax > 0 else None,
            )
            if results:
                sdf = pd.DataFrame(results)[["date", "type", "category", "amount", "memo", "account"]]
                sdf.columns = ["日付", "種類", "カテゴリ", "金額", "メモ", "口座"]
                st.success(f"{len(sdf)} 件")
                st.dataframe(sdf, use_container_width=True, hide_index=True)
                total = sdf["金額"].sum()
                st.metric("合計金額", f"¥{total:,}")
            else:
                st.info("該当なし")
        except Exception as e:
            st.error(f"検索失敗: {e}")


# ════════════════════════════════════════════════════════════════════════════
# TAB 4 — 一覧
# ════════════════════════════════════════════════════════════════════════════

with tabs[4]:
    st.subheader("取引一覧")

    lc1, lc2 = st.columns(2)
    l_year = lc1.selectbox("年", list(range(today.year - 3, today.year + 2)),
                            index=today.year - (today.year - 3), key="list_year")
    l_month = lc2.selectbox("月", list(range(1, 13)), index=today.month - 1, key="list_month")

    try:
        txs = api.get_transactions(year=l_year, month=l_month)
        if txs:
            ldf = pd.DataFrame(txs)
            display = ldf[["date", "type", "category", "amount", "memo", "account"]].copy()
            display.columns = ["日付", "種類", "カテゴリ", "金額", "メモ", "口座"]
            st.dataframe(display, use_container_width=True, hide_index=True)

            summary = api.get_monthly_summary(l_year, l_month)
            mc1, mc2, mc3 = st.columns(3)
            mc1.metric("収入合計", f"¥{summary['income']:,}")
            mc2.metric("支出合計", f"¥{summary['expense']:,}")
            mc3.metric("収支", f"¥{summary['balance']:,}")
        else:
            st.info("この月のデータはありません")
    except Exception as e:
        st.error(f"取得失敗: {e}")


# ════════════════════════════════════════════════════════════════════════════
# TAB 5 — グラフ
# ════════════════════════════════════════════════════════════════════════════

with tabs[5]:
    st.subheader("グラフ")

    gc1, gc2 = st.columns(2)
    g_year = gc1.selectbox("年", list(range(today.year - 3, today.year + 2)),
                            index=today.year - (today.year - 3), key="graph_year")
    g_month = gc2.selectbox("月", list(range(1, 13)), index=today.month - 1, key="graph_month")

    try:
        g_txs = api.get_transactions(year=g_year, month=g_month)
    except Exception as e:
        st.error(f"取得失敗: {e}")
        g_txs = []

    if g_txs:
        gdf = pd.DataFrame(g_txs)

        exp_df = gdf[gdf["type"] == "支出"]
        inc_df = gdf[gdf["type"] == "収入"]

        col_pie1, col_pie2 = st.columns(2)
        with col_pie1:
            if not exp_df.empty:
                fig_ep = px.pie(exp_df.groupby("category")["amount"].sum().reset_index(),
                                names="category", values="amount", title="支出カテゴリ構成")
                st.plotly_chart(fig_ep, use_container_width=True)
        with col_pie2:
            if not inc_df.empty:
                fig_ip = px.pie(inc_df.groupby("category")["amount"].sum().reset_index(),
                                names="category", values="amount", title="収入カテゴリ構成")
                st.plotly_chart(fig_ip, use_container_width=True)

        # Daily spending scatter
        if not exp_df.empty:
            st.markdown("#### 日別支出")
            daily = exp_df.groupby("date")["amount"].sum().reset_index()
            fig_s = px.scatter(daily, x="date", y="amount", size="amount",
                               color="amount", color_continuous_scale="Reds")
            fig_s.update_layout(margin=dict(t=10, b=10))
            st.plotly_chart(fig_s, use_container_width=True)
    else:
        st.info("この月のデータはありません")


# ════════════════════════════════════════════════════════════════════════════
# TAB 6 — ML分析
# ════════════════════════════════════════════════════════════════════════════

with tabs[6]:
    st.subheader("ML分析")

    ml_col1, ml_col2 = st.columns(2)

    with ml_col1:
        st.markdown("#### カテゴリ予測")
        memo_input = st.text_input("メモを入力してカテゴリ予測", placeholder="例: コンビニ, 電車, 病院...")
        if memo_input:
            try:
                preds = api.ml_predict(memo_input)
                if preds:
                    for p in preds[:3]:
                        conf = p.get("confidence", 0)
                        st.progress(conf, text=f"{p['category']} ({conf*100:.1f}%)")
                else:
                    st.info("学習データ不足のため予測できません")
            except Exception as e:
                st.error(f"予測失敗: {e}")

        if st.button("モデル再学習"):
            try:
                result = api.ml_retrain()
                st.success(f"再学習完了 — 精度: {result.get('accuracy', 0)*100:.1f}%")
            except Exception as e:
                st.error(f"学習失敗: {e}")

    with ml_col2:
        st.markdown("#### 支出予測(3ヶ月先)")
        try:
            forecast = api.ml_forecast()
            if forecast:
                fdf = pd.DataFrame(forecast)
                fig_f = px.line(fdf, x="month", y="predicted_expense",
                                markers=True, title="予測支出")
                fig_f.update_layout(margin=dict(t=30, b=10))
                st.plotly_chart(fig_f, use_container_width=True)
            else:
                st.info("予測に必要なデータが不足しています")
        except Exception as e:
            st.error(f"予測取得失敗: {e}")

    st.markdown("#### 異常検知")
    an_col1, an_col2 = st.columns(2)
    an_year = an_col1.selectbox("年", list(range(today.year - 3, today.year + 2)),
                                 index=today.year - (today.year - 3), key="an_year")
    an_month = an_col2.selectbox("月", list(range(1, 13)), index=today.month - 1, key="an_month")
    try:
        anomalies = api.ml_anomalies(an_year, an_month)
        if anomalies:
            andf = pd.DataFrame(anomalies)
            st.dataframe(andf[["date", "category", "amount", "z_score"]], use_container_width=True, hide_index=True)
        else:
            st.success("異常な取引はありません")
    except Exception as e:
        st.error(f"異常検知失敗: {e}")


# ════════════════════════════════════════════════════════════════════════════
# TAB 7 — 設定
# ════════════════════════════════════════════════════════════════════════════

with tabs[7]:
    st.subheader("設定")

    set_col1, set_col2 = st.columns(2)

    # ── Categories
    with set_col1:
        st.markdown("#### カテゴリ管理")
        try:
            all_cats = api.get_categories()
        except Exception:
            all_cats = []

        cat_df = pd.DataFrame(all_cats) if all_cats else pd.DataFrame(columns=["name", "type"])
        st.dataframe(cat_df.rename(columns={"name": "名称", "type": "種類"}),
                     use_container_width=True, hide_index=True)

        with st.form("add_cat"):
            cc1, cc2 = st.columns(2)
            new_cat_name = cc1.text_input("カテゴリ名")
            new_cat_type = cc2.selectbox("種類", ["支出", "収入"])
            if st.form_submit_button("追加"):
                try:
                    api.add_category(new_cat_name, new_cat_type)
                    st.success("追加しました")
                    st.rerun()
                except Exception as e:
                    st.error(f"追加失敗: {e}")

        del_cat = st.selectbox("削除するカテゴリ",
                               [""] + [c["name"] for c in all_cats],
                               key="del_cat_sel")
        if del_cat and st.button("カテゴリ削除", key="del_cat_btn"):
            try:
                api.delete_category(del_cat)
                st.success("削除しました")
                st.rerun()
            except Exception as e:
                st.error(f"削除失敗: {e}")

    # ── Accounts
    with set_col2:
        st.markdown("#### 口座管理")
        try:
            accounts = api.get_accounts()
        except Exception:
            accounts = []

        acc_df = pd.DataFrame(accounts) if accounts else pd.DataFrame(
            columns=["name", "type", "balance"])
        display_cols = [c for c in ["name", "type", "balance", "note"] if c in acc_df.columns]
        st.dataframe(acc_df[display_cols].rename(
            columns={"name": "口座名", "type": "種類", "balance": "残高", "note": "備考"}),
            use_container_width=True, hide_index=True)

        with st.form("add_acc"):
            ac1, ac2 = st.columns(2)
            acc_name = ac1.text_input("口座名")
            acc_type = ac2.selectbox("種類", ["現金", "銀行", "クレジット", "電子マネー"])
            ac3, ac4 = st.columns(2)
            acc_init = ac3.number_input("初期残高", min_value=0, value=0, step=1000)
            acc_note = ac4.text_input("備考")
            if st.form_submit_button("追加"):
                try:
                    api.add_account(acc_name, acc_type, acc_init, acc_note)
                    st.success("追加しました")
                    st.rerun()
                except Exception as e:
                    st.error(f"追加失敗: {e}")

    # ── Budgets
    st.markdown("---")
    st.markdown("#### 予算管理")
    try:
        all_cats = api.get_categories(type="支出")
        expense_cat_names = [c["name"] for c in all_cats]
    except Exception:
        expense_cat_names = []

    bc1, bc2 = st.columns(2)
    b_year = bc1.selectbox("年", list(range(today.year - 1, today.year + 2)),
                            index=1, key="budget_year")
    b_month = bc2.selectbox("月", list(range(1, 13)), index=today.month - 1, key="budget_month")

    try:
        current_budgets = api.get_budgets(b_year, b_month)
    except Exception:
        current_budgets = {}

    with st.form("set_budget"):
        bfc1, bfc2 = st.columns(2)
        bcat = bfc1.selectbox("カテゴリ", expense_cat_names if expense_cat_names else ["食費"])
        bamt = bfc2.number_input("予算(円)", min_value=100, value=30000, step=1000)
        if st.form_submit_button("予算を設定"):
            try:
                api.set_budget(b_year, b_month, bcat, int(bamt))
                st.success("設定しました")
                st.rerun()
            except Exception as e:
                st.error(f"設定失敗: {e}")

    if current_budgets:
        bdf = pd.DataFrame({"カテゴリ": list(current_budgets.keys()),
                            "予算(円)": list(current_budgets.values())})
        st.dataframe(bdf, use_container_width=True, hide_index=True)

    # ── Recurring
    st.markdown("---")
    st.markdown("#### 定期取引")
    try:
        rec_list = api.get_recurring(active_only=False)
    except Exception:
        rec_list = []

    if rec_list:
        rdf = pd.DataFrame(rec_list)
        st.dataframe(rdf[["id", "type", "category", "amount", "memo", "day_of_month", "active"]].rename(
            columns={"id": "ID", "type": "種類", "category": "カテゴリ",
                     "amount": "金額", "memo": "メモ", "day_of_month": "日付",
                     "active": "有効"}),
            use_container_width=True, hide_index=True)

    with st.form("add_rec"):
        rfc1, rfc2, rfc3 = st.columns(3)
        rec_type = rfc1.selectbox("種類", ["支出", "収入"])
        try:
            rcats = api.get_categories(type=rec_type)
            rcat_names = [c["name"] for c in rcats]
        except Exception:
            rcat_names = ["食費"]
        rec_cat = rfc2.selectbox("カテゴリ", rcat_names if rcat_names else ["食費"])
        rec_day = rfc3.number_input("毎月何日", min_value=1, max_value=28, value=1)
        rfc4, rfc5 = st.columns(2)
        rec_amount = rfc4.number_input("金額", min_value=1, value=10000, step=1000)
        rec_memo = rfc5.text_input("メモ")
        if st.form_submit_button("定期取引を追加"):
            try:
                api.add_recurring(rec_type, rec_cat, int(rec_amount), rec_memo, rec_day)
                st.success("追加しました")
                st.rerun()
            except Exception as e:
                st.error(f"追加失敗: {e}")

    rc1, rc2 = st.columns(2)
    with rc1:
        apply_year = st.selectbox("適用年", list(range(today.year - 1, today.year + 2)),
                                   index=1, key="rec_apply_year")
        apply_month = st.selectbox("適用月", list(range(1, 13)), index=today.month - 1,
                                    key="rec_apply_month")
        if st.button("この月に定期取引を適用"):
            try:
                result = api.apply_recurring(apply_year, apply_month)
                st.success(f"{result.get('applied', 0)} 件を適用しました")
                st.cache_data.clear()
            except Exception as e:
                st.error(f"適用失敗: {e}")
