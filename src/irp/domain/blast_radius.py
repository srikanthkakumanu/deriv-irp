"""Deterministic blast-radius score (0-100) domain service. No LLM involvement.

score = min(100, S + C + V + O + D)
  S impacted services      : min(30, 10 * len(impacted_services))
  C critical path involved : 25 if any impacted service has tier "critical_path"
  V max alert severity     : critical 20 | warning 10 | info 0
  O order routing impacted : 15 if an impacted service name matches the order-path
                             pattern, or (fallback) a critical-path "sink" is impacted
                             (critical-path service with no critical-path dependents)
  D deploy signal present  : 10
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Literal

from .alerts import Alert, Severity
from .base import ValueObject
from .incident import Incident
from .topology import ServiceTopology

DEFAULT_ORDER_PATTERN = r"order|execution|routing|router"
SEVERITY_POINTS: dict[Severity, int] = {Severity.CRITICAL: 20, Severity.WARNING: 10, Severity.INFO: 0}
FORMULA = (
    "score = min(100, S + C + V + O + D); S = min(30, 10 * impacted_service_count); "
    "C = 25 if any impacted service is tier 'critical_path'; V = 20 critical | 10 warning | 0 info (max alert severity); "
    "O = 15 if order routing impacted (name matches the order-path pattern, or a critical-path sink is impacted); "
    "D = 10 if a deploy/change signal is present"
)


class Factor(ValueObject):
    value: int | str | bool | tuple[str, ...]
    points: int
    detected_by: str | None = None
    note: str | None = None


class BlastRadius(ValueObject):
    incident_id: str
    blast_radius_score: int
    band: Literal["low", "medium", "high"]
    contributing_factors: dict[str, Factor]
    justification: str


class BlastRadiusCalculator:
    def __init__(self, topology: ServiceTopology, order_pattern: str = DEFAULT_ORDER_PATTERN) -> None:
        self._topology = topology
        self._order_re = re.compile(order_pattern, re.I)

    def order_path_services(self, impacted: tuple[str, ...]) -> tuple[tuple[str, ...], str]:
        by_name = tuple(s for s in impacted if self._order_re.search(s))
        if by_name:
            return by_name, "name_pattern"
        topo = self._topology
        sinks = tuple(s for s in impacted if topo.is_critical_path(s)
                      and not any(topo.is_critical_path(d) for d in topo.dependents(s)))
        return sinks, ("critical_path_sink" if sinks else "none")

    def score(self, incident: Incident, alerts_by_id: Mapping[str, Alert]) -> BlastRadius:
        impacted = incident.impacted_services
        max_sev = max((alerts_by_id[a].severity for a in incident.alert_ids), key=lambda s: s.rank)
        critical = tuple(s for s in impacted if self._topology.is_critical_path(s))
        order_svcs, order_how = self.order_path_services(impacted)
        factors = {
            "S_impacted_services": Factor(value=len(impacted), points=min(30, 10 * len(impacted))),
            "C_critical_path": Factor(
                value=critical, points=25 if critical else 0,
                note=None if any(self._topology.tier(s) for s in impacted) else "no tier information for impacted services; scored 0",
            ),
            "V_max_severity": Factor(value=max_sev.value, points=SEVERITY_POINTS[max_sev]),
            "O_order_routing": Factor(value=order_svcs, points=15 if order_svcs else 0, detected_by=order_how),
            "D_deploy_signal": Factor(value=incident.contains_deploy_signal, points=10 if incident.contains_deploy_signal else 0),
        }
        total = min(100, sum(f.points for f in factors.values()))
        band: Literal["low", "medium", "high"] = "high" if total >= 70 else "medium" if total >= 40 else "low"
        parts = [f"{len(impacted)} impacted service(s)"]
        if critical:
            parts.append(f"critical path touched ({', '.join(critical)})")
        parts.append(f"max severity {max_sev.value}")
        if order_svcs:
            parts.append(f"order routing exposed ({', '.join(order_svcs)})")
        if incident.contains_deploy_signal:
            parts.append("concurrent deploy/change signal")
        return BlastRadius(incident_id=incident.incident_id, blast_radius_score=total, band=band,
                           contributing_factors=factors, justification=f"Score {total} ({band}): " + "; ".join(parts) + ".")

    def score_all(self, incidents: list[Incident], alerts: tuple[Alert, ...] | list[Alert]) -> list[BlastRadius]:
        by_id = {a.alert_id: a for a in alerts}
        return [self.score(i, by_id) for i in incidents]
