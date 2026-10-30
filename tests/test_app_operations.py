from ikaros_app.operations import backfill_command, job_commands
import pytest


def test_fx_backfill_requires_symbols():
    with pytest.raises(ValueError, match="requires --symbols"):
        backfill_command("FX", start="2010-01-01", end="2011-01-01")


def test_fx_backfill_multiple_pairs():
    command = backfill_command(
        "FX", start="2010-01-01", end="2010-12-31", symbols=["EURPLN=X", "USDPLN=X"], write=True
    )
    assert command[1:3] == ["-m", "run.backfill_fx_snowflake"]
    assert command[-1] == "--continue-on-error"
    assert command[command.index("--symbols") + 1 : command.index("--write")] == [
        "EURPLN=X",
        "USDPLN=X",
    ]


def test_gpw_command_still_uses_existing_module():
    command = backfill_command("GPW", start="2026-10-01", end="2026-10-29", limit=3, write=True)
    assert "run.backfill_gpw_snowflake" in command
    assert "--limit" in command


def test_backward_compatible_supervision_commands():
    jobs = job_commands(fx_symbols=["EURPLN=X"], gpw_limit=None, days=7)
    assert len(jobs) == 4
    assert "--write" in jobs["scheduler"]
