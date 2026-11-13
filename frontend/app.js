"use strict";
/* NEXUS front-end (M2): talks to the FastAPI backend. PostgreSQL is the source of truth;
   nothing business-related is stored in the browser. */

const MAX_UPLOAD = 5 * 1024 * 1024;
const STATUS = { setup: "Setup", planning: "Planning", scope_review: "Scope review", plan_review: "Plan review", active: "Active", closed: "Closed" };
const KIND = { brief: "Brief", transcript: "Transcript", note: "Note", file: "File" };

const state = { me: null, projects: [], project: null, team: [], leaders: [], page: "portfolio", tab: "overview", modal: null, notice: null, navOpen: false, reqs: [], scope: null, reqFilter: "all", job: null, stories: [], tasks: [], coverage: null, planFilter: "all", planJob: null };
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

function portfolioView() {
  const rows = state.projects.map(p => `<div class="project-row"><div><div class="project-title">${esc(p.name)}</div><div class="project-sub">${esc(p.goal || "No goal added")}</div><div class="row-meta">${statusBadge(p.status)}<span class="subtle">${fmtDate(p.start_date)} → ${fmtDate(p.end_date)}</span></div></div><button class="btn btn-secondary btn-small" data-action="open-project" data-id="${p.id}">Open workspace →</button></div>`).join("");
  const empty = `<div class="empty"><div class="empty-icon">◈</div><h3>${isPM() ? "No projects yet" : "No projects assigned to you"}</h3><p>${isPM() ? "Create the first project with a brief and an optional kickoff transcript." : "A Project Manager must link you as a Team Leader on a project."}</p>${isPM() ? '<button class="btn btn-primary" data-action="modal" data-modal="create-project">Create first project</button>' : ""}</div>`;
  return `<div class="page-head"><div><div class="eyebrow">Portfolio</div><h1>${isPM() ? "All projects" : "My projects"}</h1><p>Everything shown here is loaded from the database.</p></div>${isPM() ? '<button class="btn btn-primary" data-action="modal" data-modal="create-project">＋ Create project</button>' : ""}</div><section class="card"><div class="card-body">${state.projects.length ? `<div class="project-list">${rows}</div>` : empty}</div></section>`;
}

function projectView() {
  const p = state.project;
  const tabs = [["overview", "Overview"], ["sources", "Sources (" + p.sources.length + ")"], ["team", "Team (" + state.team.length + ")"]];
  if (isPM()) tabs.push(["scope", "Scope review"], ["plan", "Work plan"]);
  const later = ["Updates (M7)", "Decisions (M8)"];
  return `<div class="workspace-head"><div class="workspace-title"><div class="project-monogram">${esc((p.name || "P")[0].toUpperCase())}</div><div><h1>${esc(p.name)}</h1><p>${esc(p.goal || "No goal added")}</p></div></div><div style="display:flex;gap:8px;align-items:center">${statusBadge(p.status)}${isPM() ? '<button class="btn btn-secondary btn-small" data-action="modal" data-modal="edit-project">Edit project</button>' : ""}</div></div><div class="tabs">${tabs.map(t => `<button class="${state.tab === t[0] ? "active" : ""}" data-action="tab" data-tab="${t[0]}">${esc(t[1])}</button>`).join("")}${later.map(l => `<button disabled style="opacity:.45;cursor:not-allowed" title="Not available yet">${esc(l)}</button>`).join("")}</div>${state.tab === "sources" ? sourcesView() : state.tab === "team" ? teamView() : state.tab === "scope" ? scopeView() : state.tab === "plan" ? planView() : overviewView()}`;
}

