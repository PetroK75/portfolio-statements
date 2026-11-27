"""Upload → check → confirm. The file is read in memory only; nothing is saved
until the confirm button is pressed, and the raw file is never stored."""
from __future__ import annotations

import hashlib

import pandas as pd
import streamlit as st

from core import db
from core.parsers import detect
from core.state import engine

st.title("Import statement")
st.caption("Statements are parsed by local code in this app. Nothing is sent to an external service.")

up = st.file_uploader("Statement file", type=["csv", "txt", "pdf", "xlsx", "xls"])

if up is None:
    st.session_state.pop("parsed", None)
else:
    raw = up.getvalue()
    sha = hashlib.sha256(raw).hexdigest()
    cached = st.session_state.get("parsed")
    if not cached or cached["sha"] != sha:
        parser = detect(raw, up.name)
        if parser is None:
            st.error("Couldn't recognise this file. Supported so far: IBKR Activity Statement (CSV).")
            st.stop()
        try:
            parsed = parser.parse(raw, up.name)
        except Exception as e:  # show, don't crash; never echo file contents
            st.error(f"Parser {parser.NAME} failed: {type(e).__name__}: {e}")
            st.stop()
        st.session_state["parsed"] = {"sha": sha, "name": up.name, "parser": parser.NAME, "st": parsed}
        cached = st.session_state["parsed"]

    ps = cached["st"]
    st.subheader(f"{ps.bank} · {ps.account_ref} · {ps.period_start} → {ps.period_end}")
    c1, c2, c3 = st.columns(3)
    c1.metric("Positions", len(ps.positions))
    c2.metric("Cash currencies", len(ps.cash))
    c3.metric("Transactions", len(ps.transactions))

    st.markdown("#### Checks against statement totals")
    checks = pd.DataFrame([{
        "check": c.name, "statement": c.expected, "parsed": c.actual,
        "difference": c.difference, "status": c.status, "how": c.note,
    } for c in ps.checks])
    if checks.empty:
        st.warning("This statement has no totals to check against.")
    else:
        st.dataframe(checks, hide_index=True, width="stretch",
                     column_config={k: st.column_config.NumberColumn(format="%.2f")
                                    for k in ("statement", "parsed", "difference")})
    for w in ps.warnings:
        st.warning(w)

    tab_p, tab_c, tab_t = st.tabs(["Positions", "Cash", "Transactions"])
    with tab_p:
        st.dataframe(pd.DataFrame([p.__dict__ for p in ps.positions]), hide_index=True, width="stretch")
    with tab_c:
        st.dataframe(pd.DataFrame([c.__dict__ for c in ps.cash]), hide_index=True, width="stretch")
    with tab_t:
        st.dataframe(pd.DataFrame([t.__dict__ for t in ps.transactions]), hide_index=True, width="stretch")

    st.divider()
    ok = ps.all_checks_ok
    if not ok:
        st.error("Some checks don't match the statement. Review before saving.")
    force = st.checkbox("Save anyway despite mismatches", value=False) if not ok else True
    if st.button("Confirm and save", type="primary", disabled=not force):
        try:
            res = db.save_statement(engine(), ps, cached["name"], cached["sha"], cached["parser"])
        except db.DuplicateImport:
            st.info("This exact file was already imported. Nothing changed.")
        else:
            st.success(
                f"Saved: {res['positions']} positions, {res['cash']} cash balances, "
                f"{res['transactions_new']} new transactions "
                f"({res['transactions_skipped']} already known)."
            )
            st.session_state.pop("parsed", None)
            st.cache_data.clear()

st.divider()
st.markdown("#### Import history")
hist = db.list_imports(engine())
if hist.empty:
    st.caption("Nothing imported yet.")
else:
    st.dataframe(hist, hide_index=True, width="stretch")
    with st.expander("Undo an import"):
        iid = st.selectbox("Import id", hist["id"].tolist())
        if st.button("Delete this import and its positions/transactions"):
            db.delete_import(engine(), int(iid))
            st.cache_data.clear()
            st.rerun()
