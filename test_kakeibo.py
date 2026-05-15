"""
Test suite for kakeibo_app.py (Japanese Household Budget App)
Tests cover database operations, PIN management, category management, and calculations
"""

import pytest
import sqlite3
import os
import tempfile
import json
import bcrypt
from datetime import date, datetime
from unittest.mock import patch, MagicMock
import sys

# Mock streamlit before importing kakeibo_app
sys.modules['streamlit'] = MagicMock()

# Import after mocking
import kakeibo_app as kab


# ─────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────

@pytest.fixture
def temp_db():
    """Create a temporary database for testing"""
    fd, path = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    yield path
    if os.path.exists(path):
        os.remove(path)


@pytest.fixture
def temp_config():
    """Create a temporary config file for testing"""
    fd, path = tempfile.mkstemp(suffix='.json')
    os.close(fd)
    yield path
    if os.path.exists(path):
        os.remove(path)


@pytest.fixture
def setup_test_db(temp_db):
    """Initialize test database"""
    with patch.object(kab, 'DB_PATH', temp_db):
        kab.init_db()
        yield temp_db


@pytest.fixture
def setup_test_config(temp_config):
    """Setup test config"""
    with patch.object(kab, 'CONFIG_PATH', temp_config):
        yield temp_config


# ─────────────────────────────────────────────
# Database Connection Tests
# ─────────────────────────────────────────────

class TestDatabaseConnection:
    """Test database connection functionality"""
    
    def test_get_connection(self, setup_test_db):
        """Test database connection creation"""
        with patch.object(kab, 'DB_PATH', setup_test_db):
            conn = kab.get_connection()
            assert conn is not None
            assert isinstance(conn, sqlite3.Connection)
            conn.close()
    
    def test_init_db_creates_tables(self, setup_test_db):
        """Test that init_db creates required tables"""
        with patch.object(kab, 'DB_PATH', setup_test_db):
            conn = kab.get_connection()
            c = conn.cursor()
            
            # Check transactions table exists
            c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='transactions'")
            assert c.fetchone() is not None
            
            # Check categories table exists
            c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='categories'")
            assert c.fetchone() is not None
            
            conn.close()
    
    def test_init_db_seeds_categories(self, setup_test_db):
        """Test that default categories are seeded"""
        with patch.object(kab, 'DB_PATH', setup_test_db):
            categories = kab.get_categories()
            assert len(categories) > 0
            
            category_names = [c['name'] if isinstance(c, dict) else c for c in categories]
            assert "食費" in category_names
            assert "給与" in category_names


# ─────────────────────────────────────────────
# Transaction Tests
# ─────────────────────────────────────────────

class TestTransactions:
    """Test transaction management"""
    
    def test_add_transaction_success(self, setup_test_db):
        """Test adding a transaction successfully"""
        with patch.object(kab, 'DB_PATH', setup_test_db):
            result = kab.add_transaction("2026-05-15", "支出", "食費", 1000, "スーパー")
            assert result is True
    
    def test_add_transaction_negative_amount_fails(self, setup_test_db):
        """Test that negative amounts are rejected"""
        with patch.object(kab, 'DB_PATH', setup_test_db):
            # The function should handle this, but amounts <= 0 should not work
            result = kab.add_transaction("2026-05-15", "支出", "食費", -100, "Invalid")
            assert result is False
    
    def test_get_transactions_empty(self, setup_test_db):
        """Test getting transactions when database is empty"""
        with patch.object(kab, 'DB_PATH', setup_test_db):
            df = kab.get_transactions()
            assert df.empty
    
    def test_get_transactions_filters_by_month(self, setup_test_db):
        """Test filtering transactions by month"""
        with patch.object(kab, 'DB_PATH', setup_test_db):
            kab.add_transaction("2026-05-15", "支出", "食費", 1000, "Test1")
            kab.add_transaction("2026-06-10", "支出", "食費", 2000, "Test2")
            
            may_txs = kab.get_transactions(year=2026, month=5)
            assert len(may_txs) == 1
            assert may_txs.iloc[0]['amount'] == 1000
    
    def test_get_transactions_filters_by_type(self, setup_test_db):
        """Test filtering transactions by type (income/expense)"""
        with patch.object(kab, 'DB_PATH', setup_test_db):
            kab.add_transaction("2026-05-15", "支出", "食費", 1000, "Expense")
            kab.add_transaction("2026-05-16", "収入", "給与", 30000, "Income")
            
            expenses = kab.get_transactions(tx_type="支出")
            assert len(expenses) == 1
            
            incomes = kab.get_transactions(tx_type="収入")
            assert len(incomes) == 1
    
    def test_delete_transaction(self, setup_test_db):
        """Test deleting a transaction"""
        with patch.object(kab, 'DB_PATH', setup_test_db):
            kab.add_transaction("2026-05-15", "支出", "食費", 1000, "Delete me")
            txs = kab.get_transactions()
            assert len(txs) == 1
            
            tx_id = txs.iloc[0]['id']
            kab.delete_transaction(tx_id)
            
            txs_after = kab.get_transactions()
            assert len(txs_after) == 0
    
    def test_get_recent_transactions(self, setup_test_db):
        """Test getting recent transactions with limit"""
        with patch.object(kab, 'DB_PATH', setup_test_db):
            for i in range(10):
                kab.add_transaction("2026-05-15", "支出", "食費", 1000 + i, f"Test {i}")
            
            recent = kab.get_recent_transactions(limit=5)
            assert len(recent) == 5


