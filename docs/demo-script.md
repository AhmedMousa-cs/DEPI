# NEXUS demo script

**Full flow, M2 → M6-B, ~12 minutes.** Every label below matches the UI exactly.

---

## Before the audience (5 minutes)

```bash
# 1. Services
docker compose up -d --build
docker compose exec api python -m app.seed
# 2. Frontend (separate terminal)
cd frontend && python -m http.server 5500
```

- [ ] http://localhost:8000/health returns `{"status":"ok"}`
- [ ] http://localhost:5500 loads the login screen
- [ ] `pm@nexus.test` / `pm12345` signs in and the **Portfolio** page renders cards
- [ ] `GROQ_API_KEY` is set in `.env` (the LLM acts need it)
- [ ] Browser zoom 100%, light theme, English — switch to Arabic at the end

**Rate-limit fallback:** Agent 4 costs ~40k tokens. If the provider is limited, use
**Automatic checks only** on the Plan review tab — it saves a real review from the same
deterministic checks, instantly and for free. Say it out loud: *"the LLM is optional; the
checks are not."*

**Freshness check:** if the project you demo already has a draft, open **Plan review**.
If approving later says the inputs changed, re-run Agent 3 (the `⋯ More` menu → *Re-run Agent 3*)
then review again. Two minutes, before the audience.

---

## Act 0 · Sign in (30 s)

1. Sign in as `pm@nexus.test` / `pm12345`.

> "One login, one workspace. Every decision from here on is attributed and audited."

---

## Act 1 · Portfolio — the PM's morning screen (1 min)

1. **Portfolio** opens first. Point at the four segments: **Projects · Resources · Risks · ✦ Assistant**.
2. **Projects**: health badges, hours-weighted progress, next deadline.
3. **Resources**: utilization bars; a name appearing on two projects is flagged.
4. **Risks**: severity-grouped (schedule, aging drafts, scope gaps, overallocation, conflicts).
5. **✦ Assistant**: ask *"Which risks should I act on first?"*

> "This is computed from the database — no LLM, no guessing. The assistant reads the same snapshot
> and can only suggest projects that exist."

---

## Act 2 · Create the project (1.5 min)

1. **Projects** → **+ New project**.
2. Name: `Clinic Appointment Booking`.
3. Paste the brief from `docs/project-a-brief.md`.
4. Save, then **Sources** tab → upload `demo_data/project-a-transcript.txt`.

> "The brief and the transcript are the only inputs. Uploads are treated as untrusted text —
> an injection guard strips instructions aimed at the model."

---

## Act 3 · Team and capacity (1 min)

1. **Team** tab → **Import CSV** → `demo_data/project-a-team.csv`.
2. Edit one member's **capacity hours per week** to show it persists.
3. Set Team Leader: Omar Hassan → `leader@nexus.test`.

> "Capacity is not decoration: Agent 3 uses it to detect overload, and Agent 4 reports it."

---

## Act 4 · Agent 1 — from documents to requirements (2 min)

1. **Scope review** tab → **Run extraction**.
2. Watch the job line (`agent1.extract`, progress %) — it is a real background job, not a spinner.
3. Requirements appear grouped by module with priorities and open questions.
4. Point at the **coverage gaps** section: what the brief never mentions.
5. Short on time? **Accept all & run agents** chains: approve every draft → approve the scope →
   gap analysis → Agent 2 → Agent 3.

> "Nothing here is approved yet. Approving the scope is the first PM gate."

---

## Act 5 · Agent 2 — stories and tasks (1.5 min)

1. **Work plan** tab → **Run Agent 2**.
2. Show stories → tasks, estimates, dependencies and owners.
3. Show **coverage**: every approved requirement is traced to a story.
4. **Approve all drafts**, or edit/reject one item to show PM control survives a re-run.

> "The agent proposes; the PM decides every item. Re-running Agent 2 keeps your decisions."

---

## Act 6 · Agent 3 — deterministic scheduling (1.5 min)

