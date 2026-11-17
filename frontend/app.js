"use strict";
/* NEXUS front-end (M2): talks to the FastAPI backend. PostgreSQL is the source of truth;
   nothing business-related is stored in the browser. */

const MAX_UPLOAD = 5 * 1024 * 1024;
const STATUS = { setup: "Setup", planning: "Planning", scope_review: "Scope review", plan_review: "Plan review", active: "Active", closed: "Closed" };
const KIND = { brief: "Brief", transcript: "Transcript", note: "Note", file: "File" };

const state = { me: null, projects: [], project: null, team: [], leaders: [], page: "portfolio", tab: "overview", modal: null, notice: null, navOpen: false, reqs: [], scope: null, gaps: null, reqFilter: "all", job: null, stories: [], tasks: [], coverage: null, planFilter: "all", planJob: null, schedule: null, scheduleJob: null, chain: null, pf: null };
const app = document.getElementById("app");
const isPM = () => state.me && state.me.role === "PM";

/* ---------- helpers ---------- */
function esc(v) { return String(v == null ? "" : v).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }
function fmtDate(v) { return v ? new Date(v + "T12:00:00").toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" }) : "Not set"; }
function fmtDateTime(v) { return v ? new Date(v).toLocaleString() : ""; }
function kv(k, v) { return `<div class="keyval"><small>${esc(k)}</small><b>${esc(v)}</b></div>`; }
function field(label, inner, full) { return `<div class="field${full ? " full" : ""}"><label>${label}</label>${inner}</div>`; }
function statusBadge(s) { return `<span class="badge ${s === "active" ? "green" : s === "closed" ? "" : "blue"}">${esc(STATUS[s] || s)}</span>`; }

function errorMessage(data, status) {
  const d = data && data.detail;
  if (typeof d === "string") return d;
  if (Array.isArray(d)) return d.map(e => ((e.loc || []).filter(x => x !== "body").join(".") + ": " + e.msg)).join("; ");
  if (d && d.message) return [d.message].concat((d.errors || []).map(e => "Line " + e.line + ": " + e.message)).join("\n");
  return "Request failed (" + status + ")";
}

async function api(path, options = {}) {
  const res = await apiFetch(path, options);   // from auth.js (handles 401)
  if (res.status === 204) return null;
  let data = null;
  try { data = await res.json(); } catch (_) { /* no body */ }
  if (!res.ok) throw new Error(errorMessage(data, res.status));
  return data;
}
const post = (path, body) => api(path, { method: "POST", body: JSON.stringify(body) });
const patch = (path, body) => api(path, { method: "PATCH", body: JSON.stringify(body) });

function uploadSource(projectId, { kind, title, text, file }) {
  const fd = new FormData();
  fd.append("kind", kind);
  if (title) fd.append("title", title);
  if (text) fd.append("text", text);
  if (file) fd.append("file", file);
  return api(`/projects/${projectId}/sources`, { method: "POST", body: fd });
}

/* ---------- data loading ---------- */
async function reloadProjects() { state.projects = await api("/projects"); }
async function reloadProject() {
  const id = state.project.id;
  const [project, team] = await Promise.all([api(`/projects/${id}`), api(`/projects/${id}/team`)]);
  state.project = project; state.team = team;
}
async function openProject(id) {
  try { state.project = { id }; await reloadProject(); state.page = "project"; state.tab = "overview"; state.navOpen = false; }
  catch (e) { state.page = "portfolio"; state.project = null; state.notice = { type: "error", text: e.message }; }
  render();
}

/* ---------- views ---------- */
function sidebar() {
  const items = state.projects.map(p => `<button data-action="open-project" data-id="${p.id}" class="${state.page === "project" && state.project && state.project.id === p.id ? "active" : ""}"><span class="nav-icon">◉</span><span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(p.name)}</span></button>`).join("");
  return `<aside class="sidebar${state.navOpen ? " open" : ""}"><div class="brand"><div class="brand-mark">N</div><div><strong>NEXUS</strong><small>Project intelligence</small></div></div><div class="nav-label">Workspace</div><nav class="nav"><button data-action="go-portfolio" class="${state.page === "portfolio" ? "active" : ""}"><span class="nav-icon">▦</span>Portfolio</button><div class="nav-label" style="margin:17px 0 3px">Projects</div>${items}${isPM() ? '<button data-action="modal" data-modal="create-project"><span class="nav-icon">＋</span>New project</button>' : ""}</nav><div class="sidebar-spacer"></div><div class="sidebar-note"><b>Human approval stays in control</b><br>Agents prepare drafts. The PM approves.</div></aside>`;
}

function topbar() {
  const crumb = state.page === "project" && state.project ? state.project.name || "Project" : "Portfolio";
  return `<header class="topbar"><div style="display:flex;gap:12px;align-items:center"><button class="btn btn-secondary btn-small mobile-menu" data-action="toggle-nav">☰</button><div class="crumb">NEXUS / <b>${esc(crumb)}</b></div></div><div class="top-actions"><span class="badge">${esc(state.me.role)}</span><span class="subtle">${esc(state.me.name)}</span><button class="btn btn-secondary btn-small" data-action="logout">Sign out</button></div></header>`;
}

function noticeHtml(n) { return `<div class="callout ${n.type === "ok" ? "" : "warning"}" style="white-space:pre-wrap">${esc(n.text)}</div>`; }

/* ---------- M7 PM command center ---------- */
function pfState() {
  if (!state.pf) state.pf = { tab: "projects", overview: null, resources: null, risks: null, chat: [], busy: false };
  return state.pf;
}

async function loadPf() {
  const pf = pfState();
  if (state.page !== "portfolio") return;
  try {
    if (pf.tab === "resources" && !pf.resources) pf.resources = await api("/portfolio/resources");
    else if (pf.tab === "risks" && !pf.risks) pf.risks = await api("/portfolio/risks");
    else if (!pf.overview) pf.overview = await api("/portfolio/overview");
  } catch (e) { if (e.message !== "unauthorized") { state.notice = { type: "error", text: e.message }; } }
  render();
}

function pfHealthDot(h) { return h === "green" ? "🟢" : h === "yellow" ? "🟡" : "🔴"; }
function pfChip(value, label, cls) { return `<div class="pf-chip ${cls || ""}"><b>${esc(value)}</b><small>${esc(label)}</small></div>`; }
function pfLoading(label) { return `<div class="empty"><div class="empty-icon">…</div><h3>${esc(label)}</h3></div>`; }

function pfTabs() {
  const pf = pfState();
  const tabs = [["projects", "Projects"], ["resources", "Resources"], ["risks", "Risks"]];
  if (isPM()) tabs.push(["assistant", "✦ Assistant"]);
  return tabs.map(t => `<button class="${pf.tab === t[0] ? "active" : ""}" data-action="pf-tab" data-tab="${t[0]}">${esc(t[1])}</button>`).join("");
}

function pfProjects() {
  const pf = pfState();
  if (!pf.overview) return pfLoading("Loading the portfolio…");
  const s = pf.overview.summary;
  const cards = pf.overview.projects.map(c => `<div class="project-row"><div style="flex:1"><div class="project-title">${pfHealthDot(c.health)} ${esc(c.name)} <span class="badge ${c.health === "green" ? "green" : c.health === "red" ? "red" : "blue"}">${esc(c.health.toUpperCase())}</span></div><div class="project-sub">${esc(STATUS[c.status] || c.status)} · next deadline: ${c.next_deadline ? fmtDate(c.next_deadline) : "not set"}${c.overdue_tasks ? ` · <b style="color:var(--red)">${c.overdue_tasks} overdue</b>` : ""}</div><div class="row-meta"><span class="subtle">${c.tasks.approved} tasks approved · ${c.tasks.done} done · ${c.requirements.draft} draft req(s)</span></div><div class="progress"><span style="width:${c.progress_pct}%"></span></div><div class="row-meta"><span class="subtle">${esc(c.health_reasons.join(" · "))}</span></div></div><button class="btn btn-secondary btn-small" data-action="open-project" data-id="${c.project_id}">Open workspace →</button></div>`).join("");
  const empty = `<div class="empty"><div class="empty-icon">◈</div><h3>${isPM() ? "No projects yet" : "No projects assigned to you"}</h3><p>${isPM() ? "Create the first project with a brief and an optional kickoff transcript." : "A Project Manager must link you as a Team Leader on a project."}</p>${isPM() ? '<button class="btn btn-primary" data-action="modal" data-modal="create-project">Create first project</button>' : ""}</div>`;
  return `<div class="pf-chips">${pfChip(s.total, "Total projects", "")}${pfChip(s.green, "On track", "ok")}${pfChip(s.yellow, "Needs attention", "warn")}${pfChip(s.red, "Critical", "bad")}</div><section class="card"><div class="card-body">${pf.overview.projects.length ? `<div class="project-list">${cards}</div>` : empty}</div></section>`;
}

function pfResources() {
  const pf = pfState();
  if (!pf.resources) return pfLoading("Loading resources…");
  const s = pf.resources.summary;
  const rows = pf.resources.members.map(r => `<div class="project-row"><div style="flex:1"><div class="project-title">${esc(r.name)} ${r.overallocated ? '<span class="badge red">Over-allocated</span>' : ""}</div><div class="project-sub">${esc(r.role || "No role title")} · ${esc(r.project_name)} · ${r.assigned_tasks} task(s) · ${r.hours_per_week} h/week of ${r.capacity_hours_per_week} h capacity</div><div class="progress"><span style="width:${Math.min(r.utilization_pct, 100)}%;${r.overallocated ? "background:var(--red)" : ""}"></span></div><div class="row-meta"><span class="subtle">${r.utilization_pct}% utilization · window ${r.window_start ? fmtDate(r.window_start) : "—"} → ${r.window_end ? fmtDate(r.window_end) : "—"}</span></div></div><button class="btn btn-secondary btn-small" data-action="open-project" data-id="${r.project_id}">Open →</button></div>`).join("");
  const conflicts = pf.resources.conflicts.length ? `<div class="callout warning">${pf.resources.conflicts.map(c => `<b>${esc(c.person)}</b> is booked in ${c.projects.map(esc).join(" + ")} with overlapping windows.`).join("<br>")}<br><span class="subtle">Rebalance the assignments or shift one project's window.</span></div>` : "";
  return `<div class="pf-chips">${pfChip(s.members, "People on projects", "")}${pfChip(s.overallocated, "Over-allocated", s.overallocated ? "bad" : "ok")}${pfChip(s.utilization_pct + "%", "Overall utilization", "")}${pfChip(s.total_capacity_hours_per_week + "h", "Capacity / week", "")}</div>${conflicts}<section class="card"><div class="card-body">${s.members ? `<div class="project-list">${rows}</div>` : `<div class="empty"><div class="empty-icon">☺</div><h3>No team members yet</h3><p>Import a team inside a project to see capacity here.</p></div>`}</div></section>`;
}

function pfRisks() {
  const pf = pfState();
  if (!pf.risks) return pfLoading("Loading the risk register…");
  const s = pf.risks.summary;
  const groups = [["high", "High severity", "red"], ["medium", "Medium severity", "blue"], ["low", "Low severity", "green"]];
  const sections = groups.map(g => {
    const rows = pf.risks.risks.filter(r => r.severity === g[0]);
    if (!rows.length) return "";
    return `<h2 style="margin:6px 0 10px">${esc(g[1])} (${rows.length})</h2><div class="project-list">${rows.map(r => `<div class="project-row"><div style="flex:1"><div class="project-title"><span class="badge ${g[2]}">${esc(r.type)}</span> ${esc(r.project_name)}</div><div class="project-sub">${esc(r.message)}</div><div class="row-meta"><span class="subtle">→ ${esc(r.suggestion)}</span></div></div>${r.project_id ? `<button class="btn btn-secondary btn-small" data-action="open-project" data-id="${r.project_id}">Open →</button>` : ""}</div>`).join("")}</div>`;
  }).join("");
  return `<div class="pf-chips">${pfChip(s.total, "Open risks", s.total ? "warn" : "ok")}${pfChip(s.high, "High", s.high ? "bad" : "ok")}${pfChip(s.medium, "Medium", "")}</div>${sections || pfLoading("No risks detected — everything is on track.")}`;
}

function pfAssistant() {
  if (!isPM()) return `<div class="callout warning">The AI assistant is available to the Project Manager.</div>`;
  const pf = pfState();
  const bubbles = pf.chat.map((m, i) => `<div class="pf-msg ${m.role}"><div class="pf-bubble">${esc(m.content)}</div>${m.suggestions && m.suggestions.length ? `<div class="pf-suggest">${m.suggestions.map((sp, j) => `<button class="btn btn-secondary btn-small" data-action="pf-suggest" data-msg="${i}" data-i="${j}">${esc(sp.title)}</button>`).join("")}</div>` : ""}</div>`).join("");
  const hello = pf.chat.length ? "" : `<div class="pf-msg assistant"><div class="pf-bubble">Ask me anything about your portfolio — health, resources, risks — and I will suggest the next action. My answers come from the live portfolio snapshot.</div></div>`;
  return `${hello}${bubbles}${pf.busy ? '<div class="pf-msg assistant"><div class="pf-bubble">Thinking…</div></div>' : ""}<div class="pf-input-row"><input id="pf-input" placeholder="Ask about your projects, resources or risks…" ${pf.busy ? "disabled" : ""} onkeydown="if(event.key==='Enter'){event.preventDefault();this.closest('.content').querySelector('[data-action=pf-send]').click();}"><button class="btn btn-primary" data-action="pf-send" ${pf.busy ? "disabled" : ""}>Send</button></div>`;
}

function portfolioView() {
  const pf = pfState();
  const body = pf.tab === "resources" ? pfResources() : pf.tab === "risks" ? pfRisks() : pf.tab === "assistant" ? pfAssistant() : pfProjects();
  return `<div class="page-head"><div><div class="eyebrow">M7 · Command center</div><h1>${isPM() ? "Portfolio" : "My projects"}</h1><p>${isPM() ? "Projects, resources and risks in one screen — plus an assistant that reads the live snapshot." : "Everything shown here is loaded from the database."}</p></div>${isPM() ? '<button class="btn btn-primary" data-action="modal" data-modal="create-project">＋ Create project</button>' : ""}</div><div class="tabs">${pfTabs()}</div>${body}`;
}

function projectView() {
  const p = state.project;
  const tabs = [["overview", "Overview"], ["sources", "Sources (" + p.sources.length + ")"], ["team", "Team (" + state.team.length + ")"]];
  if (isPM()) tabs.push(["scope", "Scope review"], ["plan", "Work plan"], ["schedule", "Schedule"]);
  const later = ["Updates (M7)", "Decisions (M8)"];
  return `<div class="workspace-head"><div class="workspace-title"><div class="project-monogram">${esc((p.name || "P")[0].toUpperCase())}</div><div><h1>${esc(p.name)}</h1><p>${esc(p.goal || "No goal added")}</p></div></div><div style="display:flex;gap:8px;align-items:center">${statusBadge(p.status)}${isPM() ? '<button class="btn btn-secondary btn-small" data-action="modal" data-modal="edit-project">Edit project</button>' : ""}</div></div><div class="tabs">${tabs.map(t => `<button class="${state.tab === t[0] ? "active" : ""}" data-action="tab" data-tab="${t[0]}">${esc(t[1])}</button>`).join("")}${later.map(l => `<button disabled style="opacity:.45;cursor:not-allowed" title="Not available yet">${esc(l)}</button>`).join("")}</div>${state.tab === "sources" ? sourcesView() : state.tab === "team" ? teamView() : state.tab === "scope" ? scopeView() : state.tab === "plan" ? planView() : state.tab === "schedule" ? scheduleView() : overviewView()}`;
}

function overviewView() {
  const p = state.project, leader = state.team.find(m => m.is_leader);
  const hours = state.team.reduce((n, m) => n + Number(m.capacity_hours_per_week || 0), 0);
  return `<div class="grid two-col"><section class="card section-card"><h2>Project snapshot</h2><p class="section-intro">Saved in PostgreSQL; survives a browser refresh.</p><div class="keyvals">${kv("Status", STATUS[p.status] || p.status)}${kv("Start", fmtDate(p.start_date))}${kv("Target end", fmtDate(p.end_date))}${kv("Team", state.team.length + " members · " + hours + " h/week")}${kv("Team Leader", leader ? leader.name + (leader.user_email ? " (" + leader.user_email + ")" : "") : "Not assigned")}${kv("Sources", String(p.sources.length))}</div></section><section class="card section-card"><h2>Project brief</h2><p class="section-intro">${esc(p.goal || "No goal added")}</p><div style="white-space:pre-wrap;font-size:13px">${esc(p.brief || "No brief")}</div></section></div><div class="callout">Next step: open <b>Scope review</b> to run extraction and approve the requirements — then plan with Agent 2 and schedule with Agent 3. Agents prepare drafts; you approve.</div>`;
}

function sourcesView() {
  const p = state.project;
  const rows = p.sources.map(s => `<div class="project-row"><div><div class="project-title">${esc(s.title)} <span class="badge blue">${esc(KIND[s.kind] || s.kind)}</span></div><div class="project-sub">${s.original_filename ? esc(s.original_filename) + " · " : ""}${fmtDateTime(s.created_at)} · ${esc(s.phase)}</div></div><button class="btn btn-secondary btn-small" data-action="view-source" data-id="${s.id}">View text</button></div>`).join("");
  return `<div class="page-head"><div><div class="eyebrow">US01</div><h1>Sources</h1><p>Brief and transcripts. Only extracted text is stored (.txt, .md, .docx, .pdf up to 5 MB).</p></div>${isPM() ? '<button class="btn btn-primary" data-action="modal" data-modal="add-source">＋ Add source</button>' : ""}</div><section class="card"><div class="card-body"><div class="project-list">${rows}</div></div></section>`;
}

function availability(m) {
  if (!m.available_from && !m.available_to) return "Always available";
  return fmtDate(m.available_from) + " → " + fmtDate(m.available_to);
}

function teamView() {
  const leader = state.team.find(m => m.is_leader);
  const rows = state.team.map(m => `<div class="project-row"><div><div class="project-title">${esc(m.name)} ${m.is_leader ? '<span class="badge green">Team Leader</span>' : ""}</div><div class="project-sub">${esc(m.role_title || "No role title")} · ${esc(m.capacity_hours_per_week)} h/week · ${availability(m)}</div><div class="row-meta">${(m.skills || []).map(s => `<span class="badge blue">${esc(s)}</span>`).join("")}${m.user_email ? `<span class="subtle">Login: ${esc(m.user_email)}</span>` : ""}</div></div>${isPM() ? `<div class="item-actions" style="margin:0"><button class="btn btn-secondary btn-small" data-action="edit-member" data-id="${m.id}">Edit</button><button class="btn btn-danger btn-small" data-action="delete-member" data-id="${m.id}">Delete</button></div>` : ""}</div>`).join("");
  const tools = isPM() ? `<div style="display:flex;gap:8px;flex-wrap:wrap"><button class="btn btn-primary" data-action="modal" data-modal="member">＋ Add member</button><button class="btn btn-secondary" data-action="modal" data-modal="import-csv">Import CSV</button><button class="btn btn-secondary" data-action="open-leader">Set Team Leader</button>${leader ? '<button class="btn btn-quiet" data-action="remove-leader">Remove leader</button>' : ""}</div>` : "";
  return `<div class="page-head"><div><div class="eyebrow">US01</div><h1>Team</h1><p>Members and weekly capacity. A linked Team Leader can see this project.</p></div>${tools}</div><section class="card"><div class="card-body">${state.team.length ? `<div class="project-list">${rows}</div>` : '<div class="empty"><div class="empty-icon">☺</div><h3>No team members yet</h3><p>Add members one by one or import a CSV.</p></div>'}</div></section>`;
}

/* ---------- modals ---------- */
function modalShell(title, subtitle, formName, body, submitLabel) {
  return `<div class="modal-backdrop" data-action="backdrop-close"><section class="modal" role="dialog" aria-modal="true"><div class="modal-head"><div><h2>${esc(title)}</h2>${subtitle ? `<p>${esc(subtitle)}</p>` : ""}</div><button type="button" class="close-x" data-action="close-modal">×</button></div><form data-form="${formName}"><div class="modal-body">${body}<p id="modal-error" class="callout warning" style="white-space:pre-wrap" hidden></p></div><div class="modal-foot"><button type="button" class="btn btn-secondary" data-action="close-modal">Cancel</button><button type="submit" class="btn btn-primary">${esc(submitLabel)}</button></div></form></section></div>`;
}

function modalView() {
  const m = state.modal, p = state.project;
  if (m.type === "create-project") return modalShell("Create a project", "Start with a brief. A kickoff transcript is optional.", "create-project",
    `<div class="form-grid">${field("Project name *", '<input name="name" required maxlength="200">')}${field("Goal", '<input name="goal">')}${field("Start date", '<input name="start_date" type="date">')}${field("Target end date", '<input name="end_date" type="date">')}${field("Project brief *", '<textarea name="brief" required></textarea>', true)}${field("Kickoff transcript (optional, pasted)", '<textarea name="transcript"></textarea>', true)}${field("Or upload a transcript file (.txt .md .docx .pdf, max 5 MB)", '<input name="transcriptFile" type="file" accept=".txt,.md,.docx,.pdf">', true)}</div>`, "Create project");
  if (m.type === "edit-project") return modalShell("Edit project", "", "edit-project",
    `<div class="form-grid">${field("Project name *", `<input name="name" required maxlength="200" value="${esc(p.name)}">`)}${field("Goal", `<input name="goal" value="${esc(p.goal || "")}">`)}${field("Start date", `<input name="start_date" type="date" value="${esc(p.start_date || "")}">`)}${field("Target end date", `<input name="end_date" type="date" value="${esc(p.end_date || "")}">`)}${field("Project brief *", `<textarea name="brief" required>${esc(p.brief || "")}</textarea>`, true)}</div>`, "Save changes");
  if (m.type === "add-source") return modalShell("Add a source", "Paste text or upload one file.", "add-source",
    `<div class="form-grid">${field("Type", '<select name="kind"><option value="transcript">Transcript</option><option value="note">Note</option><option value="file">Other file</option></select>')}${field("Title (optional)", '<input name="title" maxlength="255">')}${field("Pasted text", '<textarea name="text"></textarea>', true)}${field("Or file (.txt .md .docx .pdf, max 5 MB)", '<input name="file" type="file" accept=".txt,.md,.docx,.pdf">', true)}</div>`, "Add source");
  if (m.type === "view-source") return `<div class="modal-backdrop" data-action="backdrop-close"><section class="modal"><div class="modal-head"><div><h2>${esc(m.source.title)}</h2><p>${esc(KIND[m.source.kind] || m.source.kind)} · ${m.source.content_length} characters</p></div><button type="button" class="close-x" data-action="close-modal">×</button></div><div class="modal-body"><div style="white-space:pre-wrap;font-size:13px">${esc(m.source.content_text)}</div></div></section></div>`;
  if (m.type === "member") { const x = m.member || {}; return modalShell(m.member ? "Edit team member" : "Add team member", "", "member",
    `<div class="form-grid">${field("Name *", `<input name="name" required maxlength="120" value="${esc(x.name || "")}">`)}${field("Role title", `<input name="role_title" maxlength="120" value="${esc(x.role_title || "")}">`)}${field("Skills (separate with ; or ,)", `<input name="skills" value="${esc((x.skills || []).join("; "))}">`, true)}${field("Capacity (hours / week)", `<input name="capacity" type="number" min="0" max="168" step="0.5" required value="${esc(x.capacity_hours_per_week != null ? x.capacity_hours_per_week : 40)}">`)}${field("&nbsp;", "")}${field("Available from", `<input name="available_from" type="date" value="${esc(x.available_from || "")}">`)}${field("Available to", `<input name="available_to" type="date" value="${esc(x.available_to || "")}">`)}</div>`, m.member ? "Save" : "Add member"); }
  if (m.type === "import-csv") return modalShell("Import team from CSV", "All rows are validated first; if any row is invalid nothing is saved.", "import-csv",
    `<div class="callout">Columns: <b>name</b> (required), role_title, skills (separated by <b>;</b>), capacity_hours_per_week, available_from, available_to (YYYY-MM-DD).</div>${field("CSV file (UTF-8)", '<input name="file" type="file" accept=".csv" required>')}`, "Import");
  if (m.type === "leader") {
    const members = state.team.map(t => `<option value="${t.id}">${esc(t.name)}</option>`).join("");
    const users = state.leaders.map(u => `<option value="${esc(u.email)}">${esc(u.name)} (${esc(u.email)})</option>`).join("");
    if (!members || !users) return modalShell("Set Team Leader", "", "none", `<div class="callout warning">${!members ? "Add at least one team member first." : "No users with the TEAM_LEADER role exist."}</div>`, "OK");
    return modalShell("Set Team Leader", "The linked user gets read access to this project.", "leader",
      `<div class="form-grid">${field("Team member", `<select name="member_id">${members}</select>`)}${field("Team Leader login", `<select name="user_email">${users}</select>`)}</div>`, "Assign");
  }
  return "";
}

/* ---------- render ---------- */
function render() {
  const notice = state.notice; state.notice = null;
  const body = state.page === "project" && state.project ? projectView() : portfolioView();
  app.innerHTML = `<div class="app-shell">${sidebar()}<div class="main-area">${topbar()}<main class="content">${notice ? noticeHtml(notice) : ""}${body}</main></div></div>${state.modal ? (state.modal.type === "decide-req" || state.modal.type === "decide-req-bulk" ? scopeModal() : state.modal.type === "decide-story" || state.modal.type === "decide-task" ? planModal() : state.modal.type === "decide-schedule" ? scheduleModal() : modalView()) : ""}`;
}

function showModalError(msg) {
  const el = document.getElementById("modal-error");
  if (el) { el.textContent = msg; el.hidden = false; el.scrollIntoView({ block: "nearest" }); }
  else { state.notice = { type: "error", text: msg }; render(); }
}
function closeModal() { state.modal = null; render(); }

/* ---------- actions (clicks) ---------- */
const actions = {
  "toggle-nav": () => { state.navOpen = !state.navOpen; render(); },
  "logout": () => logout(),
  "go-portfolio": async () => { await reloadProjects(); state.page = "portfolio"; state.project = null; state.navOpen = false; if (state.pf) { state.pf.overview = state.pf.resources = state.pf.risks = null; } render(); loadPf(); },
  "pf-tab": t => { const pf = pfState(); pf.tab = t.dataset.tab; render(); loadPf(); },
  "pf-send": async () => {
    const pf = pfState();
    const input = document.getElementById("pf-input");
    const text = (input && input.value || "").trim();
    if (!text || pf.busy) return;
    pf.chat.push({ role: "user", content: text });
    pf.busy = true; render();
    try {
      const history = pf.chat.map(m => ({ role: m.role, content: m.content })).slice(-12);
      const res = await post("/portfolio/chat", { messages: history });
      pf.chat.push({ role: "assistant", content: res.reply, suggestions: res.suggestions || [] });
    } catch (e) {
      if (e.message === "unauthorized") throw e;
      pf.chat.push({ role: "assistant", content: "I could not answer just now: " + e.message, suggestions: [] });
    }
    pf.busy = false; render();
    const el = document.getElementById("pf-input"); if (el) el.focus();
  },
  "pf-suggest": async t => {
    const pf = pfState();
    const m = pf.chat[Number(t.dataset.msg)];
    const s = m && m.suggestions && m.suggestions[Number(t.dataset.i)];
    if (!s) return;
    if (s.action === "view_risks") { pf.tab = "risks"; render(); loadPf(); return; }
    if (s.action === "view_resources") { pf.tab = "resources"; render(); loadPf(); return; }
    if (s.project_id != null) {
      await openProject(s.project_id);
      if (s.action === "review_drafts") { state.tab = "scope"; render(); }
      else if (s.action === "schedule") { state.tab = "schedule"; render(); }
    }
  },
  "open-project": t => openProject(Number(t.dataset.id)),
  "tab": t => { state.tab = t.dataset.tab; render(); },
  "modal": t => { state.modal = { type: t.dataset.modal }; render(); },
  "close-modal": closeModal,
  "backdrop-close": (t, ev) => { if (ev.target === t) closeModal(); },
  "view-source": async t => {
    const s = await api(`/projects/${state.project.id}/sources/${t.dataset.id}`);
    state.modal = { type: "view-source", source: s }; render();
  },
  "edit-member": t => { state.modal = { type: "member", member: state.team.find(m => m.id === Number(t.dataset.id)) }; render(); },
  "delete-member": async t => {
    const m = state.team.find(x => x.id === Number(t.dataset.id));
    if (!m || !confirm(`Delete ${m.name} from this project?`)) return;
    await api(`/projects/${state.project.id}/team/${m.id}`, { method: "DELETE" });
    await reloadProject(); state.notice = { type: "ok", text: "Team member deleted." }; render();
  },
  "open-leader": async () => { state.leaders = await api("/users/team-leaders"); state.modal = { type: "leader" }; render(); },
  "remove-leader": async () => {
    if (!confirm("Remove the Team Leader from this project? They will lose access.")) return;
    await api(`/projects/${state.project.id}/leader`, { method: "DELETE" });
    await reloadProject(); state.notice = { type: "ok", text: "Team Leader removed." }; render();
  },
};

/* ---------- submitters (forms) ---------- */
const nul = v => (v && String(v).trim() ? String(v).trim() : null);

const submitters = {
  "create-project": async form => {
    const d = new FormData(form), file = form.elements.transcriptFile.files[0];
    if (file && file.size > MAX_UPLOAD) throw new Error("The transcript file is larger than 5 MB.");
    const project = await post("/projects", { name: d.get("name"), goal: nul(d.get("goal")), start_date: nul(d.get("start_date")), end_date: nul(d.get("end_date")), brief: d.get("brief"), transcript: nul(d.get("transcript")) });
    let notice = { type: "ok", text: "Project created." };
    if (file) {
      try { await uploadSource(project.id, { kind: "transcript", file }); notice = { type: "ok", text: "Project created and transcript file saved." }; }
      catch (e) { notice = { type: "warn", text: "Project created, but the transcript file was rejected: " + e.message }; }
    }
    state.modal = null; await reloadProjects(); state.notice = notice; await openProject(project.id);
  },
  "edit-project": async form => {
    const d = new FormData(form);
    state.project = Object.assign(state.project, await patch(`/projects/${state.project.id}`, { name: d.get("name"), goal: nul(d.get("goal")), start_date: nul(d.get("start_date")), end_date: nul(d.get("end_date")), brief: d.get("brief") }));
    await reloadProjects(); state.modal = null; state.notice = { type: "ok", text: "Project updated." }; render();
  },
  "add-source": async form => {
    const d = new FormData(form), file = form.elements.file.files[0];
    if (file && file.size > MAX_UPLOAD) throw new Error("The file is larger than 5 MB.");
    await uploadSource(state.project.id, { kind: d.get("kind"), title: nul(d.get("title")), text: nul(d.get("text")), file });
    await reloadProject(); state.modal = null; state.notice = { type: "ok", text: "Source added." }; render();
  },
  "member": async form => {
    const d = new FormData(form), cap = parseFloat(d.get("capacity"));
    if (Number.isNaN(cap)) throw new Error("Capacity must be a number.");
    const body = { name: d.get("name"), role_title: nul(d.get("role_title")), skills: String(d.get("skills") || "").split(/[;,]/).map(s => s.trim()).filter(Boolean), capacity_hours_per_week: cap, available_from: nul(d.get("available_from")), available_to: nul(d.get("available_to")) };
    const existing = state.modal.member, base = `/projects/${state.project.id}/team`;
    if (existing) await patch(`${base}/${existing.id}`, body); else await post(base, body);
    await reloadProject(); state.modal = null; state.notice = { type: "ok", text: existing ? "Team member updated." : "Team member added." }; render();
  },
  "import-csv": async form => {
    const file = form.elements.file.files[0];
    if (!file) throw new Error("Choose a CSV file.");
    const fd = new FormData(); fd.append("file", file);
    const created = await api(`/projects/${state.project.id}/team/import`, { method: "POST", body: fd });
    await reloadProject(); state.modal = null; state.notice = { type: "ok", text: `Imported ${created.length} team member(s).` }; render();
  },
  "leader": async form => {
    const d = new FormData(form);
    await api(`/projects/${state.project.id}/leader`, { method: "PUT", body: JSON.stringify({ member_id: Number(d.get("member_id")), user_email: d.get("user_email") }) });
    await reloadProject(); state.modal = null; state.notice = { type: "ok", text: "Team Leader linked to the project." }; render();
  },
  "none": async () => closeModal(),
};

app.addEventListener("click", async ev => {
  const t = ev.target.closest("[data-action]");
  if (!t || !actions[t.dataset.action]) return;
  try { await actions[t.dataset.action](t, ev); }
  catch (e) { if (e.message !== "unauthorized") { state.notice = { type: "error", text: e.message }; render(); } }
});

app.addEventListener("submit", async ev => {
  const form = ev.target.closest("form[data-form]");
  if (!form) return;
  ev.preventDefault();
  const btn = form.querySelector('button[type="submit"]');
  if (btn) btn.disabled = true;
  try { await submitters[form.dataset.form](form); }
  catch (e) { if (e.message !== "unauthorized") { showModalError(e.message); if (btn) btn.disabled = false; } }
});

/* ---------- boot ---------- */
(async function init() {
  try {
    state.me = await api("/auth/me");
    await reloadProjects();
    render();
    loadPf();
  } catch (e) {
    if (e.message !== "unauthorized") app.innerHTML = `<div style="max-width:480px;margin:12vh auto;font-family:system-ui"><h2>Cannot load NEXUS</h2><p>${esc(e.message)}</p><button onclick="location.reload()">Retry</button></div>`;
  }
})();

/* ===== Scope review (M4) ===== */
const REQ_TYPES = ["functional", "non_functional", "feature", "decision"];
const REQ_PRIORITIES = ["high", "medium", "low"];
const SUSPICIOUS = /ignore (all )?(the )?(previous|prior)|disregard (all )?(the )?(previous|prior)|تجاهل (كل )?التعليمات/i;
let jobTimer = null;

async function reloadScope() {
  const id = state.project.id;
  const [reqs, scope] = await Promise.all([
    api(`/projects/${id}/requirements`),
    api(`/projects/${id}/scope/status`).catch(e => { if (e.message === "unauthorized") throw e; return null; }),
  ]);
  state.reqs = reqs; state.scope = scope;
  state.gaps = await api(`/projects/${id}/requirements/gaps`).catch(e => { if (e.message === "unauthorized") throw e; return null; });
}

function pollJob(id) {
  clearInterval(jobTimer);
  const tick = async () => {
    try {
      const j = await api(`/jobs/${id}`);
      state.job = j;
      const finished = ["done", "failed", "cancelled", "waiting_for_pm"].includes(j.status);
      if (finished) clearInterval(jobTimer);
      if (state.page === "project" && state.tab === "scope") {
        if (finished) await reloadScope();
        if (!state.modal) render();
      }
    } catch (e) { clearInterval(jobTimer); }
  };
  tick(); jobTimer = setInterval(tick, 3000);
}

async function waitForJob(id, timeoutMs = 300000) {
  /** Await a job until it finishes; used by the accept-all chain (throws on failure). */
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    await new Promise(r => setTimeout(r, 2500));
    const j = await api(`/jobs/${id}`);
    if (["done", "waiting_for_pm"].includes(j.status)) return j;
    if (["failed", "cancelled"].includes(j.status)) throw new Error(j.error || `job ${j.status}`);
  }
  throw new Error("the job took too long");
}

