"use strict";
/* NEXUS front-end (M2): talks to the FastAPI backend. PostgreSQL is the source of truth;
   nothing business-related is stored in the browser. */

const MAX_UPLOAD = 5 * 1024 * 1024;
const STATUS = { setup: "Setup", planning: "Planning", scope_review: "Scope review", plan_review: "Plan review", active: "Active", closed: "Closed" };
const KIND = { brief: "Brief", transcript: "Transcript", note: "Note", file: "File" };

const state = { me: null, projects: [], project: null, team: [], leaders: [], page: "portfolio", tab: "overview", modal: null, notice: null, navOpen: false };
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
  const later = ["Scope review (M4)", "Work plan (M5–M6)", "Updates (M7)", "Decisions (M8)"];
  return `<div class="workspace-head"><div class="workspace-title"><div class="project-monogram">${esc((p.name || "P")[0].toUpperCase())}</div><div><h1>${esc(p.name)}</h1><p>${esc(p.goal || "No goal added")}</p></div></div><div style="display:flex;gap:8px;align-items:center">${statusBadge(p.status)}${isPM() ? '<button class="btn btn-secondary btn-small" data-action="modal" data-modal="edit-project">Edit project</button>' : ""}</div></div><div class="tabs">${tabs.map(t => `<button class="${state.tab === t[0] ? "active" : ""}" data-action="tab" data-tab="${t[0]}">${esc(t[1])}</button>`).join("")}${later.map(l => `<button disabled style="opacity:.45;cursor:not-allowed" title="Not available yet">${esc(l)}</button>`).join("")}</div>${state.tab === "sources" ? sourcesView() : state.tab === "team" ? teamView() : overviewView()}`;
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
  app.innerHTML = `<div class="app-shell">${sidebar()}<div class="main-area">${topbar()}<main class="content">${notice ? noticeHtml(notice) : ""}${body}</main></div></div>${state.modal ? modalView() : ""}`;
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
