from __future__ import annotations

import pandas as pd
import streamlit as st

from core import db, prices
from core.portfolio import value_cash, value_positions
from core.state import engine, reporting_currency

rep = reporting_currency()
st.title("Portfolio")

eng = engine()
pos = db.current_positions(eng)
cash = db.current_cash(eng)
manual = db.read_table(eng, db.manual_items)
tmap = db.read_table(eng, db.ticker_map)
ovr = db.read_table(eng, db.overrides)

if pos.empty and cash.empty and manual.empty:
    st.info("No data yet. Import a statement or add manual items to get started.")
    st.stop()

held = set(pos["instrument_key"]) if not pos.empty else set()
mapped = tuple(sorted({t for k, t in zip(tmap.get("instrument_key", []), tmap.get("yahoo_ticker", []))
                       if k in held and isinstance(t, str) and t}))
qccy = prices.quote_currencies(mapped) if mapped else {}
ccys = set(pos.get("currency", [])) | set(cash.get("currency", [])) | set(manual.get("currency", []))
ccys |= {("GBP" if c == "GBp" else c) for c in qccy.values() if c}
closes = prices.closes(mapped + prices.needed_fx(ccys, rep))

vp = value_positions(pos, tmap, ovr, rep, closes, qccy)
vc = value_cash(cash, manual, rep, closes)


def _r(df, cols=("value", "day_change", "mtd_change", "cost", "unrealised")):
    """Round money columns to whole units for display."""
    out = df.copy()
    for c in cols:
        if c in out:
            out[c] = out[c].round(0)
    return out

total = (vp["value"].sum() if not vp.empty else 0) + (vc["value"].sum() if not vc.empty else 0)
day = (vp["day_change"].sum() if not vp.empty else 0) + (vc["day_change"].sum() if not vc.empty else 0)
mtd = (vp["mtd_change"].sum() if not vp.empty else 0) + (vc["mtd_change"].sum() if not vc.empty else 0)
unrl = vp["unrealised"].sum() if not vp.empty else 0

fmt = lambda x: f"{x:,.0f}"
c1, c2, c3, c4 = st.columns(4)
c1.metric(f"Net worth ({rep})", fmt(total))
c2.metric(f"Today ({rep})", fmt(day), f"{day / (total - day):+.2%}" if total - day else None)
c3.metric(f"Month to date ({rep})", fmt(mtd), f"{mtd / (total - mtd):+.2%}" if total - mtd else None)
c4.metric(f"Unrealised P&L ({rep})", fmt(unrl))

if not vp.empty:
    stale = vp[vp.price_source == "statement"]
    if len(stale):
        st.caption(f"{len(stale)} position(s) valued at statement price (no Yahoo ticker mapped) — "
                   "map them on the Mapping page for live prices.")
    if vp["value"].isna().any():
        st.warning("Some values couldn't be converted (missing FX). Check the Positions table.")

st.markdown("#### By bank")
parts = []
if not vp.empty:
    parts.append(vp[["bank", "value", "day_change"]])
if not vc.empty:
    parts.append(vc[["bank", "value", "day_change"]])
by_bank = pd.concat(parts).groupby("bank", as_index=False).sum().sort_values("value", ascending=False)
by_bank["share"] = by_bank["value"] / total if total else 0
st.dataframe(_r(by_bank), hide_index=True, width="stretch", column_config={
    "value": st.column_config.NumberColumn(format="localized"),
    "day_change": st.column_config.NumberColumn("today", format="localized"),
    "share": st.column_config.NumberColumn(format="percent"),
})

if not vp.empty:
    live = vp[vp.price_source == "live"].dropna(subset=["day_pct"])
    if not live.empty:
        st.markdown("#### Top movers today")
        m = live.reindex(live["day_pct"].abs().sort_values(ascending=False).index).head(8)
        st.dataframe(_r(m)[["symbol", "description", "ticker", "day_pct", "day_change", "value"]],
                     hide_index=True, width="stretch", column_config={
                         "day_pct": st.column_config.NumberColumn("today %", format="percent"),
                         "day_change": st.column_config.NumberColumn("today", format="localized"),
                         "value": st.column_config.NumberColumn(format="localized"),
                     })

    st.markdown("#### Positions")
    banks = st.multiselect("Banks", sorted(vp.bank.unique()), default=sorted(vp.bank.unique()))
    show = vp[vp.bank.isin(banks)].sort_values("value", ascending=False)
    st.dataframe(_r(show)[["bank", "symbol", "description", "ticker", "price_source", "quantity", "currency",
                       "price", "value", "day_change", "mtd_change", "cost", "unrealised", "cost_overridden", "as_of"]],
                 hide_index=True, width="stretch", column_config={
                     c: st.column_config.NumberColumn(format="localized")
                     for c in ("value", "day_change", "mtd_change", "cost", "unrealised")
                 })

if not vc.empty:
    st.markdown("#### Cash and other holdings")
    st.dataframe(_r(vc)[["bank", "account", "label", "kind", "currency", "amount", "value", "as_of"]],
                 hide_index=True, width="stretch",
                 column_config={"value": st.column_config.NumberColumn(format="localized"),
                                "amount": st.column_config.NumberColumn(format="localized")})

tx = db.all_transactions(eng)
if not tx.empty:
    with st.expander("Realised P&L (from statement transactions)"):
        r = tx.dropna(subset=["realized_pnl"])
        r = r[r.realized_pnl != 0]
        if r.empty:
            st.caption("No realised P&L in imported transactions.")
        else:
            st.dataframe(r.groupby(["bank", "currency"], as_index=False)["realized_pnl"].sum(),
                         hide_index=True, width="stretch")
            st.caption("In each trade's currency, as reported by the bank.")