function reqCounts() {
  const c = { all: state.reqs.length, draft: 0, approved: 0, rejected: 0, unverified: 0 };
  state.reqs.forEach(r => { if (c[r.status] != null) c[r.status]++; if (!r.quote_verified) c.unverified++; });
  return c;
}
function filteredReqs() {
  const f = state.reqFilter;
  return state.reqs.filter(r => f === "all" ? true : f === "unverified" ? !r.quote_verified : r.status === f);
}

function reqCard(r) {
  const pct = Math.round((Number(r.confidence) || 0) * 100);
  const sus = SUSPICIOUS.test(r.text || "") || SUSPICIOUS.test(r.quote || "");
  const b = (txt, bg, fg) => `<span class="badge" style="background:${bg};color:${fg}">${esc(txt)}</span>`;
  const verified = r.quote_verified ? '<span class="badge green">Quote verified</span>' : b("Unverified quote", "#fff4e0", "#8a5a00");
  const warn = sus ? b("Looks like an instruction, not a requirement", "#fde8e8", "#b00020") : "";
  const status = r.status === "approved" ? '<span class="badge green">Approved</span>' : r.status === "rejected" ? b("Rejected", "#fde8e8", "#b00020") : '<span class="badge blue">Draft</span>';
  const src = r.source_id ? `<button class="btn btn-quiet btn-small" data-action="view-source" data-id="${r.source_id}">View source</button>` : '<span class="subtle">No source linked</span>';
  const act = r.status === "draft" ? `<div class="item-actions" style="margin:0"><button class="btn btn-primary btn-small" data-action="req-approve" data-id="${r.id}">Approve</button><button class="btn btn-secondary btn-small" data-action="req-edit" data-id="${r.id}">Edit</button><button class="btn btn-danger btn-small" data-action="req-reject" data-id="${r.id}">Reject</button></div>` : "";
  const question = r.clarify_question ? `<div class="row-meta" style="margin-top:6px">${b("❓ " + r.clarify_question, "#fff4e5", "#9a5b00")}</div><div class="subtle">Answer it with Edit or a decision comment — your answer reaches Agent 2.</div>` : "";
  return `<div class="project-row" style="align-items:flex-start"><div style="flex:1;min-width:0"><div class="project-title">${esc(r.text)}</div><blockquote style="margin:8px 0;padding:6px 10px;border-left:3px solid #dde5ef;font-size:13px;white-space:pre-wrap">${esc(r.quote || "No quote")}</blockquote><div class="row-meta">${status}<span class="badge">${esc(r.type)}</span>${r.priority ? `<span class="badge">${esc(r.priority)}</span>` : ""}${verified}<span class="subtle">Confidence ${pct}%</span>${warn}${src}</div>${question}${r.pm_comment ? `<div class="subtle">PM: ${esc(r.pm_comment)}</div>` : ""}</div>${act}</div>`;
}