function overviewView() {
  const p = state.project, leader = state.team.find(m => m.is_leader);
  const hours = state.team.reduce((n, m) => n + Number(m.capacity_hours_per_week || 0), 0);
  return `<div class="grid two-col"><section class="card section-card"><h2>Project snapshot</h2><p class="section-intro">Saved in PostgreSQL; survives a browser refresh.</p><div class="keyvals">${kv("Status", STATUS[p.status] || p.status)}${kv("Start", fmtDate(p.start_date))}${kv("Target end", fmtDate(p.end_date))}${kv("Team", state.team.length + " members · " + hours + " h/week")}${kv("Team Leader", leader ? leader.name + (leader.user_email ? " (" + leader.user_email + ")" : "") : "Not assigned")}${kv("Sources", String(p.sources.length))}</div></section><section class="card section-card"><h2>Project brief</h2><p class="section-intro">${esc(p.goal || "No goal added")}</p><div style="white-space:pre-wrap;font-size:13px">${esc(p.brief || "No brief")}</div></section></div><div class="callout">Requirement extraction, scope approval and planning arrive in the next milestones (M4 onward).</div>`;
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
  app.innerHTML = `<div class="app-shell">${sidebar()}<div class="main-area">${topbar()}<main class="content">${notice ? noticeHtml(notice) : ""}${body}</main></div></div>${state.modal ? (state.modal.type === "decide-req" ? scopeModal() : state.modal.type === "decide-story" || state.modal.type === "decide-task" ? planModal() : modalView()) : ""}`;
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
  "go-portfolio": async () => { await reloadProjects(); state.page = "portfolio"; state.project = null; state.navOpen = false; render(); },
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
  return `<div class="project-row" style="align-items:flex-start"><div style="flex:1;min-width:0"><div class="project-title">${esc(r.text)}</div><blockquote style="margin:8px 0;padding:6px 10px;border-left:3px solid #dde5ef;font-size:13px;white-space:pre-wrap">${esc(r.quote || "No quote")}</blockquote><div class="row-meta">${status}<span class="badge">${esc(r.type)}</span>${r.priority ? `<span class="badge">${esc(r.priority)}</span>` : ""}${verified}<span class="subtle">Confidence ${pct}%</span>${warn}${src}</div>${r.pm_comment ? `<div class="subtle">PM: ${esc(r.pm_comment)}</div>` : ""}</div>${act}</div>`;
}

function scopeView() {
  const c = reqCounts(), job = state.job, sc = state.scope;
  const running = job && ["queued", "running"].includes(job.status);
  const gate = sc ? (typeof sc.gate === "string" ? sc.gate : JSON.stringify(sc.gate)) : "unknown";
  const jobLine = job ? `<div class="callout${job.status === "failed" ? " warning" : ""}">Extraction job #${esc(job.id)}: <b>${esc(job.status)}</b> (${esc(job.progress)}%)${job.error ? "<br>" + esc(job.error) : ""}</div>` : "";
  const chips = [["all", "All", c.all], ["draft", "To review", c.draft], ["approved", "Approved", c.approved], ["rejected", "Rejected", c.rejected], ["unverified", "Unverified", c.unverified]]
    .map(f => `<button class="btn ${state.reqFilter === f[0] ? "btn-primary" : "btn-secondary"} btn-small" data-action="scope-filter" data-f="${f[0]}">${f[1]} (${f[2]})</button>`).join(" ");
  const list = filteredReqs().map(reqCard).join("");
  const canApprove = c.draft === 0 && c.all > 0;
  return `<div class="page-head"><div><div class="eyebrow">US02</div><h1>Scope review</h1><p>Each requirement comes with the quote it was extracted from. Approve, edit or reject every draft, then approve the scope.</p></div><div><button class="btn btn-secondary" data-action="run-extract"${running ? " disabled" : ""}>${running ? "Extracting..." : "Run extraction"}</button></div></div>${jobLine}<div class="callout">Gate: <b>${esc(gate)}</b> · Project status: <b>${esc(sc ? sc.project_status : state.project.status)}</b></div><div style="display:flex;gap:6px;flex-wrap:wrap;margin:12px 0">${chips}</div><section class="card"><div class="card-body">${list ? `<div class="project-list">${list}</div>` : '<div class="empty"><div class="empty-icon">◈</div><h3>No requirements here</h3><p>Run the extraction, or change the filter.</p></div>'}</div></section><section class="card section-card" style="margin-top:16px"><h2>Approve scope</h2><p class="section-intro">Every requirement must be decided first.</p><div class="form-grid">${field("Comment (optional)", '<input id="scope-comment" maxlength="2000">', true)}</div><div style="display:flex;gap:8px;margin-top:12px"><button class="btn btn-primary" data-action="approve-scope"${canApprove ? "" : " disabled"}>Approve scope</button><button class="btn btn-quiet" data-action="open-gate">Re-open gate</button></div>${canApprove ? "" : `<p class="subtle">${c.all === 0 ? "Run the extraction first." : c.draft + " draft(s) still need a decision."}</p>`}</section>`;
}

