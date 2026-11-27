"""Interactive Brokers — Activity Statement, CSV download.

IBKR's activity CSV is many tables stacked in one file. Every row starts with
the section name and a row kind:

    Open Positions,Header,DataDiscriminator,Asset Category,Currency,Symbol,...
    Open Positions,Data,Summary,Stocks,EUR,EXS1,...
    Open Positions,Total,,Stocks,EUR,,...

A section can repeat its Header row with different columns (e.g. Trades for
stocks vs forex), so columns are always taken from the most recent Header of
that section. Columns are looked up by name, never by position.

Not yet handled: Flex Query CSV (a different, flat layout).
"""
from __future__ import annotations

import csv
import io
import re
from collections import defaultdict
from datetime import date, datetime
from typing import Dict, List, Optional

from core.models import (
    CashBalance, Check, ParsedStatement, Position, Transaction, instrument_key,
)

BANK = "IBKR"
NAME = "ibkr_activity_csv"


# --------------------------------------------------------------------------- #
# Low-level helpers
# --------------------------------------------------------------------------- #
def _num(v) -> Optional[float]:
    if v is None:
        return None
    s = str(v).strip().replace(",", "")
    if s in ("", "--", "-", "N/A"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _date(v) -> Optional[date]:
    if not v:
        return None
    s = str(v).strip().split(",")[0].split(" ")[0].split(";")[0]
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%m/%d/%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


def _period(v: str):
    """'January 1, 2026 - March 31, 2026' or 'March 31, 2026' -> (start, end)."""
    parts = [p.strip() for p in re.split(r"\s+-\s+", v or "") if p.strip()]
    out = []
    for p in parts:
        for fmt in ("%B %d, %Y", "%b %d, %Y", "%Y-%m-%d"):
            try:
                out.append(datetime.strptime(p, fmt).date())
                break
            except ValueError:
                pass
    if not out:
        return None, None
    return (out[0], out[-1]) if len(out) > 1 else (out[0], out[0])


def read_sections(text: str) -> Dict[str, List[dict]]:
    """Return {section: [ {'_kind': Data|Total|SubTotal, col: value, ...}, ... ]}."""
    sections: Dict[str, List[dict]] = defaultdict(list)
    headers: Dict[str, List[str]] = {}
    for row in csv.reader(io.StringIO(text)):
        if len(row) < 2:
            continue
        sec, kind = row[0].strip().lstrip("﻿"), row[1].strip()
        if kind == "Header":
            headers[sec] = [h.strip() for h in row[2:]]
            continue
        cols = headers.get(sec)
        if not cols:
            continue
        rec = {"_kind": kind}
        for i, h in enumerate(cols):
            rec[h] = row[2 + i].strip() if 2 + i < len(row) else ""
        sections[sec].append(rec)
    return sections


def looks_like(text: str) -> bool:
    head = text[:4000]
    return ("Statement,Header,Field Name" in head) or (
        "Statement,Data,BrokerName,Interactive Brokers" in head
    )


def _kv(rows: List[dict]) -> Dict[str, str]:
    return {r.get("Field Name", ""): r.get("Field Value", "") for r in rows if r["_kind"] == "Data"}


def _data(rows: List[dict]) -> List[dict]:
    """Data rows, excluding embedded total rows (Currency/first col starting 'Total')."""
    out = []
    for r in rows:
        if r["_kind"] != "Data":
            continue
        first_vals = [str(v) for k, v in r.items() if k != "_kind"][:2]
        if any(v.startswith("Total") for v in first_vals):
            continue
        out.append(r)
    return out


# --------------------------------------------------------------------------- #
# Parser
# --------------------------------------------------------------------------- #
def parse(raw: bytes, file_name: str = "") -> ParsedStatement:
    text = raw.decode("utf-8-sig", errors="replace")
    S = read_sections(text)
    warnings: List[str] = []

    stmt = _kv(S.get("Statement", []))
    acct = _kv(S.get("Account Information", []))
    start, end = _period(stmt.get("Period", ""))
    if end is None:
        end = _date(stmt.get("WhenGenerated")) or date.today()
        warnings.append("Statement period not found; using generation date.")
    account_ref = acct.get("Account", "").split(" ")[0] or "unknown"
    base = (acct.get("Base Currency") or "EUR").upper()

    # --- Instrument reference data: symbol -> ISIN / description / multiplier
    ref: Dict[str, dict] = {}
    for r in _data(S.get("Financial Instrument Information", [])):
        sym = r.get("Symbol", "")
        ref[sym] = {
            "isin": r.get("Security ID") or None,
            "description": r.get("Description", ""),
            "multiplier": _num(r.get("Multiplier")) or 1.0,
            "conid": r.get("Conid"),
        }

    def key_for(sym: str) -> str:
        return instrument_key(BANK, (ref.get(sym) or {}).get("isin"), sym)

    # --- FX rates the statement used (1 unit ccy = rate base)
    fx = {base: 1.0}
    for r in _data(S.get("Base Currency Exchange Rate", [])):
        rate = _num(r.get("Rate"))
        if r.get("Currency") and rate:
            fx[r["Currency"].upper()] = rate

    # --- Positions
    positions: List[Position] = []
    stmt_pos_totals: Dict[str, float] = defaultdict(float)  # currency -> statement total value
    for r in S.get("Open Positions", []):
        disc = r.get("DataDiscriminator", "")
        if r["_kind"] == "Total" and r.get("Currency") and not r.get("Currency", "").startswith("Total"):
            v = _num(r.get("Value"))
            if v is not None:
                stmt_pos_totals[r["Currency"].upper()] += v
            continue
        if r["_kind"] != "Data" or disc not in ("Summary", ""):
            continue  # skip Lot rows: Summary already aggregates them
        sym = r.get("Symbol", "")
        if not sym:
            continue
        info = ref.get(sym, {})
        qty = _num(r.get("Quantity")) or 0.0
        positions.append(Position(
            instrument_key=key_for(sym),
            symbol=sym,
            description=info.get("description") or sym,
            asset_class=r.get("Asset Category", ""),
            currency=r.get("Currency", base).upper(),
            quantity=qty,
            isin=info.get("isin"),
            multiplier=_num(r.get("Mult")) or info.get("multiplier") or 1.0,
            cost_basis=_num(r.get("Cost Basis")),
            close_price=_num(r.get("Close Price")),
            market_value=_num(r.get("Value")),
        ))

    # --- Cash: 'Ending Cash' per currency from the Cash Report
    cash: List[CashBalance] = []
    stmt_cash_base: Optional[float] = None
    for r in S.get("Cash Report", []):
        if r["_kind"] != "Data" or r.get("Currency Summary") != "Ending Cash":
            continue
        ccy = r.get("Currency", "")
        amt = _num(r.get("Total"))
        if amt is None:
            continue
        if ccy == "Base Currency Summary":
            stmt_cash_base = amt
        elif re.fullmatch(r"[A-Z]{3}", ccy):
            cash.append(CashBalance(ccy, amt))

    # --- Transactions
    txs: List[Transaction] = []
    for r in S.get("Trades", []):
        if r["_kind"] != "Data" or r.get("DataDiscriminator") not in ("Order", "Trade"):
            continue
        d = _date(r.get("Date/Time"))
        if d is None:
            continue
        cat = r.get("Asset Category", "")
        sym = r.get("Symbol", "")
        qty = _num(r.get("Quantity")) or 0.0
        proceeds = _num(r.get("Proceeds")) or 0.0
        fee = _num(r.get("Comm/Fee")) or _num(r.get("Comm in EUR")) or 0.0
        if cat == "Forex":
            txs.append(Transaction(
                trade_date=d, tx_type="fx", currency=r.get("Currency", base).upper(),
                amount=proceeds, symbol=sym, description=f"FX {sym}",
                quantity=qty, price=_num(r.get("T. Price")), fees=fee,
            ))
            continue
        txs.append(Transaction(
            trade_date=d, tx_type="buy" if qty > 0 else "sell",
            currency=r.get("Currency", base).upper(),
            amount=proceeds + fee,
            instrument_key=key_for(sym), symbol=sym,
            description=(ref.get(sym) or {}).get("description", sym),
            quantity=qty, price=_num(r.get("T. Price")), fees=fee,
            realized_pnl=_num(r.get("Realized P/L")),
        ))

    simple_sections = [
        ("Dividends", "dividend", "Date"),
        ("Withholding Tax", "withholding_tax", "Date"),
        ("Interest", "interest", "Date"),
        ("Fees", "fee", "Date"),
        ("Deposits & Withdrawals", None, "Settle Date"),
    ]
    for sec, tx_type, date_col in simple_sections:
        for r in _data(S.get(sec, [])):
            d = _date(r.get(date_col))
            amt = _num(r.get("Amount"))
            if d is None or amt is None:
                continue
            desc = r.get("Description", "")
            t = tx_type or ("deposit" if amt > 0 else "withdrawal")
            sym = desc.split("(")[0].strip() if t in ("dividend", "withholding_tax") else None
            txs.append(Transaction(
                trade_date=d, tx_type=t, currency=r.get("Currency", base).upper(),
                amount=amt, description=desc, symbol=sym,
                instrument_key=key_for(sym) if sym else None,
                settle_date=d if date_col == "Settle Date" else None,
            ))

    # --- Statement totals: Net Asset Value section
    totals: Dict[str, float] = {}
    for r in S.get("Net Asset Value", []):
        if r["_kind"] != "Data":
            continue
        cls = r.get("Asset Class", "")
        cur = _num(r.get("Current Total"))
        if cls and cur is not None:
            totals[f"NAV {cls}"] = cur

    # --- Checks
    checks: List[Check] = []
    for ccy, expected in sorted(stmt_pos_totals.items()):
        actual = sum(p.market_value or 0 for p in positions if p.currency == ccy)
        checks.append(Check(f"Positions value ({ccy})", expected, actual, tolerance=1.0,
                            note="Sum of parsed positions vs statement's Open Positions totals"))
    if stmt_cash_base is not None:
        parsed_cash_base = None
        if all(c.currency in fx for c in cash):
            parsed_cash_base = sum(c.amount * fx[c.currency] for c in cash)
        checks.append(Check(f"Cash ({base})", stmt_cash_base, parsed_cash_base, tolerance=2.0,
                            note="Per-currency ending cash converted at statement FX vs base-currency summary"))
    nav_total = totals.get("NAV Total")
    if nav_total is not None:
        missing = {p.currency for p in positions if p.currency not in fx} | {c.currency for c in cash if c.currency not in fx}
        if missing:
            checks.append(Check(f"Net asset value ({base})", nav_total, None,
                                note=f"No statement FX rate for {', '.join(sorted(missing))}"))
        else:
            parsed = sum((p.market_value or 0) * fx[p.currency] for p in positions)
            parsed += sum(c.amount * fx[c.currency] for c in cash)
            # Accruals (interest, dividends) are part of NAV but not positions/cash.
            accr = sum(v for k, v in totals.items() if "Accrual" in k)
            checks.append(Check(f"Net asset value ({base})", nav_total, parsed + accr,
                                tolerance=max(5.0, abs(nav_total) * 0.0005),
                                note="Positions + cash + accruals at statement FX vs NAV total"))

    rt = [t for t in txs if t.tx_type in ("buy", "sell")]
    if not positions:
        warnings.append("No Open Positions section found — is this a full Activity Statement?")
    if not S.get("Trades"):
        warnings.append("No Trades section — holdings can't be rebuilt from this statement's history.")
    for p in positions:
        if p.cost_basis in (None, 0) and p.quantity:
            warnings.append(f"{p.symbol}: zero/missing cost basis — consider an override.")

    return ParsedStatement(
        bank=BANK, account_ref=account_ref, base_currency=base,
        period_start=start, period_end=end,
        positions=positions, cash=cash, transactions=txs,
        checks=checks, warnings=warnings, totals=totals, fx_to_base=fx,
    )