function reqGroupHtml(mod, items) {
  const drafts = items.filter(r => r.status === "draft");
  const ids = JSON.stringify(drafts.map(r => r.id));
  const bulk = drafts.length
    ? `<div class="item-actions" style="margin:10px 0 0"><button class="btn btn-primary btn-small" data-action="req-approve-group" data-ids='${esc(ids)}'>Approve all (${drafts.length})</button><button class="btn btn-danger btn-small" data-action="req-reject-group" data-ids='${esc(ids)}'>Reject all (needs a reason)</button></div>`
    : "";
  return `<section class="card section-card" style="margin-bottom:14px"><h2 style="display:flex;align-items:center;gap:8px">${esc(mod)} <span class="badge blue">${items.length}</span></h2>${bulk}<div class="card-body" style="margin-top:${bulk ? "10" : "0"}px"><div class="project-list">${items.map(reqCard).join("")}</div></div></section>`;
}

function gapsSection() {
  const g = state.gaps;
  if (!g || g.empty) return "";
  const llm = !!(g.llm && g.llm.prompt_version); // stored LLM verdict for THIS project
  const chip = a => {
    const tip = a.note ? ` title="${esc(a.note)}"` : "";
    const mark = a.covered ? "✓" : "✗";
    const cls = a.covered ? "badge green" : 'badge" style="background:#fde8e8;color:#b00020';
    const src = a.source === "llm" ? "" : ` <span class="subtle">(keywords)</span>`;
    return `<span class="${cls}"${tip}>${mark} ${esc(a.area)}${src}</span>`;
  };
  const verdict = llm
    ? `LLM-judged${g.missing_count ? `: ${g.missing_count} area(s) nobody mentioned — good questions for the next client meeting.` : ": every standard area was genuinely covered."}`
    : `${g.missing_count} area(s) matched by keywords only. Judge meaning, not words — run the LLM analysis.`;
  const notes = g.areas.filter(a => a.note).map(a => `<div class="row-meta"><span class="badge ${a.covered ? "green" : ""}">${esc(a.area)}</span><span class="subtle">${esc(a.note)}</span></div>`).join("");
  const analyzed = llm ? `<p class="subtle" style="margin-top:8px">Analyzed ${esc(String(g.llm.analyzed_requirements))} requirement(s) with the LLM at ${esc(fmtDateTime(g.llm.created_at))} — stored for this project, survives a refresh.</p>` : "";
  return `<section class="card section-card" style="margin-top:16px"><h2 style="display:flex;align-items:center;gap:8px">Coverage gaps <span class="badge ${g.missing_count ? "" : "green"}">${g.covered_count}/${g.areas.length} covered</span>${llm ? '<span class="badge blue">LLM-judged</span>' : ""}<span style="flex:1"></span><button class="btn btn-secondary btn-small" data-action="run-gap-analysis">${llm ? "Re-analyze with LLM" : "Analyze with LLM"}</button></h2><p class="section-intro">Standard product areas vs your requirements. A missing area is a question for the review meeting, not a fact.</p><div style="display:flex;gap:6px;flex-wrap:wrap">${g.areas.map(chip).join("")}</div>${notes ? `<div class="project-list" style="margin-top:10px">${notes}</div>` : ""}<p class="subtle" style="margin-top:8px">${esc(verdict)}</p>${analyzed}</section>`;
}

