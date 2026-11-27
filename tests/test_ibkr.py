from datetime import date
from pathlib import Path

import pytest

from core import db
from core.parsers import detect, ibkr

FIX = Path(__file__).parent / "fixtures" / "ibkr_activity_synthetic.csv"


@pytest.fixture
def raw():
    return FIX.read_bytes()


def test_detect(raw):
    assert detect(raw, "statement.csv") is ibkr
    assert detect(b"Date,Amount\n2026-01-01,5\n", "other.csv") is None


def test_header_and_period(raw):
    st = ibkr.parse(raw)
    assert st.account_ref == "U0000000"
    assert st.base_currency == "EUR"
    assert st.period_start == date(2026, 1, 1)
    assert st.period_end == date(2026, 3, 31)


def test_positions(raw):
    st = ibkr.parse(raw)
    by = {p.symbol: p for p in st.positions}
    assert set(by) == {"EXS1", "AAPL", "SPX 19DEC26 5000 P"}   # Lot row not double counted
    assert by["EXS1"].instrument_key == "DE0005933931"
    assert by["AAPL"].cost_basis == 7500
    opt = by["SPX 19DEC26 5000 P"]
    assert opt.instrument_key == "IBKR:SPX 19DEC26 5000 P"
    assert opt.quantity == -2 and opt.multiplier == 100 and opt.market_value == -8000


def test_cash_and_fx(raw):
    st = ibkr.parse(raw)
    assert {c.currency: c.amount for c in st.cash} == {"EUR": 5000, "USD": 2000}
    assert st.fx_to_base["USD"] == 0.92


def test_transactions(raw):
    st = ibkr.parse(raw)
    types = sorted(t.tx_type for t in st.transactions)
    assert types == ["buy", "deposit", "dividend", "fx", "sell", "withholding_tax"]
    buy = next(t for t in st.transactions if t.tx_type == "buy")
    assert buy.quantity == 50 and buy.amount == -7501 and buy.instrument_key == "US0378331005"
    fx = next(t for t in st.transactions if t.tx_type == "fx")
    assert fx.quantity == -7000 and fx.amount == 7609     # thousands separators parsed


def test_checks_all_pass(raw):
    st = ibkr.parse(raw)
    status = {c.name: c.status for c in st.checks}
    assert status == {
        "Positions value (EUR)": "ok",
        "Positions value (USD)": "ok",
        "Cash (EUR)": "ok",
        "Net asset value (EUR)": "ok",
    }
    assert any("zero/missing cost basis" in w for w in st.warnings)


def test_check_catches_missing_position(raw):
    text = raw.decode().replace("Open Positions,Data,Summary,Stocks,USD,AAPL", "Open Positions,Data,Lot,Stocks,USD,AAPL")
    st = ibkr.parse(text.encode())
    assert not st.all_checks_ok


def test_save_and_dedupe(raw, tmp_path):
    engine = db.make_engine(f"sqlite:///{tmp_path/'t.db'}")
    st = ibkr.parse(raw)
    r = db.save_statement(engine, st, "s.csv", "a" * 64, ibkr.NAME)
    assert r["positions"] == 3 and r["transactions_new"] == 6
    with pytest.raises(db.DuplicateImport):
        db.save_statement(engine, st, "s.csv", "a" * 64, ibkr.NAME)
    # Same content, different file (e.g. overlapping period): no duplicate transactions.
    r2 = db.save_statement(engine, st, "s2.csv", "b" * 64, ibkr.NAME)
    assert r2["transactions_new"] == 0 and r2["transactions_skipped"] == 6
    pos = db.current_positions(engine)
    assert len(pos) == 3 and pos["import_id"].nunique() == 1
    assert len(db.all_transactions(engine)) == 6
