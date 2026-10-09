/* AEGIS dashboard. Plain JS, no dependencies. Every value from the API goes through esc() before innerHTML. */
(function () {
  "use strict";

  var SEV = [
    { key: "ERROR", color: "#d03b3b" },
    { key: "WARNING", color: "#c98500" },
    { key: "INFO", color: "#3987e5" }
  ];
  var SCAN_KINDS = { scan: 1, scan_diff: 1, scan_full: 1, wake: 1 };

  function $(id) { return document.getElementById(id); }
  function esc(v) {
    return String(v == null ? "" : v).replace(/[&<>"'`]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;", "`": "&#96;" }[c];
    });
  }
  function num(v) { var n = Number(v); return isFinite(n) ? n : 0; }
  function fmtInt(v) { return num(v).toLocaleString("en-US"); }
  function fmtMs(ms) { ms = num(ms); return ms >= 1000 ? (ms / 1000).toFixed(ms >= 10000 ? 0 : 1) + " s" : Math.round(ms) + " ms"; }
  function fmtDur(h) {
    h = num(h); var s = h * 3600;
    if (s < 90) return Math.round(s) + " s";
    if (h < 1) return Math.round(s / 60) + " min";
    if (h < 48) return h.toFixed(1) + " h";
    return (h / 24).toFixed(1) + " d";
  }
  function tsOf(e) {
    var t = e && e.ts;
    if (typeof t === "number") return t > 1e12 ? t / 1000 : t;
    var p = Date.parse(t); return isFinite(p) ? p / 1000 : 0;
  }
  function getJSON(url) {
    return fetch(url, { cache: "no-store" }).then(function (r) {
      if (!r.ok) throw new Error(url + " " + r.status);
      return r.json();
    });
  }
  function isBackfill(e) { return e.agent === "backfill" || e.trigger === "backfill"; }

  /* ---------- header stats ---------- */
  function chip(v, l, off) { return '<div class="chip' + (off ? " off" : "") + '"><div class="v">' + esc(v) + '</div><div class="l">' + esc(l) + "</div></div>"; }
  function loadStats() {
    return getJSON("/api/stats").then(function (s) {
      $("chips").innerHTML =
        chip(fmtInt(s.agents), "agents") +
        chip(fmtInt(s.repos), "repos") +
        chip(s.ch ? fmtInt(s.findings) : "offline", "findings in ClickHouse", !s.ch) +
        chip(s.ch ? fmtInt(s.scans) : "—", "scans", !s.ch) +
        chip(s.ch ? fmtInt(s.query_ms) + " ms" : "—", "query latency", !s.ch);
      $("sub").textContent = "analyst: " + s.analyst + " · ClickHouse: " + (s.ch ? "online" : "offline (local state fallback)");
    }).catch(function () { $("sub").textContent = "dashboard API unreachable"; });
  }

  /* ---------- fleet ---------- */
  var hotAgent = null, hotUntil = 0;
  function loadFleet() {
    return getJSON("/api/fleet").then(function (d) {
      var now = Date.now();
      $("fleet").innerHTML = (d.agents || []).map(function (a) {
        var open = 0;
        var rows = (a.repos || []).map(function (r) {
          open += num(r.open);
          var short = String(r.repo).split("/").pop();
          return '<div class="repo"><span class="name" title="' + esc(r.repo) + '">' + esc(short) + '</span><span class="badges">' +
            '<span class="badge ' + (num(r.open) ? "open" : "zero") + '">' + esc(fmtInt(r.open)) + ' open</span>' +
            '<span class="badge fixed">' + esc(fmtInt(r.resolved)) + " fixed</span></span></div>";
        }).join("") || '<div class="repo muted">no repos assigned</div>';
        var hot = a.agent === hotAgent && now < hotUntil;
        return '<div class="card' + (hot ? " hot" : "") + '"><h3><span>' + esc(a.agent) + '</span><span class="state ' + (open ? "bad" : "ok") + '">' +
          (open ? esc(open) + " open" : "clean") + "</span></h3>" + rows + "</div>";
      }).join("") || '<div class="empty">fleet.json has no agents</div>';
    }).catch(function () {});
  }

  /* ---------- live feed + WAKE flash ---------- */
  var lastTs = null, flashTimer = null;
  function classify(e) {
    var k = String(e.kind || "").toLowerCase();
    if (SCAN_KINDS[k]) {
      if (String(e.verdict || "").toLowerCase() === "unsafe") return { cls: "k-unsafe", label: "UNSAFE" };
      if (k === "wake") return { cls: "k-scan", label: "WAKE" };
      return { cls: "k-scan", label: k === "scan" ? "SCAN" : k.replace("_", " ").toUpperCase() };
    }
    var known = { issue_opened: "ISSUE", pr_opened: "PR", issue_closed: "CLOSED", denied: "DENIED", dismissed: "DISMISSED", error: "ERROR" };
    if (known[k]) return { cls: "k-" + k, label: known[k] };
    return { cls: "k-other", label: k.toUpperCase() || "EVENT" };
  }
  var SKIP = { ts: 1, kind: 1, agent: 1, repo: 1 };
  function detail(e) {
    var head = [];
    if (e.agent) head.push("<b>" + esc(e.agent) + "</b>");
    if (e.repo) head.push(esc(String(e.repo).split("/").pop()));
    var rest = Object.keys(e).filter(function (k) { return !SKIP[k] && e[k] !== "" && e[k] != null; }).map(function (k) {
      var v = e[k];
      if (typeof v === "object") v = JSON.stringify(v);
      v = String(v); if (v.length > 80) v = v.slice(0, 80) + "…";
      if (/(_ms|^ms)$/.test(k)) v = fmtMs(v);
      return '<span class="muted">' + esc(k) + "</span>=" + esc(v);
    });
    return head.join(" · ") + (rest.length ? " &nbsp;" + rest.join(" ") : "");
  }
  function flash(e) {
    var unsafe = String(e.verdict || "").toLowerCase() === "unsafe";
    $("flashText").innerHTML = "⚡ " + esc(e.agent || "agent") + " WAKE · " + esc(String(e.repo || "").split("/").pop()) +
      (unsafe ? "<small>verdict: UNSAFE</small>" : "");
    var f = $("flash");
    f.classList.toggle("unsafe", unsafe);
    f.classList.add("on");
    clearTimeout(flashTimer);
    flashTimer = setTimeout(function () { f.classList.remove("on"); }, 2500);
    hotAgent = e.agent; hotUntil = Date.now() + 4000;
  }
  function loadFeed() {
    return getJSON("/api/events?n=60").then(function (d) {
      var evs = (d.events || []).filter(function (e) { return !isBackfill(e); });
      evs.sort(function (a, b) { return tsOf(b) - tsOf(a); });
      $("feedSrc").textContent = d.source === "clickhouse" ? "from ClickHouse" : "local log";
      var maxTs = evs.length ? tsOf(evs[0]) : 0;
      var prev = lastTs;
      if (prev !== null) {
        var fresh = evs.filter(function (e) { return tsOf(e) > prev && SCAN_KINDS[String(e.kind || "").toLowerCase()]; });
        if (fresh.length) {
          // prefer the scan row (has verdict) over the wake row for the same push
          var withVerdict = fresh.filter(function (e) { return e.verdict; });
          flash((withVerdict.length ? withVerdict : fresh)[0]);
        }
      }
      if (prev === null || maxTs > prev) lastTs = Math.max(prev || 0, maxTs);
      $("feed").innerHTML = evs.map(function (e) {
        var c = classify(e), t = tsOf(e);
        var isNew = prev !== null && t > prev;
        return '<li class="' + (isNew ? "new" : "") + '"><span class="t">' + esc(new Date(t * 1000).toLocaleTimeString([], { hour12: false })) +
          '</span><span class="k ' + c.cls + '">' + esc(c.label) + '</span><span class="d">' + detail(e) + "</span></li>";
      }).join("") || '<li><span></span><span></span><span class="d muted">no events yet — waiting for a push</span></li>';
    }).catch(function () {});
  }

  /* ---------- posture timeline (inline SVG stacked bars) ---------- */
  var tlRows = [], showTable = false;
  function addDays(iso, d) { var t = new Date(iso + "T00:00:00Z"); t.setUTCDate(t.getUTCDate() + d); return t.toISOString().slice(0, 10); }
  function aggregate(repo) {
    var by = {};
    tlRows.forEach(function (r) {
      if (repo && r.repo !== repo) return;
      var w = by[r.week] || (by[r.week] = { ERROR: 0, WARNING: 0, INFO: 0 });
      var s = SEV.some(function (x) { return x.key === r.severity; }) ? r.severity : "INFO";
      w[s] += num(r.n);
    });
    var keys = Object.keys(by).sort();
    if (keys.length > 1) {
      var range = [], w = keys[0], ok = true, guard = 0;
      while (w <= keys[keys.length - 1] && guard++ < 1000) { range.push(w); w = addDays(w, 7); }
      keys.forEach(function (k) { if (range.indexOf(k) < 0) ok = false; });
      if (ok) keys = range;
    }
    return keys.map(function (k) { var v = by[k] || { ERROR: 0, WARNING: 0, INFO: 0 }; return { week: k, v: v, total: v.ERROR + v.WARNING + v.INFO }; });
  }
  function niceStep(m) { // integer tick step so that 4 steps cover m
    var raw = Math.max(1, m / 4), p = Math.pow(10, Math.floor(Math.log10(raw))), f = raw / p;
    return Math.max(1, (f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 5 ? 5 : 10) * p);
  }
  function roundTop(x, y, w, h, r) {
    r = Math.min(r, w / 2, h);
    return "M" + x + "," + (y + h) + "V" + (y + r) + "Q" + x + "," + y + " " + (x + r) + "," + y + "H" + (x + w - r) +
      "Q" + (x + w) + "," + y + " " + (x + w) + "," + (y + r) + "V" + (y + h) + "Z";
  }
  function renderTimeline() {
    var repo = $("repoSel").value;
    var data = aggregate(repo);
    var el = $("chart");
    $("legend").innerHTML = SEV.map(function (s) { return '<span><i style="background:' + s.color + '"></i>' + esc(s.key) + "</span>"; }).join("");
    if (!data.length) { el.innerHTML = '<div class="empty">no history yet</div>'; $("tbl").innerHTML = ""; return; }
    var W = Math.max(320, el.clientWidth || 800), H = 330, ml = 64, mr = 12, mt = 12, mb = 36;
    var pw = W - ml - mr, ph = H - mt - mb, n = data.length, slot = Math.min(pw / n, 56);
    var gap = slot > 6 ? 2 : 0, bw = Math.max(1, slot - gap);
    var step = niceStep(Math.max.apply(null, data.map(function (d) { return d.total; }))), max = step * 4;
    var y = function (v) { return mt + ph - (v / max) * ph; };
    var out = ['<svg viewBox="0 0 ' + W + " " + H + '" role="img" aria-label="Findings per week stacked by severity">'];
    for (var i = 0; i <= 4; i++) {
      var v = max * i / 4, yy = y(v);
      out.push('<line class="grid" x1="' + ml + '" x2="' + (W - mr) + '" y1="' + yy + '" y2="' + yy + '"/>');
      out.push('<text x="' + (ml - 8) + '" y="' + (yy + 5) + '" text-anchor="end">' + esc(fmtInt(Math.round(v))) + "</text>");
    }
    var lastLbl = -1e9, lastMonth = "";
    var multiYear = data.length > 1 && data[data.length - 1].week.slice(0, 4) - data[0].week.slice(0, 4) >= 2; // label years
    data.forEach(function (d, i) {
      var x = ml + i * slot + gap / 2, base = mt + ph, segs = [];
      SEV.forEach(function (s) { if (d.v[s.key] > 0) segs.push(s); });
      segs.forEach(function (s, j) {
        var h = (d.v[s.key] / max) * ph, top = base - h;
        var hh = j < segs.length - 1 && h > 3 ? h - 2 : h; // 2px surface gap between stacked fills
        var yTop = j < segs.length - 1 ? top + (h - hh) : top;
        if (j === segs.length - 1 && bw >= 8) out.push('<path d="' + roundTop(x, yTop, bw, hh, 4) + '" fill="' + s.color + '"/>');
        else out.push('<rect x="' + x + '" y="' + yTop + '" width="' + bw + '" height="' + Math.max(hh, 0.5) + '" fill="' + s.color + '"/>');
        base = top;
      });
      var month = d.week.slice(0, multiYear ? 4 : 7), cx = ml + i * slot + slot / 2;
      if (month !== lastMonth && cx - lastLbl > (multiYear ? 50 : 80)) {
        var dt = new Date(d.week + "T00:00:00Z");
        var lbl = multiYear ? String(dt.getUTCFullYear())
          : dt.toLocaleString("en-US", { month: "short", timeZone: "UTC" }) + (dt.getUTCMonth() === 0 || lastLbl < 0 ? " " + dt.getUTCFullYear() : "");
        out.push('<text x="' + cx + '" y="' + (H - 10) + '" text-anchor="middle">' + esc(lbl) + "</text>");
        lastLbl = cx;
      }
      lastMonth = month;
      out.push('<rect class="hit" data-i="' + i + '" x="' + (ml + i * slot) + '" y="' + mt + '" width="' + slot + '" height="' + ph + '"/>');
    });
    out.push('<line class="grid" x1="' + ml + '" x2="' + (W - mr) + '" y1="' + (mt + ph) + '" y2="' + (mt + ph) + '" style="stroke:#3a4757"/>');
    out.push("</svg>");
    el.innerHTML = out.join("");
    el.onmousemove = function (ev) {
      var t = ev.target, tip = $("tip");
      if (!t.classList || !t.classList.contains("hit")) { tip.style.display = "none"; return; }
      var d = data[+t.getAttribute("data-i")];
      tip.innerHTML = "<div><b>week of " + esc(d.week) + "</b></div>" + SEV.map(function (s) {
        return '<div class="row"><span><i style="background:' + s.color + '"></i>' + esc(s.key) + "</span><b>" + esc(fmtInt(d.v[s.key])) + "</b></div>";
      }).join("") + '<div class="row"><span>total</span><b>' + esc(fmtInt(d.total)) + "</b></div>";
      tip.style.display = "block";
      var x = ev.clientX + 16, yv = ev.clientY + 16;
      if (x + tip.offsetWidth > window.innerWidth - 8) x = ev.clientX - tip.offsetWidth - 16;
      if (yv + tip.offsetHeight > window.innerHeight - 8) yv = ev.clientY - tip.offsetHeight - 16;
      tip.style.left = x + "px"; tip.style.top = yv + "px";
    };
    el.onmouseleave = function () { $("tip").style.display = "none"; };
    $("tbl").innerHTML = "<table><thead><tr><th>week</th>" + SEV.map(function (s) { return '<th class="num">' + esc(s.key) + "</th>"; }).join("") +
      '<th class="num">total</th></tr></thead><tbody>' + data.slice().reverse().map(function (d) {
        return "<tr><td>" + esc(d.week) + "</td>" + SEV.map(function (s) { return '<td class="num">' + esc(fmtInt(d.v[s.key])) + "</td>"; }).join("") +
          '<td class="num">' + esc(fmtInt(d.total)) + "</td></tr>";
      }).join("") + "</tbody></table>";
  }
  function loadTimeline() {
    return getJSON("/api/timeline?weeks=1100&bucket=month").then(function (d) {
      tlRows = d.rows || [];
      var sel = $("repoSel"), cur = sel.value;
      var repos = {}; tlRows.forEach(function (r) { repos[r.repo] = 1; });
      sel.innerHTML = '<option value="">All repos</option>' + Object.keys(repos).sort().map(function (r) {
        return '<option value="' + esc(r) + '"' + (r === cur ? " selected" : "") + ">" + esc(r) + "</option>";
      }).join("");
      var total = tlRows.reduce(function (a, r) { return a + num(r.n); }, 0);
      $("tlNote").textContent = d.ch && !tlRows.length
        ? "ClickHouse online, no history yet. Run: uv run python clickhouse/backfill.py"
        : d.ch
        ? fmtInt(total) + " finding-months across " + Object.keys(repos).length + " repos · distinct open fingerprints per month by commit date · query " + d.ms + " ms"
        : "ClickHouse offline — posture history unavailable (live feed and fleet cards use local state)";
      renderTimeline();
    }).catch(function () { $("tlNote").textContent = "timeline unavailable"; });
  }

  /* ---------- latency / mttr / insights ---------- */
  function loadLatency() {
    return getJSON("/api/latency").then(function (d) {
      var rows = (d.agents || []).filter(function (r) { return r.agent !== "backfill"; });
      if (!rows.length) { $("latency").innerHTML = '<div class="empty">' + (d.ch ? "no live scans yet" : "ClickHouse offline") + "</div>"; return; }
      var max = Math.max.apply(null, rows.map(function (r) { return num(r.p95_ms); })) || 1;
      $("latency").innerHTML = '<div class="key"><i style="background:var(--accent)"></i>p50<i style="background:#8a6cf0"></i>p95</div><div class="bars">' +
        rows.map(function (r) {
          return '<div class="row"><span class="lbl" title="' + esc(r.agent) + '">' + esc(String(r.agent).replace("aegis-", "")) + ' <span class="muted small">' + esc(fmtInt(r.scans)) + " scans</span></span>" +
            '<span class="track"><span class="fill p50" style="width:' + (num(r.p50_ms) / max * 100) + '%"></span><span class="fill p95" style="width:' + (num(r.p95_ms) / max * 100) + '%"></span></span>' +
            '<span class="val">' + esc(fmtMs(r.p50_ms)) + ' <small>/ ' + esc(fmtMs(r.p95_ms)) + "</small></span></div>";
        }).join("") + "</div>";
    }).catch(function () {});
  }
  function loadMttr() {
    return getJSON("/api/mttr").then(function (d) {
      var rows = (d.repos || []).filter(function (r) { return num(r.closed) > 0; }).sort(function (a, b) { return num(a.mttr_h) - num(b.mttr_h); });
      if (!rows.length) { $("mttr").innerHTML = '<div class="empty">no findings closed yet</div>'; return; }
      var max = Math.max.apply(null, rows.map(function (r) { return num(r.mttr_h); })) || 1;
      $("mttr").innerHTML = '<div class="bars">' + rows.map(function (r) {
        return '<div class="row"><span class="lbl" title="' + esc(r.repo) + '">' + esc(String(r.repo).split("/").pop()) + ' <span class="muted small">' + esc(fmtInt(r.closed)) + " closed</span></span>" +
          '<span class="track"><span class="fill one" style="width:' + (num(r.mttr_h) / max * 100) + '%"></span></span>' +
          '<span class="val">' + esc(fmtDur(r.mttr_h)) + "</span></div>";
      }).join("") + "</div>";
    }).catch(function () {});
  }
  function list(el, rows, fn, emptyMsg) {
    $(el).innerHTML = rows.length ? rows.slice(0, 5).map(fn).join("") : '<div class="muted small">' + esc(emptyMsg) + "</div>";
  }
  function loadInsights() {
    return getJSON("/api/insights?hours=24").then(function (d) {
      var off = d.ch ? "nothing notable" : "ClickHouse offline";
      $("insHours").textContent = "last " + d.hours + " h vs previous " + d.hours + " h";
      list("rising", d.rising_repos || [], function (r) {
        return '<div class="it"><span title="' + esc(r.repo) + '">' + esc(String(r.repo).split("/").pop()) + '</span><b class="up">' +
          esc(fmtInt(r.findings_prev)) + " → " + esc(fmtInt(r.findings_now)) + "</b></div>";
      }, off);
      list("noisy", d.noisy_rules || [], function (r) {
        return '<div class="it"><span title="' + esc(r.rule_id) + '">' + esc(String(r.rule_id).split(".").pop()) + "</span><b>" +
          esc(Math.round(num(r.dismiss_rate) * 100)) + "% dismissed</b></div>";
      }, off);
      list("reopened", d.reopened || [], function (r) {
        return '<div class="it"><span title="' + esc(r.repo + " " + r.fingerprint) + '">' + esc(String(r.repo).split("/").pop()) + " · " +
          esc(String(r.rule_id || "").split(".").pop()) + "</span><b>×" + esc(fmtInt(r.times)) + "</b></div>";
      }, off);
    }).catch(function () {});
  }

  /* ---------- wiring ---------- */
  $("repoSel").addEventListener("change", renderTimeline);
  $("tblBtn").addEventListener("click", function () {
    showTable = !showTable;
    this.setAttribute("aria-pressed", String(showTable));
    $("tbl").hidden = !showTable; $("chart").hidden = showTable;
    if (!showTable) renderTimeline();
  });
  var rz; window.addEventListener("resize", function () { clearTimeout(rz); rz = setTimeout(renderTimeline, 150); });

  function every(fn, ms) { fn(); setInterval(fn, ms); }
  every(loadFeed, 2000);
  every(loadFleet, 2000);
  every(loadStats, 10000);
  every(loadLatency, 15000);
  every(loadMttr, 15000);
  every(loadInsights, 15000);
  every(loadTimeline, 30000);
})();
