"""Filesystem adapters: input source and artifact store."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from ..application.ports import Clock, InputProvenance, PipelineInputs
from ..domain.alerts import RawAlertRecord
from ..domain.policy import RunbookPolicy
from ..domain.topology import ServiceTopology

ALERTS_FILE = "alerts.jsonl"
TOPOLOGY_FILE = "service_topology.json"
POLICY_FILE = "runbook_policy.json"


class InputFileError(ValueError):
    pass


class FileSystemArtifactStore:
    """ArtifactStore adapter writing atomically under a root directory."""

    def __init__(self, root: Path) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)

    def _path(self, name: str) -> Path:
        path = (self._root / name).resolve()
        if self._root.resolve() not in path.parents and path != self._root.resolve():
            raise ValueError(f"artifact path escapes store root: {name}")
        return path

    def save_json(self, name: str, data: Any) -> str:
        return self.save_text(name, json.dumps(data, indent=2, ensure_ascii=False, default=str) + "\n")

    def save_text(self, name: str, text: str) -> str:
        path = self._path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
        return str(path)

    def append_jsonl(self, name: str, record: dict[str, Any]) -> None:
        path = self._path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

    def read_json(self, name: str) -> Any:
        return json.loads(self.read_text(name))

    def read_text(self, name: str) -> str:
        return self._path(name).read_text(encoding="utf-8")

    def exists(self, name: str) -> bool:
        return self._path(name).exists()

    def remove(self, name: str) -> None:
        path = self._path(name)
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)

    def location(self, name: str) -> str:
        return str(self._path(name))

    def sha256(self, name: str) -> str:
        return hashlib.sha256(self._path(name).read_bytes()).hexdigest()


class FileSystemInputSource:
    """InputSource adapter: reads alerts.jsonl, service_topology.json and runbook_policy.json."""

    def __init__(self, input_dir: Path, clock: Clock) -> None:
        self._dir = Path(input_dir)
        self._clock = clock

    def load(self) -> PipelineInputs:
        paths = {"alerts": self._dir / ALERTS_FILE, "topology": self._dir / TOPOLOGY_FILE, "policy": self._dir / POLICY_FILE}
        missing = [str(p) for p in paths.values() if not p.is_file()]
        if missing:
            raise InputFileError(f"required input file(s) missing: {missing}")
        provenance: dict[str, InputProvenance] = {}
        contents: dict[str, bytes] = {}
        for label, path in paths.items():
            data = path.read_bytes()
            contents[label] = data
            provenance[label] = InputProvenance(path=str(path), sha256=hashlib.sha256(data).hexdigest(),
                                                bytes=len(data), read_at=self._clock.now())
        raw_topology = self._json(paths["topology"], contents["topology"])
        raw_policy = self._json(paths["policy"], contents["policy"])
        try:
            topology = ServiceTopology.model_validate(raw_topology)
            policy = RunbookPolicy.model_validate(raw_policy)
        except ValidationError as exc:
            raise InputFileError(f"input schema error: {exc}") from exc
        return PipelineInputs(alert_records=tuple(self._records(contents["alerts"].decode("utf-8"))), topology=topology,
                              policy=policy, raw_topology=raw_topology, raw_policy=raw_policy, provenance=provenance)

    @staticmethod
    def _json(path: Path, data: bytes) -> dict[str, Any]:
        try:
            parsed = json.loads(data.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise InputFileError(f"{path}: invalid JSON ({exc})") from exc
        if not isinstance(parsed, dict):
            raise InputFileError(f"{path}: expected a JSON object")
        return parsed

    @staticmethod
    def _records(text: str) -> list[RawAlertRecord]:
        records: list[RawAlertRecord] = []
        for line_no, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                records.append(RawAlertRecord(line_no=line_no, parse_error=exc.msg))
                continue
            if not isinstance(payload, dict):
                records.append(RawAlertRecord(line_no=line_no, parse_error="alert must be a JSON object"))
                continue
            records.append(RawAlertRecord(line_no=line_no, payload=payload))
        return records
