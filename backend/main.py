"""
家計簿 FastAPI バックエンド
Run: uvicorn backend.main:app --reload --port 8000
"""

import base64
import json
import os
from datetime import date
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware

from backend import database as db
from backend.models import (
    TransactionCreate, CategoryCreate, BudgetSet, RecurringCreate,
    RecurringToggle, AccountCreate,
)

app = FastAPI(title="家計簿 API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

DB_PATH = db.DB_PATH


@app.on_event("startup")
def startup():
    db.init_db(DB_PATH)


# ── Health ───────────────────────────────────

@app.get("/api/health")
def health():
    return {"status": "ok", "records": db.get_total_record_count(DB_PATH)}


# ── Transactions ─────────────────────────────

@app.get("/api/transactions")
def list_transactions(
    year: Optional[int] = None, month: Optional[int] = None,
    type: Optional[str] = None, account: Optional[str] = None,
    keyword: Optional[str] = None,
    amount_min: Optional[int] = None, amount_max: Optional[int] = None,
    start_date: Optional[str] = None, end_date: Optional[str] = None,
    categories: Optional[str] = Query(None, description="comma-separated"),
):
    cats = categories.split(",") if categories else None
    df = db.get_transactions(
        year=year, month=month, tx_type=type, categories=cats,
        start_date=start_date, end_date=end_date, account=account,
        keyword=keyword, amount_min=amount_min, amount_max=amount_max,
        db_path=DB_PATH,
    )
    return df.to_dict(orient="records")


@app.post("/api/transactions", status_code=201)
def create_transaction(body: TransactionCreate):
    row_id = db.add_transaction(
        body.date, body.type, body.category, body.amount,
        body.memo, body.account, DB_PATH,
    )
    return {"id": row_id}


@app.delete("/api/transactions/{tx_id}", status_code=204)
def remove_transaction(tx_id: int):
    db.delete_transaction(tx_id, DB_PATH)


@app.get("/api/transactions/recent")
def recent_transactions(limit: int = 5):
    df = db.get_recent_transactions(limit, DB_PATH)
    return df.to_dict(orient="records")


@app.get("/api/transactions/summary/{year}/{month}")
def monthly_summary(year: int, month: int):
    return db.get_monthly_summary(year, month, DB_PATH)


# ── Categories ───────────────────────────────

@app.get("/api/categories")
def list_categories(type: Optional[str] = None):
    return db.get_categories(type, DB_PATH)


@app.post("/api/categories", status_code=201)
def create_category(body: CategoryCreate):
    if not db.add_category(body.name, body.type, DB_PATH):
        raise HTTPException(409, f"カテゴリ「{body.name}」は既に存在します")
    return {"name": body.name}


@app.delete("/api/categories/{name}", status_code=204)
def remove_category(name: str):
    if not db.delete_category(name, DB_PATH):
        raise HTTPException(409, f"カテゴリ「{name}」は使用中のため削除できません")


# ── Budgets ──────────────────────────────────

@app.get("/api/budgets/{year}/{month}")
def get_budgets(year: int, month: int):
    return db.get_budgets(year, month, DB_PATH)


@app.post("/api/budgets/{year}/{month}", status_code=201)
def set_budget(year: int, month: int, body: BudgetSet):
    db.set_budget(year, month, body.category, body.amount, DB_PATH)
    return {"year": year, "month": month, "category": body.category, "amount": body.amount}


@app.delete("/api/budgets/{year}/{month}/{category}", status_code=204)
def delete_budget(year: int, month: int, category: str):
    db.delete_budget(year, month, category, DB_PATH)


@app.get("/api/budgets/{year}/{month}/status")
def budget_status(year: int, month: int):
    df = db.get_budget_status(year, month, DB_PATH)
    if df.empty:
        return []
    return df.to_dict(orient="records")


# ── Recurring ────────────────────────────────

@app.get("/api/recurring")
def list_recurring(active_only: bool = True):
    df = db.get_recurring(active_only, DB_PATH)
    return df.to_dict(orient="records")


@app.post("/api/recurring", status_code=201)
def create_recurring(body: RecurringCreate):
    row_id = db.add_recurring(
        body.type, body.category, body.amount,
        body.memo, body.day_of_month, DB_PATH,
    )
    return {"id": row_id}


@app.patch("/api/recurring/{rec_id}/toggle", status_code=204)
def toggle_recurring(rec_id: int, body: RecurringToggle):
    db.toggle_recurring(rec_id, body.active, DB_PATH)


@app.delete("/api/recurring/{rec_id}", status_code=204)
def remove_recurring(rec_id: int):
    db.delete_recurring(rec_id, DB_PATH)


@app.post("/api/recurring/apply/{year}/{month}")
def apply_recurring(year: int, month: int):
    n = db.apply_recurring(year, month, DB_PATH)
    return {"registered": n}


# ── Accounts ─────────────────────────────────

@app.get("/api/accounts")
def list_accounts():
    accounts = db.get_accounts(DB_PATH)
    for acc in accounts:
        acc["balance"] = db.get_account_balance(acc["name"], DB_PATH)
    return accounts


@app.post("/api/accounts", status_code=201)
def create_account(body: AccountCreate):
    if not db.add_account(body.name, body.type, body.initial_balance, body.note, DB_PATH):
        raise HTTPException(409, f"口座「{body.name}」は既に存在します")
    return {"name": body.name}


@app.delete("/api/accounts/{name}", status_code=204)
def remove_account(name: str):
    if not db.delete_account(name, DB_PATH):
        raise HTTPException(409, f"口座「{name}」は使用中のため削除できません")


@app.get("/api/accounts/{name}/balance")
def account_balance(name: str):
    return {"name": name, "balance": db.get_account_balance(name, DB_PATH)}


# ── ML ───────────────────────────────────────

_ml_model_cache = {"model": None}


def _get_model():
    if _ml_model_cache["model"] is None:
        _ml_model_cache["model"] = _train_model()
    return _ml_model_cache["model"]


def _train_model():
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.linear_model import LogisticRegression
        from sklearn.pipeline import Pipeline
        df = db.get_transactions(db_path=DB_PATH)
        if df.empty or len(df) < 5 or df["category"].nunique() < 2:
            return None
        X = df["memo"].fillna("").astype(str)
        y = df["category"]
        pipeline = Pipeline([
            ("tfidf", TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4), min_df=1)),
            ("clf", LogisticRegression(max_iter=1000, C=1.0)),
        ])
        pipeline.fit(X, y)
        return pipeline
    except Exception:
        return None


