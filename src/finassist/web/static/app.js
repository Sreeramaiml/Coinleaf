// Coinleaf GUI. No third-party code. HTML inserted here comes only from the server's
// autoescaped templates; chart tooltip text is inserted with textContent.
"use strict";
(function () {
  const csrf = document.querySelector('meta[name="csrf"]').content;
  // Every POST carries the CSRF token. A reply with X-Redirect (signed in, locked, signed out)
  // moves the page there instead of showing a fragment.
  const post = async (url, body) => {
    const r = await fetch(url, { method: "POST", headers: { "X-CSRF-Token": csrf }, body, credentials: "same-origin" });
    const to = r.headers.get("X-Redirect");
    if (to && to.startsWith("/")) { location.href = to; throw new Error("redirect"); }
    return r;
  };
  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined) e.textContent = text; return e; };

  // ---- theme toggle (remembered in this browser only)
  document.querySelectorAll("[data-theme-toggle]").forEach((b) => b.addEventListener("click", () => {
    const root = document.documentElement;
    const dark = root.dataset.theme ? root.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
    root.dataset.theme = dark ? "light" : "dark";
    try { localStorage.setItem("finassist-theme", root.dataset.theme); } catch (e) { /* ignore */ }
  }));

  // ---- ledger switch and creation
  document.querySelectorAll("[data-ledger]").forEach((b) => b.addEventListener("click", async () => {
    const fd = new FormData(); fd.append("kind", b.dataset.ledger);
    await post("/api/ledger", fd); location.reload();
  }));
  document.querySelectorAll("[data-create-ledger]").forEach((b) => b.addEventListener("click", async () => {
    b.disabled = true; const label = b.textContent; b.textContent = "Working…";
    const r = await (await post("/api/ledger/create", new FormData())).json();
    if (r.ok) { location.href = "/dashboard"; return; }
    const out = document.getElementById("create-result");
    if (out) out.replaceChildren(el("div", "msg error", r.error));
    b.disabled = false; b.textContent = label;
  }));

  // ---- lock: the sidebar button, and automatically after the idle time without mouse or keyboard use
  const lock = (why) => post("/logout", new FormData()).catch(() => {}).finally(() => { location.href = "/login?" + why + "=1"; });
  document.querySelectorAll("[data-lock]").forEach((b) => b.addEventListener("click", () => lock("signed_out")));
  const idle = Number(document.body.dataset.idleMinutes || 0) * 60 * 1000;
  if (idle) {
    let last = Date.now();
    ["mousemove", "mousedown", "keydown", "scroll", "touchstart", "wheel"].forEach((t) =>
      addEventListener(t, () => { last = Date.now(); }, { passive: true }));
    setInterval(() => { if (Date.now() - last > idle) lock("locked"); }, 15000);
  }

  // ---- password fields: show/hide, and live checks while creating one
  document.querySelectorAll("[data-pw-toggle]").forEach((b) => b.addEventListener("click", () => {
    const input = document.getElementById(b.getAttribute("aria-controls"));
    const show = input.type === "password";
    input.type = show ? "text" : "password";
    b.textContent = show ? "Hide" : "Show";
    b.setAttribute("aria-pressed", String(show));
    input.focus();
  }));
  document.querySelectorAll("form[data-pw-rules]").forEach((f) => {
    const pw = f.querySelector("#password"), again = f.querySelector("#confirm");
    const min = Number(pw.getAttribute("minlength") || 8);
    const check = () => {
      f.querySelector('[data-rule="length"]').classList.toggle("ok", pw.value.length >= min);
      f.querySelector('[data-rule="match"]').classList.toggle("ok", pw.value.length > 0 && pw.value === again.value);
    };
    pw.addEventListener("input", check); again.addEventListener("input", check);
  });

  // ---- filter forms submit as soon as a menu changes
  document.querySelectorAll("form[data-autosubmit]").forEach((f) =>
    f.querySelectorAll("select").forEach((s) => s.addEventListener("change", () => f.requestSubmit())));

  // ---- forms that post to the API and show the returned fragment: <form data-api data-target>
  document.addEventListener("submit", async (ev) => {
    const form = ev.target.closest("form[data-api]");
    if (!form) return;
    ev.preventDefault();
    const target = document.querySelector(form.dataset.target);
    const fd = new FormData(form, ev.submitter || undefined);
    const buttons = form.querySelectorAll("button");
    buttons.forEach((b) => (b.disabled = true));
    try {
      const r = await post(form.dataset.api, fd);
      target.innerHTML = await r.text();
      target.scrollIntoView({ behavior: "smooth", block: "nearest" });
      const step = document.querySelectorAll(".steps li");
      if (step.length && target.querySelector(".preview")) { step.forEach((s) => s.classList.remove("on")); step[1].classList.add("on"); }
      if (step.length && target.querySelector(".msg.ok")) { step.forEach((s) => s.classList.remove("on")); step[2].classList.add("on"); }
    } catch (e) {
      if (e.message === "redirect") return;
      target.replaceChildren(el("div", "msg error", "Couldn't reach Coinleaf. Is it still running? (" + e + ")"));
    } finally { buttons.forEach((b) => (b.disabled = false)); }
  });

  // ---- file picker: show the chosen name; drag-and-drop highlight
  document.querySelectorAll(".dropzone").forEach((z) => {
    const input = z.querySelector("input[type=file]");
    const name = z.querySelector("[data-file-name]");
    input.addEventListener("change", () => { if (input.files.length) name.textContent = input.files[0].name; });
    ["dragenter", "dragover"].forEach((t) => z.addEventListener(t, () => z.classList.add("drag")));
    ["dragleave", "drop"].forEach((t) => z.addEventListener(t, () => z.classList.remove("drag")));
  });

  // ---- chart tooltips (hover and keyboard focus); the crosshair snaps to the month
  const tip = document.getElementById("tooltip");
  function show(target, x, y) {
    tip.replaceChildren(el("div", "tt-title", target.dataset.title || ""));
    (target.dataset.rows || "").split("\n").filter(Boolean).forEach((line) => {
      const [label, value] = line.split("\t");
      const row = el("div", "tt-row"); row.append(el("span", "", label || "")); row.append(el("strong", "", value || ""));
      tip.append(row);
    });
    tip.hidden = false;
    const w = tip.offsetWidth, h = tip.offsetHeight;
    tip.style.left = Math.min(x + 14, window.innerWidth - w - 8) + "px";
    tip.style.top = Math.max(y - h - 12, 8) + "px";
    const cx = target.dataset.cx;
    const hair = target.ownerSVGElement && target.ownerSVGElement.querySelector(".crosshair");
    if (hair && cx) { hair.setAttribute("x1", cx); hair.setAttribute("x2", cx); hair.setAttribute("visibility", "visible"); }
  }
  function hide(target) {
    tip.hidden = true;
    const hair = target.ownerSVGElement && target.ownerSVGElement.querySelector(".crosshair");
    if (hair) hair.setAttribute("visibility", "hidden");
  }
  document.querySelectorAll(".hit, .seg").forEach((h) => {
    h.addEventListener("pointermove", (e) => show(h, e.clientX, e.clientY));
    h.addEventListener("pointerleave", () => hide(h));
    h.addEventListener("focus", () => { const r = h.getBoundingClientRect(); show(h, r.left + r.width / 2, r.top); });
    h.addEventListener("blur", () => hide(h));
  });
})();
