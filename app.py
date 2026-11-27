import streamlit as st

from core.auth import require_password
from core.state import REPORTING_CURRENCIES

st.set_page_config(page_title="Portfolio", page_icon="📈", layout="wide")
require_password()

with st.sidebar:
    st.session_state.setdefault("ccy", "EUR")
    st.selectbox("Reporting currency", REPORTING_CURRENCIES, key="ccy")
    if st.button("Refresh prices"):
        st.cache_data.clear()
    if st.button("Lock"):
        st.session_state.clear()
        st.rerun()

pg = st.navigation([
    st.Page("views/dashboard.py", title="Dashboard", icon="📊", default=True),
    st.Page("views/import_statement.py", title="Import statement", icon="📥"),
    st.Page("views/mapping.py", title="Mapping & overrides", icon="🔗"),
    st.Page("views/manual.py", title="Other holdings", icon="🏠"),
])
pg.run()
