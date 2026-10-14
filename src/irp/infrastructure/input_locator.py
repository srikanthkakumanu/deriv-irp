"""Locates the three pipeline input files on disk.

Resolution rules (deterministic, logged in run_manifest.json):

* ``--input-dir DIR`` given  -> all three files must exist in DIR (origin ``explicit``).
* otherwise, each file is resolved **independently**:
    1. ``<search_root>/<file>``       (origin ``repo_root``)  -- replacement fixtures dropped
                                                                in the repo root take precedence;
    2. ``<search_root>/data/<file>``  (origin ``data_dir``)   -- the bundled sample fixtures.
  A file found in neither location is an error naming both paths.

When a repo-root file overrides a *different* bundled copy, a notice is emitted so the
operator can see which fixture was used.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

INPUT_FILES: tuple[str, ...] = ("alerts.jsonl", "service_topology.json", "runbook_policy.json")
DATA_DIR_NAME = "data"

Origin = Literal["explicit", "repo_root", "data_dir"]


class InputFileError(ValueError):
    pass


class ResolvedInput(BaseModel):
    model_config = ConfigDict(frozen=True)
    name: str
    path: Path
    origin: Origin


class InputFiles(BaseModel):
    model_config = ConfigDict(frozen=True)
    files: dict[str, ResolvedInput]
    notices: tuple[str, ...] = ()

    def path(self, name: str) -> Path:
        return self.files[name].path

    def describe(self) -> list[str]:
        return [f"{f.name} <- {f.path} ({f.origin})" for f in self.files.values()]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def resolve_input_files(input_dir: Path | None, search_root: Path | None = None) -> InputFiles:
    if input_dir is not None:
        directory = Path(input_dir)
        missing = [str(directory / n) for n in INPUT_FILES if not (directory / n).is_file()]
        if missing:
            raise InputFileError(f"required input file(s) missing: {missing}")
        return InputFiles(files={n: ResolvedInput(name=n, path=directory / n, origin="explicit") for n in INPUT_FILES})

    root = Path(search_root) if search_root is not None else Path.cwd()
    files: dict[str, ResolvedInput] = {}
    notices: list[str] = []
    missing: list[str] = []
    for name in INPUT_FILES:
        at_root, in_data = root / name, root / DATA_DIR_NAME / name
        if at_root.is_file():
            files[name] = ResolvedInput(name=name, path=at_root, origin="repo_root")
            if in_data.is_file() and _sha(at_root) != _sha(in_data):
                notices.append(f"{name}: using repo-root file {at_root}; it overrides the different bundled copy in {in_data}")
        elif in_data.is_file():
            files[name] = ResolvedInput(name=name, path=in_data, origin="data_dir")
        else:
            missing.append(f"{name} (looked in {at_root} and {in_data})")
    if missing:
        raise InputFileError(f"required input file(s) missing: {missing}")
    return InputFiles(files=files, notices=tuple(notices))
