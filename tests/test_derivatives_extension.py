from datetime import date
import pytest
from ingestion.derivatives_discovery_ext import parse_bankier_options
from ingestion.derivative_review_ext import review_candidate
from providers.derivatives.listings import collect_candidates, sources


OPTIONS = """<table><thead><tr><th>Walor</th><th>Kurs</th><th>Wykonanie</th></tr></thead>
<tbody><tr><td>OW20C273000</td><td>708</td><td>2027-03-18</td></tr>
<tr><td>OW20A274700</td><td>9</td><td>2027-01-14</td></tr>
<tr><td>INVALID</td><td>5</td><td>2027-03-18</td></tr></tbody></table>"""


def test_options_table_with_thead_and_tbody():
    out = parse_bankier_options(OPTIONS, as_of="2026-11-04")
    assert [x.symbol for x in out] == ["OW20A274700", "OW20C273000"]
    assert out[0].expiry_date == "2027-01-14"
    assert out[0].source == "bankier"


def test_options_not_auto_verified():
    item = review_candidate("OW20C273000", "OPTION", date(2027, 3, 18), as_of=date(2026, 11, 4))
    assert item["category"] == "INDEX_OPTION_CANDIDATE"
    assert item["status"] == "REVIEW"
    assert "multiplier" in item["reason"]


def test_futures_review_preserved():
    item = review_candidate("FCDRZ26", "FUTURE", date(2026, 12, 17))
    assert item["category"] == "EQUITY_CANDIDATE"


def test_empty_provider_rejected():
    class Empty:
        name = "empty"

        def discover(self, *, as_of):
            return []

    with pytest.raises(RuntimeError, match="empty listing"):
        collect_candidates([Empty()], as_of="now")


def test_sources_default_bankier():
    result = sources()
    assert [x.name for x in result] == ["bankier-futures", "bankier-options"]