function scopeModal() {
  const m = state.modal, r = m.req;
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
    baseTab(t);
  },
  "scope-filter": t => { state.reqFilter = t.dataset.f; render(); },
  "req-approve": async t => {
    await post(`/projects/${state.project.id}/requirements/${t.dataset.id}/decision`, { decision: "approved" });
    await reloadScope(); render();
  },
  "req-reject": t => { state.modal = { type: "decide-req", mode: "rejected", req: reqById(t.dataset.id) }; render(); },
  "req-edit": t => { state.modal = { type: "decide-req", mode: "modified", req: reqById(t.dataset.id) }; render(); },
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
  return `<div class="project-row" style="align-items:flex-start"><div style="flex:1;min-width:0"><div class="project-title">${esc(t.title)} ${planBadge(t.review_status)}</div><div class="row-meta"><span class="badge">${esc(t.estimate_hours)} h</span><span class="badge">${esc(t.status)}</span><span class="subtle">Confidence ${Math.round((Number(t.confidence) || 0) * 100)}%</span>${deps ? `<span class="subtle">Depends on:</span> ${deps}` : ""}</div>${t.pm_comment ? `<div class="subtle">PM: ${esc(t.pm_comment)}</div>` : ""}</div>${actions}</div>`;
}

function planStoryCard(s) {
  const tasks = state.tasks.filter(t => t.story_id === s.id && planMatch(t));
  const criteria = (s.acceptance_criteria || []).map(c => `<li>${esc(c)}</li>`).join("");
  const actions = s.review_status === "draft"
    ? `<div class="item-actions" style="margin:8px 0 0"><button class="btn btn-primary btn-small" data-action="story-approve" data-id="${s.id}">Approve</button><button class="btn btn-secondary btn-small" data-action="story-edit" data-id="${s.id}">Edit</button><button class="btn btn-danger btn-small" data-action="story-reject" data-id="${s.id}">Reject</button></div>`
    : "";
  const taskBlock = tasks.length ? `<div class="project-list" style="margin-top:10px">${tasks.map(planTaskRow).join("")}</div>` : '<p class="subtle" style="margin:10px 0 0">No tasks under this story (or hidden by the filter).</p>';
  return `<div class="project-row" style="display:block;align-items:stretch"><div class="project-title">${esc(s.text)} ${planBadge(s.review_status)}</div><div class="row-meta"><span class="badge">Requirement #${s.requirement_id}</span><span class="subtle">Confidence ${Math.round((Number(s.confidence) || 0) * 100)}%</span></div><div class="subtle">${esc(reqLabel(s.requirement_id))}</div>${criteria ? `<ul style="margin:8px 0 0 18px;font-size:13px">${criteria}</ul>` : ""}${actions}${taskBlock}</div>`;
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
  return `<div class="page-head"><div><div class="eyebrow">US03</div><h1>Work plan</h1><p>Stories and tasks generated from approved requirements. The PM reviews every draft before it becomes the plan.</p></div><div><button class="btn btn-primary" data-action="run-plan"${running || !plannable ? " disabled" : ""}>${running ? "Planning..." : "Run Agent 2"}</button>${hint}</div></div>${jobLine}${coverageSection(c)}<div class="page-head" style="margin-top:18px"><div><h1 style="font-size:18px">Stories & tasks</h1><p>Approve, edit or reject every draft. Your decisions survive an Agent 2 re-run.</p></div></div><div style="display:flex;gap:6px;flex-wrap:wrap;margin:12px 0">${chips}</div><section class="card"><div class="card-body">${list ? `<div class="project-list">${list}</div>` : '<div class="empty"><div class="empty-icon">◈</div><h3>No stories yet</h3><p>Approve the scope, then run Agent 2.</p></div>'}</div></section>`;
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