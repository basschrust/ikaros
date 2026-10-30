from datetime import date
import pytest

from ingestion.wig20_stooq import parse_csv

SAMPLE = '''Date,Open,High,Low,Close,Volume
2026-10-28,4241.43,4256.15,4225.72,4247.19,22081762
2026-10-27,4191.48,4243.10,4168.69,4238.38,26823249
'''


def test_parse_and_order():
    df = parse_csv(SAMPLE, start=date(2026, 10, 1), end=date(2026, 10, 28))
    assert len(df) == 2
    assert df.iloc[0]['date'] == date(2026, 10, 27)
    assert df.iloc[-1]['close'] == 4247.19


def test_dry_date_filter():
    df = parse_csv(SAMPLE, start=date(2026, 10, 28), end=date(2026, 10, 28))
    assert len(df) == 1


def test_duplicates_rejected():
    with pytest.raises(ValueError, match='Duplicate dates'):
        parse_csv(SAMPLE + SAMPLE.splitlines()[1] + '\n', start=date(2026, 1, 1), end=date(2026, 10, 28))


def test_bad_high_rejected():
    bad = SAMPLE.replace('4256.15', '4200.00')
    with pytest.raises(ValueError, match='Inconsistent OHLC'):
        parse_csv(bad, start=date(2026, 1, 1), end=date(2026, 10, 28))


def test_non_csv_rejected():
    with pytest.raises(ValueError):
        parse_csv('No data', start=date(2026, 1, 1), end=date(2026, 10, 28))
