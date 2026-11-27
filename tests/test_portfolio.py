import numpy as np
import pandas as pd

from core.portfolio import value_cash, value_positions

IDX = pd.to_datetime(["2026-10-30", "2026-11-25", "2026-11-26"])


def _pos():
    return pd.DataFrame([
        dict(bank="IBKR", account_ref="U0", account_id=1, instrument_key="DE0005933931", symbol="EXS1",
             description="DAX", asset_class="Stocks", currency="EUR", quantity=100, multiplier=1,
             cost_basis=15000, close_price=170, market_value=17000, as_of="2026-09-30"),
        dict(bank="HL", account_ref="X", account_id=2, instrument_key="IE00B3F81R35", symbol="SLXX",
             description="Corp bond", asset_class="ETF", currency="GBP", quantity=10, multiplier=1,
             cost_basis=1000, close_price=100, market_value=1000, as_of="2026-09-30"),
        dict(bank="IBKR", account_ref="U0", account_id=1, instrument_key="IBKR:SPX P", symbol="SPX P",
             description="opt", asset_class="Options", currency="USD", quantity=-2, multiplier=100,
             cost_basis=0, close_price=40, market_value=-8000, as_of="2026-09-30"),
    ])


def test_live_statement_pence_fx_and_override():
    tmap = pd.DataFrame(dict(instrument_key=["DE0005933931", "IE00B3F81R35"],
                             yahoo_ticker=["EXS1.DE", "SLXX.L"], price_divisor=[1.0, 1.0]))
    ovr = pd.DataFrame(dict(id=[1], account_id=[np.nan], instrument_key=["IBKR:SPX P"],
                            field=["cost_basis"], value=[-5000.0], active=[1]))
    closes = pd.DataFrame({
        "EXS1.DE": [160, 175, 180],
        "SLXX.L": [11000, 11500, 12000],     # pence
        "GBPEUR=X": [1.15, 1.20, 1.20],
        "USDEUR=X": [0.90, 0.92, 0.92],
    }, index=IDX)
    v = value_positions(_pos(), tmap, ovr, "EUR", closes, {"EXS1.DE": "EUR", "SLXX.L": "GBp"}).set_index("symbol")
    assert v.loc["EXS1", "value"] == 18000 and v.loc["EXS1", "day_change"] == 500
    assert v.loc["EXS1", "mtd_change"] == 2000 and v.loc["EXS1", "unrealised"] == 3000
    assert np.isclose(v.loc["SLXX", "value"], 10 * 120 * 1.20)          # pence → GBP → EUR
    assert v.loc["SPX P", "price_source"] == "statement"
    assert np.isclose(v.loc["SPX P", "value"], -8000 * 0.92)
    assert v.loc["SPX P", "cost_overridden"] and np.isclose(v.loc["SPX P", "cost"], -5000 * 0.92)


def test_cash_and_manual():
    closes = pd.DataFrame({"USDGBP=X": [0.78, 0.79, 0.80]}, index=IDX)
    cash = pd.DataFrame([dict(bank="IBKR", account_ref="U0", currency="USD", amount=1000, as_of="2026-09-30")])
    manual = pd.DataFrame([dict(kind="illiquid", label="Flat", holder="", currency="GBP", value=200000,
                                cost_basis=150000, valued_on=None, active=1)])
    v = value_cash(cash, manual, "GBP", closes)
    assert np.isclose(v["value"].sum(), 800 + 200000)
    assert np.isclose(v["day_change"].sum(), 1000 * 0.01)
