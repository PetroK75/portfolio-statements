"""'Other' bucket: cash outside the four banks, and illiquid holdings, with
manually entered valuations."""
from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from core import db
from core.state import engine

st.title("Other cash & illiquid holdings")
eng = engine()

df = db.read_table(eng, db.manual_items)
if df.empty:
    df = pd.DataFrame(columns=[c.name for c in db.manual_items.columns])
df["active"] = df["active"].astype(bool) if len(df) else df["active"]
df["valued_on"] = pd.to_datetime(df["valued_on"]).dt.date if len(df) else df["valued_on"]

edited = st.data_editor(
    df, num_rows="dynamic", hide_index=True, width="stretch", key="manual_editor",
    disabled=["id"],
    column_config={
        "kind": st.column_config.SelectboxColumn(options=["cash", "illiquid"], default="cash", required=True),
        "label": st.column_config.TextColumn(required=True),
        "currency": st.column_config.SelectboxColumn(options=["EUR", "USD", "GBP", "CHF"], default="EUR", required=True),
        "value": st.column_config.NumberColumn(required=True, format="%.2f"),
        "cost_basis": st.column_config.NumberColumn(format="%.2f"),
        "valued_on": st.column_config.DateColumn(default=date.today()),
        "active": st.column_config.CheckboxColumn(default=True),
    },
)
if st.button("Save", type="primary"):
    e = edited.copy()
    e["active"] = e["active"].fillna(True).astype(bool)
    db.replace_rows(eng, db.manual_items, e)
    st.cache_data.clear()
    st.success("Saved.")
