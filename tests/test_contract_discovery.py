from ingestion.contract_discovery import Candidate, parse_bankier_futures, parse_money_options


def test_bankier_parse_expiry_and_skip_continuous():
    html = """<table><thead><tr><th>Walor</th><th>Kurs</th><th>Wygas.</th></tr></thead>
    <tbody><tr><td>FW20Z2620</td><td>4200</td><td>2026-12-17</td></tr>
    <tr><td>FW20KONT</td><td>4200</td><td>2026-12-17</td></tr></tbody></table>"""
    result = parse_bankier_futures(html)
    assert len(result) == 1
    assert result[0].symbol == "FW20Z2620"
    assert result[0].expiry_date == "2026-12-17"


def test_money_candidates_without_inferred_expiry():
    result = parse_money_options("<table><tr><td>OW20C272800</td></tr></table>")
    assert len(result) == 1
    assert result[0].expiry_date is None


def test_round_trip():
    c = Candidate("FW20Z2620", "FUTURE", "2026-12-17", "bankier")
    assert Candidate.decode(c.encode()) == c