function scopeView() {
  const c = reqCounts(), job = state.job, sc = state.scope;
  const running = job && ["queued", "running"].includes(job.status);
  const gate = sc ? (typeof sc.gate === "string" ? sc.gate : JSON.stringify(sc.gate)) : "unknown";
  const jobLine = job ? `<div class="callout${job.status === "failed" ? " warning" : ""}">Extraction job #${esc(job.id)}: <b>${esc(job.status)}</b> (${esc(job.progress)}%)${job.error ? "<br>" + esc(job.error) : ""}</div>` : "";
  const chips = [["all", "All", c.all], ["draft", "To review", c.draft], ["approved", "Approved", c.approved], ["rejected", "Rejected", c.rejected], ["unverified", "Unverified", c.unverified]]
    .map(f => `<button class="btn ${state.reqFilter === f[0] ? "btn-primary" : "btn-secondary"} btn-small" data-action="scope-filter" data-f="${f[0]}">${f[1]} (${f[2]})</button>`).join(" ");
  const groups = [...filteredReqs().reduce((m, r) => {
    const mod = r.module || "General";
    if (!m.has(mod)) m.set(mod, []);
    m.get(mod).push(r);
    return m;
  }, new Map())]
    .sort(([a], [b]) => (a === "General") - (b === "General") || a.localeCompare(b))
    .map(([mod, items]) => reqGroupHtml(mod, items))
    .join("");
  const canApprove = c.draft === 0 && c.all > 0;
  const chainLine = state.chain ? `<div class="callout"><b>${esc(state.chain)}</b> <span class="subtle">The whole chain is running — keep this tab open.</span></div>` : "";
  return `<div class="page-head"><div><div class="eyebrow">US02</div><h1>Scope review</h1><p>Requirements grouped by feature area. Approve a whole module in one click, or drill into any item.</p></div><div><button class="btn btn-primary" data-action="accept-all"${state.chain || c.all === 0 ? " disabled" : ""} title="Approve every draft, approve the scope, run the LLM gap analysis, then Agent 2 and Agent 3">${state.chain ? "Working..." : "✓ Accept all & run agents"}</button><button class="btn btn-secondary" data-action="run-extract"${running || state.chain ? " disabled" : ""}>${running ? "Extracting..." : "Run extraction"}</button></div></div>${chainLine}${jobLine}<div class="callout">Gate: <b>${esc(gate)}</b> · Project status: <b>${esc(sc ? sc.project_status : state.project.status)}</b></div><div style="display:flex;gap:6px;flex-wrap:wrap;margin:12px 0">${chips}</div>${groups ? groups : '<section class="card"><div class="card-body"><div class="empty"><div class="empty-icon">◈</div><h3>No requirements here</h3><p>Run the extraction, or change the filter.</p></div></div></section>'}${gapsSection()}<section class="card section-card" style="margin-top:16px"><h2>Approve scope</h2><p class="section-intro">Every requirement must be decided first.</p><div class="form-grid">${field("Comment (optional)", '<input id="scope-comment" maxlength="2000">', true)}</div><div style="display:flex;gap:8px;margin-top:12px"><button class="btn btn-primary" data-action="approve-scope"${canApprove ? "" : " disabled"}>Approve scope</button><button class="btn btn-quiet" data-action="open-gate">Re-open gate</button></div>${canApprove ? "" : `<p class="subtle">${c.all === 0 ? "Run the extraction first." : c.draft + " draft(s) still need a decision."}</p>`}</section>`;
}

