import csv
import pytest
from ikaros_app.derivative_approval import parse_approved


def test_no_auto_approval(tmp_path):
    path = tmp_path / "a.csv"
    row = {
        "symbol": "OW20X264000",
        "classification": "OPTION",
        "verified_underlying_symbol": "",
        "verified_expiry_date": "",
        "verified_multiplier": "",
        "currency": "PLN",
        "source": "bankier",
        "evidence_url": "",
        "approval": "NO",
    }
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(row))
        w.writeheader()
        w.writerow(row)
    assert parse_approved(path) == []


def test_approval_requires_provenance(tmp_path):
    path = tmp_path / "a.csv"
    row = {
        "symbol": "OW20X264000",
        "classification": "OPTION",
        "verified_underlying_symbol": "WIG20",
        "verified_expiry_date": "2026-12-18",
        "verified_multiplier": "10",
        "verified_strike": "4000",
        "verified_option_side": "PUT",
        "currency": "PLN",
        "source": "bankier",
        "evidence_url": "",
        "approval": "YES",
    }
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(row))
        w.writeheader()
        w.writerow(row)
    with pytest.raises(ValueError, match="evidence_url"):
        parse_approved(path)
