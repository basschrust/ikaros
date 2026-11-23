"""No direct-to-final source routes are allowed in the daily updater."""

from datetime import date
import sys
from ikaros_app.update import Job
from run.update_all import command_for_job, promotion_command_for_job
import pytest


@pytest.mark.parametrize(
    "dataset,kind",
    [
        ("GPW", "GPW"),
        ("US", "US"),
        ("PSE", "PSE"),
        ("XETRA", "XETRA"),
        ("PARIS", "PARIS"),
        ("LSE", "LSE"),
        ("BSE", "BSE"),
        ("FX-YAHOO", "FX"),
        ("FX-NBP", "FX"),
        ("WIG20", "WIG20"),
        ("FW20", "DERIVATIVES"),
        ("DERIVATIVES", "DERIVATIVES"),
        ("RATES", "RATES"),
    ],
)
def test_daily_jobs_stage_then_separately_promote(dataset, kind):
    job = Job(dataset, ("TEST",), date(2026, 11, 17), date(2026, 11, 18), ("legacy",))
    first = command_for_job(job)
    second = promotion_command_for_job(job)
    assert first[:5] == (sys.executable, "-m", "run.stage_candidates", "--dataset", kind)
    assert second[:5] == (sys.executable, "-m", "run.promote_candidates", "--dataset", kind)
    assert first[5:] == second[5:]


def test_stage_importer_has_no_authoritative_writer_imports():
    from pathlib import Path

    source = Path(__file__).resolve().parents[2] / "ingestion" / "candidate_stage_job.py"
    text = source.read_text()
    assert "promote_dataset" not in text
    assert "promote_equity(" not in text
    assert "promote_market(" not in text
    assert "save_fx_rates(" not in text
    assert "persist_rates(" not in text
