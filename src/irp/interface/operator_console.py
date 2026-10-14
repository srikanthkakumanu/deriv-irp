"""OperatorConsole adapters: interactive terminal / piped stdin, and scripted (decisions file)."""
from __future__ import annotations

import sys
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path


class TerminalOperatorConsole:
    """Reads review commands from stdin (TTY or pipe). EOF ends the review."""

    def __init__(self, output: Callable[[str], None] = print, reader: Callable[[str], str] = input) -> None:
        self._out = output
        self._read = reader
        self.mode = "interactive_tty" if sys.stdin.isatty() else "stdin_pipe"

    def show(self, text: str) -> None:
        self._out(text)

    def commands(self) -> Iterator[str]:
        while True:
            try:
                yield self._read("> ")
            except EOFError:
                return


class ScriptedOperatorConsole:
    """Replays review commands from a file or list (lines starting with '#' are comments)."""

    def __init__(self, lines: Iterable[str], source: str, output: Callable[[str], None] = print) -> None:
        self._lines = list(lines)
        self._out = output
        self.mode = f"decisions_file:{source}"

    @classmethod
    def from_file(cls, path: Path, output: Callable[[str], None] = print) -> "ScriptedOperatorConsole":
        return cls(Path(path).read_text(encoding="utf-8").splitlines(), str(path), output)

    def show(self, text: str) -> None:
        self._out(text)

    def commands(self) -> Iterator[str]:
        for line in self._lines:
            if line.strip().startswith("#"):
                continue
            self._out(f"> {line}")
            yield line
