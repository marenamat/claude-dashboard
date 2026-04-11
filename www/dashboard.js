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

  // Pretty-print JSONL log snippets (issue #10).
  //
  // Finds every .log-snippet <pre> inside root, tries to parse its content as
  // JSONL (one JSON object per line).  If successful, replaces the <pre> with a
  // <div class="log-pretty"> containing one collapsible <details> per record.
  //
  // Falls back silently to the raw <pre> if the content is not valid JSONL.
  function wireLogPrettyPrint(root) {
    root.querySelectorAll("pre.log-snippet").forEach(function(pre) {
      var raw = pre.textContent.trim();
      if (!raw) return;

      var lines = raw.split("\n").filter(function(l) { return l.trim(); });
      var records = [];
      for (var i = 0; i < lines.length; i++) {
        try {
          records.push(JSON.parse(lines[i]));
        } catch (e) {
          // Not valid JSONL — leave the pre as-is
          return;
        }
      }
      if (records.length === 0) return;

      var div = document.createElement("div");
      div.className = "log-pretty";
      records.forEach(function(rec) {
        div.appendChild(buildLogRecord(rec));
      });

      // Replace the <details> containing the pre (to hide the old "show" summary)
      var details = pre.closest("details");
      if (details) {
        details.replaceWith(div);
      } else {
        pre.replaceWith(div);
      }
    });
  }

  // Build a <details class="log-record"> for one JSONL record.
  function buildLogRecord(rec) {
    var det = document.createElement("details");
    det.className = "log-record";

    var sum = document.createElement("summary");

    // Type badge
    var badge = document.createElement("span");
    badge.className = "log-type-badge " + logTypeBadgeClass(rec);
    badge.textContent = logTypeLabel(rec);
    sum.appendChild(badge);

    // Excerpt
    var exc = document.createElement("span");
    exc.className = "log-record-excerpt";
    exc.textContent = logExcerpt(rec);
    sum.appendChild(exc);

    det.appendChild(sum);

    // Full JSON in a pre, shown when expanded
    var pre = document.createElement("pre");
    pre.textContent = JSON.stringify(rec, null, 2);
    det.appendChild(pre);

    return det;
  }

  // Determine CSS badge class for a log record type.
  function logTypeBadgeClass(rec) {
    var t = rec.type || "";
    var s = rec.subtype || "";
    if (t === "assistant")      return "log-type-assistant";
    if (t === "user")           return "log-type-user";
    if (t === "system")         return "log-type-system";
    if (t === "rate_limit_event") return "log-type-rate-limit";
    if (t === "result") {
      return (s === "error" || rec.is_error) ? "log-type-result-error" : "log-type-result";
    }
    return "log-type-other";
  }

  // Short human-readable type label for the badge.
  function logTypeLabel(rec) {
    var t = rec.type || "?";
    var s = rec.subtype;
    if (s) return t + "/" + s;
    if (t === "rate_limit_event") return "rate limit";
    return t;
  }

  // One-line excerpt summarising the record content.
  function logExcerpt(rec) {
    var t = rec.type || "";
    if (t === "system" && rec.subtype === "init") {
      return "model: " + (rec.model || "?") + "  cwd: " + (rec.cwd || "?");
    }
    if (t === "assistant") {
      // Pull first text block from the message
      var msg = rec.message;
      if (msg && Array.isArray(msg.content)) {
        for (var i = 0; i < msg.content.length; i++) {
          var blk = msg.content[i];
          if (blk.type === "text" && blk.text) {
            return blk.text.replace(/\s+/g, " ").slice(0, 120);
          }
        }
      }
      if (rec.error) return "error: " + String(rec.error).slice(0, 100);
      return "";
    }
    if (t === "result") {
      var parts = [];
      if (rec.subtype) parts.push(rec.subtype);
      if (rec.num_turns != null) parts.push(rec.num_turns + " turns");
      if (rec.duration_ms != null) parts.push((rec.duration_ms / 1000).toFixed(1) + "s");
      return parts.join("  ");
    }
    if (t === "rate_limit_event") {
      var info = rec.rate_limit_info;
      if (info && info.resets_at) return "resets at " + info.resets_at;
      return "";
    }
    return "";
  }

  // ---------------------------------------------------------------------------
  // Running-state overlay (issue #9)
  //
  // runningProjects tracks which project names currently have clanker running.
  // After every render we re-apply the 'running' class so it survives re-renders.
  // ---------------------------------------------------------------------------
  var runningProjects = new Set();

  function setRunning(name, running) {
    if (running) runningProjects.add(name);
    else runningProjects.delete(name);
    applyRunning();
  }

  function applyRunning() {
    runningProjects.forEach(function(name) {
      var section = document.getElementById("proj-" + name);
      if (section) section.classList.add("running");
    });
  }

  // ---------------------------------------------------------------------------
  // WebSocket autoreload (issue #9)
  //
  // Connects to /ws on the same host.  On "data-updated" re-fetches and
  // re-renders the dashboard.  On "running" toggles the visual indicator.
  // Reconnects automatically after a 5-second delay on disconnect.
  // ---------------------------------------------------------------------------
  var wsRendering = false;  // guard against concurrent re-renders

  function connectWebSocket() {
    var proto = location.protocol === "https:" ? "wss:" : "ws:";
    var url = proto + "//" + location.host + "/ws";
    var ws;
    try {
      ws = new WebSocket(url);
    } catch (e) {
      // WebSocket not available — no autoreload, degrade silently
      return;
    }

    ws.onmessage = function(ev) {
      var msg;
      try { msg = JSON.parse(ev.data); } catch (e) { return; }

      if (msg.type === "data-updated") {
        if (!wsRendering) {
          wsRendering = true;
          run().finally(function() { wsRendering = false; });
        }
      } else if (msg.type === "running") {
        setRunning(msg.name, msg.running);
      }
    };

    ws.onclose = function() {
      setTimeout(connectWebSocket, 5000);
    };

    ws.onerror = function() {
      ws.close();
    };
  }

  // ---------------------------------------------------------------------------
  // Fetch data.cbor as ArrayBuffer
  // ---------------------------------------------------------------------------
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

  // run() fetches data + renders the dashboard.  Returns a Promise so callers
  // (e.g. the WebSocket handler) can chain on completion.  WASM is cached by
  // the browser module cache after the first load.
  function run() {
    showLoading();

    // Current UTC epoch seconds for relative timestamp display
    const nowSecs = Math.floor(Date.now() / 1000);
    // Browser UTC offset in seconds: positive = east of UTC (e.g. UTC+1 → +3600)
    const tzOffsetSecs = -(new Date().getTimezoneOffset()) * 60;

    return Promise.all([fetchData(), loadWasm()])
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
          wireLogPrettyPrint(content);
        }

        // Re-apply running indicators that may have arrived via WebSocket
        // before or during this render (issue #9)
        applyRunning();
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
          wireLogPrettyPrint(content);
          applyRunning();
        }
      });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function() {
      run();
      connectWebSocket();
    });
  } else {
    run();
    connectWebSocket();
  }
}());
