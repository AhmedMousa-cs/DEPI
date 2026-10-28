const API = "http://localhost:8000";
const TOKEN_KEY = "nexus-token";

function getToken() {
  return sessionStorage.getItem(TOKEN_KEY);
}

function logout() {
  sessionStorage.removeItem(TOKEN_KEY);
  location.reload();
}

async function apiFetch(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  const token = getToken();
  if (token) headers.Authorization = "Bearer " + token;
  const res = await fetch(API + path, { ...options, headers });
  if (res.status === 401) {
    sessionStorage.removeItem(TOKEN_KEY);
    showLogin("Session expired. Please sign in again.");
    throw new Error("unauthorized");
  }
  return res;
}

function showLogin(message) {
  const el = document.getElementById("app");
  el.innerHTML =
    '<div style="max-width:360px;margin:12vh auto;padding:24px;font-family:system-ui,sans-serif">' +
    "<h2>Sign in to NEXUS</h2>" +
    '<form id="login-form">' +
    '<label>Email<br><input name="email" type="email" required style="width:100%;padding:8px;margin:4px 0 12px"></label>' +
    '<label>Password<br><input name="password" type="password" required style="width:100%;padding:8px;margin:4px 0 12px"></label>' +
    '<button style="width:100%;padding:10px">Sign in</button>' +
    '<p id="login-error" style="color:#b00020;min-height:1.2em"></p>' +
    "</form></div>";
  const err = document.getElementById("login-error");
  if (message) err.textContent = message;
  document.getElementById("login-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const d = new FormData(e.target);
    err.textContent = "";
    try {
      const res = await fetch(API + "/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: d.get("email"), password: d.get("password") }),
      });
      if (!res.ok) {
        err.textContent = res.status === 401 ? "Invalid email or password" : "Server error";
        return;
      }
      const data = await res.json();
      sessionStorage.setItem(TOKEN_KEY, data.access_token);
      location.reload();
    } catch (_) {
      err.textContent = "Cannot reach the server. Is the backend running?";
    }
  });
}