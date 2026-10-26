from datetime import date

import pytest

from providers.derivatives.biznesradar import BiznesRadarProvider, parse_history_html


HTML = """<html><table><tr><th>Other</th></tr><tr><td>Hi</td></tr></table>
<table><thead><tr><th>Data</th><th>Otwarcie</th><th>Max</th><th>Min</th>
<th>Zamknięcie</th><th>Wolumen</th><th>Obrót</th></tr></thead>
<tbody><tr><td>23.10.2026</td><td>4 227,00</td><td>4 238,00</td>
<td>4 166,00</td><td>4 189,00</td><td>26 759</td><td>2 242 701 200</td></tr>
<tr><td>22.10.2026</td><td>4260.00</td><td>4276.00</td><td>4223.00</td>
<td>4227.00</td><td>29 593</td><td>2515963240</td></tr></tbody></table></html>"""


def test_parse_realistic_table():
    df = parse_history_html(
        HTML, symbol="FW20Z2620", start=date(2026, 10, 1), end=date(2026, 10, 23)
    )
    assert len(df) == 2
    assert df.iloc[-1]["close"] == 4189
    assert df.iloc[-1]["volume"] == 26759


def test_date_filter():
    df = parse_history_html(
        HTML, symbol="FW20Z2620", start=date(2026, 10, 23), end=date(2026, 10, 23)
    )
    assert len(df) == 1


def test_empty_rejected():
    with pytest.raises(ValueError, match="No historical rows"):
        parse_history_html(HTML, symbol="FW20Z2620", start=date(2026, 9, 1), end=date(2026, 9, 1))


def test_missing_table_rejected():
    with pytest.raises(ValueError, match="exactly one"):
        parse_history_html(
            "<html>No table</html>",
            symbol="FW20Z2620",
            start=date(2026, 1, 1),
            end=date(2026, 10, 23),
        )


def test_duplicate_rejected():
    duplicate = HTML.replace(
        "</tbody>",
        "<tr><td>23.10.2026</td><td>1</td><td>2</td><td>1</td><td>2</td><td>1</td><td>2</td></tr></tbody>",
    )
    with pytest.raises(ValueError, match="Duplicate"):
        parse_history_html(
            duplicate, symbol="FW20Z2620", start=date(2026, 10, 1), end=date(2026, 10, 23)
        )


def test_provider_reads_snapshot(tmp_path):
    (tmp_path / "FW20Z2620.html").write_text(HTML, encoding="utf-8")
    provider = BiznesRadarProvider(tmp_path)
    assert len(provider.get_history("FW20Z2620", date(2026, 10, 1), date(2026, 10, 23))) == 2


def test_symbol_path_safety(tmp_path):
    with pytest.raises(ValueError, match="Invalid contract symbol"):
        BiznesRadarProvider(tmp_path).get_history(
            "../../etc/passwd", date(2026, 1, 1), date(2026, 2, 1)
        )
