from collections import Counter
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from ikaros_app.derivative_approval import parse_approved, promote

CSV = Path(__file__).resolve().parents[2] / "output" / "derivative_approval_review.csv"


def test_approved_inventory():
    contracts = parse_approved(CSV)
    assert len(contracts) == 332
    assert Counter(c["instrument_type"] for c in contracts) == {"FUTURE": 141, "OPTION": 191}
    lookup = {c["symbol"]: c for c in contracts}
    assert lookup["FW20Z2620"]["multiplier"] == 20
    assert lookup["FW20Z2620"]["expiry_date"] == date(2026, 12, 18)
    assert lookup["FLPPZ26"]["multiplier"] == 1.02
    assert lookup["FLPPH27"]["multiplier"] == 1
    assert lookup["FEURZ26"]["multiplier"] == 1000
    assert lookup["OW20X264000"]["multiplier"] == 10
    assert lookup["OW20X264000"]["option_side"] == "PUT"
    assert lookup["OW20X264000"]["strike"] == 4000
    assert "FBASZ26" not in lookup and "FFINZ26" not in lookup


def test_preflight_conflict_blocks_any_write():
    conn = MagicMock()
    cursor = conn.cursor.return_value.__enter__.return_value
    existing = ("FW20Z2620", "FUTURE", "WIG20", date(2026, 12, 18), "PLN", 10, None, None)
    cursor.fetchall.return_value = [existing]
    cursor.fetchone.return_value = (1,)
    contract = {
        "contract_id": "FW20Z2620",
        "symbol": "FW20Z2620",
        "instrument_type": "FUTURE",
        "underlying_symbol": "WIG20",
        "expiry_date": date(2026, 12, 18),
        "currency": "PLN",
        "multiplier": 20,
        "strike": None,
        "option_side": None,
        "source": "verified:gpw.pl",
    }
    with pytest.raises(ValueError, match="CONFLICT"):
        promote(conn, [contract], write=True)
    statements = [c.args[0] for c in cursor.execute.call_args_list]
    assert not any("INSERT INTO" in s or s == "BEGIN" for s in statements)
