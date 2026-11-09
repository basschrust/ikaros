"""Offline NY Fed adapter tests."""

from datetime import date
import pytest

from ikaros_app.rates import (
    NYFedOvernightProvider,
    configured_series,
    parse_nyfed_json,
    selected_sources,
)


def test_registers_two_overnight_series():
    series = configured_series()
    assert series["NYFED_SOFR_USD_ON"].source == "NYFED"
    assert series["NYFED_EFFR_USD_ON"].tenor == "ON"
    selected, sources = selected_sources(["NYFED_SOFR_USD_ON"])
    assert set(selected) == {"NYFED_SOFR_USD_ON"}
    assert sources == {"NYFED"}


def test_nyfed_rates_normalized_and_bounded():
    result = parse_nyfed_json(
        {
            "refRates": [
                {"effectiveDate": "2026-11-05", "type": "SOFR", "percentRate": 3.94},
                {"effectiveDate": "2026-11-06", "type": "SOFR", "percentRate": 3.95},
            ]
        },
        "SOFR",
        date(2026, 11, 6),
        date(2026, 11, 6),
    )
    assert len(result) == 1
    assert result[0].rate == 0.0395
    assert result[0].day == date(2026, 11, 6)


def test_rejects_unexpected_series_or_missing_rate():
    with pytest.raises(ValueError, match="Unexpected"):
        parse_nyfed_json(
            {"refRates": [{"type": "EFFR", "effectiveDate": "2026-11-06", "percentRate": 3.4}]},
            "SOFR",
            date(2026, 11, 6),
            date(2026, 11, 6),
        )
    with pytest.raises(ValueError, match="Missing"):
        parse_nyfed_json(
            {"refRates": [{"type": "SOFR", "effectiveDate": "2026-11-06"}]},
            "SOFR",
            date(2026, 11, 6),
            date(2026, 11, 6),
        )


def test_provider_requests_both_series():
    class Response:
        def __init__(self, name):
            self.name = name

        def raise_for_status(self):
            pass

        def json(self):
            return {
                "refRates": [
                    {"type": self.name, "effectiveDate": "2026-11-06", "percentRate": "3.50"}
                ]
            }

    class Session:
        def __init__(self):
            self.calls = []

        def get(self, url, **kwargs):
            name = "SOFR" if "/sofr/" in url else "EFFR"
            self.calls.append(kwargs["params"])
            return Response(name)

    session = Session()
    result = NYFedOvernightProvider().history(session, date(2026, 11, 5), date(2026, 11, 6))
    assert len(result) == 2
    assert {r.series_id for r in result} == {"NYFED_SOFR_USD_ON", "NYFED_EFFR_USD_ON"}
    assert all(c["startDate"] == "2026-11-05" for c in session.calls)
