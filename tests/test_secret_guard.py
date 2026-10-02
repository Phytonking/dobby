"""Keep credential detection useful without exempting regression fixtures."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import check_secrets

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def repository(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    # The Docker test image intentionally has no Git. Substitute only file discovery;
    # run the actual scanner against real temporary files.
    def files(*args, **kwargs):
        names = [p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file()]
        return SimpleNamespace(stdout="\0".join(names).encode())

    monkeypatch.setattr(check_secrets.subprocess, "run", files)
    return tmp_path


def test_regression_fixture_passes_secret_guard(repository):
    fixture = repository / "test_regression"
    fixture.mkdir()
    (fixture / "ci.env").write_bytes((ROOT / "test_regression" / "ci.env").read_bytes())
    check_secrets.main()


def test_secret_in_regression_fixture_is_still_rejected(repository, capsys):
    fixture = repository / "test_regression"
    fixture.mkdir()
    # Construct the sentinel so this test file does not itself match the scanner.
    secret = "x" * 24
    (fixture / "ci.env").write_text("DISCORD_TOKEN=" + secret + "\n", encoding="utf-8")
    with pytest.raises(SystemExit) as exit_info:
        check_secrets.main()
    assert exit_info.value.code == 1
    output = capsys.readouterr()
    assert "test_regression/ci.env" in output.out
    assert secret not in output.out + output.err


def test_tracked_env_file_is_rejected(repository, capsys):
    (repository / ".env").write_text("SETTING=placeholder\n", encoding="utf-8")
    with pytest.raises(SystemExit) as exit_info:
        check_secrets.main()
    assert exit_info.value.code == 1
    assert ".env" in capsys.readouterr().out
