"use strict";

const state = {
  data: null,
  q: "",
  category: "",
  subcategory: "",
  type: "",
  confidence: "",
  verified: false,
  view: "grid",
  page: "home",
  entityId: null,
  taskId: null,
};

const $ = (sel, root = document) => root.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => (
  { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

async function boot() {
  try {
    const res = await fetch("data/dataseek.json", { cache: "no-cache" });
    state.data = await res.json();
  } catch (err) {
    document.querySelector("main").innerHTML =
      `<div class="empty">Could not load data/dataseek.json — run <code>scripts/v2/export.py</code> first.</div>`;
    return;
  }
  window.addEventListener("hashchange", route);
  $("#theme-toggle").addEventListener("click", toggleTheme);
  $("#q").addEventListener("input", (e) => { state.q = e.target.value; renderPage(); });
  route();
}

function toggleTheme() {
  const cur = document.documentElement.getAttribute("data-theme") === "light" ? "dark" : "light";
  document.documentElement.setAttribute("data-theme", cur);
  localStorage.setItem("ds-theme", cur);
}

function route() {
  const hash = location.hash.replace(/^#/, "") || "/";
  const parts = hash.split("/").filter(Boolean);
  state.page = parts[0] || "home";
  state.entityId = parts[0] === "resource" ? parts[1] : null;
  state.taskId = parts[0] === "record" ? parts[1] : null;
  if (state.page === "home") { state.q = ""; const q = $("#q"); if (q) q.value = ""; }
  renderPage();
  window.scrollTo(0, 0);
}

function filteredResources() {
  const q = state.q.trim().toLowerCase();
  return state.data.resources.filter((r) => {
    if (state.category && r.primary_category !== state.category &&
        !(r.secondary_categories || []).includes(state.category)) return false;
    if (state.subcategory && r.subcategory !== state.subcategory) return false;
    if (state.type && (r.type || "") !== state.type) return false;
    if (state.confidence && (r.confidence || "") !== state.confidence) return false;
    if (state.verified && !["HIGH", "MEDIUM"].includes(r.confidence)) return false;
    if (!q) return true;
    const hay = [r.name, r.canonical_name, r.short_description, r.primary_category,
      r.subcategory, (r.tags || []).join(" "), (r.urls || []).join(" "),
      (r.features || []).join(" "), (r.technologies || []).join(" "),
      r.entity_id].join(" ").toLowerCase();
    if (hay.includes(q)) return true;
    const tokens = q.split(/\s+/).filter((t) => t.length > 2);
    return tokens.length > 0 && tokens.every((t) => hay.includes(t));
  }).sort((a, b) => {
    if (q) {
      const as = (a.name || "").toLowerCase().startsWith(q) ? 1 : 0;
      const bs = (b.name || "").toLowerCase().startsWith(q) ? 1 : 0;
      if (as !== bs) return bs - as;
    }
    return (b.source_count || 0) - (a.source_count || 0);
  });
}

function renderPage() {
  const main = $("main");
  if (state.page === "resource") return renderResource(main);
  if (state.page === "record") return renderRecord(main);
  if (state.page === "categories") return renderCategories(main);
  return renderHome(main);
}

function confidencePill(c) {
  const cls = c === "HIGH" ? "good" : c === "MEDIUM" ? "warn" : c === "LOW" ? "bad" : "";
  return `<span class="pill ${cls}">${esc(c || "unverified")}</span>`;
}

function resourceCard(r) {
  return `<a class="card" href="#resource/${esc(r.entity_id)}">
    <div class="meta">
      <span class="pill cat">${esc(r.primary_category)}${r.subcategory ? " › " + esc(r.subcategory) : ""}</span>
      ${confidencePill(r.confidence)}
    </div>
    <h3>${esc(r.name)}</h3>
    <div class="desc">${esc(r.short_description || "No verified description yet.")}</div>
    <div class="meta">${(r.tags || []).slice(0, 4).map((t) => `<span class="pill">${esc(t)}</span>`).join("")}</div>
    <div class="foot"><span>${esc(r.type || "resource")}</span><span>${r.source_count || 0} source${(r.source_count || 0) === 1 ? "" : "s"}</span></div>
  </a>`;
}

function renderHome(main) {
  const s = state.data.statistics;
  const cats = (s.category_breakdown || []).filter((c) => c.count > 0);
  const results = filteredResources();
  const subcats = [...new Set(state.data.resources
    .filter((r) => !state.category || r.primary_category === state.category)
    .map((r) => r.subcategory).filter(Boolean))].sort();
  const types = [...new Set(state.data.resources.map((r) => r.type).filter(Boolean))].sort();

  main.innerHTML = `
    <section class="hero">
      <h1>DataSeek</h1>
      <p>Search a curated knowledge base of resources, tools, repositories, websites and apps —
      each one traced back to its source evidence, with extraction and research provenance.</p>
    </section>
    <div class="stats">
      ${stat(s.records, "Records")}
      ${stat(s.resources, "Resources")}
      ${stat(s.categories, "Categories")}
      ${stat(s.technologies, "Technologies")}
      ${stat(s.github_repositories, "GitHub repos")}
      ${stat(s.ai_tools, "AI tools")}
      ${stat(s.verified_resources, "Verified")}
      ${stat(s.urls, "URLs")}
    </div>
    <div class="chips">
      <button class="chip ${!state.category ? "active" : ""}" data-cat="">All</button>
      ${cats.map((c) => `<button class="chip ${state.category === c.category ? "active" : ""}"
        data-cat="${esc(c.category)}">${esc(c.category)} <small>${c.count}</small></button>`).join("")}
    </div>
    <div class="layout">
      <aside class="filters">
        <h4>Filters</h4>
        <label class="pill">Subcategory</label>
        <select id="f-sub"><option value="">All</option>
          ${subcats.map((x) => `<option ${state.subcategory === x ? "selected" : ""}>${esc(x)}</option>`).join("")}</select>
        <label class="pill">Resource type</label>
        <select id="f-type"><option value="">All</option>
          ${types.map((x) => `<option ${state.type === x ? "selected" : ""}>${esc(x)}</option>`).join("")}</select>
        <label class="pill">Confidence</label>
        <select id="f-conf"><option value="">All</option>
          ${["HIGH", "MEDIUM", "LOW"].map((x) => `<option ${state.confidence === x ? "selected" : ""}>${x}</option>`).join("")}</select>
        <label style="display:flex;gap:8px;align-items:center;font-size:13px">
          <input type="checkbox" id="f-verified" ${state.verified ? "checked" : ""}/> Web-verified only</label>
      </aside>
      <section>
        <div class="toolbar">
          <span class="count">${results.length} resource${results.length === 1 ? "" : "s"}</span>
          <span class="spacer"></span>
          <div class="seg">
            <button data-view="grid" class="${state.view === "grid" ? "active" : ""}">Grid</button>
            <button data-view="list" class="${state.view === "list" ? "active" : ""}">List</button>
            <button data-view="visual" class="${state.view === "visual" ? "active" : ""}">Visual</button>
          </div>
        </div>
        ${results.length
          ? `<div class="cards ${state.view}">${results.map(resourceCard).join("")}</div>`
          : `<div class="empty">No resources match. Run the v2 pipeline to discover more, or clear filters.</div>`}
      </section>
    </div>`;

  main.querySelectorAll("[data-cat]").forEach((b) => b.addEventListener("click", () => {
    state.category = b.dataset.cat; renderPage();
  }));
  $("#f-sub").addEventListener("change", (e) => { state.subcategory = e.target.value; renderPage(); });
  $("#f-type").addEventListener("change", (e) => { state.type = e.target.value; renderPage(); });
  $("#f-conf").addEventListener("change", (e) => { state.confidence = e.target.value; renderPage(); });
  $("#f-verified").addEventListener("change", (e) => { state.verified = e.target.checked; renderPage(); });
  main.querySelectorAll("[data-view]").forEach((b) => b.addEventListener("click", () => {
    state.view = b.dataset.view; renderPage();
  }));
}

function stat(n, label) {
  return `<div class="stat"><div class="n">${(n ?? 0).toLocaleString()}</div><div class="l">${esc(label)}</div></div>`;
}

function relatedResources(r, limit = 8) {
  // Evidence-based relationships only: same subcategory, or shared technologies.
  const tech = new Set(r.technologies || []);
  return state.data.resources
    .filter((x) => x.entity_id !== r.entity_id)
    .map((x) => {
      let score = 0;
      if (x.primary_category === r.primary_category) score += 1;
      if (r.subcategory && x.subcategory === r.subcategory) score += 2;
      for (const t of x.technologies || []) if (tech.has(t)) score += 1;
      return { x, score };
    })
    .filter((e) => e.score > 0)
    .sort((a, b) => b.score - a.score || (b.x.source_count || 0) - (a.x.source_count || 0))
    .slice(0, limit)
    .map((e) => e.x);
}

function renderResource(main) {
  const r = state.data.resources.find((x) => x.entity_id === state.entityId);
  if (!r) { main.innerHTML = `<div class="empty">Resource not found.</div>`; return; }
  const shots = state.data.records.filter((s) => s.entity && s.entity.entity_id === r.entity_id);
  const related = relatedResources(r);
  main.innerHTML = `
    <p><a href="#/">← Back to search</a></p>
    <div class="detail">
      <h1>${esc(r.name)}</h1>
      <div class="sub">${esc(r.primary_category)}${r.subcategory ? " › " + esc(r.subcategory) : ""}
        · ${esc(r.type || "resource")} · ${confidencePill(r.confidence)}</div>
      ${r.confidence === "LOW" ? `<div class="banner">Candidate resource — identity corroborated by OCR and
        vision only. Not yet verified against an official source.</div>` : ""}
      <p>${esc(r.short_description || "No verified description.")}</p>
      <div class="section-title">Details</div>
      <div class="grid2">
        <dl class="kv">
          <dt>Resource ID</dt><dd>${esc(r.entity_id)}</dd>
          <dt>Canonical URL</dt><dd>${r.canonical_name ? `<a href="${esc(r.canonical_name)}" rel="noopener">${esc(r.canonical_name)}</a>` : "—"}</dd>
          <dt>GitHub</dt><dd>${r.github_url ? `<a href="${esc(r.github_url)}" rel="noopener">${esc(r.github_url)}</a>` : "—"}</dd>
          <dt>License</dt><dd>${esc(r.license || "—")}</dd>
          <dt>Developer</dt><dd>${esc(r.developer || "—")}</dd>
          <dt>Platforms</dt><dd>${esc(r.platforms || "—")}</dd>
          <dt>Technologies</dt><dd>${esc((r.technologies || []).join(", ") || "—")}</dd>
          <dt>Source count</dt><dd>${r.source_count || 0}</dd>
          <dt>Quality level</dt><dd>${r.quality_level || 0} / 6</dd>
        </dl>
        <div>
          <div class="section-title">Tags</div>
          <div class="meta">${(r.tags || []).map((t) => `<span class="pill">${esc(t)}</span>`).join("") || "—"}</div>
          <div class="section-title">URLs</div>
          ${(r.urls || []).map((u) => `<div><a href="${esc(u)}" rel="noopener">${esc(u)}</a></div>`).join("") || "—"}
        </div>
      </div>
      ${(r.features || []).length ? `<div class="section-title">Features (official source)</div>
        <ul>${r.features.map((f) => `<li>${esc(f)}</li>`).join("")}</ul>` : ""}
      ${related.length ? `<div class="section-title">Related resources</div>
        <div class="cards">${related.map(resourceCard).join("")}</div>` : ""}
      <div class="section-title">Source records (${shots.length})</div>
      <div class="shots">${shots.map((s) => `<a class="card" href="#record/${esc(s.task_id)}">
        <div class="thumb">${esc(s.task_id)}</div>
        <div class="foot"><span>${esc(s.record_type || "—")}</span><span>L${s.quality_level}</span></div></a>`).join("")}</div>
      <div class="section-title">Evidence</div>
      <table class="data"><tr><th>Record</th><th>OCR status</th><th>Confidence</th><th>Type</th></tr>
        ${shots.map((s) => `<tr><td><a href="#record/${esc(s.task_id)}">${esc(s.task_id)}</a></td>
          <td>${esc(s.ocr_status || "—")}</td><td>${s.confidence ?? "—"}</td>
          <td>${esc(s.record_type || "—")}</td></tr>`).join("")}</table>
    </div>`;
}

function renderRecord(main) {
  const s = state.data.records.find((x) => x.task_id === state.taskId);
  if (!s) { main.innerHTML = `<div class="empty">Record not found.</div>`; return; }
  main.innerHTML = `
    <p><a href="#/">← Back to search</a></p>
    <div class="detail">
      <h1>${esc(s.task_id)}</h1>
      <div class="sub">${s.width}×${s.height} · ${esc(s.status)}</div>
      <div class="grid2">
        <dl class="kv">
          <dt>OCR status</dt><dd>${esc(s.ocr_status || "—")}</dd>
          <dt>Record type</dt><dd>${esc(s.record_type || "—")}</dd>
          <dt>Confidence</dt><dd>${s.confidence ?? "—"}</dd>
          <dt>Quality level</dt><dd>${s.quality_level} / 6</dd>
          <dt>Resource</dt><dd>${s.entity ? `<a href="#resource/${esc(s.entity.entity_id)}">${esc(s.entity.name)}</a>` : "UNCONFIRMED"}</dd>
          <dt>Provenance ID</dt><dd><code>${esc((s.sha256 || "").slice(0, 24))}…</code></dd>
          <dt>Visibility</dt><dd>${esc(s.visibility || "REVIEW_REQUIRED")}</dd>
        </dl>
        <div>
          <div class="section-title">URLs</div>
          ${(s.urls || []).map((u) => `<div><a href="${esc(u)}" rel="noopener">${esc(u)}</a></div>`).join("") || "—"}
          <div class="section-title">OCR engines</div>
          <table class="data"><tr><th>Engine</th><th>Version</th><th>Confidence</th><th>Chars</th></tr>
            ${(s.engines || []).map((e) => `<tr><td>${esc(e.engine)}</td><td>${esc(e.version || "—")}</td>
              <td>${e.confidence ?? "—"}</td><td>${e.chars}</td></tr>`).join("")}</table>
        </div>
      </div>
      <div class="section-title">OCR / visual text (unverified evidence)</div>
      <pre class="ocr">${esc(s.text || "[no text captured]")}</pre>
      <p style="color:var(--text-dim);font-size:12.5px">Underlying source media is private evidence and is
      not published. This page shows the extracted knowledge and provenance only.</p>
    </div>`;
}

function renderCategories(main) {
  const cats = (state.data.statistics.category_breakdown || []).filter((c) => c.count > 0);
  main.innerHTML = `<h1>Category explorer</h1>
    <div class="cards">${cats.map((c) => `<a class="card" href="#/" data-jump="${esc(c.category)}">
      <h3>${esc(c.category)}</h3><div class="desc">${c.count} resource${c.count === 1 ? "" : "s"}</div></a>`).join("")}</div>`;
  main.querySelectorAll("[data-jump]").forEach((a) => a.addEventListener("click", () => {
    state.category = a.dataset.jump;
    state.subcategory = state.type = "";
    location.hash = "#/";
  }));
}

boot();
