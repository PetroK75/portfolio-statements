"""Bank-agnostic data model that every statement parser produces.

Parsers turn a raw statement into a `ParsedStatement`. Nothing here touches the
database or the network, so a parsed statement can be shown, checked and thrown
away without anything being saved.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, asdict
from datetime import date
from typing import Optional, List, Dict

BANKS = ["IBKR", "Saxo", "HL", "SQ", "Other"]

# Transaction types used across all banks.
TX_TYPES = [
    "buy", "sell", "dividend", "withholding_tax", "interest", "fee",
    "deposit", "withdrawal", "fx", "corporate_action", "other",
]


def instrument_key(bank: str, isin: Optional[str], symbol: Optional[str]) -> str:
    """Stable identity for an instrument across statements.

    ISIN when the statement gives one (the same ISIN at two banks is the same
    instrument); otherwise the bank's own symbol, namespaced by bank.
    """
    if isin and len(isin.strip()) == 12:
        return isin.strip().upper()
    return f"{bank}:{(symbol or '').strip()}"


@dataclass
class Position:
    instrument_key: str
    symbol: str
    description: str
    asset_class: str
    currency: str
    quantity: float
    isin: Optional[str] = None
    multiplier: float = 1.0
    cost_basis: Optional[float] = None      # total, in instrument currency
    close_price: Optional[float] = None     # statement price, instrument currency
    market_value: Optional[float] = None    # statement value, instrument currency


@dataclass
class Transaction:
    trade_date: date
    tx_type: str
    currency: str
    amount: float                           # cash effect, signed, instrument currency
    instrument_key: Optional[str] = None
    symbol: Optional[str] = None
    description: str = ""
    quantity: Optional[float] = None        # signed: + bought, - sold
    price: Optional[float] = None
    fees: float = 0.0
    realized_pnl: Optional[float] = None
    settle_date: Optional[date] = None

    def fingerprint(self, account_ref: str) -> str:
        """Deterministic id so re-importing an overlapping statement does not
        duplicate transactions."""
        raw = "|".join(str(x) for x in (
            account_ref, self.trade_date, self.tx_type, self.currency,
            round(self.amount, 4), self.instrument_key, self.description,
            self.quantity, self.price,
        ))
        return hashlib.sha256(raw.encode()).hexdigest()[:32]


@dataclass
class CashBalance:
    currency: str
    amount: float


@dataclass
class Check:
    """One reconciliation between what we parsed and what the statement says."""
    name: str
    expected: Optional[float]
    actual: Optional[float]
    tolerance: float = 1.0
    note: str = ""

    @property
    def status(self) -> str:
        if self.expected is None or self.actual is None:
            return "n/a"
        return "ok" if abs(self.expected - self.actual) <= self.tolerance else "mismatch"

    @property
    def difference(self) -> Optional[float]:
        if self.expected is None or self.actual is None:
            return None
        return self.actual - self.expected


@dataclass
class ParsedStatement:
    bank: str
    account_ref: str
    base_currency: str
    period_start: Optional[date]
    period_end: date
    positions: List[Position] = field(default_factory=list)
    cash: List[CashBalance] = field(default_factory=list)
    transactions: List[Transaction] = field(default_factory=list)
    checks: List[Check] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    # Statement-reported totals, kept for audit (e.g. NAV by asset class).
    totals: Dict[str, float] = field(default_factory=dict)
    # FX rates the statement itself used: 1 unit of currency = rate base.
    fx_to_base: Dict[str, float] = field(default_factory=dict)

    @property
    def all_checks_ok(self) -> bool:
        return all(c.status != "mismatch" for c in self.checks)

    def to_dict(self) -> dict:
        return asdict(self)