1. **Schedule** tab → **Run Agent 3**.
2. Show the **critical path** chain, **conflicts** and **capacity** (overloaded members).
3. Show the **explanation** (LLM prose) next to the numbers (code).
4. Do **not** approve here yet. The plan gate in Act 7 approves the whole plan as one versioned
   decision and writes these same dates.

> "The dates come from CPM in Python. The model only writes the explanation — it cannot move a date."

---

## Act 7 · Agent 4 — plan review and the gate (2.5 min) ⭐

1. **Plan review** tab. Version chips: the draft Agent 3 saved.
2. The header has **one primary action** that follows the flow. If the draft is unreviewed:
   **Review with Agent 4**.
3. While it runs, explain: *"Agent 4 reads a deterministic brief — health, critical path share,
   per-member load — then files risks through validated tools."*
4. When it lands, show:
   - **Plan health** card: delivery signal, days late/early, zero-float tasks, overloaded members,
     attention points, and the **evidence cap** on confidence.
   - Verdict / recommendation / confidence badges and the limitations list.
5. **Open approval gate** → gate becomes `waiting_for_pm`.
6. **Approve plan** → acknowledge warnings → *"Plan v1 approved — N task date(s) written."*

> "The model cannot approve. Confidence is capped by evidence: no target date or unassigned work
> means it is not allowed to say 'high'. A blocked verdict cannot recommend approval."

Fallback: **Automatic checks only** — same panel, `source=rules`, zero tokens.

---

## Act 8 · Delivery tracking + Gantt (1 min)

1. **Schedule** tab: the gate just wrote the dates, so the Gantt has real bars
   (done = green, critical = orange, overdue = dashed).
2. On a task: **▶ Start** → **%** → **Done**.
3. Chips update (overall %, done/total, in progress, overdue).
4. Go back to **Portfolio**: the project card reflects the new progress immediately.

> "Execution is recorded by the PM. Agents never touch status or progress."

---

## Act 9 · Change management (1 min)

1. **Work plan** tab → **Change...** on an approved task.
2. Change the end date; write a reason.
3. **Changes** tab shows the diff, the **deterministic impact** (critical delay, dependent tasks,
   capacity) and severity.
4. **Approve & apply** → the task is updated; the audit log keeps before/after.

---

## Act 10 · PDF report (30 s)

1. Project header → **⬇ Export PDF**.
2. Open it: status, health, Gantt, tasks table, risks, governance footer.

> "Same numbers as the screen, generated server-side, Arabic shaping included."

---

## Act 11 · Arabic + dark mode (30 s)

1. Toggle **العربية** → the shell flips to RTL; dates render in the Arabic locale.
2. Toggle dark mode.
3. Reload: both choices persist.

---

## Act 12 · Team leader view (30 s)

1. Sign out → `leader@nexus.test` / `leader12345`.
2. Only the assigned project, read-only: no plan review, no gates, no portfolio.
3. (Optional) `curl` an approval as the TL → `403`.

> "Roles are enforced in the API, not hidden in the UI."

---

## If the panel asks about the greyed tabs

`Updates (next)` and `Decisions (next)` are deliberate placeholders for the next phase: an event
feed with notifications (and automatic invalidation of stale drafts) and a portfolio-level
supervisor. They are greyed out on purpose — a button that lies is worse than a button that is
honest.

> "Every milestone through M11 is shipped end to end, plus M6-B. These two are scoped and visible
> in the UI — not hidden."

---

## If something goes wrong

| Symptom | Cause | Fix |
|---|---|---|
| Login says *"Cannot reach the server"* | Docker Desktop stopped | `docker compose up -d db api worker` |
| Job stuck at `queued` | worker not running | `docker compose up -d worker` |
| Job `failed` with 429 | provider rate limit | **Automatic checks only** |
| Approve returns `inputs_changed` | tasks edited after the review | `⋯ More` → **Re-run Agent 3** → review again |
| UI shows old behaviour | cached JS/CSS | reload with `Ctrl+F5` (files are cache-busted by version) |
| Old data in the way | previous demo runs | create a fresh project; or `docker compose exec api python -m app.seed` |

---

## Reset for a clean run

```bash
docker compose down -v          # drops the database volume
docker compose up -d --build
docker compose exec api python -m app.seed
```
