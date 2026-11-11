"""Offline SNB monthly spot-rate checks."""

from datetime import date

import pytest

from ikaros_app.rates_swiss_government import (
    SNBGovernmentYieldProvider,
    configured_series,
    parse_snb_government_csv,
)

SAMPLE = """CubeId;"rendoblim"
PublishingDate;"2026-11-01"
"Date";"D0";"Value"
2026-01;1J;-0.25
2026-01;10J;0.45
2026-02;10J;0.40
2026-02;30J;:
"""


def test_catalog_is_chf_and_distinguishes_tenors():
    series = configured_series()
    assert len(series) == 13
    assert all(s.currency == "CHF" and s.curve_type == "GOV_ZERO_SPOT" for s in series.values())
    assert "SNB_GOV_SPOT_CHF_10Y_MONTHLY" in series


def test_snb_month_end_negative_rates_and_missing_values():
    obs = parse_snb_government_csv(SAMPLE, date(2026, 1, 1), date(2026, 2, 28))
    assert len(obs) == 3
    assert ("SNB_GOV_SPOT_CHF_1Y_MONTHLY", date(2026, 1, 31), -0.0025) in [
        (r.series_id, r.day, r.rate) for r in obs
    ]
    assert ("SNB_GOV_SPOT_CHF_10Y_MONTHLY", date(2026, 2, 28), 0.004) in [
        (r.series_id, r.day, r.rate) for r in obs
    ]


def test_unrecognized_tenor_fails_closed():
    with pytest.raises(ValueError, match="tenor"):
        parse_snb_government_csv("Date;D0;Value\n2026-01;99J;1", date(2026, 1, 1), date(2026, 2, 1))


def test_provider_requests_official_cube():
    class Response:
        text = SAMPLE

        def raise_for_status(self):
            pass

    class Session:
        def get(self, url, **kwargs):
            assert url.endswith("/rendoblim/data/csv/en")
            assert kwargs["params"]["dimSel"].startswith("D0(")
            return Response()

    assert (
        len(SNBGovernmentYieldProvider().history(Session(), date(2026, 1, 1), date(2026, 2, 28)))
        == 3
    )