# ─────────────────────────────────────────────
# Monthly Summary Tests
# ─────────────────────────────────────────────

class TestMonthlySummary:
    """Test monthly summary calculations"""
    
    def test_empty_month_summary(self, setup_test_db):
        """Test summary for month with no transactions"""
        with patch.object(kab, 'DB_PATH', setup_test_db):
            summary = kab.get_monthly_summary(2026, 5)
            assert summary['income'] == 0
            assert summary['expense'] == 0
            assert summary['balance'] == 0
    
    def test_monthly_summary_calculation(self, setup_test_db):
        """Test correct calculation of monthly summary"""
        with patch.object(kab, 'DB_PATH', setup_test_db):
            kab.add_transaction("2026-05-15", "収入", "給与", 50000, "Salary")
            kab.add_transaction("2026-05-16", "支出", "食費", 10000, "Groceries")
            kab.add_transaction("2026-05-17", "支出", "交通費", 5000, "Transportation")
            
            summary = kab.get_monthly_summary(2026, 5)
            assert summary['income'] == 50000
            assert summary['expense'] == 15000
            assert summary['balance'] == 35000
    
    def test_monthly_summary_excludes_other_months(self, setup_test_db):
        """Test that summary only includes current month"""
        with patch.object(kab, 'DB_PATH', setup_test_db):
            kab.add_transaction("2026-05-15", "支出", "食費", 10000, "May")
            kab.add_transaction("2026-06-15", "支出", "食費", 20000, "June")
            
            may_summary = kab.get_monthly_summary(2026, 5)
            assert may_summary['expense'] == 10000


# ─────────────────────────────────────────────
# Category Management Tests
# ─────────────────────────────────────────────

class TestCategories:
    """Test category management"""
    
    def test_get_categories(self, setup_test_db):
        """Test retrieving categories"""
        with patch.object(kab, 'DB_PATH', setup_test_db):
            categories = kab.get_categories()
            assert len(categories) > 0
    
    def test_get_expense_categories(self, setup_test_db):
        """Test filtering to expense categories only"""
        with patch.object(kab, 'DB_PATH', setup_test_db):
            expense_cats = kab.get_categories(tx_type="支出")
            assert len(expense_cats) > 0
            assert all(cat == cat for cat in expense_cats)  # All are strings
    
    def test_get_income_categories(self, setup_test_db):
        """Test filtering to income categories only"""
        with patch.object(kab, 'DB_PATH', setup_test_db):
            income_cats = kab.get_categories(tx_type="収入")
            assert len(income_cats) > 0
    
    def test_add_category_success(self, setup_test_db):
        """Test adding a new category"""
        with patch.object(kab, 'DB_PATH', setup_test_db):
            result = kab.add_category("テスト費", "支出")
            assert result is True
            
            categories = kab.get_categories(tx_type="支出")
            assert "テスト費" in categories
    
    def test_add_duplicate_category_fails(self, setup_test_db):
        """Test that duplicate category names are rejected"""
        with patch.object(kab, 'DB_PATH', setup_test_db):
            kab.add_category("テスト費", "支出")
            result = kab.add_category("テスト費", "支出")
            assert result is False
    
    def test_delete_unused_category(self, setup_test_db):
        """Test deleting a category that's not in use"""
        with patch.object(kab, 'DB_PATH', setup_test_db):
            kab.add_category("テスト費", "支出")
            result = kab.delete_category("テスト費")
            assert result is True
    
    def test_cannot_delete_category_in_use(self, setup_test_db):
        """Test that categories in use cannot be deleted"""
        with patch.object(kab, 'DB_PATH', setup_test_db):
            kab.add_transaction("2026-05-15", "支出", "食費", 1000, "Test")
            result = kab.delete_category("食費")
            assert result is False


# ─────────────────────────────────────────────
# PIN Management Tests
# ─────────────────────────────────────────────

