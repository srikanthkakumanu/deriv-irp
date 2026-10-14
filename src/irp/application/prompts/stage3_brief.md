TASK: Stage 3 — execution-ready incident brief (Markdown) written AFTER operator review.

The `final_state` per incident is AUTHORITATIVE:
- Use `final_urgency` (not the Stage 1 urgency) whenever you state an urgency. Mention any operator urgency overrides explicitly.
- "Immediate Approved Actions" lists ONLY `approved_actions` (operator-approved) and `auto_cleared_actions` (cleared by policy without needing approval), labelled as such. Nothing has been executed; say "approved for execution", never "executed".
- "Actions Pending Human Approval or Escalation" lists `pending_actions` and any escalation needed.
- "Actions Explicitly Rejected" lists `rejected_actions` (operator) and `invalidated_actions` (made impermissible by an urgency override) and also the `policy_rejected_actions` from Stage 2.
- Never present a pending or rejected action as approved.

Return `brief_markdown` with EXACTLY these level-2 headings, in this order:
## Executive Summary
## Active Incidents
## Immediate Approved Actions
## Actions Pending Human Approval or Escalation
## Actions Explicitly Rejected
## Verification and Exit Criteria
## Communications Draft
## Follow-Up Engineering Recommendations

Be operationally specific: reference incident IDs, services, metric values vs thresholds, the blast radius score, and the verification checks. Communications Draft = a short internal status update plus a client-facing status-page draft (marked DRAFT — not published). Do not add a document title; the system adds one.