function scopeModal() {
  const m = state.modal, r = m.req;
  if (m.type === "decide-req-bulk")
    return modalShell("Reject a whole module", `${m.ids.length} requirement(s) will be rejected. No dates, no stories — nothing is lost, you can re-run the extraction.`, "decide-req-bulk", field("Reason *", '<textarea name="comment" required></textarea>', true), "Reject all");
  if (m.mode === "rejected")
    return modalShell("Reject requirement", r.text, "decide-req", field("Reason *", '<textarea name="comment" required></textarea>', true), "Reject");
  const types = REQ_TYPES.includes(r.type) ? REQ_TYPES : REQ_TYPES.concat(r.type);
  const typeOpts = types.map(t => `<option value="${esc(t)}"${t === r.type ? " selected" : ""}>${esc(t)}</option>`).join("");
  const prioOpts = '<option value="">Not set</option>' + REQ_PRIORITIES.map(p => `<option value="${esc(p)}"${p === r.priority ? " selected" : ""}>${esc(p)}</option>`).join("");
  return modalShell("Edit requirement", "Your edit is saved as a PM decision.", "decide-req",
    `<div class="form-grid">${field("Requirement text *", `<textarea name="text" required minlength="3" maxlength="1000">${esc(r.text)}</textarea>`, true)}${field("Type", `<select name="type">${typeOpts}</select>`)}${field("Priority", `<select name="priority">${prioOpts}</select>`)}${field("Comment *", '<textarea name="comment" required></textarea>', true)}</div>`, "Save edit");
}

const reqById = id => state.reqs.find(r => r.id === Number(id));
const baseTab = actions.tab;
Object.assign(actions, {
  "tab": async t => {
    if (t.dataset.tab === "scope") {
      await reloadScope();
      try {
        const jobs = await api(`/projects/${state.project.id}/jobs`);
        const active = jobs.find(j => j.type === "agent1.extract" && ["queued", "running"].includes(j.status));
        if (active) { state.job = active; pollJob(active.id); }
      } catch (e) { if (e.message === "unauthorized") throw e; }
    }
    if (t.dataset.tab === "plan") {
      await reloadPlan();
      try {
        const jobs = await api(`/projects/${state.project.id}/jobs`);
        const active = jobs.find(j => j.type === "agent2.plan" && ["queued", "running"].includes(j.status));
        if (active) { state.planJob = active; pollPlanJob(active.id); }
      } catch (e) { if (e.message === "unauthorized") throw e; }
    }
    if (t.dataset.tab === "schedule") {
      await reloadSchedule();
      try {
        const jobs = await api(`/projects/${state.project.id}/jobs`);
        const active = jobs.find(j => j.type === "agent3.schedule" && ["queued", "running"].includes(j.status));
        if (active) { state.scheduleJob = active; pollScheduleJob(active.id); }
      } catch (e) { if (e.message === "unauthorized") throw e; }
    }
    baseTab(t);
  },
  "scope-filter": t => { state.reqFilter = t.dataset.f; render(); },
  "req-approve": async t => {
    await post(`/projects/${state.project.id}/requirements/${t.dataset.id}/decision`, { decision: "approved" });
    await reloadScope(); render();
  },
  "req-reject": t => { state.modal = { type: "decide-req", mode: "rejected", req: reqById(t.dataset.id) }; render(); },
  "req-edit": t => { state.modal = { type: "decide-req", mode: "modified", req: reqById(t.dataset.id) }; render(); },
  "req-approve-group": async t => {
    const ids = JSON.parse(t.dataset.ids);
    if (!ids.length || !confirm(`Approve ${ids.length} requirement(s) in this module?`)) return;
    await post(`/projects/${state.project.id}/requirements/decision`, { ids, decision: "approved" });
    await reloadScope(); state.notice = { type: "ok", text: `${ids.length} requirement(s) approved.` }; render();
  },
  "req-reject-group": t => { state.modal = { type: "decide-req-bulk", ids: JSON.parse(t.dataset.ids) }; render(); },
  "run-gap-analysis": async t => {
    t.disabled = true; t.textContent = "Analyzing...";
    try {
      const report = await post(`/projects/${state.project.id}/requirements/gaps/analyze`, {});
      state.gaps = report;
      state.notice = { type: "ok", text: "LLM coverage analysis done." };
    } finally { render(); }
  },
  "run-extract": async () => {
    if (!confirm("Run extraction? New draft requirements will be added for review.")) return;
    const job = await post(`/projects/${state.project.id}/jobs`, { type: "agent1.extract" });
    state.job = job; render(); pollJob(job.id);
  },
  "open-gate": async () => {
    await api(`/projects/${state.project.id}/scope/open`, { method: "POST" });
    await reloadScope(); state.notice = { type: "ok", text: "Scope gate opened." }; render();
  },
  "approve-scope": async () => {
    const el = document.getElementById("scope-comment");
    await post(`/projects/${state.project.id}/scope/approve`, { comment: el ? el.value : "" });
    await reloadProject(); await reloadScope();
    state.notice = { type: "ok", text: "Scope approved." }; render();
  },
  "accept-all": async () => {
    const id = state.project.id;
    if (!confirm("Accept everything?\n\n1. All draft requirements are approved\n2. The scope is approved\n3. The LLM coverage analysis runs\n4. Agent 2 generates the plan and all its drafts are approved\n5. Agent 3 computes the schedule draft\n\nYou still review and approve the schedule yourself — that is what writes the dates.")) return;
    state.chain = "Approving requirements..."; render();
    try {
      const drafts = state.reqs.filter(r => r.status === "draft").map(r => r.id);
      if (drafts.length) await post(`/projects/${id}/requirements/decision`, { ids: drafts, decision: "approved" });
      state.chain = "Approving the scope..."; render();
      await post(`/projects/${id}/scope/approve`, { comment: "Accepted all requirements." });
      await reloadProject(); await reloadScope(); render();
      state.chain = "Analyzing coverage gaps with the LLM..."; render();
      if (state.gaps && state.gaps.llm && state.gaps.llm.prompt_version) {
        // Agent 1 already ran the analysis after extraction; the stored report covers these reqs
        state.notice = { type: "ok", text: "Using the coverage analysis that ran after the extraction." }; render();
      } else {
        try { state.gaps = await post(`/projects/${id}/requirements/gaps/analyze`, {}); }
        catch (e) { if (e.message === "unauthorized") throw e; }
      }
      state.chain = "Running Agent 2 (stories & tasks)..."; render();
      const j2 = await post(`/projects/${id}/jobs`, { type: "agent2.plan" });
      state.planJob = j2; await waitForJob(j2.id);
      await reloadPlan();
      state.chain = "Approving the plan drafts..."; render();
      const sIds = state.stories.filter(s => s.review_status === "draft").map(s => s.id);
      if (sIds.length) await post(`/projects/${id}/stories/decision`, { ids: sIds, decision: "approved" });
      await reloadPlan();
      const rejectedStories = new Set(state.stories.filter(s => s.review_status === "rejected").map(s => s.id));
      const tIds = state.tasks.filter(x => x.review_status === "draft" && !rejectedStories.has(x.story_id)).map(x => x.id);
      if (tIds.length) await post(`/projects/${id}/tasks/decision`, { ids: tIds, decision: "approved" });
      await reloadPlan(); render();
      state.chain = "Running Agent 3 (schedule)..."; render();
      const j3 = await post(`/projects/${id}/jobs`, { type: "agent3.schedule" });
      state.scheduleJob = j3; await waitForJob(j3.id);
      await reloadSchedule();
      state.chain = null; state.tab = "schedule";
      state.notice = { type: "ok", text: "Everything is ready — review the schedule draft and approve it to write the dates." };
      render();
    } catch (e) {
      state.chain = null;
      state.notice = { type: "error", text: "Stopped here: " + (e.message || "unknown error") + " — you can continue manually from this step." };
      render();
    }
  },
});

