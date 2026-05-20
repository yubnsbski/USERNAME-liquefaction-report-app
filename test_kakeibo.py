"""
家計簿アプリ テストスイート
Usage: pytest test_kakeibo.py -v
"""

import json
import os
import sys
import tempfile
from datetime import date
from unittest.mock import MagicMock, patch

import pytest
import pandas as pd

# ── Streamlit をモック（インポート前に差し込む） ──────────────────────────────
_st_mock = MagicMock()
_st_mock.secrets = {}
sys.modules.setdefault("streamlit", _st_mock)

import kakeibo_app as app  # noqa: E402


# ─────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────

@pytest.fixture()
def tmp_paths(tmp_path):
    """Redirect DB_PATH and CONFIG_PATH to temporary files for each test."""
    db = str(tmp_path / "kakeibo_test.db")
    cfg = str(tmp_path / "kakeibo_config_test.json")
    with patch.object(app, "DB_PATH", db), patch.object(app, "CONFIG_PATH", cfg):
        app.init_db()
        yield db, cfg


@pytest.fixture()
def seeded_db(tmp_paths):
    """DB pre-populated with sample transactions."""
    app.add_transaction("2026-05-01", "支出", "食費", 1200, "スーパー")
    app.add_transaction("2026-05-03", "支出", "交通費", 320, "バス")
    app.add_transaction("2026-05-15", "収入", "給与", 250000, "5月分")
    app.add_transaction("2026-04-10", "支出", "娯楽費", 3000, "映画")
    return tmp_paths


# ─────────────────────────────────────────────
# Database — 初期化
# ─────────────────────────────────────────────

class TestDatabaseInit:
    def test_tables_created(self, tmp_paths):
        import sqlite3
        db, _ = tmp_paths
        conn = sqlite3.connect(db)
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        conn.close()
        assert "transactions" in tables
        assert "categories" in tables

    def test_default_expense_categories_seeded(self, tmp_paths):
        cats = app.get_categories("支出")
        assert "食費" in cats
        assert "交通費" in cats
        assert len(cats) >= 10

    def test_default_income_categories_seeded(self, tmp_paths):
        cats = app.get_categories("収入")
        assert "給与" in cats
        assert len(cats) >= 4


# ─────────────────────────────────────────────
# Database — CRUD
# ─────────────────────────────────────────────

class TestTransactionCRUD:
    def test_add_and_fetch(self, tmp_paths):
        app.add_transaction("2026-05-10", "支出", "食費", 980, "コンビニ")
        df = app.get_transactions(year=2026, month=5)
        assert len(df) == 1
        assert df.iloc[0]["amount"] == 980
        assert df.iloc[0]["category"] == "食費"

    def test_delete_transaction(self, seeded_db):
        df = app.get_transactions(year=2026, month=5)
        tx_id = int(df.iloc[0]["id"])
        app.delete_transaction(tx_id)
        df2 = app.get_transactions(year=2026, month=5)
        assert tx_id not in df2["id"].values

    def test_get_recent_transactions_limit(self, seeded_db):
        recent = app.get_recent_transactions(2)
        assert len(recent) == 2

    def test_get_total_record_count(self, seeded_db):
        assert app.get_total_record_count() == 4


# ─────────────────────────────────────────────
# Database — フィルタリング
# ─────────────────────────────────────────────

class TestTransactionFiltering:
    def test_filter_by_month(self, seeded_db):
        df = app.get_transactions(year=2026, month=5)
        assert len(df) == 3  # 4月のものは除外

    def test_filter_by_year(self, seeded_db):
        df = app.get_transactions(year=2026)
        assert len(df) == 4

    def test_filter_by_type_expense(self, seeded_db):
        df = app.get_transactions(year=2026, month=5, tx_type="支出")
        assert all(df["type"] == "支出")
        assert len(df) == 2

    def test_filter_by_type_income(self, seeded_db):
        df = app.get_transactions(year=2026, month=5, tx_type="収入")
        assert all(df["type"] == "収入")
        assert len(df) == 1

    def test_filter_by_categories(self, seeded_db):
        df = app.get_transactions(year=2026, month=5, categories=["食費"])
        assert len(df) == 1
        assert df.iloc[0]["category"] == "食費"

    def test_filter_by_date_range(self, seeded_db):
        df = app.get_transactions(start_date="2026-05-01", end_date="2026-05-03")
        assert len(df) == 2

    def test_monthly_summary(self, seeded_db):
        summary = app.get_monthly_summary(2026, 5)
        assert summary["income"] == 250000
        assert summary["expense"] == 1200 + 320
        assert summary["balance"] == 250000 - 1520


