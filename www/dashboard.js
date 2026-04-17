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

  // ---------------------------------------------------------------------------
  // Log overlay (issue #10).
  //
  // Each table row has a "show" button with class "log-show-btn" holding the
  // raw log text in data-log.  On click the global #log-overlay is populated
  // and shown.  Content is pretty-printed when the log is valid JSONL;
  // otherwise shown as a raw <pre>.
  // ---------------------------------------------------------------------------

  function wireLogPrettyPrint(root) {
    // Wire log-show-btn buttons (WASM-rendered HTML path).
    root.querySelectorAll(".log-show-btn").forEach(function(btn) {
      btn.addEventListener("click", function(e) {
        e.stopPropagation();
        showLogOverlay(btn.dataset.log || "");
      });
    });

    // Convert any remaining <details><pre class="log-snippet"> from static HTML
    // fallback (e.g. when WASM failed) into "show" buttons for the overlay.
    root.querySelectorAll("pre.log-snippet").forEach(function(pre) {
      var raw = pre.textContent.trim();
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "btn btn-link btn-sm p-0 log-show-btn";
      btn.textContent = "show";
      (function(captured) {
        btn.addEventListener("click", function(e) {
          e.stopPropagation();
          showLogOverlay(captured);
        });
      }(raw));
      var details = pre.closest("details");
      (details || pre).replaceWith(btn);
    });
  }

  function showLogOverlay(raw) {
    var overlay = document.getElementById("log-overlay");
    var body    = document.getElementById("log-overlay-body");
    if (!overlay || !body) return;

    body.innerHTML = "";

    var trimmed = raw.trim();
    if (trimmed) {
      // Try to parse as JSONL
      var lines = trimmed.split("\n").filter(function(l) { return l.trim(); });
      var records = [];
      var isJsonl = true;
      for (var i = 0; i < lines.length; i++) {
        try { records.push(JSON.parse(lines[i])); }
        catch (e) { isJsonl = false; break; }
      }

      if (isJsonl && records.length > 0) {
        // First pass: build tool_use_id → true (auto-approved) / false (denied) map.
        // A tool_result in a user message with is_error=false means the tool ran —
        // i.e. it was approved by config (clanker never asks humans interactively).
        var approvalMap = {};
        records.forEach(function(rec) {
          if (rec.type !== "user") return;
          var content = (rec.message && Array.isArray(rec.message.content))
            ? rec.message.content : [];
          content.forEach(function(blk) {
            if (blk.type === "tool_result" && blk.tool_use_id)
              approvalMap[blk.tool_use_id] = !blk.is_error;
          });
        });

        // Pretty-printed JSONL
        var div = document.createElement("div");
        div.className = "log-pretty";
        records.forEach(function(rec) { div.appendChild(buildLogRecord(rec, approvalMap)); });
        body.appendChild(div);
      } else {
        // Raw text fallback
        var pre = document.createElement("pre");
        pre.className = "log-snippet m-0";
        pre.textContent = trimmed;
        body.appendChild(pre);
      }
    } else {
      body.innerHTML = '<p class="text-muted small m-3">No log available.</p>';
    }

    overlay.classList.remove("d-none");
    document.getElementById("log-overlay-close").focus();
  }

  function hideLogOverlay() {
    var overlay = document.getElementById("log-overlay");
    if (overlay) overlay.classList.add("d-none");
  }

  function wireLogOverlay() {
    var overlay = document.getElementById("log-overlay");
    if (!overlay) return;
    // Click on backdrop closes
    overlay.addEventListener("click", function(e) {
      if (e.target === overlay) hideLogOverlay();
    });
    var closeBtn = document.getElementById("log-overlay-close");
    if (closeBtn) closeBtn.addEventListener("click", hideLogOverlay);
    // Escape key closes
    document.addEventListener("keydown", function(e) {
      if (e.key === "Escape" && !overlay.classList.contains("d-none")) hideLogOverlay();
    });
  }

  // Build a <div class="log-record"> for one JSONL record.
  // For assistant/user records, render structured content blocks inline (no
  // top-level collapsing — content shown directly, per issue #10 comment).
  // For other types, fall back to a JSON dump.
  // approvalMap: optional {tool_use_id → bool} — built by showLogOverlay first pass.
  function buildLogRecord(rec, approvalMap) {
    var div = document.createElement("div");
    div.className = "log-record";

    var hdr = document.createElement("div");
    hdr.className = "log-record-header";

    // Type badge
    var badge = document.createElement("span");
    badge.className = "log-type-badge " + logTypeBadgeClass(rec);
    badge.textContent = logTypeLabel(rec);
    hdr.appendChild(badge);

    div.appendChild(hdr);

    // Body: structured content blocks for assistant/user; raw JSON for others
    var msg = (rec.message && typeof rec.message === "object") ? rec.message : null;
    var content = (msg && Array.isArray(msg.content)) ? msg.content : null;

    if (content && content.length > 0) {
      // Structured body shown directly — no excerpt needed in header
      var body = document.createElement("div");
      body.className = "log-record-body";
      var recType = rec.type || "";
      content.forEach(function(blk) {
        body.appendChild(buildContentBlock(blk, recType, approvalMap));
      });
      div.appendChild(body);
    } else {
      // No structured content: show a one-line excerpt in the header + collapsible JSON dump
      var exc = document.createElement("span");
      exc.className = "log-record-excerpt";
      exc.textContent = logExcerpt(rec);
      hdr.appendChild(exc);

      div.appendChild(buildCollapsibleBlock(JSON.stringify(rec, null, 2), "log-block-raw-json"));
    }

    return div;
  }

  // Build a display element for one content block.
  // approvalMap: optional {tool_use_id → bool} passed through from showLogOverlay.
  function buildContentBlock(blk, recType, approvalMap) {
    var bt = blk.type || "unknown";
    if (bt === "text")        return buildTextBlock(blk.text || "");
    if (bt === "thinking")    return buildCollapsibleBlock(blk.thinking || "", "log-block-thinking");
    if (bt === "tool_use")    return buildToolUseBlock(blk, approvalMap);
    if (bt === "tool_result") return buildToolResultBlock(blk);

    // Unknown: collapsible JSON fallback
    var div = document.createElement("div");
    div.className = "log-block log-block-unknown";
    div.appendChild(buildCollapsibleBlock(JSON.stringify(blk, null, 2), "log-block-raw-json"));
    return div;
  }

  // Plain text block: collapsible if >3 lines.
  function buildTextBlock(text) {
    return buildCollapsibleBlock(text, "log-block-text");
  }

  // Tool use block: header with tool name, then each input field.
  // Primary fields (command, pattern, …) shown first and more prominently.
  // approvalMap: optional {tool_use_id → bool} — if blk.id maps to true, show
  //   a green "approved by config" badge (clanker never asks humans interactively).
  function buildToolUseBlock(blk, approvalMap) {
    var div = document.createElement("div");
    div.className = "log-block log-block-tool-use";

    // Tool name header
    var hdr = document.createElement("div");
    hdr.className = "log-block-tool-header";
    var nameBadge = document.createElement("span");
    nameBadge.className = "log-tool-name";
    nameBadge.textContent = blk.name || "tool";
    hdr.appendChild(nameBadge);

    // "approved by config" badge: shown when the tool ran successfully
    if (approvalMap && blk.id && approvalMap[blk.id] === true) {
      var appBadge = document.createElement("span");
      appBadge.className = "badge bg-success ms-2 log-auto-approved";
      appBadge.title = "Tool ran — approved by config (settings.json / CLAUDE.md)";
      appBadge.textContent = "approved by config";
      hdr.appendChild(appBadge);
    }

    div.appendChild(hdr);

    var input = blk.input;
    if (input && typeof input === "object") {
      // Show primary keys first, then others
      var primaryKeys = ["command", "pattern", "file_path", "old_string", "new_string",
                         "prompt", "query", "skill", "path", "content"];
      var allKeys = Object.keys(input);
      var ordered = primaryKeys.filter(function(k) { return allKeys.indexOf(k) >= 0; });
      allKeys.forEach(function(k) { if (ordered.indexOf(k) < 0) ordered.push(k); });
      ordered.forEach(function(k) {
        var val = String(input[k] == null ? "" : input[k]);
        var isPrimary = primaryKeys.indexOf(k) >= 0;
        div.appendChild(buildLabeledBlock(k, val, isPrimary ? "log-block-primary" : "log-block-secondary"));
      });
    } else if (input != null) {
      div.appendChild(buildCollapsibleBlock(String(input), "log-block-primary"));
    }

    return div;
  }

  // Tool result block: shows the return value; red if error.
  function buildToolResultBlock(blk) {
    var isErr = !!blk.is_error;
    var content = blk.content;
    var text = "";
    if (typeof content === "string") text = content;
    else if (Array.isArray(content))
      text = content.map(function(c) { return (typeof c === "object" && c.text) ? c.text : JSON.stringify(c); }).join("\n");
    else if (content != null) text = JSON.stringify(content);

    var div = document.createElement("div");
    div.className = "log-block " + (isErr ? "log-block-result-error" : "log-block-result");
    div.appendChild(buildCollapsibleBlock(text || "(empty)", ""));
    return div;
  }

  // A labeled key: value display; value is collapsible if >3 lines.
  function buildLabeledBlock(label, text, extraClass) {
    var wrap = document.createElement("div");
    wrap.className = "log-labeled-block" + (extraClass ? " " + extraClass : "");

    var lbl = document.createElement("span");
    lbl.className = "log-field-label";
    lbl.textContent = label + ":";
    wrap.appendChild(lbl);

    wrap.appendChild(buildCollapsibleBlock(text, ""));
    return wrap;
  }

  // Collapsible content: if >3 lines use <details>; else plain <pre>.
  function buildCollapsibleBlock(text, extraClass) {
    var wrap = document.createElement("div");
    wrap.className = "log-block-content" + (extraClass ? " " + extraClass : "");

    if (!text) {
      var em = document.createElement("em");
      em.className = "text-muted";
      em.textContent = "(empty)";
      wrap.appendChild(em);
      return wrap;
    }

    var lines = text.split("\n");
    if (lines.length > 3) {
      var det = document.createElement("details");
      det.className = "log-collapsible";
      var s = document.createElement("summary");
      s.textContent = lines.slice(0, 3).join("\n") + "\u2026";
      det.appendChild(s);
      var pre = document.createElement("pre");
      pre.textContent = text;
      det.appendChild(pre);
      wrap.appendChild(det);
    } else {
      var pre = document.createElement("pre");
      pre.textContent = text;
      wrap.appendChild(pre);
    }
    return wrap;
  }

  // Determine CSS badge class for a log record type.
  function logTypeBadgeClass(rec) {
    var t = rec.type || "";
    var s = rec.subtype || "";
    if (t === "assistant")        return "log-type-assistant";
    if (t === "user")             return "log-type-user";
    if (t === "system")           return "log-type-system";
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

  // One-line excerpt for the summary line.
  function logExcerpt(rec) {
    var t = rec.type || "";
    if (t === "system" && rec.subtype === "init") {
      return "model: " + (rec.model || "?") + "  cwd: " + (rec.cwd || "?");
    }
    if (t === "assistant") {
      var msg = rec.message;
      if (msg && Array.isArray(msg.content)) {
        // Prefer text; fall back to tool name or thinking indicator
        for (var i = 0; i < msg.content.length; i++) {
          var blk = msg.content[i];
          if (blk.type === "text" && blk.text)
            return blk.text.replace(/\s+/g, " ").slice(0, 120);
        }
        for (var i = 0; i < msg.content.length; i++) {
          var blk = msg.content[i];
          if (blk.type === "tool_use") return blk.name + "(\u2026)";
          if (blk.type === "thinking") return "thinking\u2026";
        }
      }
      if (rec.error) return "error: " + String(rec.error).slice(0, 100);
      return "";
    }
    if (t === "user") {
      var msg = rec.message;
      if (msg && Array.isArray(msg.content)) {
        for (var i = 0; i < msg.content.length; i++) {
          var blk = msg.content[i];
          if (blk.type === "tool_result") {
            var c = blk.content;
            var s = typeof c === "string" ? c : JSON.stringify(c);
            return s.replace(/\s+/g, " ").slice(0, 120);
          }
        }
      }
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
  // Denied-permissions overlay (issue #12).
  //
  // Each denied-permissions badge is a <button class="denied-btn"> with:
  //   data-denials — JSON array of {tool, input} objects
  //
  // On click the global #permissions-overlay is populated and shown.
  // Clicking the backdrop or the Close button hides it.
  function wirePermissionDenials(root) {
    root.querySelectorAll(".denied-btn").forEach(function(btn) {
      btn.addEventListener("click", function(e) {
        e.stopPropagation();
        var denials = [];
        try { denials = JSON.parse(btn.dataset.denials || "[]"); } catch (_) {}
        showDenialsOverlay(denials);
      });
    });
  }

  function showDenialsOverlay(denials) {
    var overlay = document.getElementById("permissions-overlay");
    if (!overlay) return;

    // Deduplicated tool list for the settings snippet
    var tools = denials.map(function(d) { return d.tool || "unknown"; })
      .filter(function(t, i, a) { return a.indexOf(t) === i; });

    // Build list HTML: show tool name and the offending action (command/path/etc.)
    // prominently so the user can see exactly what was blocked (issue #12 comment).
    var listHtml = denials.map(function(d) {
      var actionHtml = d.input
        ? '<div class="denied-action"><code>' + escHtml(d.input) + '</code></div>'
        : "";
      return (
        '<li class="mb-2">'
        + '<code class="denied-tool-name">' + escHtml(d.tool || "unknown") + '</code>'
        + actionHtml
        + '</li>'
      );
    }).join("");
    overlay.querySelector(".denied-list").innerHTML = listHtml || "<li>No details available.</li>";

    // settings.json snippet
    var settings = {permissions: {allow: tools.map(function(t) { return t + "(*)"; })}};
    overlay.querySelector(".denied-settings").textContent = JSON.stringify(settings, null, 2);

    overlay.classList.remove("d-none");
    document.getElementById("permissions-close").focus();
  }

  function hideDenialsOverlay() {
    var overlay = document.getElementById("permissions-overlay");
    if (overlay) overlay.classList.add("d-none");
  }

  // Wire the overlay close controls once the DOM is ready.
  function wireOverlay() {
    var overlay = document.getElementById("permissions-overlay");
    if (!overlay) return;
    // Click on backdrop (outside card) closes
    overlay.addEventListener("click", function(e) {
      if (e.target === overlay) hideDenialsOverlay();
    });
    var closeBtn = document.getElementById("permissions-close");
    if (closeBtn) closeBtn.addEventListener("click", hideDenialsOverlay);
    // Escape key closes
    document.addEventListener("keydown", function(e) {
      if (e.key === "Escape" && !overlay.classList.contains("d-none")) hideDenialsOverlay();
    });
  }

  // ---------------------------------------------------------------------------
  // Clone commands overlay (issue #16).
  //
  // Each project heading has a "clone" button with class "clone-btn" holding
  // the bash setup snippet in data-clone-cmds.  On click the global
  // #clone-overlay is populated and shown.
  // ---------------------------------------------------------------------------

  function wireCloneButtons(root) {
    root.querySelectorAll(".clone-btn").forEach(function(btn) {
      btn.addEventListener("click", function(e) {
        e.stopPropagation();
        showCloneOverlay(btn.dataset.cloneCmds || "", btn.dataset.createUrl || "");
      });
    });
  }

  function showCloneOverlay(cmds, createUrl) {
    var overlay = document.getElementById("clone-overlay");
    if (!overlay) return;
    var pre = overlay.querySelector(".clone-cmds");
    if (pre) pre.textContent = cmds;
    // Show or hide the "create repo" note
    var note = overlay.querySelector(".clone-create-note");
    if (note) {
      if (createUrl) {
        var link = note.querySelector(".clone-create-link");
        if (link) link.href = createUrl;
        note.classList.remove("d-none");
      } else {
        note.classList.add("d-none");
      }
    }
    overlay.classList.remove("d-none");
    document.getElementById("clone-overlay-close").focus();
  }

  function hideCloneOverlay() {
    var overlay = document.getElementById("clone-overlay");
    if (overlay) overlay.classList.add("d-none");
  }

  function wireCloneOverlay() {
    var overlay = document.getElementById("clone-overlay");
    if (!overlay) return;
    overlay.addEventListener("click", function(e) {
      if (e.target === overlay) hideCloneOverlay();
    });
    var closeBtn = document.getElementById("clone-overlay-close");
    if (closeBtn) closeBtn.addEventListener("click", hideCloneOverlay);
    document.addEventListener("keydown", function(e) {
      if (e.key === "Escape" && !overlay.classList.contains("d-none")) hideCloneOverlay();
    });
  }

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
          wirePermissionDenials(content);
          wireCloneButtons(content);
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
          wirePermissionDenials(content);
          wireCloneButtons(content);
          applyRunning();
        }
      });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function() {
      wireLogOverlay();
      wireOverlay();
      wireCloneOverlay();
      run();
      connectWebSocket();
    });
  } else {
    wireLogOverlay();
    wireOverlay();
    wireCloneOverlay();
    run();
    connectWebSocket();
  }
}());
