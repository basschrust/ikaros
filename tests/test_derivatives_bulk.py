from pathlib import Path

import pandas as pd
import pytest

from run.ingest_gpw_derivatives_bulk import collect, price_path


def test_price_path_rejects_traversal(tmp_path):
    with pytest.raises(ValueError, match="Unsafe"):
        price_path(tmp_path, "../../secret")


def test_price_path_contract(tmp_path):
    assert price_path(tmp_path, "FW20Z2620") == tmp_path / "FW20Z2620.csv"


def test_missing_is_reported(tmp_path):
    data, report = collect(pd.DataFrame({"contract_id": ["FW20Z2620"]}), tmp_path, allow_missing=True)
    assert data.empty
    assert report[0]["status"] == "MISSING_FILE"


def test_invalid_file_is_reported(tmp_path):
    (tmp_path / "FW20Z2620.csv").write_text("contract_id,date,close\nOTHER,2026-10-23,10\n")
    data, report = collect(pd.DataFrame({"contract_id": ["FW20Z2620"]}), tmp_path, allow_missing=True)
    assert data.empty
    assert report[0]["status"] == "INVALID"


def test_single_valid_contract(tmp_path):
    (tmp_path / "FW20Z2620.csv").write_text(
        "contract_id,date,open,high,low,close,volume,open_interest,settlement,source\n"
        "FW20Z2620,2026-10-23,4227,4238,4166,4189,26759,,,snapshot\n"
    )
    data, report = collect(pd.DataFrame({"contract_id": ["FW20Z2620"]}), tmp_path)
    assert len(data) == 1
    assert report[0]["status"] == "READY"


def test_mixed_manifest_requires_opt_in(tmp_path):
    (tmp_path / "FW20Z2620.csv").write_text(
        "contract_id,date,open,high,low,close,volume,open_interest,settlement,source\n"
        "FW20Z2620,2026-10-23,4227,4238,4166,4189,26759,,,snapshot\n"
    )
    manifest = pd.DataFrame({"contract_id": ["FW20Z2620", "OW20TEST"]})
    with pytest.raises(ValueError, match="incomplete"):
        collect(manifest, tmp_path)
    data, report = collect(manifest, tmp_path, allow_missing=True)
    assert len(data) == 1 and len(report) == 2