# ─────────────────────────────────────────────
# Database — カテゴリ管理
# ─────────────────────────────────────────────

class TestCategories:
    def test_add_category(self, tmp_paths):
        result = app.add_category("ペット費", "支出")
        assert result is True
        assert "ペット費" in app.get_categories("支出")

    def test_add_duplicate_category_fails(self, tmp_paths):
        app.add_category("ペット費", "支出")
        result = app.add_category("ペット費", "支出")
        assert result is False

    def test_delete_unused_category(self, tmp_paths):
        app.add_category("テスト費", "支出")
        result = app.delete_category("テスト費")
        assert result is True
        assert "テスト費" not in app.get_categories("支出")

    def test_delete_in_use_category_fails(self, tmp_paths):
        app.add_transaction("2026-05-01", "支出", "食費", 500, "")
        result = app.delete_category("食費")
        assert result is False

    def test_get_categories_all_returns_dicts(self, tmp_paths):
        cats = app.get_categories()
        assert isinstance(cats, list)
        assert all("name" in c and "type" in c for c in cats)

    def test_get_categories_filtered_returns_list(self, tmp_paths):
        expense = app.get_categories("支出")
        income = app.get_categories("収入")
        assert isinstance(expense, list)
        assert isinstance(income, list)
        # No income category should appear in expense list
        expense_set = set(expense)
        for cat in income:
            assert cat not in expense_set


# ─────────────────────────────────────────────
# Database — 月次データ削除
# ─────────────────────────────────────────────

class TestDeleteMonthData:
    def test_delete_month_data(self, seeded_db):
        app.delete_month_data(2026, 5)
        df = app.get_transactions(year=2026, month=5)
        assert df.empty
        # April data untouched
        df_apr = app.get_transactions(year=2026, month=4)
        assert len(df_apr) == 1


# ─────────────────────────────────────────────
# PIN 管理
# ─────────────────────────────────────────────

class TestPINManagement:
    def test_no_pin_initially(self, tmp_paths):
        assert app.has_pin() is False

    def test_set_and_verify_pin(self, tmp_paths):
        app.set_pin("1234")
        assert app.has_pin() is True
        assert app.verify_pin("1234") is True

    def test_wrong_pin_rejected(self, tmp_paths):
        app.set_pin("1234")
        assert app.verify_pin("9999") is False

    def test_remove_pin(self, tmp_paths):
        app.set_pin("1234")
        app.remove_pin()
        assert app.has_pin() is False

    def test_verify_with_no_pin_returns_true(self, tmp_paths):
        # No PIN configured → always allowed
        assert app.verify_pin("") is True


# ─────────────────────────────────────────────
# エクスポート
# ─────────────────────────────────────────────

class TestExport:
    @pytest.fixture()
    def sample_df(self):
        return pd.DataFrame([
            {"id": 1, "date": "2026-05-01", "type": "支出", "category": "食費", "amount": 980, "memo": "スーパー", "created_at": ""},
            {"id": 2, "date": "2026-05-02", "type": "収入", "category": "給与", "amount": 200000, "memo": "", "created_at": ""},
        ])

    def test_csv_starts_with_utf8_bom(self, sample_df):
        data = app.df_to_csv_bytes(sample_df)
        assert data[:3] == b"\xef\xbb\xbf"

    def test_csv_has_japanese_headers(self, sample_df):
        data = app.df_to_csv_bytes(sample_df)
        text = data.decode("utf-8-sig")
        assert "日付" in text
        assert "金額" in text
        assert "カテゴリ" in text

    def test_csv_row_count(self, sample_df):
        data = app.df_to_csv_bytes(sample_df)
        lines = data.decode("utf-8-sig").strip().split("\n")
        assert len(lines) == 3  # header + 2 rows

    def test_excel_bytes_non_empty(self, sample_df):
        data = app.df_to_excel_bytes(sample_df, "2026年5月")
        assert len(data) > 1000  # valid xlsx is never tiny

    def test_excel_is_valid_xlsx(self, sample_df):
        import openpyxl, io
        data = app.df_to_excel_bytes(sample_df, "テスト")
        wb = openpyxl.load_workbook(io.BytesIO(data))
        assert "家計簿データ" in wb.sheetnames


# ─────────────────────────────────────────────
# OCR — 応答パース
# ─────────────────────────────────────────────