@app.get("/api/ml/predict")
def ml_predict(memo: str = ""):
    if not memo.strip():
        return {"predictions": []}
    model = _get_model()
    if not model:
        return {"predictions": []}
    proba = model.predict_proba([memo])[0]
    top = sorted(zip(model.classes_, proba), key=lambda x: -x[1])[:3]
    return {"predictions": [{"category": c, "confidence": round(float(p), 4)} for c, p in top]}


@app.post("/api/ml/retrain")
def ml_retrain():
    _ml_model_cache["model"] = _train_model()
    return {"status": "ok", "trained": _ml_model_cache["model"] is not None}


@app.get("/api/ml/forecast")
def ml_forecast():
    try:
        from sklearn.linear_model import LinearRegression
        import numpy as np
        df = db.get_transactions(db_path=DB_PATH)
        exp = df[df["type"] == "支出"].copy()
        if exp.empty:
            return []
        exp["period"] = exp["date"].str[:7]
        exp["period_ord"] = exp["period"].apply(
            lambda p: int(p[:4]) * 12 + int(p[5:7]))
        results = []
        for cat, grp in exp.groupby("category"):
            monthly = grp.groupby("period_ord")["amount"].sum().reset_index()
            if len(monthly) < 2:
                results.append({"category": cat, "predicted_amount": int(grp["amount"].mean())})
                continue
            X = monthly["period_ord"].values.reshape(-1, 1)
            y = monthly["amount"].values
            reg = LinearRegression().fit(X, y)
            next_ord = int(monthly["period_ord"].max()) + 1
            pred = max(0, int(reg.predict([[next_ord]])[0]))
            results.append({"category": cat, "predicted_amount": pred})
        return sorted(results, key=lambda x: -x["predicted_amount"])
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/api/ml/anomalies/{year}/{month}")
def ml_anomalies(year: int, month: int):
    try:
        all_df = db.get_transactions(db_path=DB_PATH)
        if all_df.empty:
            return []
        stats = all_df.groupby("category")["amount"].agg(["mean", "std"]).reset_index()
        stats.columns = ["category", "mean", "std"]
        stats["std"] = stats["std"].fillna(0)
        month_df = db.get_transactions(year=year, month=month, db_path=DB_PATH)
        if month_df.empty:
            return []
        merged = month_df.merge(stats, on="category", how="left")
        merged["z_score"] = merged.apply(
            lambda r: (r["amount"] - r["mean"]) / r["std"] if r["std"] > 0 else 0, axis=1)
        flagged = merged[merged["z_score"] >= 2.0].copy()
        return flagged[["id", "date", "category", "amount", "memo", "z_score"]].to_dict(orient="records")
    except Exception as e:
        raise HTTPException(500, str(e))


