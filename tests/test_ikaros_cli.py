from run.ikaros import command, docker_compose, job_commands


def test_command_uses_current_python():
    result = command("run.kafka_sink", "--dataset", "FX")
    assert result[1:] == ["-m", "run.kafka_sink", "--dataset", "FX"]


def test_docker_compose():
    assert docker_compose("down") == ["docker", "compose", "-f", "compose.kafka.yaml", "down"]


def test_all_gpw_by_default():
    jobs = job_commands(fx_symbols=["EURPLN=X"], gpw_limit=None, days=7)
    assert "--gpw-limit" not in jobs["scheduler"]
    assert "--write" in jobs["scheduler"]
    assert len(jobs) == 4


def test_pilot_gpw_limit():
    jobs = job_commands(fx_symbols=["EURPLN=X"], gpw_limit=3, days=7)
    assert jobs["scheduler"][-2:] == ["--gpw-limit", "3"]