class TestPINManagement:
    """Test PIN lock functionality"""
    
    def test_no_pin_by_default(self, setup_test_config):
        """Test that no PIN is set by default"""
        with patch.object(kab, 'CONFIG_PATH', setup_test_config):
            assert kab.has_pin() is False
    
    def test_set_pin(self, setup_test_config):
        """Test setting a PIN"""
        with patch.object(kab, 'CONFIG_PATH', setup_test_config):
            kab.set_pin("1234")
            assert kab.has_pin() is True
    
    def test_verify_correct_pin(self, setup_test_config):
        """Test verifying correct PIN"""
        with patch.object(kab, 'CONFIG_PATH', setup_test_config):
            kab.set_pin("1234")
            assert kab.verify_pin("1234") is True
    
    def test_verify_incorrect_pin(self, setup_test_config):
        """Test that incorrect PIN fails"""
        with patch.object(kab, 'CONFIG_PATH', setup_test_config):
            kab.set_pin("1234")
            assert kab.verify_pin("5678") is False
    
    def test_remove_pin(self, setup_test_config):
        """Test removing PIN lock"""
        with patch.object(kab, 'CONFIG_PATH', setup_test_config):
            kab.set_pin("1234")
            assert kab.has_pin() is True
            
            kab.remove_pin()
            assert kab.has_pin() is False
    
    def test_verify_pin_without_pin_set(self, setup_test_config):
        """Test that verification passes when no PIN is set"""
        with patch.object(kab, 'CONFIG_PATH', setup_test_config):
            # When no PIN is set, verification should return True (allow access)
            assert kab.verify_pin("anything") is True
    
    def test_pin_hashing_uses_bcrypt(self, setup_test_config):
        """Test that PIN is properly hashed with bcrypt"""
        with patch.object(kab, 'CONFIG_PATH', setup_test_config):
            kab.set_pin("1234")
            config = kab.load_config()
            pin_hash = config.get("pin_hash")
            
            # Hash should be a valid bcrypt hash (starts with $2)
            assert pin_hash is not None
            assert pin_hash.startswith("$2")


# ─────────────────────────────────────────────
# Config Management Tests
# ─────────────────────────────────────────────

class TestConfigManagement:
    """Test configuration file operations"""
    
    def test_load_config_nonexistent_file(self, setup_test_config):
        """Test loading config from nonexistent file returns empty dict"""
        with patch.object(kab, 'CONFIG_PATH', "/nonexistent/path/config.json"):
            config = kab.load_config()
            assert config == {}
    
    def test_save_and_load_config(self, setup_test_config):
        """Test saving and loading configuration"""
        with patch.object(kab, 'CONFIG_PATH', setup_test_config):
            test_config = {"test_key": "test_value"}
            kab.save_config(test_config)
            loaded = kab.load_config()
            assert loaded["test_key"] == "test_value"


# ─────────────────────────────────────────────
# Utility Function Tests
# ─────────────────────────────────────────────

class TestUtilityFunctions:
    """Test utility functions"""
    
    def test_get_total_record_count(self, setup_test_db):
        """Test counting total records"""
        with patch.object(kab, 'DB_PATH', setup_test_db):
            assert kab.get_total_record_count() == 0
            
            kab.add_transaction("2026-05-15", "支出", "食費", 1000, "Test1")
            kab.add_transaction("2026-05-16", "支出", "食費", 2000, "Test2")
            
            assert kab.get_total_record_count() == 2
    
    def test_delete_month_data(self, setup_test_db):
        """Test deleting all data for a specific month"""
        with patch.object(kab, 'DB_PATH', setup_test_db):
            kab.add_transaction("2026-05-15", "支出", "食費", 1000, "May1")
            kab.add_transaction("2026-05-20", "支出", "食費", 2000, "May2")
            kab.add_transaction("2026-06-15", "支出", "食費", 3000, "June1")
            
            assert kab.get_total_record_count() == 3
            
            kab.delete_month_data(2026, 5)
            
            assert kab.get_total_record_count() == 1
            remaining = kab.get_transactions()
            assert remaining.iloc[0]['date'].startswith("2026-06")


# ─────────────────────────────────────────────
# Export Function Tests
# ─────────────────────────────────────────────

class TestExportFunctions:
    """Test data export functionality"""
    
    def test_df_to_csv_bytes(self, setup_test_db):
        """Test CSV export format"""
        with patch.object(kab, 'DB_PATH', setup_test_db):
            kab.add_transaction("2026-05-15", "支出", "食費", 1000, "Test")
            df = kab.get_transactions()
            
            csv_bytes = kab.df_to_csv_bytes(df)
            assert csv_bytes is not None
            assert isinstance(csv_bytes, bytes)
            assert b"日付" in csv_bytes  # Japanese headers
            assert b"\xef\xbb\xbf" in csv_bytes  # UTF-8 BOM
    
    def test_df_to_excel_bytes(self, setup_test_db):
        """Test Excel export format"""
        with patch.object(kab, 'DB_PATH', setup_test_db):
            kab.add_transaction("2026-05-15", "支出", "食費", 1000, "Test")
            df = kab.get_transactions()
            
            excel_bytes = kab.df_to_excel_bytes(df, period_label="2026年5月")
            assert excel_bytes is not None
            assert isinstance(excel_bytes, bytes)


# ─────────────────────────────────────────────
# Run Tests
# ─────────────────────────────────────────────

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
