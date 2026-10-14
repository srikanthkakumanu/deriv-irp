"""Incident entity and the deterministic IncidentCorrelator domain service.

Correlation algorithm (no LLM)
------------------------------
1. Split alerts into *symptoms* and *change signals* (``Alert.is_change_signal``:
   metric matches ``deploy|release|rollout|change`` or message matches
   ``deploy|canary|rollout|release``).
2. Symptoms are linked pairwise (union-find, so links chain) when ALL hold:
   a. shared region; b. ``|Δts| <= window_s``; c. causal adjacency: same service, or
   one service is a transitive dependency of the other in the topology.
   Services absent from the topology are adjacent only to themselves.
3. Each change signal joins the best eligible incident: same region, within
   ``window_s`` of the incident's span, its service causally adjacent to an alerting
   service. Preference: same service, then smallest time gap, then lowest index.
   Otherwise it becomes its own incident.
4. Incidents are numbered ``INC-001..`` by earliest alert.

Derived fields: ``primary_service`` = most upstream alerting service (no transitive
dependency on another alerting service in the incident), tie-break max severity,
earliest alert, name. ``impacted_services`` = alerting services plus their transitive
dependents.
"""
from __future__ import annotations

from datetime import datetime

from .alerts import Alert, Severity, format_utc
from .base import ValueObject
from .topology import ServiceTopology

DEFAULT_WINDOW_S = 120


class Incident(ValueObject):
    incident_id: str
    alert_ids: tuple[str, ...]
    primary_service: str
    impacted_services: tuple[str, ...]
    region: str
    correlation_basis: tuple[str, ...]
    contains_deploy_signal: bool
    alerting_services: tuple[str, ...]
    deploy_alert_ids: tuple[str, ...]
    start_ts: str
    end_ts: str
    max_severity: Severity
    tiers: tuple[str, ...]
    critical_path_services: tuple[str, ...]


class _DisjointSet:
    def __init__(self, size: int) -> None:
        self._parent = list(range(size))

    def find(self, x: int) -> int:
        while self._parent[x] != x:
            self._parent[x] = self._parent[self._parent[x]]
            x = self._parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self._parent[max(ra, rb)] = min(ra, rb)


