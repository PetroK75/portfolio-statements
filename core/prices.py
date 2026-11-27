"""Market data from Yahoo Finance. Only tickers and FX pairs are sent to Yahoo —
never quantities, values or account details."""
from __future__ import annotations

from typing import Dict, Iterable, Tuple

import pandas as pd
import streamlit as st


def fx_ticker(ccy: str, rep: str) -> str:
    return f"{ccy}{rep}=X"


@st.cache_data(ttl=900, show_spinner="Fetching prices…")
def closes(tickers: Tuple[str, ...]) -> pd.DataFrame:
    """Daily closes for ~3 months, columns = tickers, index = date."""
    if not tickers:
        return pd.DataFrame()
    import yfinance as yf
    df = yf.download(list(tickers), period="3mo", interval="1d", auto_adjust=False,
                     progress=False, group_by="column", threads=True)
    if df.empty:
        return pd.DataFrame()
    c = df["Close"]
    if isinstance(c, pd.Series):
        c = c.to_frame(tickers[0])
    c.index = pd.to_datetime(c.index).tz_localize(None).normalize()
    return c.sort_index().ffill()


@st.cache_data(ttl=86400, show_spinner=False)
def quote_currencies(tickers: Tuple[str, ...]) -> Dict[str, str]:
    import yfinance as yf
    out = {}
    for t in tickers:
        try:
            out[t] = yf.Ticker(t).fast_info["currency"]
        except Exception:
            out[t] = None
    return out


def needed_fx(currencies: Iterable[str], rep: str) -> Tuple[str, ...]:
    return tuple(sorted({fx_ticker(c, rep) for c in currencies if c and c.upper() != rep}))
