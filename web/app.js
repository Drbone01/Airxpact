(function () {
  const C = window.AIRXPACT_CONFIG;
  const $ = (id) => document.getElementById(id);
  const store = {
    get: (k) => { try { return sessionStorage.getItem(k); } catch (e) { return null; } },
    set: (k, v) => { try { sessionStorage.setItem(k, v); } catch (e) {} },
    del: (k) => { try { sessionStorage.removeItem(k); } catch (e) {} },
  };

  const b64url = (buf) => btoa(String.fromCharCode(...new Uint8Array(buf))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  const randomString = () => b64url(crypto.getRandomValues(new Uint8Array(32)));
  const sha256 = (s) => crypto.subtle.digest("SHA-256", new TextEncoder().encode(s));

  async function login() {
    const verifier = randomString();
    const state = randomString();
    store.set("pkce_verifier", verifier);
    store.set("pkce_state", state);
    const challenge = b64url(await sha256(verifier));
    const p = new URLSearchParams({
      response_type: "code", client_id: C.CLIENT_ID, redirect_uri: C.REDIRECT_URI,
      scope: C.SCOPES, state, code_challenge_method: "S256", code_challenge: challenge,
    });
    location.assign(`${C.COGNITO_DOMAIN}/oauth2/authorize?${p}`);
  }

  async function handleCallback() {
    const q = new URLSearchParams(location.search);
    if (!q.get("code")) return;
    if (q.get("state") !== store.get("pkce_state")) { alert("Sign-in state mismatch. Please try again."); return; }
    const res = await fetch(`${C.COGNITO_DOMAIN}/oauth2/token`, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({
        grant_type: "authorization_code", client_id: C.CLIENT_ID, code: q.get("code"),
        redirect_uri: C.REDIRECT_URI, code_verifier: store.get("pkce_verifier") || "",
      }),
    });
    history.replaceState({}, "", location.pathname);
    if (!res.ok) { alert("Sign-in failed: " + (await res.text())); return; }
    const t = await res.json();
    store.set("access_token", t.access_token);
    store.set("id_token", t.id_token);
    store.set("expires_at", String(Date.now() + (t.expires_in - 60) * 1000));
    store.del("pkce_verifier"); store.del("pkce_state");
  }

  function signedIn() {
    return store.get("access_token") && Number(store.get("expires_at") || 0) > Date.now();
  }

  function logout() {
    ["access_token", "id_token", "expires_at", "session_id"].forEach(store.del);
    const p = new URLSearchParams({ client_id: C.CLIENT_ID, logout_uri: C.REDIRECT_URI });
    location.assign(`${C.COGNITO_DOMAIN}/logout?${p}`);
  }

  function email() {
    try { return JSON.parse(atob(store.get("id_token").split(".")[1].replace(/-/g, "+").replace(/_/g, "/"))).email || ""; }
    catch (e) { return ""; }
  }

  function sessionId() {
    let s = store.get("session_id");
    if (!s) { s = "trip-" + randomString(); store.set("session_id", s); }
    return s;
  }

  function escapeHtml(s) { return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }
  function render(md) {
    let h = escapeHtml(md);
    h = h.replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
    h = h.replace(/(^|[\s(])(https?:\/\/[^\s<)]+)/g, '$1<a href="$2" target="_blank" rel="noopener">link</a>');
    h = h.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    h = h.replace(/^#{1,4} (.*)$/gm, "<strong>$1</strong>");
    h = h.replace(/^\s*[-*] (.*)$/gm, "&bull; $1");
    return h.replace(/\n/g, "<br>");
  }

  function addMsg(text, who, cls) {
    const d = document.createElement("div");
    d.className = `msg ${who} ${cls || ""}`;
    d.innerHTML = who === "bot" ? render(text) : escapeHtml(text);
    $("messages").appendChild(d);
    $("messages").scrollTop = $("messages").scrollHeight;
    return d;
  }

  async function send(prompt) {
    if (!signedIn()) { login(); return; }
    $("suggestions").classList.add("hidden");
    addMsg(prompt, "user");
    const typing = addMsg("Airxpact is checking routes, weather and sources...", "bot", "typing");
    $("send").disabled = true;
    try {
      const res = await fetch(C.API_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: "Bearer " + store.get("access_token") },
        body: JSON.stringify({ prompt, session_id: sessionId() }),
      });
      const data = await res.json().catch(() => ({}));
      typing.remove();
      if (res.status === 401 || res.status === 403) { addMsg("Your sign-in expired. Please sign in again.", "bot", "err"); setTimeout(login, 1500); return; }
      if (!res.ok) { addMsg(data.error ? `${data.error}` : `Error ${res.status}`, "bot", "err"); return; }
      addMsg(data.response || "(no answer)", "bot");
    } catch (e) {
      typing.remove();
      addMsg("Network error: " + e.message, "bot", "err");
    } finally {
      $("send").disabled = false;
    }
  }

  window.addEventListener("DOMContentLoaded", async () => {
    await handleCallback();
    const ok = signedIn();
    $("welcome").classList.toggle("hidden", ok);
    $("chat").classList.toggle("hidden", !ok);
    $("logout").classList.toggle("hidden", !ok);
    $("newChat").classList.toggle("hidden", !ok);
    if (ok) $("who").textContent = email();
    $("login").onclick = login;
    $("logout").onclick = logout;
    $("newChat").onclick = () => { store.del("session_id"); location.reload(); };
    $("form").onsubmit = (e) => { e.preventDefault(); const v = $("input").value.trim(); if (v) { $("input").value = ""; send(v); } };
    $("input").addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("form").requestSubmit(); } });
    document.querySelectorAll("#suggestions button").forEach((b) => (b.onclick = () => send(b.textContent)));
  });
})();
