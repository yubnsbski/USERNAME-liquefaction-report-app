"""
FastAPI backend integration tests using TestClient.
Usage: pytest tests/test_api.py -v
"""

import os
import sys
import tempfile
import pytest
from fastapi.testclient import TestClient

# ── Redirect DB to temp file before importing the app ──
_tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp_db.close()
os.environ["KAKEIBO_DB_PATH"] = _tmp_db.name

import backend.database as _db_mod
_db_mod.DB_PATH = _tmp_db.name

from backend.main import app, DB_PATH
import backend.main as _main_mod
_main_mod.DB_PATH = _tmp_db.name

client = TestClient(app)

# Ensure DB is initialized once at module load
_db_mod.init_db(_tmp_db.name)


@pytest.fixture(autouse=True)
def reset_db():
    """Wipe mutable tables before every test."""
    import sqlite3
    conn = sqlite3.connect(_tmp_db.name, timeout=10)
    conn.execute("DELETE FROM transactions")
    conn.execute("DELETE FROM budgets")
    conn.execute("DELETE FROM recurring")
    # Keep default categories and accounts
    conn.commit()
    conn.close()
    yield


# ─────────────────────────────────────────────
# Health
# ─────────────────────────────────────────────

class TestHealth:
    def test_health_returns_ok(self):
        r = client.get("/api/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"


# ─────────────────────────────────────────────
# Transactions
# ─────────────────────────────────────────────

class TestTransactions:
    def _add(self, **kwargs):
        payload = {"date": "2026-05-01", "type": "支出", "category": "食費",
                   "amount": 1000, "memo": "テスト", "account": "現金"}
        payload.update(kwargs)
        return client.post("/api/transactions", json=payload)

    def test_create_transaction(self):
        r = self._add()
        assert r.status_code == 201
        assert "id" in r.json()

    def test_list_transactions(self):
        self._add()
        r = client.get("/api/transactions")
        assert r.status_code == 200
        assert len(r.json()) == 1

    def test_filter_by_month(self):
        self._add(date="2026-05-10")
        self._add(date="2026-04-10")
        r = client.get("/api/transactions", params={"year": 2026, "month": 5})
        assert len(r.json()) == 1

    def test_filter_by_type(self):
        self._add(type="支出")
        self._add(type="収入", category="給与", amount=200000)
        r = client.get("/api/transactions", params={"type": "支出"})
        data = r.json()
        assert all(t["type"] == "支出" for t in data)

    def test_filter_by_keyword(self):
        self._add(memo="コンビニ弁当")
        self._add(memo="電車代")
        r = client.get("/api/transactions", params={"keyword": "コンビニ"})
        assert len(r.json()) == 1

    def test_filter_by_amount_range(self):
        self._add(amount=500)
        self._add(amount=5000)
        self._add(amount=50000)
        r = client.get("/api/transactions", params={"amount_min": 1000, "amount_max": 10000})
        assert all(1000 <= t["amount"] <= 10000 for t in r.json())

    def test_filter_by_account(self):
        self._add(account="現金")
        self._add(account="銀行口座")
        r = client.get("/api/transactions", params={"account": "現金"})
        assert all(t["account"] == "現金" for t in r.json())

    def test_delete_transaction(self):
        tx_id = self._add().json()["id"]
        r = client.delete(f"/api/transactions/{tx_id}")
        assert r.status_code == 204
        assert client.get("/api/transactions").json() == []

    def test_recent_transactions(self):
        for i in range(7):
            self._add(amount=100 * (i + 1))
        r = client.get("/api/transactions/recent", params={"limit": 3})
        assert len(r.json()) == 3

    def test_monthly_summary(self):
        self._add(type="収入", category="給与", amount=200000, date="2026-05-01")
        self._add(type="支出", category="食費", amount=30000, date="2026-05-10")
        r = client.get("/api/transactions/summary/2026/5")
        data = r.json()
        assert data["income"] == 200000
        assert data["expense"] == 30000
        assert data["balance"] == 170000


# ─────────────────────────────────────────────
# Categories
# ─────────────────────────────────────────────

class TestCategories:
    def test_list_categories(self):
        r = client.get("/api/categories")
        assert r.status_code == 200
        assert len(r.json()) > 0

    def test_list_categories_by_type(self):
        r = client.get("/api/categories", params={"type": "支出"})
        assert all(isinstance(c, str) for c in r.json())

    def test_create_category(self):
        r = client.post("/api/categories", json={"name": "ペット費", "type": "支出"})
        assert r.status_code == 201
        names = client.get("/api/categories", params={"type": "支出"}).json()
        assert "ペット費" in names

    def test_create_duplicate_category_returns_409(self):
        client.post("/api/categories", json={"name": "テスト費", "type": "支出"})
        r = client.post("/api/categories", json={"name": "テスト費", "type": "支出"})
        assert r.status_code == 409

    def test_delete_category(self):
        client.post("/api/categories", json={"name": "削除テスト", "type": "支出"})
        r = client.delete("/api/categories/削除テスト")
        assert r.status_code == 204

    def test_delete_in_use_category_returns_409(self):
        client.post("/api/transactions", json={
            "date": "2026-05-01", "type": "支出", "category": "食費",
            "amount": 1000, "memo": "", "account": "現金"})
        r = client.delete("/api/categories/食費")
        assert r.status_code == 409


# ─────────────────────────────────────────────
# Budgets
# ─────────────────────────────────────────────

class TestBudgets:
    def test_set_and_get_budget(self):
        client.post("/api/budgets/2026/5", json={"category": "食費", "amount": 30000})
        r = client.get("/api/budgets/2026/5")
        assert r.json()["食費"] == 30000

    def test_overwrite_budget(self):
        client.post("/api/budgets/2026/5", json={"category": "食費", "amount": 30000})
        client.post("/api/budgets/2026/5", json={"category": "食費", "amount": 40000})
        assert client.get("/api/budgets/2026/5").json()["食費"] == 40000

    def test_delete_budget(self):
        client.post("/api/budgets/2026/5", json={"category": "食費", "amount": 30000})
        client.delete("/api/budgets/2026/5/食費")
        assert "食費" not in client.get("/api/budgets/2026/5").json()

    def test_budget_status_over(self):
        client.post("/api/budgets/2026/5", json={"category": "食費", "amount": 10000})
        client.post("/api/transactions", json={
            "date": "2026-05-01", "type": "支出", "category": "食費",
            "amount": 20000, "memo": "", "account": "現金"})
        r = client.get("/api/budgets/2026/5/status")
        row = next(x for x in r.json() if x["category"] == "食費")
        assert row["over_budget"] is True
        assert row["usage_pct"] == 200.0


# ─────────────────────────────────────────────
# Recurring
# ─────────────────────────────────────────────

class TestRecurring:
    def _add(self):
        return client.post("/api/recurring", json={
            "type": "支出", "category": "住居費",
            "amount": 80000, "memo": "家賃", "day_of_month": 1,
        })

    def test_create_and_list(self):
        self._add()
        r = client.get("/api/recurring")
        assert len(r.json()) == 1

    def test_toggle_inactive(self):
        rec_id = self._add().json()["id"]
        client.patch(f"/api/recurring/{rec_id}/toggle", json={"active": False})
        r = client.get("/api/recurring", params={"active_only": True})
        assert r.json() == []

    def test_delete(self):
        rec_id = self._add().json()["id"]
        client.delete(f"/api/recurring/{rec_id}")
        assert client.get("/api/recurring").json() == []

    def test_apply_recurring(self):
        self._add()
        r = client.post("/api/recurring/apply/2026/5")
        assert r.json()["registered"] == 1
        txns = client.get("/api/transactions", params={"year": 2026, "month": 5}).json()
        assert len(txns) == 1

    def test_apply_recurring_no_duplicate(self):
        self._add()
        client.post("/api/recurring/apply/2026/5")
        r = client.post("/api/recurring/apply/2026/5")
        assert r.json()["registered"] == 0


# ─────────────────────────────────────────────
# Accounts
# ─────────────────────────────────────────────

class TestAccounts:
    def test_list_defaults(self):
        r = client.get("/api/accounts")
        names = [a["name"] for a in r.json()]
        assert "現金" in names

    def test_create_account(self):
        client.post("/api/accounts", json={"name": "楽天銀行", "type": "銀行", "initial_balance": 50000})
        names = [a["name"] for a in client.get("/api/accounts").json()]
        assert "楽天銀行" in names

    def test_duplicate_account_returns_409(self):
        r = client.post("/api/accounts", json={"name": "現金", "type": "現金"})
        assert r.status_code == 409

    def test_account_balance(self):
        client.post("/api/accounts", json={"name": "テスト口座", "type": "銀行", "initial_balance": 100000})
        client.post("/api/transactions", json={
            "date": "2026-05-01", "type": "収入", "category": "給与",
            "amount": 250000, "memo": "", "account": "テスト口座"})
        client.post("/api/transactions", json={
            "date": "2026-05-10", "type": "支出", "category": "食費",
            "amount": 30000, "memo": "", "account": "テスト口座"})
        r = client.get("/api/accounts/テスト口座/balance")
        assert r.json()["balance"] == 100000 + 250000 - 30000

    def test_delete_unused_account(self):
        client.post("/api/accounts", json={"name": "削除テスト口座", "type": "その他"})
        r = client.delete("/api/accounts/削除テスト口座")
        assert r.status_code == 204

    def test_delete_in_use_account_returns_409(self):
        client.post("/api/transactions", json={
            "date": "2026-05-01", "type": "支出", "category": "食費",
            "amount": 1000, "memo": "", "account": "現金"})
        r = client.delete("/api/accounts/現金")
        assert r.status_code == 409


# ─────────────────────────────────────────────
# ML
# ─────────────────────────────────────────────

class TestML:
    def _seed(self):
        records = [
            ("2026-05-01", "支出", "食費", 1200, "スーパー"),
            ("2026-05-02", "支出", "食費", 980, "コンビニ弁当"),
            ("2026-05-03", "支出", "交通費", 320, "電車代"),
            ("2026-05-04", "支出", "交通費", 470, "バス代"),
            ("2026-05-05", "支出", "食費", 1500, "スーパー肉"),
            ("2026-05-06", "支出", "交通費", 340, "電車通勤"),
            ("2026-05-07", "収入", "給与", 250000, "5月分"),
            ("2026-05-08", "収入", "給与", 250000, "6月分"),
            ("2026-05-09", "支出", "娯楽費", 2000, "映画"),
            ("2026-05-10", "支出", "娯楽費", 1500, "ゲーム"),
        ]
        for date, type_, cat, amt, memo in records:
            client.post("/api/transactions", json={
                "date": date, "type": type_, "category": cat,
                "amount": amt, "memo": memo, "account": "現金"})

    def test_predict_empty_returns_empty(self):
        r = client.get("/api/ml/predict", params={"memo": ""})
        assert r.status_code == 200
        assert r.json()["predictions"] == []

    def test_predict_insufficient_data_returns_empty(self):
        client.post("/api/transactions", json={
            "date": "2026-05-01", "type": "支出", "category": "食費",
            "amount": 500, "memo": "test", "account": "現金"})
        client.post("/api/ml/retrain")
        r = client.get("/api/ml/predict", params={"memo": "食費"})
        assert r.json()["predictions"] == []

    def test_retrain(self):
        self._seed()
        r = client.post("/api/ml/retrain")
        assert r.status_code == 200
        assert r.json()["trained"] is True

    def test_predict_after_training(self):
        self._seed()
        client.post("/api/ml/retrain")
        r = client.get("/api/ml/predict", params={"memo": "電車代"})
        preds = r.json()["predictions"]
        assert len(preds) > 0
        assert all("category" in p and "confidence" in p for p in preds)
        assert abs(sum(p["confidence"] for p in preds) - 1.0) < 0.01 or len(preds) <= 3

    def test_forecast_empty_returns_empty(self):
        r = client.get("/api/ml/forecast")
        assert r.json() == []

    def test_anomalies_empty_returns_empty(self):
        r = client.get("/api/ml/anomalies/2026/5")
        assert r.json() == []


# ─────────────────────────────────────────────
# Dashboard
# ─────────────────────────────────────────────

class TestDashboard:
    def test_dashboard_structure(self):
        r = client.get("/api/dashboard/2026/5")
        assert r.status_code == 200
        data = r.json()
        for key in ["summary", "budget_status", "account_balances",
                    "top_expenses", "monthly_trend", "upcoming_recurring"]:
            assert key in data

    def test_dashboard_trend_has_6_months(self):
        r = client.get("/api/dashboard/2026/5")
        assert len(r.json()["monthly_trend"]) == 6

    def test_dashboard_summary_with_data(self):
        client.post("/api/transactions", json={
            "date": "2026-05-01", "type": "収入", "category": "給与",
            "amount": 300000, "memo": "", "account": "現金"})
        client.post("/api/transactions", json={
            "date": "2026-05-10", "type": "支出", "category": "食費",
            "amount": 50000, "memo": "", "account": "現金"})
        r = client.get("/api/dashboard/2026/5")
        s = r.json()["summary"]
        assert s["income"] == 300000
        assert s["expense"] == 50000
        assert s["balance"] == 250000
