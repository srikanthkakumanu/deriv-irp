import shutil
from pathlib import Path

import pytest

from irp.infrastructure.input_locator import INPUT_FILES, InputFileError, resolve_input_files

DATA = Path(__file__).resolve().parents[3] / "data"


def seed(root: Path, where: str, names=INPUT_FILES) -> None:
    target = root if where == "root" else root / "data"
    target.mkdir(parents=True, exist_ok=True)
    for n in names:
        shutil.copy(DATA / n, target / n)


def origins(files):
    return {n: f.origin for n, f in files.files.items()}


def test_bundled_data_dir_used_by_default(tmp_path):
    seed(tmp_path, "data")
    files = resolve_input_files(None, tmp_path)
    assert set(origins(files).values()) == {"data_dir"}
    assert files.path("alerts.jsonl") == tmp_path / "data" / "alerts.jsonl"
    assert files.notices == ()


def test_repo_root_files_take_precedence(tmp_path):
    seed(tmp_path, "data")
    seed(tmp_path, "root")
    (tmp_path / "alerts.jsonl").write_text((DATA / "alerts.jsonl").read_text().replace("eu-west-1", "us-east-1"))
    files = resolve_input_files(None, tmp_path)
    assert set(origins(files).values()) == {"repo_root"}
    # only the file that actually differs from the bundled copy raises a notice
    assert len(files.notices) == 1 and "alerts.jsonl" in files.notices[0]


def test_resolution_is_per_file(tmp_path):
    seed(tmp_path, "data")
    seed(tmp_path, "root", names=("alerts.jsonl",))
    assert origins(resolve_input_files(None, tmp_path)) == {
        "alerts.jsonl": "repo_root", "service_topology.json": "data_dir", "runbook_policy.json": "data_dir"}


def test_root_only_without_data_dir(tmp_path):
    seed(tmp_path, "root")
    assert set(origins(resolve_input_files(None, tmp_path)).values()) == {"repo_root"}


def test_missing_everywhere_names_both_locations(tmp_path):
    seed(tmp_path, "data", names=("alerts.jsonl", "service_topology.json"))
    with pytest.raises(InputFileError) as err:
        resolve_input_files(None, tmp_path)
    assert "runbook_policy.json" in str(err.value)
    assert str(tmp_path / "runbook_policy.json") in str(err.value) and str(tmp_path / "data" / "runbook_policy.json") in str(err.value)


def test_explicit_dir_wins_and_must_be_complete(tmp_path):
    seed(tmp_path, "root")
    seed(tmp_path / "other", "root")
    files = resolve_input_files(tmp_path / "other", tmp_path)
    assert set(origins(files).values()) == {"explicit"} and files.path("alerts.jsonl").parent == tmp_path / "other"
    (tmp_path / "other" / "alerts.jsonl").unlink()
    with pytest.raises(InputFileError, match="missing"):
        resolve_input_files(tmp_path / "other", tmp_path)
