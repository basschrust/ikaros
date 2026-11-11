"""Parser regression tests. No external HTTP/Snowflake access."""

from datetime import date
import pytest
from ikaros_app.rates_central_europe import (
    configured_series,
    parse_nbp_xml,
    parse_cnb_txt,
    parse_mnb_html,
)


def test_config():
    catalog = configured_series()
    assert len(catalog) == 9
    assert {s.currency for s in catalog.values()} == {"PLN", "CZK", "HUF"}


def test_nbp_history_and_effective_rates():
    xml = """<stopy>
      <pozycje obowiazuje_od="2024-01-01">
        <pozycja id="ref" oprocentowanie="5,75"/><pozycja id="dep" oprocentowanie="5,25"/>
        <pozycja id="lom" oprocentowanie="6,25"/><pozycja id="red" oprocentowanie="5,80"/>
        <pozycja id="dys" oprocentowanie="5,85"/>
      </pozycje>
      <pozycje obowiazuje_od="2025-01-02">
        <pozycja id="ref" oprocentowanie="5,50"/><pozycja id="dep" oprocentowanie="5,00"/>
        <pozycja id="lom" oprocentowanie="6,00"/><pozycja id="red" oprocentowanie="5,55"/>
        <pozycja id="dys" oprocentowanie="5,60"/>
      </pozycje></stopy>"""
    rows = parse_nbp_xml(xml, date(2025, 1, 1), date(2025, 1, 3))
    assert len(rows) == 15
    ref = [r for r in rows if r.series_id == "NBP_REFERENCE_PLN"]
    assert [r.rate for r in ref] == pytest.approx([0.0575, 0.055, 0.055])


def test_cnb_pipe_format():
    content = "\ufeffVALID_FROM|CNB_REPO_RATE_IN_%\n20241201|4.00\n20250102|3.75\n"
    rows = parse_cnb_txt(content, "REPO_2W", date(2025, 1, 1), date(2025, 1, 3))
    assert [r.rate for r in rows] == pytest.approx([0.04, 0.0375, 0.0375])


def test_mnb_english_html_table():
    html = """<table><tr><th>Date</th><th>Base rate</th></tr>
    <tr><td>25 September 2024</td><td>6.50%</td></tr>
    <tr><td>25 February 2026</td><td>6.25%</td></tr></table>"""
    rows = parse_mnb_html(html, date(2026, 2, 24), date(2026, 2, 26))
    assert [r.rate for r in rows] == pytest.approx([0.065, 0.0625, 0.0625])


def test_missing_baseline_fails():
    with pytest.raises(ValueError, match="baseline"):
        parse_cnb_txt(
            "VALID_FROM|CNB_REPO_RATE_IN_%\n20260102|4.00\n",
            "REPO_2W",
            date(2025, 1, 1),
            date(2025, 1, 3),
        )
