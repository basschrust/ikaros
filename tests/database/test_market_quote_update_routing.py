"""Normal update-all stage workers must not write final prices."""

from datetime import date
from ikaros_app.update import Job
from run.update_all import command_for_job, promotion_command_for_job


def job(dataset):
    return Job(
        dataset,
        ("WIG20",) if dataset == "WIG20" else ("FW20Z2620",),
        date(2026, 11, 16),
        date(2026, 11, 17),
        ("python", "-m", "legacy"),
    )


def test_index_uses_candidate_stage_and_independent_promotion():
    stage = command_for_job(job("WIG20"))
    assert stage[2:5] == ("run.stage_candidates", "--dataset", "WIG20")
    assert "WIG20" in stage
    assert promotion_command_for_job(job("WIG20"))[2] == "run.promote_candidates"


def test_derivatives_and_options_use_candidate_stage():
    stage = command_for_job(job("DERIVATIVES"))
    assert stage[2:5] == ("run.stage_candidates", "--dataset", "DERIVATIVES")
    assert "FW20Z2620" in stage
    assert promotion_command_for_job(job("DERIVATIVES"))[2] == "run.promote_candidates"


def test_gpw_equities_also_stage_only():
    assert command_for_job(job("GPW"))[2] == "run.stage_candidates"


def test_previous_legacy_datasets_are_now_candidate_only():
    for dataset in ("FX", "US", "RATES", "LSE", "XETRA"):
        stage = command_for_job(job(dataset))
        assert stage[2] == "run.stage_candidates"
        assert promotion_command_for_job(job(dataset))[2] == "run.promote_candidates"