Object.assign(submitters, {
  "decide-req": async form => {
    const m = state.modal, d = new FormData(form);
    const body = { decision: m.mode, comment: String(d.get("comment") || "").trim() };
    if (m.mode === "modified") {
      body.text = d.get("text"); body.type = d.get("type");
      if (d.get("priority")) body.priority = d.get("priority");
    }
    await post(`/projects/${state.project.id}/requirements/${m.req.id}/decision`, body);
    state.modal = null; await reloadScope();
    state.notice = { type: "ok", text: m.mode === "rejected" ? "Requirement rejected." : "Requirement updated." }; render();
  },
  "decide-req-bulk": async form => {
    const m = state.modal, d = new FormData(form);
    await post(`/projects/${state.project.id}/requirements/decision`, { ids: m.ids, decision: "rejected", comment: String(d.get("comment") || "").trim() });
    state.modal = null; await reloadScope();
    state.notice = { type: "ok", text: `${m.ids.length} requirement(s) rejected.` }; render();
  },
});

/* ===== Work plan / Agent 2 (M5) ===== */
let planTimer = null;

async function reloadPlan() {
  const id = state.project.id;
  const [stories, tasks, coverage, reqs] = await Promise.all([
    api(`/projects/${id}/stories`),
    api(`/projects/${id}/tasks`),
    api(`/projects/${id}/planning/coverage`),
    api(`/projects/${id}/requirements`).catch(e => { if (e.message === "unauthorized") throw e; return []; }),
  ]);
  state.stories = stories; state.tasks = tasks; state.coverage = coverage; state.planReqs = reqs;
}

function pollPlanJob(id) {
  clearInterval(planTimer);
  const tick = async () => {
    try {
      const j = await api(`/jobs/${id}`);
      state.planJob = j;
      const finished = ["done", "failed", "cancelled", "waiting_for_pm"].includes(j.status);
      if (finished) clearInterval(planTimer);
      if (state.page === "project" && state.tab === "plan") {
        if (finished) await reloadPlan();
        if (!state.modal) render();
      }
    } catch (e) { clearInterval(planTimer); }
  };
  tick(); planTimer = setInterval(tick, 3000);
}

const planBadge = s => s === "approved" ? '<span class="badge green">Approved</span>' : s === "rejected" ? '<span class="badge" style="background:#fde8e8;color:#b00020">Rejected</span>' : '<span class="badge blue">Draft</span>';
const planMatch = x => state.planFilter === "all" ? true : x.review_status === state.planFilter;
const planStoryById = id => state.stories.find(s => s.id === Number(id));
const planTaskById = id => state.tasks.find(t => t.id === Number(id));
const reqLabel = id => { const r = (state.planReqs || []).find(x => x.id === id); return r ? r.text : "Requirement #" + id; };
const taskLabel = id => { const t = planTaskById(id); return t ? t.title : "Task #" + id; };

function planTaskRow(t) {
  const deps = (t.depends_on || []).map(id => `<span class="badge">${esc(taskLabel(id))}</span>`).join(" ");
  const actions = t.review_status === "draft"
    ? `<div class="item-actions" style="margin:0"><button class="btn btn-primary btn-small" data-action="task-approve" data-id="${t.id}">Approve</button><button class="btn btn-secondary btn-small" data-action="task-edit" data-id="${t.id}">Edit</button><button class="btn btn-danger btn-small" data-action="task-reject" data-id="${t.id}">Reject</button></div>`
    : "";
  const order = t.plan_order ? `<span class="badge">Step ${esc(t.plan_order)}</span>` : "";
  const critical = t.is_critical ? '<span class="badge" style="background:#fff4e5;color:#9a5b00">Critical path</span>' : "";
  return `<div class="project-row" style="align-items:flex-start"><div style="flex:1;min-width:0"><div class="project-title">${esc(t.title)} ${planBadge(t.review_status)}${critical}</div><div class="row-meta">${order}<span class="badge">${esc(t.estimate_hours)} h</span><span class="badge">${esc(t.status)}</span><span class="subtle">Confidence ${Math.round((Number(t.confidence) || 0) * 100)}%</span>${deps ? `<span class="subtle">Depends on:</span> ${deps}` : ""}</div>${t.rationale ? `<div class="subtle">Why: ${esc(t.rationale)}</div>` : ""}${t.pm_comment ? `<div class="subtle">PM: ${esc(t.pm_comment)}</div>` : ""}</div>${actions}</div>`;
}

function planStoryCard(s) {
  const tasks = state.tasks.filter(t => t.story_id === s.id && planMatch(t));
  const criteria = (s.acceptance_criteria || []).map(c => `<li>${esc(c)}</li>`).join("");
  const actions = s.review_status === "draft"
    ? `<div class="item-actions" style="margin:8px 0 0"><button class="btn btn-primary btn-small" data-action="story-approve" data-id="${s.id}">Approve</button><button class="btn btn-secondary btn-small" data-action="story-edit" data-id="${s.id}">Edit</button><button class="btn btn-danger btn-small" data-action="story-reject" data-id="${s.id}">Reject</button></div>`
    : "";
  const order = s.plan_order ? `<span class="badge">Order ${esc(s.plan_order)}</span>` : "";
  const taskBlock = tasks.length ? `<div class="project-list" style="margin-top:10px">${tasks.map(planTaskRow).join("")}</div>` : '<p class="subtle" style="margin:10px 0 0">No tasks under this story (or hidden by the filter).</p>';
  return `<div class="project-row" style="display:block;align-items:stretch"><div class="project-title">${esc(s.text)} ${planBadge(s.review_status)}</div><div class="row-meta">${order}<span class="badge">Requirement #${s.requirement_id}</span><span class="subtle">Confidence ${Math.round((Number(s.confidence) || 0) * 100)}%</span></div><div class="subtle">${esc(reqLabel(s.requirement_id))}</div>${criteria ? `<ul style="margin:8px 0 0 18px;font-size:13px">${criteria}</ul>` : ""}${actions}${taskBlock}</div>`;
}

function coverageSection(c) {
  const gaps = [];
  if (c.requirements_without_story.length) gaps.push(`${c.requirements_without_story.length} approved requirement(s) without a story: ${c.requirements_without_story.map(id => "#" + id).join(", ")}`);
  if (c.stories_without_tasks.length) gaps.push(`${c.stories_without_tasks.length} story(ies) without tasks: ${c.stories_without_tasks.map(id => "#" + id).join(", ")}`);
  if (c.tasks_without_estimate.length) gaps.push(`${c.tasks_without_estimate.length} task(s) without a valid estimate: ${c.tasks_without_estimate.map(id => "#" + id).join(", ")}`);
  if (c.dependency_cycles.length) gaps.push(`Dependency cycle(s): ${c.dependency_cycles.map(x => x.join(" → ")).join("; ")}`);
  const gapHtml = gaps.length ? `<div class="callout warning">${gaps.map(esc).join("<br>")}</div>` : '<div class="callout">Every approved requirement has a story, every story has a task, and no dependency cycle exists.</div>';
  return `<section class="card section-card"><h2>Coverage <span class="badge ${c.clean ? "green" : ""}">${c.clean ? "Clean" : "Needs review"}</span></h2><p class="section-intro">Deterministic checks over the saved plan. Nothing here is produced by the LLM.</p><div class="keyvals">${kv("Approved requirements", c.approved_requirements)}${kv("Stories", c.stories + " · " + c.approved_stories + " approved · " + c.unreviewed_stories + " to review")}${kv("Tasks", c.tasks + " · " + c.approved_tasks + " approved · " + c.unreviewed_tasks + " to review")}${kv("Total estimate", c.total_estimate_hours + " h")}</div>${gapHtml}</section>`;
}

