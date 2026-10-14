"""Use case: operator review checkpoint (human-in-the-loop)."""
from __future__ import annotations

from ...domain.errors import InvalidDecisionError
from ...domain.review import ReviewSession
from ..documents import CommandLogEntry, OperatorDecisionsDocument, ReviewView
from ..ports import Clock, OperatorConsole, ReportPresenter
from ..review_commands import REVIEW_PROMPT, CommandSyntaxError, parse_command


class ConductOperatorReview:
    def __init__(self, console: OperatorConsole, presenter: ReportPresenter, clock: Clock, operator_name: str) -> None:
        self._console = console
        self._presenter = presenter
        self._clock = clock
        self._operator = operator_name

    def execute(self, session: ReviewSession, view: ReviewView) -> OperatorDecisionsDocument:
        started = self._clock.now()
        self._console.show(self._presenter.review_screen(view))
        log: list[CommandLogEntry] = []
        for raw in self._console.commands():
            line = raw.strip()
            if not line:
                break
            ts = self._clock.now()
            error = self._apply(session, line, ts)
            log.append(CommandLogEntry(seq=len(log) + 1, ts=ts, line=line,
                                       status="error" if error else "applied", error=error))
            self._console.show(f"  ! {error}" if error else "  ok")
        final = session.final_states()
        self._console.show(self._presenter.review_summary(final))
        return OperatorDecisionsDocument(
            operator=self._operator, input_mode=self._console.mode, review_started_at=started,
            review_completed_at=self._clock.now(), prompt_shown=REVIEW_PROMPT, raw_commands=log,
            decisions=list(session.decisions), final_state=final,
        )

    @staticmethod
    def _apply(session: ReviewSession, line: str, ts: str) -> str | None:
        try:
            cmd = parse_command(line)
            if cmd.verb == "urgency":
                session.override_urgency(cmd.incident_id, cmd.argument, ts)
            elif cmd.verb == "approve":
                session.approve(cmd.incident_id, cmd.argument, ts)
            else:
                session.reject(cmd.incident_id, cmd.argument, ts)
        except (CommandSyntaxError, InvalidDecisionError) as exc:
            return str(exc)
        return None
