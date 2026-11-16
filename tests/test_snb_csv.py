"""SNB CSV parser regression cases; no network or Snowflake needed."""

from datetime import date
import pytest
from ikaros_app.rates_international import parse_snb_csv


def test_snb_quoted_header_and_preamble():
    csv_data = (
        'CubeId;"snboffzisa"\nPublishingDate;"2026-11-11 12:00"\n'
        '\n"Date";"D0";"Value"\n'
        '2025-01;"LZ";"0.50"\n2025-02;"UG1";"0.75"\n'
        '2025-02;"LZ";"0.25"\n'
    )
    records = parse_snb_csv(csv_data, date(2025, 1, 1), date(2025, 2, 28))
    assert [(r.day, r.rate) for r in records] == [
        (date(2025, 1, 31), 0.005),
        (date(2025, 2, 28), 0.0025),
    ]


def test_snb_unquoted_header_and_missing_value():
    content = "Date;Dimension;Value\n2025-03;LZ;:\n2025-04;LZ;0.00\n"
    records = parse_snb_csv(content, date(2025, 3, 1), date(2025, 5, 1))
    assert len(records) == 1
    assert records[0].rate == 0
    assert records[0].day == date(2025, 4, 30)


def test_snb_rejects_unknown_header():
    with pytest.raises(ValueError, match="missing Date/D0/Value"):
        parse_snb_csv("wrong response", date(2025, 1, 1), date(2025, 12, 31))


def test_snb_rejects_invalid_period():
    with pytest.raises(ValueError, match="Invalid SNB monthly period"):
        parse_snb_csv('"Date";"D0";"Value"\n2025/01;LZ;0.5', date(2025, 1, 1), date(2025, 12, 31))
