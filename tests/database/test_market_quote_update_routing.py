"""The normal update-all worker must not bypass market quote candidates."""

from datetime import date
from ikaros_app.update import Job
from run.update_all import command_for_job


def job(dataset):
    return Job(
        dataset,
        ("WIG20",) if dataset == "WIG20" else ("FW20Z2620",),
        date(2026, 11, 16),
        date(2026, 11, 17),
        ("python", "-m", "legacy"),
    )


def test_index_uses_candidate_ingest():
    argv = command_for_job(job("WIG20"))
    assert argv[2] == "run.ingest_market_prices"
    assert argv[argv.index("--dataset") + 1] == "INDEX"
    assert "WIG20" in argv


def test_derivatives_including_options_use_candidate_ingest():
    argv = command_for_job(job("DERIVATIVES"))
    assert argv[2] == "run.ingest_market_prices"
    assert argv[argv.index("--dataset") + 1] == "DERIVATIVE"
    assert "FW20Z2620" in argv


def test_gpw_equities_keep_existing_multisource():
    argv = command_for_job(job("GPW"))
    assert argv[2] == "run.ingest_gpw_prices"


def test_other_datasets_unchanged():
    assert command_for_job(job("FX"))[-1] == "legacy"
