// dashboard.js — glue between WASM module and the browser
// Fetches data.cbor, loads the WASM, calls render_dashboard(), injects HTML.

(function() {
  "use strict";

  // Paths relative to the page location
  const DATA_URL = "data.cbor";
  const WASM_URL = "pkg/claude_dashboard_bg.wasm";
  const JS_URL   = "pkg/claude_dashboard.js";

  const content = document.getElementById("dash-content");

  function showLoading() {
    if (!content) return;
    content.innerHTML = '<div id="dash-loading">Loading dashboard data\u2026</div>';
  }

  function showError(msg) {
    if (!content) return;
    content.innerHTML = `<div id="dash-error" class="alert alert-danger">${escHtml(msg)}</div>`;
  }

  function escHtml(s) {
    return s
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  // Fetch data.cbor as ArrayBuffer
  function fetchData() {
    return fetch(DATA_URL).then(function(r) {
      if (!r.ok) throw new Error(`HTTP ${r.status} fetching ${DATA_URL}`);
      return r.arrayBuffer();
    });
  }

  // Dynamically load the wasm-pack generated JS module, then the WASM binary.
  // wasm-pack generates an ES module; we load it via a dynamic import if supported,
  // otherwise fall back to the static HTML already in the page.
  function loadWasm() {
    return import(JS_URL).then(function(mod) {
      return mod.default(WASM_URL).then(function() { return mod; });
    });
  }

  function run() {
    showLoading();

    Promise.all([fetchData(), loadWasm()])
      .then(function([buf, wasm]) {
        const bytes = new Uint8Array(buf);
        const html = wasm.render_dashboard(bytes);

        if (html.startsWith("ERROR:")) {
          showError(html.slice(6).trim());
          return;
        }

        // The WASM returns three divs: nav items, generated-at, content
        const tmp = document.createElement("div");
        tmp.innerHTML = html;

        // Inject nav items
        const nav = document.getElementById("project-nav");
        const navItems = tmp.querySelector("#dash-nav-items");
        if (nav && navItems) nav.innerHTML = navItems.innerHTML;

        // Update generated-at timestamp
        const genAt = document.getElementById("generated-at");
        const genAtNew = tmp.querySelector("#dash-generated-at");
        if (genAt && genAtNew) genAt.textContent = "Generated: " + genAtNew.textContent;

        // Replace main content
        const sections = tmp.querySelector("#dash-content");
        if (content && sections) content.innerHTML = sections.innerHTML;
      })
      .catch(function(err) {
        // WASM unavailable or data missing: leave static HTML in place.
        console.warn("Dashboard WASM unavailable, using static HTML:", err);
        if (content)
          content.insertAdjacentHTML(
            "afterbegin",
            `<div class="alert alert-warning small mb-3">
              Live rendering unavailable (${escHtml(err.message)}). Showing static data.
             </div>`
          );
      });
  }

  if (document.readyState === "loading")
    document.addEventListener("DOMContentLoaded", run);
  else
    run();
}());
