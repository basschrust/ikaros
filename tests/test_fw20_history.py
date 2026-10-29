from datetime import date
import pytest
from ingestion.fw20_history import parse_history

HTML = """<html><table><tr><th>Data</th><th>Otwarcie</th><th>Max</th><th>Min</th><th>Zamknięcie</th><th>Wolumen</th><th>Obrót</th></tr>
<tr><td>27.10.2026</td><td>4256.00</td><td>4315.00</td><td>4238.00</td><td>4309.00</td><td>351</td><td>30 064 020</td></tr>
<tr><td>29.10.2026</td><td>4309</td><td>4338</td><td>4309</td><td>4335</td><td>190</td><td>1000000</td></tr>
<tr><td>26.10.2026</td><td>4243</td><td>4276</td><td>4236</td><td>4265</td><td>303</td><td>25790840</td></tr></table></html>"""


def test_history_and_cutoff():
    rows = parse_history(HTML, symbol="FW20H2720", through=date(2026, 10, 28))
    assert [r["date"] for r in rows] == [date(2026, 10, 26), date(2026, 10, 27)]
    assert rows[1]["close"] == 4309
    assert rows[1]["volume"] == 351
    assert rows[1]["settlement"] is None


def test_real_contract_only():
    with pytest.raises(ValueError):
        parse_history(HTML, symbol="FW20KONT", through=date(2026, 10, 28))


def test_reject_conflicting_prices():
    bad = HTML.replace("4256.00</td><td>4315.00", "4256.00</td><td>4200.00")
    with pytest.raises(ValueError):
        parse_history(bad, symbol="FW20H2720", through=date(2026, 10, 28))


def test_no_history_table():
    assert (
        parse_history(
            "<table><tr><td>Live quote</td></tr></table>",
            symbol="FW20U2720",
            through=date(2026, 10, 28),
        )
        == []
    )
