from pydantic import BaseModel, Field
from typing import Optional


class TransactionCreate(BaseModel):
    date: str
    type: str
    category: str
    amount: int = Field(gt=0)
    memo: str = ""
    account: str = "現金"


class TransactionOut(BaseModel):
    id: int
    date: str
    type: str
    category: str
    amount: int
    memo: str
    account: str
    created_at: str = ""


class CategoryCreate(BaseModel):
    name: str
    type: str


class BudgetSet(BaseModel):
    category: str
    amount: int = Field(gt=0)


class BudgetStatusItem(BaseModel):
    category: str
    budget: int
    actual: int
    usage_pct: float
    over_budget: bool


class RecurringCreate(BaseModel):
    type: str
    category: str
    amount: int = Field(gt=0)
    memo: str = ""
    day_of_month: int = Field(default=1, ge=1, le=28)


class RecurringToggle(BaseModel):
    active: bool


class AccountCreate(BaseModel):
    name: str
    type: str = "現金"
    initial_balance: int = 0
    note: str = ""


class AccountOut(BaseModel):
    id: int
    name: str
    type: str
    initial_balance: int
    note: str
    balance: int = 0


class MLPredictOut(BaseModel):
    predictions: list[dict]  # [{category, confidence}]


class DashboardOut(BaseModel):
    summary: dict
    budget_status: list
    account_balances: list
    top_expenses: list
    monthly_trend: list
    upcoming_recurring: list
