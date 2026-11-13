from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock

import pytest

from ikaros_app.derivative_prices import collect, parse_page, history_url, advertised_pages

HTML = """<html><h1>Notowania historyczne OW20L264000</h1>
<table><tr><th>Data</th><th>Otwarcie</th><th>Max</th><th>Min</th><th>Zamknięcie</th><th>Wolumen</th><th>Obrót</th></tr>
<tr><td>29.10.2026</td><td>349.60</td><td>349.60</td><td>349.60</td><td>329.05</td><td>1</td><td>40 000</td></tr>
<tr><td>30.09.2026</td><td>261.55</td><td>261.55</td><td>261.55</td><td>261.55</td><td>6</td><td>15 693</td></tr>
<tr><td>17.07.2026</td><td>80.00</td><td>85.00</td><td>80.00</td><td>85.00</td><td>10</td><td>8 450</td></tr>
</table></html>"""


def test_parse_options_with_sparse_trade_days():
    rows = parse_page(HTML, "OW20L264000")
    assert len(rows) == 3
    assert rows[0]["settlement"] is None
    assert rows[0]["source"] == "biznesradar|html_history"


def test_collect_quarter_no_synthetic_dates():
    s = Mock()
    s.get.return_value.text = HTML
    rows, coverage, pages = collect(s, "OW20L264000", date(2026, 7, 1), date(2026, 9, 30), delay=0)
    assert pages == 1
    assert [r["date"] for r in rows] == [date(2026, 7, 17), date(2026, 9, 30)]
    assert coverage == "SOURCE_NAVIGATION_EXHAUSTED"


def test_wrong_symbol_fails_closed():
    with pytest.raises(ValueError, match="does not identify"):
        parse_page(HTML, "FW20Z2620")


def test_url_and_pagination():
    assert history_url("FCDRZ26", 2).endswith("FCDRZ26,2")
    assert advertised_pages('<a href="/notowania-historyczne/FCDRZ26,2">2</a>', "FCDRZ26") == {2}
    with pytest.raises(ValueError):
        history_url("../oops")


def test_offline_snapshot():
    with TemporaryDirectory() as d:
        Path(d, "OW20L264000.html").write_text(HTML, encoding="utf-8")
        rows, status, pages = collect(
            None, "OW20L264000", date(2026, 7, 1), date(2026, 9, 30), html_dir=Path(d)
        )
        assert len(rows) == 2 and pages == 1


def test_page_of_newer_data_still_traverses_to_quarter(monkeypatch):
    newer = HTML.replace("30.09.2026", "28.10.2026").replace("17.07.2026", "27.10.2026")
    newer = newer.replace(
        "</table>", '</table><a href="/notowania-historyczne/OW20L264000,2">older</a>'
    )
    second = HTML.replace("29.10.2026", "17.07.2026").replace("30.09.2026", "30.08.2026")
    # Remove duplicate date so the second page is internally valid.
    second = second.replace(
        "<tr><td>17.07.2026</td><td>80.00</td><td>85.00</td><td>80.00</td><td>85.00</td><td>10</td><td>8 450</td></tr>",
        "",
    )
    with TemporaryDirectory() as d:
        p = Path(d)
        (p / "OW20L264000.html").write_text(newer, encoding="utf-8")
        (p / "OW20L264000,2.html").write_text(second, encoding="utf-8")
        rows, status, pages = collect(
            None, "OW20L264000", date(2026, 7, 1), date(2026, 9, 30), html_dir=p
        )
        assert pages == 2
        assert [r["date"] for r in rows] == [date(2026, 7, 17), date(2026, 8, 30)]