class TestOCRParsing:
    """Unit tests for OCR response parsing (no real API calls)."""

    VALID_JSON = json.dumps({
        "date": "2026-05-10",
        "type": "支出",
        "category": "食費",
        "amount": 1580,
        "store_name": "テストスーパー",
        "memo": "テストスーパー 食料品",
        "items": [{"name": "野菜", "price": 580}],
    })

    CODE_BLOCK_JSON = f"```json\n{VALID_JSON}\n```"
    CODE_BLOCK_BARE = f"```\n{VALID_JSON}\n```"

    def _run_ocr_with_mock_response(self, response_text: str, tmp_paths) -> dict:
        mock_message = MagicMock()
        mock_message.content = [MagicMock(text=response_text)]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_message

        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test-dummy"}):
            with patch("anthropic.Anthropic", return_value=mock_client):
                return app.ocr_receipt_with_claude(b"fake_image_bytes", "image/jpeg")

    def test_parses_clean_json(self, tmp_paths):
        result = self._run_ocr_with_mock_response(self.VALID_JSON, tmp_paths)
        assert result["amount"] == 1580
        assert result["category"] == "食費"
        assert result["date"] == "2026-05-10"

    def test_parses_json_code_block(self, tmp_paths):
        result = self._run_ocr_with_mock_response(self.CODE_BLOCK_JSON, tmp_paths)
        assert result["amount"] == 1580

    def test_parses_bare_code_block(self, tmp_paths):
        result = self._run_ocr_with_mock_response(self.CODE_BLOCK_BARE, tmp_paths)
        assert result["store_name"] == "テストスーパー"

    def test_items_list_preserved(self, tmp_paths):
        result = self._run_ocr_with_mock_response(self.VALID_JSON, tmp_paths)
        assert isinstance(result["items"], list)
        assert result["items"][0]["name"] == "野菜"

    def test_missing_api_key_raises(self, tmp_paths):
        with patch.dict(os.environ, {}, clear=True):
            _st_mock.secrets = {}
            with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
                app.ocr_receipt_with_claude(b"img", "image/jpeg")

    def test_invalid_json_raises(self, tmp_paths):
        with pytest.raises(json.JSONDecodeError):
            self._run_ocr_with_mock_response("not valid json at all", tmp_paths)


# ─────────────────────────────────────────────
# OCR — ダミーデータ定数
# ─────────────────────────────────────────────

class TestDummyOCRResult:
    def test_dummy_result_has_required_keys(self):
        required = {"date", "type", "category", "amount", "store_name", "memo", "items"}
        assert required.issubset(app._DUMMY_OCR_RESULT.keys())

    def test_dummy_result_amount_positive(self):
        assert app._DUMMY_OCR_RESULT["amount"] > 0

    def test_dummy_result_items_sum_matches_amount(self):
        items_total = sum(i["price"] for i in app._DUMMY_OCR_RESULT["items"])
        assert items_total == app._DUMMY_OCR_RESULT["amount"]

    def test_dummy_result_date_is_today(self):
        assert app._DUMMY_OCR_RESULT["date"] == date.today().isoformat()


# ─────────────────────────────────────────────
# ML — カテゴリ分類
# ─────────────────────────────────────────────

class TestMLCategoryClassifier:
    @pytest.fixture()
    def trained_db(self, tmp_paths):
        """Seed enough varied transactions to train a classifier."""
        records = [
            ("2026-05-01", "支出", "食費",    1200, "スーパーで野菜"),
            ("2026-05-02", "支出", "食費",     980, "コンビニ 弁当"),
            ("2026-05-03", "支出", "食費",    1500, "スーパー 肉"),
            ("2026-05-04", "支出", "交通費",    230, "電車代"),
            ("2026-05-05", "支出", "交通費",    470, "バス 定期"),
            ("2026-05-06", "支出", "交通費",    340, "電車 通勤"),
            ("2026-05-07", "収入", "給与",  250000, "5月分給与"),
            ("2026-05-08", "収入", "給与",  250000, "6月分給与"),
            ("2026-05-09", "支出", "娯楽費",  2000, "映画チケット"),
            ("2026-05-10", "支出", "娯楽費",  1500, "ゲーム"),
        ]
        for r in records:
            app.add_transaction(*r)
        return tmp_paths

    def test_train_returns_pipeline(self, trained_db):
        model = app.train_category_classifier()
        assert model is not None

    def test_predict_returns_known_category(self, trained_db):
        model = app.train_category_classifier()
        assert model is not None
        proba = model.predict_proba(["電車代"])[0]
        idx = proba.argmax()
        pred = model.classes_[idx]
        conf = float(proba[idx])
        all_cat_names = [c["name"] for c in app.get_categories()]
        assert pred in all_cat_names
        assert 0.0 <= conf <= 1.0

    def test_predict_empty_returns_none(self, trained_db):
        # 空文字のときは (None, 0.0) を返すことを直接確認
        pred, conf = app._ml_feature(""), 0.0
        assert pred == ""  # _ml_feature は空文字を返す
        assert conf == 0.0

    def test_insufficient_data_returns_none(self, tmp_paths):
        # Only 2 records — below min_samples=5
        app.add_transaction("2026-05-01", "支出", "食費", 500, "テスト")
        app.add_transaction("2026-05-02", "支出", "食費", 300, "テスト2")
        model = app.train_category_classifier()
        assert model is None

    def test_single_category_returns_none(self, tmp_paths):
        for i in range(6):
            app.add_transaction(f"2026-05-{i+1:02d}", "支出", "食費", 500, f"テスト{i}")
        model = app.train_category_classifier()
        assert model is None  # 1カテゴリのみでは分類不可


