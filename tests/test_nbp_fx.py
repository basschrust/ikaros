from datetime import date
import pytest
from ingestion.nbp_fx import windows, parse_table, cross_rows


def test_windows():
    chunks = list(windows(date(2020, 1, 1), date(2020, 7, 1)))
    assert all((b - a).days < 93 for a, b in chunks)
    assert chunks[0][0] == date(2020, 1, 1)
    assert chunks[-1][-1] == date(2020, 7, 1)
    for prev, cur in zip(chunks, chunks[1:]):
        assert (cur[0] - prev[1]).days == 1


def test_nbp_mid_crosses_and_100_jpy():
    data = [
        {
            "effectiveDate": "2026-10-29",
            "rates": [
                {"code": "CZK", "mid": 0.1807},
                {"code": "EUR", "mid": 4.3951},
                {"code": "JPY", "mid": 2.4990},
            ],
        }
    ]
    table = parse_table(data)
    pairs = cross_rows(table, ["CZKPLN=X", "EURCZK=X", "JPYPLN=X", "PLNCZK=X"])
    by_symbol = {row[0]: row[2] for row in pairs}
    assert by_symbol["CZKPLN=X"] == pytest.approx(0.1807)
    assert by_symbol["EURCZK=X"] == pytest.approx(4.3951 / 0.1807)
    assert by_symbol["JPYPLN=X"] == pytest.approx(0.02499)
    assert by_symbol["PLNCZK=X"] == pytest.approx(1 / 0.1807)
    assert all("NBP" in row[3] for row in pairs)


def test_missing_pair_currency_skips():
    table = parse_table([{"effectiveDate": "2020-01-02", "rates": [{"code": "EUR", "mid": 4.2}]}])
    assert [row[0] for row in cross_rows(table, ["EURPLN=X", "USDPLN=X"])] == ["EURPLN=X"]


def test_invalid_symbol():
    with pytest.raises(ValueError):
        cross_rows({}, ["BAD"])


def test_duplicate_day_fails():
    item = {"effectiveDate": "2020-01-02", "rates": [{"code": "EUR", "mid": 4.2}]}
    with pytest.raises(ValueError):
        parse_table([item, item])


def test_identical_duplicate_code_does_not_abort():
    table = parse_table(
        [
            {
                "effectiveDate": "2002-01-02",
                "rates": [
                    {"code": "EUR", "mid": 3.6},
                    {"code": "EUR", "mid": 3.6},
                    {"code": "CZK", "mid": 0.12},
                ],
            }
        ]
    )
    rows = cross_rows(table, ["EURPLN=X", "CZKPLN=X"])
    assert {row[0]: row[2] for row in rows} == {"EURPLN=X": 3.6, "CZKPLN=X": 0.12}


def test_conflicting_duplicate_skips_only_ambiguous_currency(caplog):
    table = parse_table(
        [
            {
                "effectiveDate": "2002-01-02",
                "rates": [
                    {"code": "EUR", "mid": 3.6},
                    {"code": "EUR", "mid": 3.7},
                    {"code": "CZK", "mid": 0.12},
                ],
            }
        ]
    )
    rows = cross_rows(table, ["EURPLN=X", "CZKPLN=X", "EURCZK=X"])
    assert [r[0] for r in rows] == ["CZKPLN=X"]
    assert "ambiguous duplicate codes EUR" in caplog.text


def test_ambiguous_currency_cannot_reappear_from_third_quote():
    table = parse_table(
        [
            {
                "effectiveDate": "2002-01-02",
                "rates": [
                    {"code": "EUR", "mid": 3.6},
                    {"code": "EUR", "mid": 3.7},
                    {"code": "EUR", "mid": 3.6},
                    {"code": "CZK", "mid": 0.12},
                ],
            }
        ]
    )
    assert "EUR" not in table[date(2002, 1, 2)]
    assert table[date(2002, 1, 2)]["CZK"] == pytest.approx(0.12)
