import pytest
from run.seed_fx_reference import select_reference


@pytest.fixture
def cfg():
    return {"currencies": [
        {"code": "EUR", "name": "Euro"},
        {"code": "USD", "name": "US Dollar"},
        {"code": "PLN", "name": "Polish Zloty"},
    ], "currency_pairs": [
        {"base": "EUR", "quote": "PLN", "symbol": "EURPLN=X"},
        {"base": "USD", "quote": "PLN", "symbol": "USDPLN=X"},
        {"base": "EUR", "quote": "USD", "symbol": "EURUSD=X"},
    ]}


def test_select_multiple_pairs(cfg):
    currencies, pairs = select_reference(cfg, ["USDPLN=X", "EURUSD=X"])
    assert {c["code"] for c in currencies} == {"USD", "EUR", "PLN"}
    assert [p["symbol"] for p in pairs] == ["USDPLN=X", "EURUSD=X"]


def test_selection_deduplicated(cfg):
    _, pairs = select_reference(cfg, ["USDPLN=X", "USDPLN=X"])
    assert len(pairs) == 1


def test_missing_symbol_rejected(cfg):
    with pytest.raises(ValueError, match="Unknown symbols"):
        select_reference(cfg, ["ABC=X"])


def test_all_pairs(cfg):
    _, pairs = select_reference(cfg, None)
    assert len(pairs) == 3
