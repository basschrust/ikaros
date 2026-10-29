import sys
from run.ikaros_scheduler import INTERVALS, commands


def test_schedules():
    assert INTERVALS == {
        "FX": 1440,
        "GPW": 14400,
        "INDEX": 14400,
        "DERIVATIVES": 21600,
        "FW20_PRICES": 14400,
    }


def test_index_write_command():
    cmd = commands("INDEX", fx_symbols=[], gpw_symbols=[], days=7, write=True)
    assert cmd == [sys.executable, "-m", "run.refresh_index_prices", "--days", "10", "--write"]


def test_index_dry_run_command():
    cmd = commands("INDEX", fx_symbols=[], gpw_symbols=[], days=12, write=False)
    assert cmd[-2:] == ["--days", "12"]
