from datetime import date
import pandas as pd
import pytest

from ingestion.derivatives import validate_contracts, validate_prices


def contract(**changes):
    row = dict(
        contract_id="FW20_TEST_DEC26",
        symbol="FAKE_DEC26",
        instrument_type="FUTURE",
        underlying_symbol="WIG20",
        expiry_date="2026-12-18",
        currency="PLN",
        multiplier=20,
        strike=None,
        option_side=None,
        source="TEST_FIXTURE",
    )
    row.update(changes)
    return pd.DataFrame([row])


def price(**changes):
    row = dict(
        contract_id="FW20_TEST_DEC26",
        date="2026-10-23",
        open=3000,
        high=3050,
        low=2990,
        close=3020,
        volume=100,
        open_interest=500,
        settlement=3020,
        source="TEST_FIXTURE",
    )
    row.update(changes)
    return pd.DataFrame([row])


def test_valid_future():
    assert validate_contracts(contract()).iloc[0].expiry_date == date(2026, 12, 18)


def test_option_requires_strike_and_side():
    with pytest.raises(ValueError):
        validate_contracts(contract(instrument_type="OPTION"))


def test_valid_option():
    assert (
        validate_contracts(contract(instrument_type="OPTION", strike=3000, option_side="CALL"))
        .iloc[0]
        .option_side
        == "CALL"
    )


def test_duplicate_contract():
    with pytest.raises(ValueError):
        validate_contracts(pd.concat([contract(), contract()]))


def test_valid_price():
    assert validate_prices(price(), {"FW20_TEST_DEC26"}).iloc[0].close == 3020


def test_reject_unknown_contract():
    with pytest.raises(ValueError):
        validate_prices(price(), {"OTHER"})


def test_reject_duplicate_date():
    with pytest.raises(ValueError):
        validate_prices(pd.concat([price(), price()]), {"FW20_TEST_DEC26"})


def test_reject_negative_volume():
    with pytest.raises(ValueError):
        validate_prices(price(volume=-1), {"FW20_TEST_DEC26"})
