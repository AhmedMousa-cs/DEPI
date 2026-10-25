# NEXUS

## Multi-Agent Project Monitoring and Evaluation System

NEXUS helps Project Managers monitor and evaluate multiple projects from one portfolio. Each project has its own Project Orchestrator, which processes project information, coordinates four specialist agents, maintains project state and prepares reports. A Global Supervisor aggregates project reports and alerts for the portfolio.

NEXUS supports decisions with evidence and recommendations. People remain responsible for approving project scope, plans and changes.

## The problem NEXUS addresses

Project information is scattered across meeting transcripts, project plans, team notes, risk reports and status updates. Project Managers often have to reconcile these sources manually to answer questions such as:

- What was agreed at project kickoff?
- Which requirements and user stories have been confirmed?
- Is the project on schedule and adequately resourced?
- Which blockers or risks need attention?
- Does a proposed change affect another project?
- Which decisions are waiting for the Project Manager?

NEXUS organizes this information into a traceable project workflow and a portfolio-level view.

## Product goals

- Turn kickoff briefs and optional initial transcripts into reviewable project evidence.
- Convert approved scope into requirements, user stories, tasks and a proposed timeline.
- Keep Team Leader runtime updates separate from kickoff inputs.
- Surface blockers, schedule risks and cross-project signals.
- Give the Project Manager clear, evidence-backed decisions to review.
- Preserve decision history and plan versions.

## Architecture overview

### Project Orchestrator - one per project

A Project Orchestrator coordinates a single project's workflow. New projects use a shared orchestrator template with project-specific configuration and data. The orchestrator:

- Receives kickoff inputs and runtime events through separate paths.
- Runs the specialist agents in dependency order.
- Stores drafts, approvals, project state and plan versions.
- Recalculates only affected project data after an approved change.
- Produces a structured project report for the Global Supervisor.

### Four specialist agents

1. **Meeting and Evidence Agent**  
   Reads a project brief and optional kickoff transcripts. It extracts evidence-linked draft requirements, decisions and action items, retaining source excerpts and confidence.

2. **Requirements and User Stories Agent**  
   Uses the approved scope baseline to propose features, requirements and user stories.

3. **Timeline and Resource Estimation Agent**  
   Uses approved stories, project dates, team skills and availability to propose tasks, milestones and resource allocations.

4. **Risk Analysis Agent**  
   Uses approved scope, tasks, schedule, resources and runtime updates to identify risks, estimate likelihood and impact, and suggest mitigations.

The Project Orchestrator is workflow software, not a fifth specialist agent. Project state, event storage and report generation are supporting services.

### Global Supervisor

The Global Supervisor aggregates reports and events from project orchestrators. It monitors delays, blockers, high risks, pending decisions and potential cross-project resource conflicts. It sends alerts and recommendations to the Project Manager with their evidence and impact.

## Human decision authority

NEXUS uses explicit human approval gates:

1. **Scope gate:** A Project Manager or scope owner reviews extracted evidence and approves the project baseline.
2. **Plan gate:** The Project Manager reviews the proposed stories, schedule, resources and risks before execution starts.
3. **Change gate:** A date, scope or resource change is applied only after the Project Manager approves it.
4. **Runtime update path:** After execution starts, the Team Leader submits progress notes, blockers and proposed changes. The system records each update as a dated, attributed event.
5. **Decision audit:** Approvals, rejections, modifications, information requests and deferrals are recorded with the decision maker and reason.

Agents analyze and recommend. They do not approve project scope, change plans or contact project teams directly.

## End-to-end workflow

### 1. Project kickoff

The Project Manager creates a project, enters a project brief and may attach an initial meeting transcript. These are kickoff inputs. They are stored with the project as source material.

### 2. Evidence extraction and review

The Meeting and Evidence Agent creates draft items with source excerpts and confidence labels. The Project Manager or scope owner edits, approves or rejects each item. Only approved items become part of the scope baseline.

### 3. Planning

The Requirements and User Stories Agent creates stories from the approved baseline. The Timeline and Resource Estimation Agent proposes tasks, milestones and resource needs. The Risk Analysis Agent prepares an initial risk register. The Project Manager reviews and approves the plan before execution.

### 4. Execution updates

Once a project is active, the Team Leader can submit progress updates, notes, blockers or proposed changes. The Project Update Handler stores each submission as a runtime event and refreshes the affected project state and report. Runtime updates are not processed as kickoff transcripts.

### 5. Portfolio monitoring and decisions

The Global Supervisor combines project reports and events into a portfolio view. The Project Manager reviews critical alerts and proposals, then approves, rejects, modifies, requests more information or defers each decision.

