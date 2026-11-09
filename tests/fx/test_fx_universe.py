from run.extend_fx_universe import merge_universe


def test_merge_idempotent_and_preserves_existing():
    current = {
        "history": {"start_date": "2020-01-01"},
        "currencies": [{"code": "USD", "name": "US Dollar", "minor_unit": 2}],
        "currency_pairs": [],
    }
    expansion = {
        "currencies": [{"code": "CAD", "name": "Canadian Dollar", "minor_unit": 2}],
        "backfill_groups": {"usd": ["USDCAD=X"]},
    }
    first, counts = merge_universe(current, expansion)
    assert counts == {"currencies": 1, "pairs": 1}
    assert current["currency_pairs"] == []
    assert first["history"] == current["history"]
    second, counts = merge_universe(first, expansion)
    assert counts == {"currencies": 0, "pairs": 0}
    assert second == first


def test_existing_pair_preserved():
    current = {
        "currencies": [
            {"code": "USD", "name": "US Dollar", "minor_unit": 2},
            {"code": "CAD", "name": "Canadian Dollar", "minor_unit": 2},
        ],
        "currency_pairs": [{"base": "USD", "quote": "CAD", "symbol": "USDCAD=X"}],
    }
    _, counts = merge_universe(current, {"backfill_groups": {"usd": ["USDCAD=X"]}})
    assert counts == {"currencies": 0, "pairs": 0}