# ── OCR ──────────────────────────────────────

@app.post("/api/ocr")
async def ocr_receipt(file: UploadFile = File(...)):
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise HTTPException(400, "ANTHROPIC_API_KEY が設定されていません")
    try:
        import anthropic
        image_bytes = await file.read()
        image_data = base64.standard_b64encode(image_bytes).decode()
        ext = (file.filename or "image.jpg").rsplit(".", 1)[-1].lower()
        media_type_map = {"jpg": "image/jpeg", "jpeg": "image/jpeg",
                          "png": "image/png", "gif": "image/gif", "webp": "image/webp"}
        media_type = media_type_map.get(ext, "image/jpeg")

        expense_cats = db.get_categories("支出", DB_PATH)
        income_cats = db.get_categories("収入", DB_PATH)
        prompt = (
            f"以下のレシート画像を読み取りJSONのみで返してください。\n"
            f'{{"date":"YYYY-MM-DD","type":"支出","category":"食費","amount":1234,'
            f'"store_name":"店名","memo":"概要","items":[{{"name":"品目","price":100}}]}}\n'
            f"支出カテゴリ: {', '.join(expense_cats)}\n"
            f"収入カテゴリ: {', '.join(income_cats)}\n"
            f"日付不明は {date.today().isoformat()}、金額不明は0、JSONのみ返すこと"
        )
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001", max_tokens=1024,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": image_data}},
                {"type": "text", "text": prompt},
            ]}],
        )
        raw = msg.content[0].text.strip()
        if raw.startswith("```"):
            raw = "\n".join(raw.split("\n")[1:]).rstrip("`").strip()
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise HTTPException(422, f"AIレスポンスのパース失敗: {e}")
    except Exception as e:
        raise HTTPException(500, str(e))


# ── Dashboard ────────────────────────────────

@app.get("/api/dashboard/{year}/{month}")
def dashboard(year: int, month: int):
    summary = db.get_monthly_summary(year, month, DB_PATH)

    # Budget status
    bdf = db.get_budget_status(year, month, DB_PATH)
    budget_status = bdf.to_dict(orient="records") if not bdf.empty else []

    # Account balances
    accounts = db.get_accounts(DB_PATH)
    account_balances = [{"name": a["name"], "type": a["type"],
                         "balance": db.get_account_balance(a["name"], DB_PATH)} for a in accounts]

    # Top expenses
    exp_df = db.get_transactions(year=year, month=month, tx_type="支出", db_path=DB_PATH)
    if not exp_df.empty:
        top = exp_df.groupby("category")["amount"].sum().nlargest(5).reset_index()
        top_expenses = top.rename(columns={"category": "カテゴリ", "amount": "金額"}).to_dict(orient="records")
    else:
        top_expenses = []

    # 6-month trend
    monthly_trend = []
    for delta in range(5, -1, -1):
        total_months = (year * 12 + month - 1) - delta
        y, m = divmod(total_months, 12)
        m += 1
        s = db.get_monthly_summary(y, m, DB_PATH)
        monthly_trend.append({"month": f"{y}/{m:02d}", **s})

    # Upcoming recurring
    rec_df = db.get_recurring(active_only=True, db_path=DB_PATH)
    upcoming = rec_df.to_dict(orient="records") if not rec_df.empty else []

    return {
        "summary": summary,
        "budget_status": budget_status,
        "account_balances": account_balances,
        "top_expenses": top_expenses,
        "monthly_trend": monthly_trend,
        "upcoming_recurring": upcoming,
    }
