# Plan approval (M6 / US04): Agent 3 -> Agent 4 -> PM gate

```
PM  POST /plan/generate ──► job agent3.schedule ──► DRAFT plan version N (snapshot + diff)
                                   │ queues
                                   ▼
                            job agent4.review  (create_agent loop, draft-only writes)
                                   │ risks (drafts) + verdict, then opens the gate
                                   ▼
                     project.status = plan_review, LangGraph interrupt()
                                   │
        PM  POST /plan/versions/N/approve  |  /reject (comment required)
                                   ▼
                  plan_approval.approve_plan  (one transaction, all checks re-run)
                  version approved, tasks get start/end/critical, risks open, project active
```

The Schedule tab shows the Agent 3 preview and the published Gantt as separate sections.
Plan Review is the PM decision point for a generated schedule. The retired
`POST /projects/{id}/scheduling/{draft_id}/decision` returns `410 Gone` for PMs on existing projects.
`GET /projects/{id}/scheduling` still serves the preview, and previously published dates and
historical schedule decisions remain available.

## Publishing manual task changes

In **Changes**, proposing a task edit stores the requested fields and an impact estimate. Accepting
it creates a new draft version with a recalculated schedule. The existing plan remains published
while the PM checks the draft's diff and runs Agent 4 or the automatic checks. Approval in Plan
Review publishes the edit, the recalculated task dates and the risks together. Rejecting the draft
preserves the published plan. The Changes tab links to the version and shows its publication status.

A requested start or end is an exact working-day constraint. Editing one endpoint keeps the other
endpoint from the current task. Infeasible ranges and dependencies must be corrected before the
PM can accept the request. The project has one draft at a time. A draft with an accepted change
must be approved or rejected before another draft can replace it.

## Rules enforced in code (each has a test)
- Agents write **drafts only**. Agent 4 has 7 read tools and 2 write tools (`save_draft_risks`,
  `save_plan_review`); there is no approve/reject tool, and no module under `app/agents` or `app/tools`
  imports the approval code (AST test). Only `services/plan_approval.py` sets a PlanVersion to `approved`.
- `project_id` / `plan_version_id` come from the run context, never from tool arguments.
- Approval requires a project in `plan_review`, the current reviewed draft, and matching plan inputs
  (project dates, approved tasks, dependencies, and team capacity and availability). If the review
  has warnings, submit `acknowledge_warnings: true` with the `warnings_token` from the version detail.
  A `409` with `code: warnings_changed` requires fetching the version again and reviewing the new
  warnings. `decided_by` comes from the authenticated PM.
- The automatic checks (`services/plan_rules.py`) always run; the agent's verdict can be raised by them,
  never lowered. Every risk needs severity, likelihood and evidence that exists in the plan.
- A second deterministic floor comes from the saved risk register: a `high` risk forces at least
  `needs_attention`, a `critical` risk forces `blocked`. A reviewer cannot record a critical risk and
  then call the plan ready.
- Agent 4 starts from `get_plan_health`, a code-computed brief (`services/plan_health.py`): delivery
  signal, days late/early, critical-path share, per-member workload, longest dependency chain,
  under-allocated estimates, dependency timing problems and a ranked attention list. The model spends
  its tokens on judgement, not arithmetic.
- `save_plan_review` also records advisory `confidence`, `recommendation` (`approve` |
  `approve_with_changes` | `revise`) and up to 5 `open_questions`. `recommendation: approve` with a
  non-ready verdict is rejected by the schema, and a `blocked` verdict only accepts `revise`, so the
  review can never contradict itself.
- Confidence is capped by the evidence, not by optimism: no scheduled tasks → `low`; no target end date,
  no team capacity or unassigned tasks → at most `medium` (`plan_health.confidence_cap`). The cap reasons
  are stored as `review.limitations` (written by the tool/rules, never by the model).
- Non-draft versions are immutable. Each version keeps a full JSON snapshot; `diff_from_prev` compares with
  the previous version number, `GET /plan/diff?from_version=&to_version=` compares any two, and the detail
  view also returns `diff_vs_approved`.

## Endpoints (PM only)
| Method | Path | |
|---|---|---|
| POST | `/projects/{id}/plan/generate` | queue Agent 3 (`overrides`: per-task `duration_days` / `assignee_member_id`) |
| POST | `/projects/{id}/plan/review` | queue another Agent 4 review (`rules_only` skips the LLM) |
| GET | `/projects/{id}/plan/versions`, `/versions/{v}` | list / detail (snapshot, risks, warnings, warning token, diffs) |
| GET | `/projects/{id}/plan/versions/{v}/health` | deterministic plan health + evidence cap (no LLM) |
| GET | `/projects/{id}/plan/diff?from_version=&to_version=` | diff of any two versions |
| GET/POST | `/projects/{id}/plan/versions/{v}/gate`, `/open-gate` | gate state / (re)open |
| POST | `/projects/{id}/plan/versions/{v}/approve`, `/reject` | the PM decision |
| GET | `/projects/{id}/plan/risks?status=` | risks (`draft`, `open`, `dismissed`, `superseded`) |

## Agent 4 settings (env)
`AGENT4_SUMMARY_TRIGGER_TOKENS` (60000), `AGENT4_SUMMARY_KEEP_MESSAGES` (12), `AGENT4_MAX_MODEL_CALLS` (12),
`AGENT4_MAX_TOOL_CALLS` (20), `AGENT4_MAX_GRAPH_STEPS` (120). The loop's token budget is `LLM_MAX_TOKENS_PER_JOB`;
the loop re-sends its history every turn, so use a larger value than the structured agents need (e.g. 40000) or
Agent 4 will stop early and the automatic checks write the review (`review.source = "rules"`).

The summary trigger must stay well above one review (a real run reports 8-12k tokens), otherwise the
middleware compacts the context after the read tools and the agent restarts its review from scratch. The graph
step limit is separate: every turn runs the middleware chain around the model and the tools, so one turn costs
several steps - 40 cut real reviews off after five tool calls (seen in the live eval).

## Evaluation
`evals/tests/test_agent4_tool_correctness.py` scores tool choice, inputs and outputs with DeepEval's
`ToolCorrectnessMetric` (deterministic, no API key, runs in `make eval-check`). `make eval-agent4` runs the same
scenarios with the real model and writes `evals/results/agent4_tool_correctness.json`.