class IncidentCorrelator:
    def __init__(self, topology: ServiceTopology, window_s: int = DEFAULT_WINDOW_S) -> None:
        self._topology = topology
        self._window_s = window_s

    @property
    def window_s(self) -> int:
        return self._window_s

    def correlate(self, alerts: tuple[Alert, ...] | list[Alert]) -> list[Incident]:
        ordered = sorted(alerts, key=lambda a: a.sort_key)
        symptoms = [a for a in ordered if not a.is_change_signal]
        changes = [a for a in ordered if a.is_change_signal]
        clusters = self._cluster_symptoms(symptoms)
        attached, orphans = self._attach_change_signals(clusters, changes)
        groups: list[tuple[list[Alert], list[Alert]]] = list(zip(clusters, attached)) + [([], [c]) for c in orphans]
        groups.sort(key=lambda g: min(a.sort_key for a in g[0] + g[1]))
        return [self._build(f"INC-{n:03d}", sym, chg) for n, (sym, chg) in enumerate(groups, start=1)]

    # -- steps -------------------------------------------------------------
    def _cluster_symptoms(self, symptoms: list[Alert]) -> list[list[Alert]]:
        dsu = _DisjointSet(len(symptoms))
        for i, a in enumerate(symptoms):
            for j in range(i + 1, len(symptoms)):
                b = symptoms[j]
                if (b.ts - a.ts).total_seconds() > self._window_s:
                    break  # sorted by time: nothing later can be inside the window
                if a.region == b.region and self._topology.causally_adjacent(a.service, b.service):
                    dsu.union(i, j)
        buckets: dict[int, list[Alert]] = {}
        for i, a in enumerate(symptoms):
            buckets.setdefault(dsu.find(i), []).append(a)
        return sorted(buckets.values(), key=lambda g: g[0].sort_key)

    def _attach_change_signals(self, clusters: list[list[Alert]], changes: list[Alert]) -> tuple[list[list[Alert]], list[Alert]]:
        attached: list[list[Alert]] = [[] for _ in clusters]
        orphans: list[Alert] = []
        for change in changes:
            best: tuple[tuple[int, float, int], int] | None = None
            for idx, cluster in enumerate(clusters):
                if cluster[0].region != change.region:
                    continue
                gap = self._gap_seconds(change.ts, cluster[0].ts, max(a.ts for a in cluster))
                if gap > self._window_s:
                    continue
                services = {a.service for a in cluster}
                if not any(self._topology.causally_adjacent(change.service, s) for s in services):
                    continue
                key = (0 if change.service in services else 1, gap, idx)
                if best is None or key < best[0]:
                    best = (key, idx)
            if best is None:
                orphans.append(change)
            else:
                attached[best[1]].append(change)
        return attached, orphans

    @staticmethod
    def _gap_seconds(t: datetime, start: datetime, end: datetime) -> float:
        if start <= t <= end:
            return 0.0
        return min(abs((t - start).total_seconds()), abs((t - end).total_seconds()))

    def _build(self, incident_id: str, symptoms: list[Alert], changes: list[Alert]) -> Incident:
        topo = self._topology
        members = sorted(symptoms + changes, key=lambda a: a.sort_key)
        alerting = sorted({a.service for a in symptoms}) or sorted({a.service for a in changes})
        alerting_set = set(alerting)

        def max_rank(svc: str) -> int:
            return max(a.severity.rank for a in members if a.service == svc)

        def first_seen(svc: str) -> datetime:
            return min(a.ts for a in members if a.service == svc)

        roots = [s for s in alerting if not (topo.upstream(s) & (alerting_set - {s}))] or alerting
        primary = sorted(roots, key=lambda s: (-max_rank(s), first_seen(s), s))[0]

        impacted: set[str] = set(alerting) | {a.service for a in changes}
        for svc in alerting:
            impacted |= topo.downstream(svc)
        impacted_sorted = tuple(sorted(impacted))
        critical = tuple(s for s in impacted_sorted if topo.is_critical_path(s))

        return Incident(
            incident_id=incident_id,
            alert_ids=tuple(a.alert_id for a in members),
            primary_service=primary,
            impacted_services=impacted_sorted,
            region=members[0].region,
            correlation_basis=tuple(self._basis(members, symptoms, changes, alerting, impacted, critical)),
            contains_deploy_signal=bool(changes),
            alerting_services=tuple(alerting),
            deploy_alert_ids=tuple(a.alert_id for a in changes),
            start_ts=format_utc(members[0].ts),
            end_ts=format_utc(members[-1].ts),
            max_severity=max((a.severity for a in members), key=lambda s: s.rank),
            tiers=tuple(sorted({t for s in impacted_sorted if (t := topo.tier(s))})),
            critical_path_services=critical,
        )

    def _basis(self, members: list[Alert], symptoms: list[Alert], changes: list[Alert], alerting: list[str],
               impacted: set[str], critical: tuple[str, ...]) -> list[str]:
        topo = self._topology
        basis = [f"shared_region:{members[0].region}"]
        if len(members) > 1:
            basis.append(f"time_window<={self._window_s}s")
        for svc in alerting:
            for dep in sorted(topo.direct_dependencies(svc)):
                if dep in alerting:
                    basis.append(f"topology:{svc}->depends_on->{dep}")
        for svc in alerting:
            own = [a for a in symptoms if a.service == svc]
            if len(own) > 1:
                families = sorted({a.metric.split("_")[0] for a in own})
                basis.append(f"same_service_repeat:{svc}[{','.join(families)}]")
        downstream_only = sorted(impacted - set(alerting) - {a.service for a in changes})
        if downstream_only:
            basis.append(f"downstream_blast:{','.join(downstream_only)}")
        if critical:
            basis.append(f"critical_path_impacted:{','.join(critical)}")
        basis += [f"deploy_signal:{a.service}@{format_utc(a.ts)}" for a in changes]
        unknown = [s for s in alerting if not topo.known(s)]
        if unknown:
            basis.append(f"service_not_in_topology:{','.join(unknown)}")
        if len(members) == 1:
            basis.append("singleton:no_correlated_alerts")
        return basis