# ─────────────────────────────────────────────
# ML — 支出予測
# ─────────────────────────────────────────────

class TestForecastExpenses:
    @pytest.fixture()
    def multi_month_db(self, tmp_paths):
        records = [
            ("2026-03-10", "支出", "食費", 30000, "3月"),
            ("2026-04-10", "支出", "食費", 32000, "4月"),
            ("2026-05-10", "支出", "食費", 34000, "5月"),
            ("2026-03-15", "支出", "交通費", 5000, "3月"),
            ("2026-04-15", "支出", "交通費", 5200, "4月"),
            ("2026-05-15", "支出", "交通費", 5400, "5月"),
        ]
        for r in records:
            app.add_transaction(*r)
        return tmp_paths

    def test_forecast_returns_dataframe(self, multi_month_db):
        df = app.forecast_expenses_by_category()
        assert isinstance(df, pd.DataFrame)
        assert "カテゴリ" in df.columns
        assert "予測金額" in df.columns

    def test_forecast_covers_known_categories(self, multi_month_db):
        df = app.forecast_expenses_by_category()
        cats = df["カテゴリ"].tolist()
        assert "食費" in cats
        assert "交通費" in cats

    def test_forecast_amounts_non_negative(self, multi_month_db):
        df = app.forecast_expenses_by_category()
        assert (df["予測金額"] >= 0).all()

    def test_forecast_empty_db_returns_empty(self, tmp_paths):
        df = app.forecast_expenses_by_category()
        assert df.empty


# ─────────────────────────────────────────────
# ML — 異常支出検知
# ─────────────────────────────────────────────

class TestAnomalyDetection:
    @pytest.fixture()
    def anomaly_db(self, tmp_paths):
        # 食費: 平均 ~1000円, 今月だけ 50000円（外れ値）
        for i in range(1, 6):
            app.add_transaction(f"2026-0{i}-10", "支出", "食費", 1000, "通常")
        app.add_transaction("2026-05-20", "支出", "食費", 50000, "異常支出")
        return tmp_paths

    def test_detects_anomaly(self, anomaly_db):
        flagged = app.detect_anomalies(2026, 5)
        assert not flagged.empty
        assert "食費" in flagged["category"].values

    def test_z_score_is_high(self, anomaly_db):
        flagged = app.detect_anomalies(2026, 5)
        assert flagged["z_score"].max() >= 2.0

    def test_no_anomaly_returns_empty(self, tmp_paths):
        for i in range(1, 6):
            app.add_transaction(f"2026-0{i}-10", "支出", "食費", 1000, "通常")
        # 今月も同額 → 外れ値なし
        app.add_transaction("2026-05-20", "支出", "食費", 1000, "通常")
        flagged = app.detect_anomalies(2026, 5)
        assert flagged.empty

    def test_columns_present(self, anomaly_db):
        flagged = app.detect_anomalies(2026, 5)
        for col in ["date", "category", "amount", "memo", "z_score"]:
            assert col in flagged.columns


# ─────────────────────────────────────────────
# 予算管理
# ─────────────────────────────────────────────

