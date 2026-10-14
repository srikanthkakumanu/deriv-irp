import os
import shutil
from pathlib import Path

import pytest

from irp.bootstrap import build_language_model
from irp.infrastructure.offline_model import OfflineModel
from irp.interface import cli
from irp.interface.operator_console import ScriptedOperatorConsole, TerminalOperatorConsole


class TestConsoles:
    def test_terminal_reads_until_eof(self):
        lines = iter(["approve INC-001 x", ""])

        def reader(_prompt):
            try:
                return next(lines)
            except StopIteration:
                raise EOFError

        console = TerminalOperatorConsole(output=lambda _: None, reader=reader)
        assert list(console.commands()) == ["approve INC-001 x", ""]

    def test_scripted_skips_comments(self, tmp_path):
        f = tmp_path / "d.txt"
        f.write_text("# comment\nreject INC-001 a\n")
        shown = []
        console = ScriptedOperatorConsole.from_file(f, output=shown.append)
        assert list(console.commands()) == ["reject INC-001 a"]
        assert console.mode.startswith("decisions_file:") and shown == ["> reject INC-001 a"]


class TestDotenv:
    def test_loads_without_overriding(self, tmp_path, monkeypatch):
        env = tmp_path / ".env"
        env.write_text('# c\nexport IRP_T1="one"\nIRP_T2=two\nIRP_T3=\nbad line\n')
        monkeypatch.setenv("IRP_T2", "keep")
        monkeypatch.delenv("IRP_T1", raising=False)
        cli.load_dotenv(env)
        assert os.environ["IRP_T1"] == "one" and os.environ["IRP_T2"] == "keep" and "IRP_T3" not in os.environ
        monkeypatch.delenv("IRP_T1")

    def test_missing_file_is_noop(self, tmp_path):
        cli.load_dotenv(tmp_path / "nope")


class TestProviderSelection:
    def test_auto_without_key_is_offline(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        assert isinstance(build_language_model("auto"), OfflineModel)

    def test_openai_without_key_fails(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
            build_language_model("openai")

    def test_auto_with_key_is_openai(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        assert build_language_model("auto").name == "openai"

    def test_unknown_provider(self):
        with pytest.raises(ValueError):
            build_language_model("llama")


class TestCli:
    def test_run_then_validate(self, workspace, monkeypatch, capsys):
        decisions = workspace / "d.txt"
        decisions.write_text("reject INC-001 freeze_deployments\n\n")
        monkeypatch.chdir(workspace)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        io = ["--input-dir", ".", "--out-dir", "out"]
        assert cli.main(["run", "--provider", "offline", "--decisions-file", str(decisions), *io]) == 0
        assert (workspace / "out" / "incident_brief.md").exists()
        assert cli.main(["validate", *io]) == 0
        assert "checks passed" in capsys.readouterr().out

    def test_validate_on_empty_dir_fails(self, workspace, monkeypatch):
        monkeypatch.chdir(workspace)
        assert cli.main(["validate"]) == 1

    def test_missing_inputs_returns_error_code(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        assert cli.main(["run", "--provider", "offline", "--decisions-file", "/dev/null"]) == 1
        assert "missing" in capsys.readouterr().err

    def test_validate_script_entrypoint(self, workspace):
        import subprocess, sys
        root = Path(__file__).resolve().parents[3]
        shutil.copy(root / "validate.py", workspace / "validate.py")
        proc = subprocess.run([sys.executable, "validate.py"], cwd=workspace, capture_output=True, text=True,
                              env={**os.environ, "PYTHONPATH": str(root / "src")})
        assert proc.returncode == 1 and "FAIL" in proc.stdout


def test_cli_defaults_point_at_data_dirs(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert cli.main(["run", "--provider", "offline"]) == 1
    assert "data/alerts.jsonl" in capsys.readouterr().err