function planView() {
  const c = state.coverage || { approved_requirements: 0, requirements_without_story: [], stories: 0, approved_stories: 0, unreviewed_stories: 0, stories_without_tasks: [], tasks: 0, approved_tasks: 0, unreviewed_tasks: 0, tasks_without_estimate: [], total_estimate_hours: 0, dependency_cycles: [], clean: false };
  const job = state.planJob;
  const running = job && ["queued", "running"].includes(job.status);
  const plannable = ["planning", "plan_review"].includes(state.project.status);
  const jobLine = job ? `<div class="callout${job.status === "failed" ? " warning" : ""}">Planning job #${esc(job.id)}: <b>${esc(job.status)}</b> (${esc(job.progress)}%)${job.error ? "<br>" + esc(job.error) : ""}</div>` : "";
  const counts = { all: state.stories.length, draft: 0, approved: 0, rejected: 0 };
  state.stories.forEach(s => { if (counts[s.review_status] != null) counts[s.review_status]++; });
  const chips = [["all", "All", counts.all], ["draft", "To review", counts.draft], ["approved", "Approved", counts.approved], ["rejected", "Rejected", counts.rejected]]
    .map(f => `<button class="btn ${state.planFilter === f[0] ? "btn-primary" : "btn-secondary"} btn-small" data-action="plan-filter" data-f="${f[0]}">${f[1]} (${f[2]})</button>`).join(" ");
  const stories = state.stories.filter(planMatch);
  const list = stories.map(planStoryCard).join("");
  const hint = !plannable ? `<p class="subtle">Agent 2 only runs after the scope is approved (project status: ${esc(state.project.status)}).</p>` : "";
  return `<div class="page-head"><div><div class="eyebrow">US03</div><h1>Work plan</h1><p>Stories and tasks generated from approved requirements. The PM reviews every draft before it becomes the plan.</p></div><div><button class="btn btn-primary" data-action="plan-approve-all"${state.chain ? " disabled" : ""} title="Approve every draft story and task in one transaction each">Approve all drafts</button><button class="btn btn-secondary" data-action="run-plan"${running || !plannable ? " disabled" : ""}>${running ? "Planning..." : "Run Agent 2"}</button>${hint}</div></div>${jobLine}${coverageSection(c)}<div class="page-head" style="margin-top:18px"><div><h1 style="font-size:18px">Stories & tasks</h1><p>Approve, edit or reject every draft. Your decisions survive an Agent 2 re-run.</p></div></div><div style="display:flex;gap:6px;flex-wrap:wrap;margin:12px 0">${chips}</div><section class="card"><div class="card-body">${list ? `<div class="project-list">${list}</div>` : '<div class="empty"><div class="empty-icon">◈</div><h3>No stories yet</h3><p>Approve the scope, then run Agent 2.</p></div>'}</div></section>`;
}

function planModal() {
  const m = state.modal, x = m.item;
  if (m.type === "decide-story") {
    if (m.mode === "rejected")
      return modalShell("Reject story", x.text, "decide-story", field("Reason *", '<textarea name="comment" required></textarea>', true), "Reject");
    return modalShell("Edit story", "Your edit is saved as a PM decision and approves this story.", "decide-story",
      `<div class="form-grid">${field("Story *", `<textarea name="text" required minlength="10" maxlength="500">${esc(x.text)}</textarea>`, true)}${field("Acceptance criteria * (one per line)", `<textarea name="criteria" required>${esc((x.acceptance_criteria || []).join("\n"))}</textarea>`, true)}${field("Comment *", '<textarea name="comment" required></textarea>', true)}</div>`, "Save & approve");
  }
  if (m.mode === "rejected")
    return modalShell("Reject task", x.title, "decide-task", field("Reason *", '<textarea name="comment" required></textarea>', true), "Reject");
  return modalShell("Edit task", "Your edit is saved as a PM decision and approves this task.", "decide-task",
    `<div class="form-grid">${field("Task title *", `<input name="title" required minlength="5" maxlength="200" value="${esc(x.title)}">`)}${field("Estimate (hours) *", `<input name="estimate_hours" type="number" min="0.5" max="80" step="0.5" required value="${esc(x.estimate_hours)}">`)}${field("Comment *", '<textarea name="comment" required></textarea>', true)}</div>`, "Save & approve");
}

Object.assign(actions, {
  "plan-filter": t => { state.planFilter = t.dataset.f; render(); },
  "story-approve": async t => {
    await post(`/projects/${state.project.id}/stories/${t.dataset.id}/decision`, { decision: "approved" });
    await reloadPlan(); state.notice = { type: "ok", text: "Story approved." }; render();
  },
  "story-edit": t => { state.modal = { type: "decide-story", mode: "modified", item: planStoryById(t.dataset.id) }; render(); },
  "story-reject": t => { state.modal = { type: "decide-story", mode: "rejected", item: planStoryById(t.dataset.id) }; render(); },
  "task-approve": async t => {
    await post(`/projects/${state.project.id}/tasks/${t.dataset.id}/decision`, { decision: "approved" });
    await reloadPlan(); state.notice = { type: "ok", text: "Task approved." }; render();
  },
  "task-edit": t => { state.modal = { type: "decide-task", mode: "modified", item: planTaskById(t.dataset.id) }; render(); },
  "task-reject": t => { state.modal = { type: "decide-task", mode: "rejected", item: planTaskById(t.dataset.id) }; render(); },
  "run-plan": async () => {
    if (!confirm("Run Agent 2? New draft stories and tasks will be generated from the approved requirements. PM decisions are kept.")) return;
    const job = await post(`/projects/${state.project.id}/jobs`, { type: "agent2.plan" });
    state.planJob = job; render(); pollPlanJob(job.id);
  },
  "plan-approve-all": async () => {
    const id = state.project.id;
    const sIds = state.stories.filter(s => s.review_status === "draft").map(s => s.id);
    if (!sIds.length && !state.tasks.some(x => x.review_status === "draft")) { state.notice = { type: "ok", text: "Nothing to approve — every story and task is decided." }; render(); return; }
    if (!confirm(`Approve all draft stories (${sIds.length}) and their draft tasks in one go?`)) return;
    if (sIds.length) await post(`/projects/${id}/stories/decision`, { ids: sIds, decision: "approved" });
    await reloadPlan();
    const rejectedStories = new Set(state.stories.filter(s => s.review_status === "rejected").map(s => s.id));
    const tIds = state.tasks.filter(x => x.review_status === "draft" && !rejectedStories.has(x.story_id)).map(x => x.id);
    if (tIds.length) await post(`/projects/${id}/tasks/decision`, { ids: tIds, decision: "approved" });
    await reloadPlan();
    state.notice = { type: "ok", text: "Plan approved — Agent 3 can now compute the schedule." }; render();
  },
});

Object.assign(submitters, {
  "decide-story": async form => {
    const m = state.modal, d = new FormData(form);
    const body = { decision: m.mode, comment: String(d.get("comment") || "").trim() };
    if (m.mode === "modified") {
      body.text = String(d.get("text") || "").trim();
      body.acceptance_criteria = String(d.get("criteria") || "").split("\n").map(x => x.trim()).filter(Boolean);
    }
    await post(`/projects/${state.project.id}/stories/${m.item.id}/decision`, body);
    state.modal = null; await reloadPlan();
    state.notice = { type: "ok", text: m.mode === "rejected" ? "Story rejected." : "Story edited and approved." }; render();
  },
  "decide-task": async form => {
    const m = state.modal, d = new FormData(form);
    const body = { decision: m.mode, comment: String(d.get("comment") || "").trim() };
    if (m.mode === "modified") {
      body.title = String(d.get("title") || "").trim();
      body.estimate_hours = Number(d.get("estimate_hours"));
    }
    await post(`/projects/${state.project.id}/tasks/${m.item.id}/decision`, body);
    state.modal = null; await reloadPlan();
    state.notice = { type: "ok", text: m.mode === "rejected" ? "Task rejected." : "Task edited and approved." }; render();
  },
});

/* ===== Schedule / Agent 3 (M6.1) ===== */
let scheduleTimer = null;

async function reloadSchedule() {
  const id = state.project.id;
  const [draft, tasks] = await Promise.all([
    api(`/projects/${id}/scheduling`).catch(e => { if (e.message === "unauthorized") throw e; return null; }),
    api(`/projects/${id}/tasks`),
  ]);
  state.schedule = draft; state.tasks = tasks;
}

function pollScheduleJob(id) {
  clearInterval(scheduleTimer);
  const tick = async () => {
    try {
      const j = await api(`/jobs/${id}`);
      state.scheduleJob = j;
      const finished = ["done", "failed", "cancelled", "waiting_for_pm"].includes(j.status);
      if (finished) clearInterval(scheduleTimer);
      if (state.page === "project" && state.tab === "schedule") {
        if (finished) await reloadSchedule();
        if (!state.modal) render();
      }
    } catch (e) { clearInterval(scheduleTimer); }
  };
  tick(); scheduleTimer = setInterval(tick, 3000);
}

const scheduleBadge = s => s === "approved" ? '<span class="badge green">Approved</span>' : s === "rejected" ? '<span class="badge" style="background:#fde8e8;color:#b00020">Rejected</span>' : s === "superseded" ? '<span class="badge">Superseded</span>' : '<span class="badge blue">Draft</span>';
const estimateBadge = s => s === "llm" ? '<span class="badge blue">LLM</span>' : s === "pm" ? '<span class="badge green">PM</span>' : '<span class="badge">Hours→days</span>';
const memberName = id => { const m = state.team.find(x => String(x.id) === String(id)); return m ? m.name : null; };

function scheduleTaskRow(t, titles) {
  const assignee = t.assignee_id ? memberName(t.assignee_id) : null;
  const critical = t.is_critical ? '<span class="badge" style="background:#fff4e5;color:#9a5b00">Critical</span>' : "";
  const float = t.total_float > 0 ? `<span class="subtle">Float: ${esc(t.total_float)} working day${t.total_float === 1 ? "" : "s"}</span>` : '<span class="subtle">Float: 0 (critical)</span>';
  return `<div class="project-row"><div style="flex:1;min-width:0"><div class="project-title">${esc(titles[t.id] || t.title || "Task #" + t.id)} ${critical}</div><div class="row-meta"><span class="badge">${esc(fmtDate(t.start_date))} → ${esc(fmtDate(t.end_date))}</span><span class="badge">${esc(t.duration_days)} working day${t.duration_days === 1 ? "" : "s"}</span><span class="badge">${esc(assignee || "Unassigned")}</span>${float}</div></div></div>`;
}