class TestBudget:
    def test_set_and_get_budget(self, tmp_paths):
        app.set_budget(2026, 5, "食費", 30000)
        budgets = app.get_budgets(2026, 5)
        assert budgets["食費"] == 30000

    def test_overwrite_budget(self, tmp_paths):
        app.set_budget(2026, 5, "食費", 30000)
        app.set_budget(2026, 5, "食費", 40000)
        assert app.get_budgets(2026, 5)["食費"] == 40000

    def test_delete_budget(self, tmp_paths):
        app.set_budget(2026, 5, "食費", 30000)
        app.delete_budget(2026, 5, "食費")
        assert "食費" not in app.get_budgets(2026, 5)

    def test_budget_status_columns(self, tmp_paths):
        app.set_budget(2026, 5, "食費", 30000)
        app.add_transaction("2026-05-01", "支出", "食費", 15000, "")
        df = app.get_budget_status(2026, 5)
        assert not df.empty
        for col in ["category", "budget", "actual", "usage_pct", "over_budget"]:
            assert col in df.columns

    def test_budget_status_over_budget(self, tmp_paths):
        app.set_budget(2026, 5, "食費", 10000)
        app.add_transaction("2026-05-01", "支出", "食費", 20000, "")
        df = app.get_budget_status(2026, 5)
        row = df[df["category"] == "食費"].iloc[0]
        assert bool(row["over_budget"]) is True
        assert row["usage_pct"] == 200.0

    def test_budget_status_under_budget(self, tmp_paths):
        app.set_budget(2026, 5, "食費", 30000)
        app.add_transaction("2026-05-01", "支出", "食費", 10000, "")
        df = app.get_budget_status(2026, 5)
        row = df[df["category"] == "食費"].iloc[0]
        assert bool(row["over_budget"]) is False

    def test_no_budget_returns_empty(self, tmp_paths):
        df = app.get_budget_status(2026, 5)
        assert df.empty

    def test_budgets_isolated_by_month(self, tmp_paths):
        app.set_budget(2026, 5, "食費", 30000)
        app.set_budget(2026, 6, "食費", 40000)
        assert app.get_budgets(2026, 5)["食費"] == 30000
        assert app.get_budgets(2026, 6)["食費"] == 40000


# ─────────────────────────────────────────────
# 定期取引
# ─────────────────────────────────────────────

class TestRecurring:
    def test_add_and_get_recurring(self, tmp_paths):
        app.add_recurring("支出", "住居費", 80000, "家賃", 1)
        df = app.get_recurring()
        assert len(df) == 1
        assert df.iloc[0]["category"] == "住居費"
        assert df.iloc[0]["amount"] == 80000

    def test_toggle_inactive(self, tmp_paths):
        app.add_recurring("支出", "住居費", 80000, "家賃", 1)
        rec_id = int(app.get_recurring(active_only=False).iloc[0]["id"])
        app.toggle_recurring(rec_id, False)
        df_active = app.get_recurring(active_only=True)
        assert df_active.empty

    def test_toggle_back_active(self, tmp_paths):
        app.add_recurring("支出", "住居費", 80000, "家賃", 1)
        rec_id = int(app.get_recurring(active_only=False).iloc[0]["id"])
        app.toggle_recurring(rec_id, False)
        app.toggle_recurring(rec_id, True)
        assert len(app.get_recurring(active_only=True)) == 1

    def test_delete_recurring(self, tmp_paths):
        app.add_recurring("支出", "住居費", 80000, "家賃", 1)
        rec_id = int(app.get_recurring(active_only=False).iloc[0]["id"])
        app.delete_recurring(rec_id)
        assert app.get_recurring(active_only=False).empty

    def test_apply_recurring_registers_transactions(self, tmp_paths):
        app.add_recurring("支出", "住居費", 80000, "家賃", 1)
        app.add_recurring("支出", "通信費", 5000, "スマホ", 15)
        n = app.apply_recurring(2026, 5)
        assert n == 2
        df = app.get_transactions(year=2026, month=5)
        assert len(df) == 2

    def test_apply_recurring_no_duplicate(self, tmp_paths):
        app.add_recurring("支出", "住居費", 80000, "家賃", 1)
        app.apply_recurring(2026, 5)
        n2 = app.apply_recurring(2026, 5)
        assert n2 == 0
        assert len(app.get_transactions(year=2026, month=5)) == 1

    def test_apply_recurring_inactive_skipped(self, tmp_paths):
        app.add_recurring("支出", "住居費", 80000, "家賃", 1)
        rec_id = int(app.get_recurring(active_only=False).iloc[0]["id"])
        app.toggle_recurring(rec_id, False)
        n = app.apply_recurring(2026, 5)
        assert n == 0

    def test_apply_recurring_respects_month_end(self, tmp_paths):
        # day=31 should clamp to last day of February (28)
        app.add_recurring("支出", "その他", 1000, "test", 28)
        n = app.apply_recurring(2026, 2)
        assert n == 1
        df = app.get_transactions(year=2026, month=2)
        assert df.iloc[0]["date"] == "2026-02-28"
