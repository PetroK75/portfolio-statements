"""Shared app state: DB engine and reporting currency."""
from __future__ import annotations

import streamlit as st

from core import db

REPORTING_CURRENCIES = ["EUR", "USD", "GBP"]


@st.cache_resource(show_spinner=False)
def _engine(url: str):
    return db.make_engine(url)


def engine():
    try:
        url = st.secrets["connections"]["db"]["url"]
    except Exception:
        st.error("No database configured. Add `[connections.db] url` to Streamlit Secrets.")
        st.stop()
    return _engine(url)


def reporting_currency() -> str:
    return st.session_state.get("ccy", "EUR")
