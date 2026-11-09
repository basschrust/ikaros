import pytest
from ingestion.index_prices import read_index_csv


def test_snapshot():
    rows = read_index_csv("data/wig20_eod_2026_10.csv")
    assert len(rows) == 8
    assert rows[-1][1] == "2026-10-28"
    assert rows[-1][5] == 4247.19


def test_bad_price(tmp_path):
    target = tmp_path / "bad.csv"
    target.write_text(
        "index_symbol,date,open,high,low,close,source\\n".replace("\\n", "\n")
        + "WIG20,2026-10-28,4300,4256,4225,4247,manual\n"
    )
    with pytest.raises(ValueError, match="Inconsistent OHLC"):
        read_index_csv(target)


def test_duplicate(tmp_path):
    target = tmp_path / "dup.csv"
    header = "index_symbol,date,open,high,low,close,source\n"
    row = "WIG20,2026-10-28,4241,4256,4225,4247,manual\n"
    target.write_text(header + row + row)
    with pytest.raises(ValueError, match="Duplicate"):
        read_index_csv(target)
