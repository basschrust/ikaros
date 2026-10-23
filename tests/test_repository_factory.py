import pytest
from database.repositories.factory import create_repository, repository_from_config


def test_unknown_driver():
    with pytest.raises(ValueError, match="Unsupported database driver"):
        create_repository(driver="unknown")


def test_duckdb_factory(tmp_path):
    pytest.importorskip("duckdb")
    repo = create_repository(driver="duckdb", output_dir=tmp_path)
    try:
        assert (tmp_path / "ikaros.duckdb").exists()
        assert repo.connection.execute("SELECT 1").fetchone() == (1,)
    finally:
        repo.connection.close()


def test_duckdb_config(tmp_path):
    pytest.importorskip("duckdb")
    repo = repository_from_config({"database": {"driver": "duckdb", "duckdb": {"output_dir": str(tmp_path)}}})
    try:
        assert repo.connection.execute("SELECT 2").fetchone() == (2,)
    finally:
        repo.connection.close()


def test_invalid_driver_config():
    with pytest.raises(ValueError, match="must be a mapping"):
        repository_from_config({"database": {"driver": "duckdb", "duckdb": "bad"}})