function conflictRow(c, titles) {
  const tasks = c.task_ids.map(id => titles[id] || "Task #" + id).join(", ");
  const ratio = c.overload_ratio != null ? ` · peak ${(Number(c.overload_ratio) * 100).toFixed(0)}% of capacity` : "";
  return `<div class="project-row"><div><div class="project-title"><span class="badge" style="background:#fde8e8;color:#b00020">${esc(c.kind === "overallocation" ? "Overallocation" : "Outside availability")}</span> ${esc(memberName(c.assignee_id) || c.assignee_id)}</div><div class="row-meta"><span class="subtle">${esc(tasks)}</span><span class="subtle">${esc(fmtDate(c.start_date))} → ${esc(fmtDate(c.end_date))}</span><span class="subtle">${esc(c.working_days)} working day${c.working_days === 1 ? "" : "s"}${ratio}</span></div></div></div>`;
}

function scheduleView() {
  const d = state.schedule;
  const job = state.scheduleJob;
  const running = job && ["queued", "running"].includes(job.status);
  const schedulable = ["planning", "plan_review", "active"].includes(state.project.status) && state.tasks.some(t => t.review_status === "approved");
  const jobLine = job ? `<div class="callout${job.status === "failed" ? " warning" : ""}">Scheduling job #${esc(job.id)}: <b>${esc(job.status)}</b> (${esc(job.progress)}%)${job.error ? "<br>" + esc(job.error) : ""}</div>` : "";
  const hint = !schedulable ? `<p class="subtle">Agent 3 needs the work plan approved: review the plan tasks first (project status: ${esc(state.project.status)}).</p>` : "";

  const head = `<div class="page-head"><div><div class="eyebrow">Agent 3</div><h1>Schedule</h1><p>Deterministic dates, critical path, resource conflicts and capacity. Task dates are written only when you approve the draft.</p></div><div><button class="btn btn-primary" data-action="run-schedule"${running || !schedulable ? " disabled" : ""}>${running ? "Scheduling..." : "Run Agent 3"}</button>${hint}</div></div>`;
  if (!d) return `${head}${jobLine}<section class="card"><div class="card-body"><div class="empty"><div class="empty-icon">◷</div><h3>No schedule yet</h3><p>Approve the work plan tasks, then run Agent 3 to compute the project schedule.</p></div></div></section>`;

  const p = d.payload || {};
  const schedule = p.schedule || { tasks: [], critical_path: [], project_start: d.project_start, project_end: d.project_end, total_duration_days: d.total_duration_days };
  const titles = {};
  schedule.tasks.forEach(t => { titles[t.id] = t.title || state.tasks.find(x => String(x.id) === String(t.id))?.title || "Task #" + t.id; });
  const conflicts = p.conflicts || [];
  const capacity = p.capacity || { members: [], total_available_hours: 0, total_assigned_hours: 0 };
  const explanation = p.explanation || {};
  const decisions = d.review_status === "draft"
    ? `<div class="item-actions" style="margin-top:12px"><button class="btn btn-primary" data-action="schedule-approve">Approve & write dates</button><button class="btn btn-secondary" data-action="schedule-reject">Reject</button></div><p class="subtle" style="margin-top:6px">Approving writes start/end dates on every task (PM-locked tasks are skipped). Rejecting keeps the plan undated.</p>`
    : d.review_status === "rejected"
      ? `<div class="callout warning" style="margin-top:12px">Rejected${d.pm_comment ? ": " + esc(d.pm_comment) : ""}. Re-run Agent 3 after fixing the plan.</div>`
      : `<div class="callout" style="margin-top:12px">Approved${d.pm_comment ? ": " + esc(d.pm_comment) : ""}. Task dates are live in the plan.</div>`;

  const chain = (schedule.critical_path || []).map(id => esc(titles[id] || id)).join(' <span class="subtle">→</span> ');
  const taskRows = schedule.tasks.map(t => scheduleTaskRow(t, titles)).join("");
  const conflictRows = conflicts.map(c => conflictRow(c, titles)).join("");
  const capacityRows = (capacity.members || []).map(m => {
    const util = m.utilization == null ? "—" : Math.round(Number(m.utilization) * 100) + "%";
    const avail = m.available_days ? `${esc(m.available_days)} days · ${esc(Math.round(Number(m.available_hours)))} h available` : "No availability in this window";
    return `<div class="project-row"><div><div class="project-title">${esc(m.name || m.member_id)} ${m.is_overloaded ? '<span class="badge" style="background:#fde8e8;color:#b00020">Overloaded</span>' : ""}</div><div class="row-meta"><span class="badge">${esc(m.hours_per_day)} h/day</span><span class="badge">Utilization ${esc(util)}</span><span class="subtle">${avail}</span><span class="subtle">${esc(Math.round(Number(m.assigned_hours)))} h assigned</span></div></div></div>`;
  }).join("");
  const estimateRows = (p.estimates || []).map(e => {
    const hours = state.tasks.find(x => String(x.id) === String(e.task_id));
    return `<div class="project-row"><div><div class="project-title">${esc(titles[e.task_id] || "Task #" + e.task_id)} ${estimateBadge(e.source)}</div><div class="row-meta"><span class="badge">${esc(e.duration_days)} working day${e.duration_days === 1 ? "" : "s"}</span>${hours ? `<span class="subtle">${esc(hours.estimate_hours)} h estimate</span>` : ""}${e.rationale ? `<span class="subtle">Why: ${esc(e.rationale)}</span>` : ""}</div></div></div>`;
  }).join("");
  const explainLines = [explanation.summary, explanation.critical_path].filter(Boolean).map(x => `<p style="margin:6px 0">${esc(x)}</p>`).join("");
  const explainList = (explanation.conflicts || []).concat(explanation.estimates || []).map(x => `<li>${esc(x)}</li>`).join("");
  const explainSource = explanation.source === "template" ? '<span class="badge">Template wording</span>' : explanation.source === "llm" ? '<span class="badge blue">LLM wording</span>' : "";

  return `${head}${jobLine}
<div class="grid two-col">
  <section class="card section-card"><h2>Summary ${scheduleBadge(d.review_status)}</h2><p class="section-intro">Computed by code (CPM over working days, Fri+Sat weekend). The LLM never produces dates.</p>
    <div class="keyvals">${kv("Project start", fmtDate(schedule.project_start))}${kv("Project end", fmtDate(schedule.project_end))}${kv("Total duration", schedule.total_duration_days + " working days")}${kv("Tasks scheduled", String(schedule.tasks.length))}${kv("Conflicts", String(conflicts.length))}</div>
    ${decisions}
  </section>
  <section class="card section-card"><h2>Why this schedule ${explainSource}</h2><p class="section-intro">${explanation.source === "template" ? "Deterministic template (the LLM was unavailable)." : "Written by the LLM from numbers the code computed — it cannot change them."}</p>
    ${explainLines || '<p class="subtle">No explanation stored.</p>'}${explainList ? `<ul style="margin:8px 0 0 18px;font-size:13px">${explainList}</ul>` : ""}
    <p class="subtle" style="margin-top:10px">Draft #${esc(d.id)} · created ${esc(fmtDateTime(d.created_at))}${d.reviewed_at ? " · decided " + esc(fmtDateTime(d.reviewed_at)) : ""}</p>
  </section>
</div>
<section class="card section-card"><h2>Critical path</h2><p class="section-intro">One representative chain with zero float. Any delay here moves the project end.</p>
  ${chain ? `<p style="margin:4px 0">${chain}</p>` : '<p class="subtle">No critical chain stored.</p>'}
</section>
<section class="card section-card"><h2>Tasks (${schedule.tasks.length})</h2><div class="card-body"><div class="project-list">${taskRows}</div></div></section>
<section class="card section-card"><h2>Resource conflicts</h2>${conflicts.length ? `<div class="card-body"><div class="project-list">${conflictRows}</div></div>` : '<div class="card-body"><div class="callout">No resource conflicts: nobody is overallocated and every task fits its assignee availability.</div></div>'}</section>
<section class="card section-card"><h2>Team capacity</h2><div class="card-body"><div class="project-list">${capacityRows || '<div class="empty"><div class="empty-icon">☺</div><h3>No members</h3><p>Add team members to see capacity.</p></div>'}</div></div></section>
<section class="card section-card"><h2>How durations were estimated</h2><p class="section-intro">PM duration &gt; hours→days conversion &gt; LLM initial estimate (validated by code).</p><div class="card-body"><div class="project-list">${estimateRows}</div></div></section>`;
}

function scheduleModal() {
  return modalShell("Reject schedule", "No task dates will be written. Explain what should change before re-running Agent 3.", "decide-schedule",
    field("Reason *", '<textarea name="comment" required></textarea>', true), "Reject");
}

Object.assign(actions, {
  "run-schedule": async () => {
    if (!confirm("Run Agent 3? It computes a draft schedule (dates, critical path, conflicts, capacity). Task dates change only when you approve the draft.")) return;
    const job = await post(`/projects/${state.project.id}/jobs`, { type: "agent3.schedule" });
    state.scheduleJob = job; render(); pollScheduleJob(job.id);
  },
  "schedule-approve": async () => {
    const d = state.schedule;
    if (!confirm("Approve this schedule? Start/end dates are written on every task except PM-locked ones.")) return;
    await post(`/projects/${state.project.id}/scheduling/${d.id}/decision`, { decision: "approved" });
    await reloadSchedule(); await reloadPlan();
    state.notice = { type: "ok", text: "Schedule approved and task dates written." }; render();
  },
  "schedule-reject": () => { state.modal = { type: "decide-schedule" }; render(); },
});

Object.assign(submitters, {
  "decide-schedule": async form => {
    const d = new FormData(form);
    await post(`/projects/${state.project.id}/scheduling/${state.schedule.id}/decision`, { decision: "rejected", comment: String(d.get("comment") || "").trim() });
    state.modal = null; await reloadSchedule();
    state.notice = { type: "ok", text: "Schedule rejected. No dates were written." }; render();
  },
});