## MVP user stories

- **US01 - Create a project:** Create a project from a template with a brief and optional kickoff transcript.
- **US02 - Extract evidence:** Generate draft requirements, decisions and action items with source excerpts.
- **US03 - Review scope:** Edit, approve or reject extracted items before they enter the baseline.
- **US04 - Submit runtime updates:** Allow a Team Leader to report progress, blockers and proposed changes after execution starts.
- **US05 - Review a project plan:** View stories, tasks, dates, resources and risks in a project workspace.
- **US06 - Monitor the portfolio:** View project status, alerts, high risks and shared issues.
- **US07 - Decide on proposals:** Approve, reject, modify, request information or defer, with a recorded decision.

## Team delivery approach

The team works story by story. All members contribute to the same end-to-end user story using temporary pairs that rotate between stories. Each story includes the relevant interface, data/API, agent or workflow work, integration and acceptance walkthrough.

For a six-person team, begin with three temporary pairs:

- Pair A: project creation flow and interface.
- Pair B: Project and Source data model and storage.
- Pair C: optional transcript intake, integration and acceptance walkthrough.

For a five-person team, use two pairs and one rotating integration driver. Rotate pair membership and integration duties after each user story.

### Suggested delivery sequence

| Story or phase | Target dates | Exit outcome |
|---|---|---|
| US01 - Project creation and kickoff intake | 25-28 October 2026 | Project and brief are saved; transcript is optional |
| US02 - Evidence extraction | 29 October-2 November 2026 | Drafts include source excerpts and confidence |
| US03 - Scope review and user stories | 3-6 November 2026 | Human-approved baseline and stories are available |
| US04 - Team Leader runtime updates | 7-10 November 2026 | Updates are logged as events and refresh project status |
| US05 - Timeline, resources and risk | 11-14 November 2026 | Planning and risk proposals await PM approval |
| US06 - Workspace and portfolio alerts | 15-18 November 2026 | Project and portfolio status are visible |
| US07 - PM decision workflow | 19-20 November 2026 | Decisions are logged; approved changes update the plan |
| Integration and presentation freeze | 21-23 November 2026 | End-to-end demo and documentation are ready |

The MVP completion target is 23 November 2026, before the 24 November cutoff. The project discussion is scheduled for 2 December 2026.

## Current implementation

The current code is a dependency-free browser prototype in the project-manager-mvp folder.

- **Interface:** HTML and CSS.
- **Application logic:** Vanilla JavaScript.
- **Persistence:** Browser local storage.
- **Extraction and alert logic:** Deterministic demo rules.
- **External services:** None required for the current prototype.

### Implemented in the prototype

- Create a project with a brief and optional initial transcript or TXT file.
- Run demo extraction and review source-linked drafts.
- Approve scope and create a planning proposal.
- Review and decide on a plan proposal.
- Start execution only after plan approval.
- Submit Team Leader runtime updates while the project is active.
- Create critical alerts and PM decision items for blockers and proposed changes.
- View portfolio status, project workspaces and decision history.
- Update task status and export a project report as JSON.

### Prototype limitations

- Agent stages are represented in the workflow. Kickoff extraction and alert signals use deterministic demo rules; there is no LLM API integration.
- Timeline, resource estimates and cross-project scheduling are not automatically calculated yet.
- Data is stored only in the current browser. It is not shared with other users or devices.
- There is no backend API, database, authentication, role-based access control or server-side audit trail.
- Transcript intake supports pasted text and TXT files; audio transcription and integrations are outside the current prototype.
- Portfolio signals use simple rules and are not predictive analytics.

## Run locally

1. Install Python 3 if it is not already available.
2. Open a terminal in the project-manager-mvp folder.
3. Start a local static server with: python -m http.server 8765
4. Open http://localhost:8765 in a browser.

No package installation is required.

## Recommended next development steps

1. Agree the Project, Source, ExtractedItem, Story, Task, Risk, Update, Proposal and Decision data contracts.
2. Replace local storage with a backend API and persistent database.
3. Add authentication and PM/Team Leader permissions.
4. Connect each specialist agent through a controlled service layer and preserve source evidence.
5. Add server-side event history, plan versioning and decision audit.
6. Add multi-user synchronization, report scheduling and notification integrations.
7. Keep human approval gates and the separate kickoff/runtime input paths in every release.

## Repository structure

- index.html - application entry point.
- styles.css - layout, components and responsive styling.
- app.js - application state, UI rendering and prototype workflow.
- README.md - product overview, architecture and setup guide.


