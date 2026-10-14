"""Service topology value object (directed graph: service -> depends_on)."""
from __future__ import annotations

from functools import cached_property

from pydantic import Field

from .base import ValueObject

CRITICAL_PATH_TIER = "critical_path"


class ServiceNode(ValueObject):
    name: str
    depends_on: tuple[str, ...] = ()
    tier: str | None = None


class ServiceTopology(ValueObject):
    services: tuple[ServiceNode, ...] = Field(default_factory=tuple)

    @cached_property
    def _by_name(self) -> dict[str, ServiceNode]:
        return {s.name: s for s in self.services}

    @cached_property
    def _dependents(self) -> dict[str, frozenset[str]]:
        rev: dict[str, set[str]] = {s.name: set() for s in self.services}
        for svc in self.services:
            for dep in svc.depends_on:
                rev.setdefault(dep, set()).add(svc.name)
        return {k: frozenset(v) for k, v in rev.items()}

    def known(self, name: str) -> bool:
        return name in self._by_name

    def tier(self, name: str) -> str | None:
        node = self._by_name.get(name)
        return node.tier if node else None

    def is_critical_path(self, name: str) -> bool:
        return self.tier(name) == CRITICAL_PATH_TIER

    def direct_dependencies(self, name: str) -> frozenset[str]:
        node = self._by_name.get(name)
        return frozenset(node.depends_on) if node else frozenset()

    def dependents(self, name: str) -> frozenset[str]:
        return self._dependents.get(name, frozenset())

    def upstream(self, name: str) -> frozenset[str]:
        """Transitive dependencies of ``name``."""
        return self._walk(name, self.direct_dependencies)

    def downstream(self, name: str) -> frozenset[str]:
        """Transitive dependents of ``name`` (services that feel its failure)."""
        return self._walk(name, self.dependents)

    def causally_adjacent(self, a: str, b: str) -> bool:
        return a == b or b in self.upstream(a) or a in self.upstream(b)

    @staticmethod
    def _walk(start: str, edges) -> frozenset[str]:  # edges: Callable[[str], frozenset[str]]
        seen: set[str] = set()
        stack = list(edges(start))
        while stack:
            node = stack.pop()
            if node in seen or node == start:
                continue
            seen.add(node)
            stack.extend(edges(node))
        return frozenset(seen)
