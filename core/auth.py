"""App password gate. Viewer restriction by email is configured separately in
Streamlit Community Cloud (app → Settings → Sharing) when the plan allows a
private app; this password applies either way."""
from __future__ import annotations

import hmac
import time

import streamlit as st

MAX_ATTEMPTS = 5
LOCKOUT_SECONDS = 300


def require_password() -> None:
    if st.session_state.get("authed"):
        return

    expected = st.secrets.get("app", {}).get("password")
    if not expected:
        st.error("No app password configured. Add `[app] password` to Streamlit Secrets.")
        st.stop()

    locked_until = st.session_state.get("locked_until", 0)
    if time.time() < locked_until:
        st.error("Too many attempts. Try again in a few minutes.")
        st.stop()

    st.title("Portfolio")
    with st.form("login"):
        pw = st.text_input("App password", type="password")
        ok = st.form_submit_button("Unlock")
    if ok:
        if hmac.compare_digest(pw.encode(), str(expected).encode()):
            st.session_state["authed"] = True
            st.session_state.pop("attempts", None)
            st.rerun()
        n = st.session_state.get("attempts", 0) + 1
        st.session_state["attempts"] = n
        if n >= MAX_ATTEMPTS:
            st.session_state["locked_until"] = time.time() + LOCKOUT_SECONDS
            st.session_state["attempts"] = 0
        st.error("Wrong password.")
    st.stop()
