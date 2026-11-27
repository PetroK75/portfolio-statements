"""Ticker mapping, cost-basis overrides and notes/tags — all keyed by
instrument_key (ISIN, or BANK:symbol when the statement has no ISIN)."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from core import db
from core.state import engine

st.title("Mapping, overrides & notes")
eng = engine()

pos = db.current_positions(eng)
tmap = db.read_table(eng, db.ticker_map)
nts = db.read_table(eng, db.notes)

# ---------------------------------------------------------------- tickers
st.markdown("#### Yahoo tickers")
st.caption("e.g. EXIC.DE, SLXX.L. London lines quoted in pence are converted automatically. "
           "Unmapped positions are valued at their statement price.")
if pos.empty:
    instruments = pd.DataFrame(columns=["instrument_key", "description", "banks"])
else:
    instruments = (pos.groupby("instrument_key", as_index=False)
                   .agg(description=("description", "first"), symbol=("symbol", "first"),
                        banks=("bank", lambda s: ", ".join(sorted(set(s))))))
grid = instruments.merge(tmap[["instrument_key", "yahoo_ticker"]] if not tmap.empty
                         else pd.DataFrame(columns=["instrument_key", "yahoo_ticker"]),
                         on="instrument_key", how="left")
edited = st.data_editor(
    grid, hide_index=True, width="stretch", key="tick_editor",
    disabled=["instrument_key", "description", "symbol", "banks"],
    column_config={"yahoo_ticker": st.column_config.TextColumn("Yahoo ticker")},
)
if st.button("Save tickers"):
    before = dict(zip(grid.instrument_key, grid.yahoo_ticker))
    n = 0
    for r in edited.itertuples():
        new = (r.yahoo_ticker or "").strip() if isinstance(r.yahoo_ticker, str) else ""
        old = before.get(r.instrument_key)
        old = old if isinstance(old, str) else ""
        if new != old:
            db.upsert_ticker(eng, r.instrument_key, new or None, r.description)
            n += 1
    st.cache_data.clear()
    st.success(f"Saved {n} change(s).")

# ---------------------------------------------------------------- overrides
st.markdown("#### Cost-basis overrides")
st.caption("Total cost in the instrument's currency. Use for positions the bank reports at zero or wrong cost "
           "(e.g. SPX option structures). Leave account_id empty to apply to all accounts.")
ovr = db.read_table(eng, db.overrides)
if ovr.empty:
    ovr = pd.DataFrame(columns=[c.name for c in db.overrides.columns])
keys = sorted(set(instruments.instrument_key)) or [""]
ovr_view = ovr[["id", "account_id", "instrument_key", "field", "value", "currency", "reason", "active"]].copy()
ovr_view["active"] = ovr_view["active"].astype(bool) if len(ovr_view) else ovr_view["active"]
ovr_edit = st.data_editor(
    ovr_view, num_rows="dynamic", hide_index=True, width="stretch", key="ovr_editor",
    disabled=["id"],
    column_config={
        "instrument_key": st.column_config.SelectboxColumn(options=keys, required=True),
        "field": st.column_config.SelectboxColumn(options=["cost_basis"], default="cost_basis", required=True),
        "value": st.column_config.NumberColumn(required=True, format="%.2f"),
        "active": st.column_config.CheckboxColumn(default=True),
    },
)
if st.button("Save overrides"):
    df = ovr_edit.copy()
    df["field"] = df["field"].fillna("cost_basis")
    df["active"] = df["active"].fillna(True).astype(bool)
    db.replace_rows(eng, db.overrides, df)
    st.cache_data.clear()
    st.success("Overrides saved.")

# ---------------------------------------------------------------- notes
st.markdown("#### Notes & tags")
ngrid = instruments[["instrument_key", "description"]].merge(
    nts[["instrument_key", "note", "tags"]] if not nts.empty
    else pd.DataFrame(columns=["instrument_key", "note", "tags"]),
    on="instrument_key", how="left")
nedit = st.data_editor(ngrid, hide_index=True, width="stretch", key="notes_editor",
                       disabled=["instrument_key", "description"],
                       column_config={"tags": st.column_config.TextColumn(help="comma-separated")})
if st.button("Save notes"):
    for r in nedit.itertuples():
        note = r.note if isinstance(r.note, str) else ""
        tags = r.tags if isinstance(r.tags, str) else ""
        if note or tags or r.instrument_key in set(nts.get("instrument_key", [])):
            db.upsert_note(eng, r.instrument_key, note, tags)
    st.success("Notes saved.")
