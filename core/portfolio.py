"""Turn stored positions + cash + manual items into a valued portfolio in the
reporting currency. Pure functions over DataFrames so it is testable offline.

Price precedence per position: live Yahoo close (if mapped) → statement close.
Cost precedence: active override → statement cost basis.
Day / month-to-date change compare today's value with the previous close and
the last close of the previous month, using the same quantities (trades made
since are not adjusted for).
"""
from __future__ import annotations

from datetime import date
from typing import Dict, Optional

import numpy as np
import pandas as pd

from core.prices import fx_ticker


def _series_points(s: pd.Series, today: Optional[pd.Timestamp] = None):
    """(last, previous, last-of-previous-month) from a daily close series."""
    s = s.dropna()
    if s.empty:
        return (np.nan, np.nan, np.nan)
    today = today or s.index[-1]
    last = s.iloc[-1]
    prev = s.iloc[-2] if len(s) > 1 else last
    month_start = pd.Timestamp(today.year, today.month, 1)
    before = s[s.index < month_start]
    mstart = before.iloc[-1] if not before.empty else s.iloc[0]
    return (last, prev, mstart)


def fx_points(ccy: str, rep: str, closes: pd.DataFrame):
    if not ccy or ccy.upper() == rep:
        return (1.0, 1.0, 1.0)
    t = fx_ticker(ccy.upper(), rep)
    if t in closes.columns:
        return _series_points(closes[t])
    return (np.nan, np.nan, np.nan)


def value_positions(pos: pd.DataFrame, tmap: pd.DataFrame, ovr: pd.DataFrame, rep: str,
                    closes: pd.DataFrame, quote_ccy: Dict[str, str]) -> pd.DataFrame:
    if pos.empty:
        return pos
    pos = pos.copy()
    tick = dict(zip(tmap.get("instrument_key", []), tmap.get("yahoo_ticker", [])))
    div = dict(zip(tmap.get("instrument_key", []), tmap.get("price_divisor", [])))

    # Overrides: account-specific beats global; latest wins.
    cost_ovr = {}
    if not ovr.empty:
        o = ovr[ovr["active"].astype(bool) & (ovr["field"] == "cost_basis")].sort_values("id")
        for r in o.itertuples():
            cost_ovr[(r.account_id if pd.notna(r.account_id) else None, r.instrument_key)] = r.value

    rows = []
    for r in pos.itertuples():
        t = tick.get(r.instrument_key) or None
        mult = r.multiplier or 1.0
        src = "statement"
        ccy = r.currency
        if t and t in closes.columns and closes[t].notna().any():
            qc = quote_ccy.get(t) or r.currency
            d = float(div.get(r.instrument_key) or 1.0)
            if qc == "GBp":           # London pence quotes → GBP
                qc, d = "GBP", 100.0
            p_last, p_prev, p_m = (x / d for x in _series_points(closes[t]))
            ccy, src = qc.upper(), "live"
        else:
            p = r.close_price if pd.notna(r.close_price) else (
                (r.market_value / (r.quantity * mult)) if r.quantity else 0.0)
            p_last = p_prev = p_m = p

        f_last, f_prev, f_m = fx_points(ccy, rep, closes)
        units = r.quantity * mult
        v_now = units * p_last * f_last
        v_prev = units * p_prev * f_prev
        v_m = units * p_m * f_m

        cost_local = cost_ovr.get((r.account_id, r.instrument_key),
                                  cost_ovr.get((None, r.instrument_key), r.cost_basis))
        cf = fx_points(r.currency, rep, closes)[0]   # cost at today's FX (simplification)
        cost = cost_local * cf if cost_local is not None and pd.notna(cost_local) else np.nan

        rows.append(dict(
            bank=r.bank, account=r.account_ref, instrument_key=r.instrument_key,
            symbol=r.symbol, description=r.description, asset_class=r.asset_class,
            ticker=t, price_source=src, currency=ccy, quantity=r.quantity,
            price=p_last, value=v_now, day_change=v_now - v_prev,
            day_pct=(p_last / p_prev - 1) if p_prev else np.nan,
            mtd_change=v_now - v_m, cost=cost,
            unrealised=v_now - cost if pd.notna(cost) else np.nan,
            cost_overridden=(r.account_id, r.instrument_key) in cost_ovr or (None, r.instrument_key) in cost_ovr,
            as_of=r.as_of,
        ))
    return pd.DataFrame(rows)


def value_cash(cash: pd.DataFrame, manual: pd.DataFrame, rep: str, closes: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for r in (cash.itertuples() if not cash.empty else []):
        f = fx_points(r.currency, rep, closes)
        rows.append(dict(bank=r.bank, account=r.account_ref, label=f"Cash {r.currency}",
                         kind="cash", currency=r.currency, amount=r.amount,
                         value=r.amount * f[0], day_change=r.amount * (f[0] - f[1]),
                         mtd_change=r.amount * (f[0] - f[2]), cost=np.nan, as_of=r.as_of))
    if not manual.empty:
        for r in manual[manual["active"].astype(bool)].itertuples():
            f = fx_points(r.currency, rep, closes)
            cost = r.cost_basis * f[0] if pd.notna(r.cost_basis) else np.nan
            rows.append(dict(bank="Other", account=r.holder or "", label=r.label,
                             kind=r.kind, currency=r.currency, amount=r.value,
                             value=r.value * f[0], day_change=r.value * (f[0] - f[1]),
                             mtd_change=r.value * (f[0] - f[2]), cost=cost, as_of=r.valued_on))
    return pd.DataFrame(rows)
