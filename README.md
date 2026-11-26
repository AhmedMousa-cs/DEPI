# NEXUS — Project Intelligence Platform

[![CI](https://github.com/AhmedMousa-cs/DEPI/actions/workflows/ci.yml/badge.svg)](https://github.com/AhmedMousa-cs/DEPI/actions/workflows/ci.yml)

NEXUS turns a project brief and meeting transcripts into an **approved, dated and tracked plan** —
without ever letting an LLM change the plan behind the project manager's back.

> **Agents draft. The PM decides. Every decision is audited.**

---

## Why

Project managers lose days turning scattered documents into a plan, and generic AI assistants
happily invent dates, assign people who are already booked, and "approve" their own work.
NEXUS splits the job:

- **Agents do the drafting** — extraction, stories/tasks, scheduling, review.
- **The PM does the deciding** — every draft is explicitly approved, edited or rejected.
- **Code does the arithmetic** — critical path, capacity, conflicts and delivery health are
  computed deterministically, never guessed by a model.
- **The database is the only writer** — agents propose through validated tools; they never
  write task rows, statuses or dates.

---

## What it does

| # | Stage | Agent | Output | Gate |
|---|-------|-------|--------|------|
| 1 | **Sources** | — | Brief + transcripts stored as text (`.txt`, `.md`, `.docx`, `.pdf`, ≤ 5 MB) | — |
| 2 | **Scope review** | Agent 1 | Requirements grouped by module, with priorities and open questions | PM approves the scope |
| 3 | **Work plan** | Agent 2 | Stories → tasks with estimates, dependencies and assignments | PM decides every draft item |
| 4 | **Schedule** | Agent 3 | CPM dates, critical path, resource conflicts, a versioned plan snapshot | Preview only — the decision happens in Plan review |
| 5 | **Plan review** | Agent 4 | Verdict, risks, findings, confidence, recommendation | PM approves or rejects — the only decision point |
| 6 | **Delivery** | — | Gantt + per-task progress (`todo → in progress → done`) | PM records execution |
| 7 | **Changes** | — | Field-level diff, deterministic impact, a staged draft plan | PM accepts a proposal → approves the draft in Plan review |
| 8 | **Portfolio** | — | Cross-project health, resources, risks, assistant chat | PM only |
| 9 | **Report** | — | PDF status report with Gantt, risks and Arabic shaping | PM only |

Everything is available in **English and Arabic (RTL)**, with a **dark mode**.

---

## Architecture

```
┌──────────────────────────┐
│  Browser (vanilla JS)    │  no build step, no framework
│  app.js · auth.js · CSS  │  EN/AR · RTL · dark mode
└────────────┬─────────────┘
             │ JWT (Bearer)
┌────────────▼─────────────┐        ┌───────────────────────────┐
│  FastAPI (api)           │◄──────►│  PostgreSQL 16 + pgvector │
│  routers: auth, projects │        │  SQLAlchemy 2 · Alembic   │
│  sources, scope, plan,   │        │  (single migration head)  │
│  schedule, plan, changes │        └───────────────────────────┘
│  portfolio, reports      │
└────────────┬─────────────┘
             │ enqueue job
┌────────────▼─────────────┐
│  jobs table (PostgreSQL) │  SKIP LOCKED claim · retry with
│  queued/running/failed   │  backoff · stale-job recovery
└────────────┬─────────────┘
             │
┌────────────▼─────────────┐
│  worker (python -m app.worker)
│   ├─ agent1.extract   → LLM  (requirements)
│   ├─ agent2.plan      → LLM  (stories & tasks, self-correction loop)
│   ├─ agent3.schedule  → LLM + deterministic CPM
│   └─ agent4.review    → LLM tool-calling over a deterministic brief
└──────────────────────────┘
             │
             ▼
   LLM provider (Groq / any OpenAI-compatible API)
   LangGraph · LangChain · Langfuse tracing
```

**Design rules enforced in code**

- A model can only act through **validated tools** with Pydantic schemas; invalid output is
  rejected and retried, never partially applied.
- **Deterministic checks own the facts**: critical path, capacity overload, dependency timing and
  delivery health come from `services/plan_health.py`, `plan_rules.py` and `scheduling.py`.
- **Evidence caps confidence**: with no tasks, no target date or unassigned work, Agent 4 cannot
  report `high` confidence — the cap reason is stored as a `limitation`.
- **Verdict consistency**: a `blocked` verdict cannot carry an `approve` recommendation.
- **Budgets are enforced server-side** (`LLM_MAX_TOKENS_PER_JOB`, `LLM_DAILY_BUDGET_USD`).
- **Uploaded documents are treated as untrusted input** (`services/injection_guard.py`).
- **Every decision writes an audit row** (`audit_log`): who, what, before/after.

---

## Quick start

**Prerequisites:** Docker Desktop (with Compose) and Python 3 (only to serve the static frontend).

```bash
git clone https://github.com/AhmedMousa-cs/DEPI.git
cd DEPI

# 1. Environment
cp .env.example .env          # PowerShell: Copy-Item .env.example .env
#    → set JWT_SECRET to 32+ random characters
#    → set GROQ_API_KEY for the LLM agents (optional: the deterministic path works without it)

# 2. Database + API + worker
docker compose up -d --build

# 3. Seed the demo users
docker compose exec api python -m app.seed

# 4. Frontend (separate terminal)
cd frontend
python -m http.server 5500
```

| URL | What |
|---|---|
| http://localhost:5500 | NEXUS UI |
| http://localhost:8000/docs | OpenAPI / Swagger |
| http://localhost:8000/health | API health check |

### Demo accounts

| Role | Email | Password | Sees |
|---|---|---|---|
| Project Manager | `pm@nexus.test` | `pm12345` | Everything, all projects, all decisions |
| Team Leader | `leader@nexus.test` | `leader12345` | Only assigned projects, read-only (approvals → 403) |

> Containers serve a **built image**. After changing backend code run
> `docker compose up -d --build api worker`.

---

## The plan approval gate

The plan is approved as **one versioned unit**, not task by task:

```
draft ──review──► draft + review ──open gate──► waiting_for_pm ──┬── approve ──► approved (dates written)
                                                                 └── reject  ──► rejected (risks archived)
```

- A **draft** is a full snapshot (tasks, dates, critical path, conflicts, risks).
- The **Schedule** tab is preview-only: Agent 3's draft next to the published Gantt. The legacy
  `POST /projects/{id}/scheduling/{draft_id}/decision` returns `410 Gone`; every decision happens here.
- A **task change** takes the same road: accepting a proposal in Changes stages a draft version
  (the published plan stays live). Approving that draft publishes the edited fields, the recalculated
  dates and the risks in one transaction; rejecting it keeps the current plan. One change draft can
  be open at a time.
- A requested start or end date is an **exact working-day constraint** and stays pinned in later
  Agent 3 runs until the PM changes it again.
- Approving writes the snapshot dates to the tasks and moves the project to `active`.
- Rejecting archives the draft and its risks; nothing is written to tasks.
- Approval re-checks the planning inputs inside the transaction (project dates, approved tasks,
  dependencies, capacity and availability). If the warnings shown to the PM have changed, the API
  returns `409 warnings_changed` and the screen refreshes them before the acknowledgement.
- `GET /projects/{id}/plan/versions/{v}/health` is deterministic and PM-only: it explains *why*
  a plan is at risk without spending a single token.

---

## Tests

```bash
# Backend (recommended: same image as production)
make test
# → docker compose run --rm -e RUN_TESTS_IN_DOCKER=1 -v "$PWD/backend:/code" api python -m pytest -q

# Backend on the host (needs a reachable PostgreSQL)
cd backend && python -m pytest -q

# Deterministic golden evals (no API key, no network)
# needs the eval extras once: pip install -r evals/requirements.txt
make eval-check

# Live evals (needs GROQ_API_KEY; free tier is rate-limited)
make eval-agent1
make eval-agent2
make eval-agent2-quality
make eval-agent4
```

Current state: **607 backend tests**, 48 test modules, deterministic golden projects A/B/C, and a
DeepEval tool-correctness suite for Agent 4.

> Run only **one** pytest session at a time — every run rebuilds the shared `nexus_test` database.

CI (`.github/workflows/ci.yml`) runs the backend suite, the deterministic evals and a
`node --check` gate on every push and pull request.

---

## Repository layout

```
backend/
  app/
    api/            FastAPI routers (auth, projects, sources, scope, planning,
                    scheduling, plan, changes, portfolio, reports, jobs, users)
    agents/         Agent 1–4 + prompts (versioned .md files) + deterministic checks
    services/       jobs queue, plan versions/gate, plan health & rules, delivery,
                    portfolio, changes, PDF reports, injection guard
    tools/          validated LLM tools (compute_dates, capacity, conflicts, save_*)
    db/             SQLAlchemy models + session
    schemas/        Pydantic contracts
  alembic/versions/ 12 migrations, single head
  tests/            48 pytest modules
frontend/
  index.html        cache-busted app.js / styles.css
  app.js            single-file SPA: views, actions, polling, i18n (EN/AR)
  auth.js           login + token handling
  styles.css        light/dark themes, RTL-aware layout
evals/
  golden/           Project A/B/C fixtures + expected metrics
  tests/            deterministic golden + scheduling + Agent 4 tool tests
docs/               handoff, ADRs, plan-approval guide, demo script
demo_data/          brief, transcript and team CSV for the demo project
```

---

## Documentation

| File | Contents |
|---|---|
| [`docs/HANDOFF.md`](docs/HANDOFF.md) | Milestone-by-milestone evidence log (the source of truth) |
| [`docs/decisions.md`](docs/decisions.md) | Architecture Decision Records (ADR-001 → ADR-024) |
| [`docs/plan-approval.md`](docs/plan-approval.md) | How the approval gate and Agent 4 work |
| [`docs/demo-script.md`](docs/demo-script.md) | End-to-end demo script, M2 → M6-B |
| [`docs/project-a-brief.md`](docs/project-a-brief.md) | Demo brief used by the script |

---

## Known limitations / next phase

Honest list, so nobody is surprised in a demo:

- **`Updates (next)` and `Decisions (next)` tabs are disabled placeholders.** The next blueprint
  phase (event feed with notifications, automatic invalidation of stale drafts, and a
  portfolio-level supervisor) is not built yet.
- **Progress updates use polling**, not push (no WebSocket/SSE).
- **The live Agent 4 eval needs a paid LLM tier** (~40k tokens per review). The
  *Automatic checks only* button runs the same deterministic checks with no model call, so a
  demo never depends on the rate limit.
- **No frontend test suite**; CI only syntax-checks `app.js` / `auth.js`.

---

## Contributing

- `main` is protected: all changes go through a Pull Request.
- Branch names describe the work (`feature/…`, `fix/…`).
- Definition of Done: tests pass, UI is wired end to end, `.env.example` is up to date, and the
  migration chain still has **one head**.
