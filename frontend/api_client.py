"""
HTTP client for the 家計簿 backend API.
All methods return plain Python dicts/lists — no Streamlit dependency.
"""

import os
from typing import Optional

import requests

BASE_URL = os.environ.get("KAKEIBO_API_URL", "http://localhost:8000")


class KakeiboClient:
    def __init__(self, base_url: str = BASE_URL):
        self.base = base_url.rstrip("/")
        self._session = requests.Session()

    def _get(self, path: str, **params):
        params = {k: v for k, v in params.items() if v is not None}
        r = self._session.get(f"{self.base}{path}", params=params, timeout=10)
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, json=None, **kwargs):
        r = self._session.post(f"{self.base}{path}", json=json, timeout=10, **kwargs)
        r.raise_for_status()
        return r.json() if r.content else None

    def _patch(self, path: str, json=None):
        r = self._session.patch(f"{self.base}{path}", json=json, timeout=10)
        r.raise_for_status()

    def _delete(self, path: str):
        r = self._session.delete(f"{self.base}{path}", timeout=10)
        r.raise_for_status()

    # ── Health ───────────────────────────────

    def health(self) -> dict:
        return self._get("/api/health")

    # ── Transactions ─────────────────────────

    def get_transactions(
        self,
        year: Optional[int] = None, month: Optional[int] = None,
        type: Optional[str] = None, account: Optional[str] = None,
        keyword: Optional[str] = None,
        amount_min: Optional[int] = None, amount_max: Optional[int] = None,
        start_date: Optional[str] = None, end_date: Optional[str] = None,
        categories: Optional[list] = None,
    ) -> list:
        cats_str = ",".join(categories) if categories else None
        return self._get("/api/transactions",
                         year=year, month=month, type=type, account=account,
                         keyword=keyword, amount_min=amount_min, amount_max=amount_max,
                         start_date=start_date, end_date=end_date, categories=cats_str)

    def add_transaction(self, date: str, type: str, category: str, amount: int,
                        memo: str = "", account: str = "現金") -> dict:
        return self._post("/api/transactions", json={
            "date": date, "type": type, "category": category,
            "amount": amount, "memo": memo, "account": account,
        })

    def delete_transaction(self, tx_id: int):
        self._delete(f"/api/transactions/{tx_id}")

    def get_recent_transactions(self, limit: int = 5) -> list:
        return self._get("/api/transactions/recent", limit=limit)

    def get_monthly_summary(self, year: int, month: int) -> dict:
        return self._get(f"/api/transactions/summary/{year}/{month}")

    # ── Categories ───────────────────────────

    def get_categories(self, type: Optional[str] = None) -> list:
        return self._get("/api/categories", type=type)

    def add_category(self, name: str, type: str) -> dict:
        return self._post("/api/categories", json={"name": name, "type": type})

    def delete_category(self, name: str):
        self._delete(f"/api/categories/{name}")

    # ── Budgets ──────────────────────────────

    def get_budgets(self, year: int, month: int) -> dict:
        return self._get(f"/api/budgets/{year}/{month}")

    def set_budget(self, year: int, month: int, category: str, amount: int) -> dict:
        return self._post(f"/api/budgets/{year}/{month}", json={"category": category, "amount": amount})

    def delete_budget(self, year: int, month: int, category: str):
        self._delete(f"/api/budgets/{year}/{month}/{category}")

    def get_budget_status(self, year: int, month: int) -> list:
        return self._get(f"/api/budgets/{year}/{month}/status")

    # ── Recurring ────────────────────────────

    def get_recurring(self, active_only: bool = True) -> list:
        return self._get("/api/recurring", active_only=active_only)

    def add_recurring(self, type: str, category: str, amount: int,
                      memo: str = "", day_of_month: int = 1) -> dict:
        return self._post("/api/recurring", json={
            "type": type, "category": category, "amount": amount,
            "memo": memo, "day_of_month": day_of_month,
        })

    def toggle_recurring(self, rec_id: int, active: bool):
        self._patch(f"/api/recurring/{rec_id}/toggle", json={"active": active})

    def delete_recurring(self, rec_id: int):
        self._delete(f"/api/recurring/{rec_id}")

    def apply_recurring(self, year: int, month: int) -> dict:
        return self._post(f"/api/recurring/apply/{year}/{month}")

    # ── Accounts ─────────────────────────────

    def get_accounts(self) -> list:
        return self._get("/api/accounts")

    def get_account_names(self) -> list:
        return [a["name"] for a in self.get_accounts()]

    def add_account(self, name: str, type: str = "現金",
                    initial_balance: int = 0, note: str = "") -> dict:
        return self._post("/api/accounts", json={
            "name": name, "type": type,
            "initial_balance": initial_balance, "note": note,
        })

    def delete_account(self, name: str):
        self._delete(f"/api/accounts/{requests.utils.quote(name)}")

    def get_account_balance(self, name: str) -> int:
        return self._get(f"/api/accounts/{requests.utils.quote(name)}/balance")["balance"]

    # ── ML ───────────────────────────────────

    def ml_predict(self, memo: str) -> list:
        return self._get("/api/ml/predict", memo=memo)["predictions"]

    def ml_retrain(self) -> dict:
        return self._post("/api/ml/retrain")

    def ml_forecast(self) -> list:
        return self._get("/api/ml/forecast")

    def ml_anomalies(self, year: int, month: int) -> list:
        return self._get(f"/api/ml/anomalies/{year}/{month}")

    # ── OCR ──────────────────────────────────

    def ocr_receipt(self, file_bytes: bytes, filename: str) -> dict:
        files = {"file": (filename, file_bytes)}
        r = self._session.post(f"{self.base}/api/ocr", files=files, timeout=30)
        r.raise_for_status()
        return r.json()

    # ── Dashboard ────────────────────────────

    def get_dashboard(self, year: int, month: int) -> dict:
        return self._get(f"/api/dashboard/{year}/{month}")
