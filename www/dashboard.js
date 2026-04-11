// dashboard.js — glue between WASM module and the browser
// Fetches data.cbor, loads the WASM, calls render_dashboard(), injects HTML.

(function() {
  "use strict";

  // Paths relative to the page location
  const DATA_URL = "data.cbor";
  const WASM_URL = "pkg/claude_dashboard_bg.wasm";
  const JS_URL   = "./pkg/claude_dashboard.js";

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

  // Wire up progressive "show more" / collapse buttons (issue #7).
  //
  // Each show-more button has:
  //   data-tbody  — id of the tbody containing run rows
  //   data-batch  — current batch size to reveal on next click (starts at 5,
  //                 doubles after each click: 5, 10, 20, 40 …)
  //
  // Rows beyond SHOW_INITIAL are marked run-hidden + d-none by the WASM renderer.
  // The collapse button re-hides all rows beyond data-initial on the tbody.
  function wireShowMore(root) {
    root.querySelectorAll(".show-more-btn").forEach(function(btn) {
      btn.addEventListener("click", function() {
        const tbody = document.getElementById(btn.dataset.tbody);
        if (!tbody) return;

        // Reveal up to `batch` hidden rows
        var batch = parseInt(btn.dataset.batch) || 5;
        var hidden = Array.from(tbody.querySelectorAll("tr.run-hidden"));
        hidden.slice(0, batch).forEach(function(tr) {
          tr.classList.remove("d-none", "run-hidden");
        });

        // Double the next batch size; cap at 1280
        var nextBatch = Math.min(batch * 2, 1280);
        btn.dataset.batch = nextBatch;

        // Update button label or hide when nothing left
        var remaining = tbody.querySelectorAll("tr.run-hidden").length;
        if (remaining === 0) {
          btn.classList.add("d-none");
        } else {
          var nextShow = Math.min(nextBatch, remaining);
          btn.textContent = nextShow + " more\u2026";
        }

        // Show the collapse button (sibling in the same tfoot cell)
        var collapseBtn = btn.closest("td").querySelector(".collapse-runs-btn");
        if (collapseBtn) collapseBtn.classList.remove("d-none");
      });
    });

    root.querySelectorAll(".collapse-runs-btn").forEach(function(btn) {
      btn.addEventListener("click", function() {
        const tbody = document.getElementById(btn.dataset.tbody);
        if (!tbody) return;

        // Re-hide all rows beyond the initial count
        var initial = parseInt(tbody.dataset.initial) || 5;
        var rows = Array.from(tbody.querySelectorAll("tr"));
        rows.slice(initial).forEach(function(tr) {
          tr.classList.add("d-none", "run-hidden");
        });

        // Reset show-more button
        var showMore = btn.closest("td").querySelector(".show-more-btn");
        if (showMore) {
          showMore.dataset.batch = 5;
          showMore.textContent = "5 more\u2026";
          showMore.classList.remove("d-none");
        }

        // Hide collapse button
        btn.classList.add("d-none");
      });
    });
  }

  // Fetch data.cbor as ArrayBuffer
  function fetchData() {
    return fetch(DATA_URL).then(function(r) {
      if (!r.ok) throw new Error(`HTTP ${r.status} fetching ${DATA_URL}`);
      return r.arrayBuffer();
    });
  }

  // Dynamically load the wasm-pack generated JS module, then the WASM binary.
  function loadWasm() {
    return import(JS_URL).then(function(mod) {
      return mod.default(WASM_URL).then(function() { return mod; });
    });
  }

  function run() {
    showLoading();

    // Current UTC epoch seconds for relative timestamp display
    const nowSecs = Math.floor(Date.now() / 1000);
    // Browser UTC offset in seconds: positive = east of UTC (e.g. UTC+1 → +3600)
    const tzOffsetSecs = -(new Date().getTimezoneOffset()) * 60;

    Promise.all([fetchData(), loadWasm()])
      .then(function([buf, wasm]) {
        const bytes = new Uint8Array(buf);
        const html = wasm.render_dashboard(bytes, nowSecs, tzOffsetSecs);

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
        if (content && sections) {
          content.innerHTML = sections.innerHTML;
          wireShowMore(content);
        }
      })
      .catch(function(err) {
        // WASM unavailable or data missing: leave static HTML in place.
        console.warn("Dashboard WASM unavailable, using static HTML:", err);
        if (content) {
          content.insertAdjacentHTML(
            "afterbegin",
            `<div class="alert alert-warning small mb-3">
              Live rendering unavailable (${escHtml(err.message)}). Showing static data.
             </div>`
          );
          wireShowMore(content);
        }
      });
  }

  if (document.readyState === "loading")
    document.addEventListener("DOMContentLoaded", run);
  else
    run();
}());
