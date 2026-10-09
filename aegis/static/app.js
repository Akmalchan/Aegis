/* AEGIS dashboard. Plain JS, no dependencies. Every API value goes through esc() before innerHTML. */
(function () {
  "use strict";

  var $ = function (id) { return document.getElementById(id); };
  var REDUCED = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var SVGNS = "http://www.w3.org/2000/svg";

  function esc(v) {
    return String(v == null ? "" : v).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function getJSON(url) {
    return fetch(url, { cache: "no-store" }).then(function (r) {
      if (!r.ok) throw new Error(url + " " + r.status);
      return r.json();
    });
  }
  function short(repo) { return String(repo || "").split("/").pop(); }
  function agentShort(a) { return String(a || "").replace(/^aegis-/, ""); }
  function ruleShort(r) { var p = String(r || "").split("."); return p[p.length - 1] || r; }
  function fmtInt(n) { return Math.round(n || 0).toLocaleString("en-US"); }
  function fmtMs(ms) {
    if (!ms && ms !== 0) return "–";
    return ms < 1000 ? Math.round(ms) + " ms" : (ms / 1000).toFixed(ms < 10000 ? 1 : 0) + " s";
  }
  function fmtDur(s) {
    if (s < 90) return Math.round(s) + " s";
    if (s < 5400) return (s / 60).toFixed(s < 600 ? 1 : 0) + " min";
    return (s / 3600).toFixed(1) + " h";
  }
  function ago(ts) {
    var s = Math.max(0, Date.now() / 1000 - ts);
    if (s < 60) return Math.round(s) + "s";
    if (s < 3600) return Math.round(s / 60) + "m";
    if (s < 86400) return Math.round(s / 3600) + "h";
    return Math.round(s / 86400) + "d";
  }
  function el(tag, attrs, parent) {
    var e = document.createElementNS(SVGNS, tag);
    for (var k in attrs) e.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(e);
    return e;
  }

  /* ================= background: living dot field ================= */
  var bg = $("bg"), ctx = bg.getContext("2d");
  var W = 0, H = 0, DPR = 1, ripples = [], mouse = { x: -9999, y: -9999 };
  var GAP = 26;
  function resize() {
    DPR = Math.min(window.devicePixelRatio || 1, 2);
    W = window.innerWidth; H = window.innerHeight;
    bg.width = W * DPR; bg.height = H * DPR;
    ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
  }
  window.addEventListener("resize", resize);
  window.addEventListener("pointermove", function (e) { mouse.x = e.clientX; mouse.y = e.clientY; });
  resize();

  function ripple(x, y, color) {
    ripples.push({ x: x, y: y, t0: performance.now(), c: color });
    if (ripples.length > 6) ripples.shift();
  }

  var lastFrame = 0;
  function frame(now) {
    if (!REDUCED) requestAnimationFrame(frame);
    if (now - lastFrame < 33) return;  // ~30 fps is plenty for a backdrop
    lastFrame = now;
    var t = now;
    ctx.clearRect(0, 0, W, H);
    ripples = ripples.filter(function (r) { return t - r.t0 < 4200; });
    var cols = Math.ceil(W / GAP) + 1, rows = Math.ceil(H / GAP) + 1;
    for (var j = 0; j < rows; j++) {
      var y = j * GAP + (GAP / 2);
      var fadeY = 0.35 + 0.65 * (1 - y / H);  // brighter at the top, like a horizon
      for (var i = 0; i < cols; i++) {
        var x = i * GAP + (GAP / 2);
        var v = Math.sin(x * 0.011 + t * 0.00035) * Math.cos(y * 0.014 - t * 0.00028) +
                Math.sin((x + y) * 0.0045 + t * 0.00022) * 0.6;
        v = (v + 1.6) / 3.2;
        var a = (0.035 + 0.13 * v * v) * fadeY;
        var dm = Math.hypot(x - mouse.x, y - mouse.y);
        if (dm < 180) a += 0.22 * (1 - dm / 180);
        var size = 1.2 + v * 0.8;
        ctx.fillStyle = "rgba(239,229,211," + a.toFixed(3) + ")";
        ctx.fillRect(x - size / 2, y - size / 2, size, size);
        for (var k = 0; k < ripples.length; k++) {
          var r = ripples[k], age = t - r.t0;
          var radius = age * 0.42, d = Math.hypot(x - r.x, y - r.y);
          var w = Math.exp(-Math.pow((d - radius) / 46, 2)) * (1 - age / 4200);
          if (w > 0.04) {
            ctx.fillStyle = "rgba(" + r.c + "," + (w * 0.85).toFixed(3) + ")";
            var s2 = size + w * 2.4;
            ctx.fillRect(x - s2 / 2, y - s2 / 2, s2, s2);
          }
        }
      }
    }
  }
  requestAnimationFrame(frame);
  var RED = "255,106,82", GREEN = "143,214,162", AMBER = "233,180,92";

  /* ================= routing ================= */
  function route() {
    var page = (location.hash || "#live").slice(1);
    if (page !== "how") page = "live";
    ["live", "how"].forEach(function (p) { $("page-" + p).hidden = p !== page; });
    document.querySelectorAll(".tab").forEach(function (a) { a.classList.toggle("on", a.dataset.page === page); });
    window.scrollTo({ top: 0 });
  }
  window.addEventListener("hashchange", route);
  route();

  /* ================= clock + status ================= */
  function tickClock() { $("clock").textContent = new Date().toLocaleTimeString("en-US", { hour12: false }); }
  setInterval(tickClock, 1000); tickClock();
  function setStatus(ok, text) {
    $("status").className = "status " + (ok ? "ok" : "bad");
    $("statusText").textContent = text;
  }

  /* ================= KPIs (count-up) ================= */
  var kpiVals = {};
  function countTo(id, target, fmt) {
    var node = $(id), from = kpiVals[id] || 0;
    kpiVals[id] = target;
    if (REDUCED || from === target) { node.textContent = fmt(target); return; }
    var t0 = performance.now(), dur = 1100;
    (function step(now) {
      var p = Math.min(1, (now - t0) / dur), e = 1 - Math.pow(1 - p, 3);
      node.textContent = fmt(from + (target - from) * e);
      if (p < 1) requestAnimationFrame(step);
    })(t0);
  }
  function loadStats() {
    return getJSON("/api/stats").then(function (s) {
      countTo("kAgents", s.agents, fmtInt);
      countTo("kRepos", s.repos, fmtInt);
      countTo("kFindings", s.findings, fmtInt);
      $("howFindings").textContent = fmtInt(s.findings);
      setStatus(true, s.ch ? "fleet online" : "online · no memory");
    }).catch(function () { setStatus(false, "offline"); });
  }
  function loadLatency() {
    return getJSON("/api/latency").then(function (d) {
      var rows = (d.agents || []).filter(function (a) { return /^aegis-/.test(a.agent); });
      if (!rows.length) rows = d.agents || [];
      var scans = rows.reduce(function (s, a) { return s + (a.scans || 0); }, 0);
      var p50 = scans ? rows.reduce(function (s, a) { return s + a.p50_ms * (a.scans || 0); }, 0) / scans : 0;
      $("kScan").textContent = p50 ? fmtMs(p50) : "–";
      var max = Math.max.apply(null, rows.map(function (a) { return a.p95_ms || 0; }).concat([1]));
      $("latency").innerHTML = rows.length ? rows.map(function (a) {
        return '<li><div class="n">' + esc(agentShort(a.agent)) + "<small>" + fmtInt(a.scans) + " scans</small>" +
          '<div class="meter"><i style="width:' + Math.round(100 * (a.p95_ms || 0) / max) + '%"></i></div></div>' +
          '<div class="v">' + fmtMs(a.p50_ms) + " · " + fmtMs(a.p95_ms) + "</div></li>";
      }).join("") : '<li class="empty">No scans yet</li>';
    }).catch(function () {});
  }
  function loadMttr() {
    return getJSON("/api/mttr").then(function (d) {
      var rows = d.repos || [], n = 0, sum = 0;
      rows.forEach(function (r) { n += r.closed || 0; sum += (r.mttr_h || 0) * (r.closed || 0); });
      $("kMttr").textContent = n ? fmtDur(sum / n * 3600) : "–";
    }).catch(function () {});
  }

  /* ================= fleet map ================= */
  var map = $("map"), mapNodes = { agents: {}, repos: {}, edges: {} }, repoAgent = {};
  function buildMap(agents) {
    map.innerHTML = "";
    mapNodes = { agents: {}, repos: {}, edges: {} };
    var gEdges = el("g", {}, map), gNodes = el("g", {}, map);
    var n = agents.length || 1, H0 = 380, band = H0 / n;
    agents.forEach(function (a, ai) {
      var ay = band * ai + band / 2, ax = 108;
      var repos = a.repos || [];
      repos.forEach(function (r, ri) {
        var ry = ay + (ri - (repos.length - 1) / 2) * Math.min(42, (band - 10) / Math.max(1, repos.length));
        var path = el("path", { "class": "edge", d: "M" + (ax + 34) + " " + ay + " C " + (ax + 140) + " " + ay + ", " + (330 - 90) + " " + ry + ", 330 " + ry }, gEdges);
        mapNodes.edges[r.repo] = path;
        var g = el("g", { "class": "repo", transform: "translate(330 " + (ry - 16) + ")" }, gNodes);
        el("rect", { width: 290, height: 32, rx: 9 }, g);
        var tn = el("text", { "class": "rn", x: 14, y: 20.5 }, g); tn.textContent = short(r.repo);
        var to = el("text", { "class": "ro", x: 278, y: 20, "text-anchor": "end" }, g);
        g._counts = to;
        mapNodes.repos[r.repo] = g;
        repoAgent[r.repo] = a.agent;
      });
      var ga = el("g", { "class": "agent", transform: "translate(" + ax + " " + ay + ")" }, gNodes);
      el("circle", { "class": "halo", r: 30 }, ga);
      el("circle", { "class": "core", r: 34 }, ga);
      var t1 = el("text", { "class": "an", y: 2 }, ga); t1.textContent = agentShort(a.agent).replace("sentinel-", "Sentinel ");
      var t2 = el("text", { "class": "as", y: 18 }, ga); t2.textContent = "idle";
      ga._state = t2;
      mapNodes.agents[a.agent] = ga;
    });
  }
  var fleetSig = "";
  function loadFleet() {
    return getJSON("/api/fleet").then(function (d) {
      var agents = d.agents || [];
      var sig = agents.map(function (a) { return a.agent + ":" + (a.repos || []).map(function (r) { return r.repo; }).join(","); }).join("|");
      if (sig !== fleetSig) { fleetSig = sig; buildMap(agents); }
      var nrepos = 0;
      agents.forEach(function (a) {
        (a.repos || []).forEach(function (r) {
          nrepos++;
          var g = mapNodes.repos[r.repo];
          if (g) g._counts.innerHTML = '<tspan class="o">' + fmtInt(r.open) + ' open</tspan>  ·  <tspan class="f">' + fmtInt(r.resolved) + " fixed</tspan>";
        });
      });
      $("fleetHint").textContent = agents.length + " agents · " + nrepos + " repos";
    }).catch(function () {});
  }
  function paintMap(events) {
    var now = Date.now() / 1000, repoState = {}, agentLast = {};
    events.slice().reverse().forEach(function (e) {   // oldest -> newest, last write wins
      if (now - e.ts > 300) return;
      var hot = (e.kind === "scan" && e.verdict === "unsafe") || e.kind === "issue_opened" || e.kind === "pr_opened" || e.kind === "denied";
      var cool = (e.kind === "scan" && e.verdict === "safe") || e.kind === "issue_closed" || e.kind === "verified";
      if (hot) repoState[e.repo] = "hot"; else if (cool) repoState[e.repo] = "cool";
      var ag = e.agent || repoAgent[e.repo];
      if (ag) agentLast[ag] = Math.max(agentLast[ag] || 0, e.ts);
    });
    Object.keys(mapNodes.repos).forEach(function (repo) {
      var st = repoState[repo] || "";
      mapNodes.repos[repo].setAttribute("class", "repo " + st);
      mapNodes.edges[repo].setAttribute("class", "edge " + st);
    });
    Object.keys(mapNodes.agents).forEach(function (a) {
      var g = mapNodes.agents[a], last = agentLast[a] || 0, age = now - last;
      var awake = age < 45, calm = !awake && age < 300;
      g.setAttribute("class", "agent " + (awake ? "awake" : calm ? "calm" : ""));
      g._state.textContent = awake ? "working" : calm ? "on watch · " + ago(last) : "idle";
    });
  }
  function nodeCenter(repo) {
    var g = mapNodes.repos[repo];
    if (!g || $("page-live").hidden) return null;
    var r = g.getBoundingClientRect();
    return { x: r.left + r.width * 0.15, y: r.top + r.height / 2 };
  }

  /* ================= incident ================= */
  var STEP_LABEL = {
    scan: "Scanned", status_set: "Commit status set", issue_opened: "Issue opened", pr_opened: "Fix PR opened",
    pr_reviewed: "PR reviewed", verified: "Fix verified", issue_closed: "Issue closed", dismissed: "Dismissed as false positive",
    denied: "Blocked by policy"
  };
  function paintIncident(events) {
    var scans = events.filter(function (e) { return e.kind === "scan" && /^aegis-/.test(e.agent || ""); });
    if (!scans.length) return;
    var anchor = scans.find(function (e) { return e.verdict === "unsafe" && Date.now() / 1000 - e.ts < 1800; }) || scans[0];
    var repo = anchor.repo;
    var evs = events.filter(function (e) { return e.repo === repo && e.ts >= anchor.ts - 1; })
      .sort(function (a, b) { return a.ts - b.ts; });
    var groups = [], idx = {};
    evs.forEach(function (e) {
      var key = e.kind === "scan" ? "scan:" + e.ts : e.kind;
      if (e.kind === "scan" && groups.length && e !== evs[0]) key = "rescan";
      if (idx[key] == null) { idx[key] = groups.length; groups.push({ kind: e.kind, first: e, last: e, n: 0, refs: [] }); }
      var g = groups[idx[key]]; g.last = e; g.n++; if (e.ref) g.refs.push(e.ref);
    });
    var verdict = anchor.verdict || "";
    $("incTitle").innerHTML = esc(short(repo)) + ' <span class="v-' + esc(verdict) + '">' + (verdict === "unsafe" ? "unsafe" : "safe") + "</span>";
    $("incHint").textContent = agentShort(anchor.agent) + " · " + ago(anchor.ts) + " ago";
    var t0 = anchor.ts;
    var html = groups.map(function (g, i) {
      var e = g.first, label = STEP_LABEL[g.kind] || g.kind, meta = "", cls = "done";
      if (g.kind === "scan") {
        label = i === 0 ? "Push scanned" : "Re-scanned after fix";
        meta = (e.n_findings || 0) + " finding" + (e.n_findings === 1 ? "" : "s") + (e.total_ms ? " · " + fmtMs(e.total_ms) : "");
        cls += e.verdict === "unsafe" ? " bad" : " good";
      } else {
        if (g.n > 1) label += " ×" + g.n;
        var refs = g.refs.filter(function (r) { return /^\d+$/.test(r); }).slice(0, 4).map(function (r) { return "#" + r; }).join(" ");
        meta = refs;
        if (g.kind === "issue_closed" || g.kind === "verified") cls += " good";
        if (g.kind === "issue_opened" || g.kind === "denied") cls += " bad";
      }
      if (i === groups.length - 1) cls += " last";
      return '<li class="' + cls + '"><span class="pt"></span><span>' + esc(label) +
        (meta ? ' <span class="pm">' + esc(meta) + "</span>" : "") + '</span><span class="pm">+' + fmtDur(g.last.ts - t0) + "</span></li>";
    }).join("");
    var closed = groups.some(function (g) { return g.kind === "issue_closed" || g.kind === "verified"; });
    if (!closed && verdict === "unsafe") html += '<li><span class="pt"></span><span>Waiting for the fix…</span><span class="pm"></span></li>';
    $("pipeline").innerHTML = html;
    var span = evs.length ? evs[evs.length - 1].ts - t0 : 0;
    $("incTotal").innerHTML = closed ? "Detect → fix → verify in <b>" + fmtDur(span) + "</b>, nobody touched anything" :
      verdict === "unsafe" ? "Agent working · <b>" + fmtDur(Date.now() / 1000 - t0) + "</b> since push" : "Clean push · <b>" + fmtMs(anchor.total_ms) + "</b> scan";
  }

  /* ================= feed + live reactions ================= */
  var lastTs = null;
  function feedKind(e) {
    if (e.kind === "scan") return { cls: "k-" + (e.verdict || "scan"), label: e.verdict || "scan" };
    return { cls: "k-" + e.kind, label: String(e.kind).replace(/_/g, " ") };
  }
  function feedDetail(e) {
    var bits = [agentShort(e.agent), short(e.repo)];
    if (e.kind === "scan") bits.push((e.n_findings || 0) + " findings", fmtMs(e.total_ms));
    else if (e.ref) bits.push(/^\d+$/.test(e.ref) ? "#" + e.ref : String(e.ref).slice(0, 10));
    return bits.filter(Boolean).join(" · ");
  }
  var toastTimer = null;
  function toast(kind, k, t) {
    var n = $("toast");
    n.className = "toast on " + kind;
    $("toastK").textContent = k; $("toastT").textContent = t;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { n.className = "toast " + kind; }, 3600);
  }
  function react(fresh) {
    var top = null;
    fresh.forEach(function (e) {
      var c = nodeCenter(e.repo);
      var color = (e.kind === "scan" && e.verdict === "safe") || e.kind === "issue_closed" || e.kind === "verified" ? GREEN :
                  e.kind === "scan" || e.kind === "issue_opened" || e.kind === "denied" ? RED : AMBER;
      if (c) ripple(c.x, c.y, color);
      var rank = { scan: 3, pr_opened: 2, verified: 2, denied: 4 }[e.kind] || 0;
      if (rank && (!top || rank > top.rank)) top = { e: e, rank: rank };
    });
    if (!top) return;
    var e = top.e, who = agentShort(e.agent).replace("sentinel-", "Sentinel ") + " · " + short(e.repo);
    if (e.kind === "scan") toast(e.verdict === "unsafe" ? "bad" : "good", e.verdict === "unsafe" ? "Unsafe push · " + (e.n_findings || 0) + " findings" : "Clean push", who + " woke up");
    else if (e.kind === "pr_opened") toast("", "Fix proposed", who + " opened PR #" + e.ref);
    else if (e.kind === "verified") toast("good", "Fix verified", who);
    else if (e.kind === "denied") toast("bad", "Blocked by policy", who);
  }
  function loadEvents() {
    return getJSON("/api/events?n=80").then(function (d) {
      var events = (d.events || []).filter(function (e) { return e.repo && !/^local:|selftest/.test(e.repo); });
      var fresh = lastTs == null ? [] : events.filter(function (e) { return e.ts > lastTs; });
      if (events.length) lastTs = Math.max(lastTs || 0, events[0].ts);
      $("feed").innerHTML = events.slice(0, 40).map(function (e) {
        var k = feedKind(e), isNew = fresh.indexOf(e) >= 0;
        return '<li class="' + (isNew ? "new" : "") + '"><span class="t">' + ago(e.ts) + ' ago</span><span class="k ' + esc(k.cls) + '">' +
          esc(k.label) + '</span><span class="d">' + esc(feedDetail(e)) + "</span></li>";
      }).join("") || '<li><span class="d">Waiting for the first push…</span></li>';
      paintMap(events);
      paintIncident(events);
      if (fresh.length) { react(fresh); loadFleet(); }
    }).catch(function () {});
  }

  /* ================= alerts ================= */
  function loadAlerts() {
    return getJSON("/api/alerts?hours=48").then(function (d) {
      var a = d.alerts || [], box = $("alerts");
      box.hidden = !a.length;
      box.innerHTML = a.slice(0, 3).map(function (x) {
        if (x.type === "injection") {
          return '<div class="alert"><div class="alert-k">Attack caught</div><div class="alert-t"><b>' + esc(short(x.repo)) +
            "</b> contains a comment telling the agent to approve the code. Ignored and flagged at <code>" +
            esc(x.path) + ":" + esc(x.line) + "</code></div></div>";
        }
        return '<div class="alert"><div class="alert-k">Blocked by policy</div><div class="alert-t"><b>' + esc(agentShort(x.agent)) +
          "</b> tried to act on <b>" + esc(short(x.repo)) + "</b>, outside its fence. Denied by Guild.</div></div>";
      }).join("");
    }).catch(function () {});
  }

  /* ================= insights ================= */
  function loadInsights() {
    return getJSON("/api/insights?hours=24").then(function (d) {
      var noisy = d.noisy_rules || [], reo = d.reopened || [];
      $("noisy").innerHTML = noisy.length ? noisy.slice(0, 5).map(function (r) {
        var rate = r.dismiss_rate || 0;
        return '<li><div class="n">' + esc(ruleShort(r.rule_id)) + "<small>" + fmtInt(r.dismissed) + " of " + fmtInt(r.filed) + " dismissed</small>" +
          '<div class="meter"><i style="width:' + Math.round(rate * 100) + '%"></i></div></div><div class="v">' + Math.round(rate * 100) + "%</div></li>";
      }).join("") : '<li class="empty">No triage decisions yet</li>';
      $("reopened").innerHTML = reo.length ? reo.slice(0, 5).map(function (r) {
        return '<li><div class="n">' + esc(ruleShort(r.rule_id)) + "<small>" + esc(short(r.repo)) + "</small></div><div class=\"v\">×" + fmtInt(r.times) + "</div></li>";
      }).join("") : '<li class="empty">Nothing reopened</li>';
    }).catch(function () {});
  }

  /* ================= posture timeline ================= */
  var tlRows = [];
  function drawTimeline() {
    var repo = $("repoSel").value, byM = {}, months = [];
    tlRows.forEach(function (r) {
      if (repo && r.repo !== repo) return;
      var m = String(r.week).slice(0, 7);
      if (!byM[m]) byM[m] = { ERROR: 0, WARNING: 0, INFO: 0 };
      byM[m][r.severity in byM[m] ? r.severity : "INFO"] += r.n;
    });
    var keys = Object.keys(byM).sort();
    if (!keys.length) { $("timeline").innerHTML = '<div class="empty">No history yet</div>'; return; }
    var y0 = +keys[0].slice(0, 4), m0 = +keys[0].slice(5, 7) - 1, last = keys[keys.length - 1];
    var y1 = +last.slice(0, 4), m1 = +last.slice(5, 7) - 1;
    for (var y = y0, m = m0; y < y1 || (y === y1 && m <= m1); m++) {
      if (m > 11) { m = 0; y++; }
      if (y > y1 || (y === y1 && m > m1)) break;
      months.push(y + "-" + String(m + 1).padStart(2, "0"));
    }
    var VW = 1200, VH = 240, padL = 34, padB = 22, iw = VW - padL - 6, ih = VH - padB - 10;
    var max = 1;
    months.forEach(function (k) { var v = byM[k]; if (v) max = Math.max(max, v.ERROR + v.WARNING + v.INFO); });
    var bw = iw / months.length;
    var svg = '<svg viewBox="0 0 ' + VW + " " + VH + '" preserveAspectRatio="none">';
    [0.25, 0.5, 0.75, 1].forEach(function (f) {
      var gy = 10 + ih - ih * f;
      svg += '<line class="grid" x1="' + padL + '" x2="' + VW + '" y1="' + gy + '" y2="' + gy + '"/>' +
        '<text class="axis" x="0" y="' + (gy + 3) + '">' + fmtInt(max * f) + "</text>";
    });
    months.forEach(function (k, i) {
      var v = byM[k], x = padL + i * bw, yb = 10 + ih;
      if (v) {
        ["INFO", "WARNING", "ERROR"].forEach(function (s) {
          if (!v[s]) return;
          var h = ih * v[s] / max;
          yb -= h;
          svg += '<rect class="bar sev-' + s + '" data-k="' + k + '" x="' + (x + bw * 0.12).toFixed(2) + '" y="' + yb.toFixed(2) +
            '" width="' + Math.max(0.8, bw * 0.76).toFixed(2) + '" height="' + h.toFixed(2) + '" fill="' +
            { ERROR: "#ff6a52", WARNING: "#e9b45c", INFO: "#8db7e8" }[s] + '" opacity=".85"/>';
        });
      }
      if (k.slice(5) === "01") {
        var yr = +k.slice(0, 4);
        var every = months.length > 180 ? 3 : months.length > 60 ? 2 : 1;
        if (yr % every === 0) svg += '<text class="axis" x="' + x.toFixed(1) + '" y="' + (VH - 4) + '">' + yr + "</text>";
      }
    });
    svg += "</svg>";
    $("timeline").innerHTML = svg;
    $("timeline").onmousemove = function (ev) {
      var t = ev.target, tip = $("tip");
      if (!t.dataset || !t.dataset.k) { tip.hidden = true; return; }
      var v = byM[t.dataset.k];
      tip.hidden = false;
      tip.style.left = ev.clientX + 14 + "px"; tip.style.top = ev.clientY - 10 + "px";
      tip.textContent = t.dataset.k + " · " + v.ERROR + " error · " + v.WARNING + " warning · " + v.INFO + " info";
    };
    $("timeline").onmouseleave = function () { $("tip").hidden = true; };
    var total = tlRows.reduce(function (s, r) { return s + (!repo || r.repo === repo ? r.n : 0); }, 0);
    $("tlNote").textContent = fmtInt(total) + " finding-months · " + keys[0].slice(0, 4) + "–" + last.slice(0, 4) + " · by commit date";
  }
  function loadTimeline() {
    return getJSON("/api/timeline?weeks=1100&bucket=month").then(function (d) {
      tlRows = d.rows || [];
      var sel = $("repoSel"), cur = sel.value;
      var repos = Array.from(new Set(tlRows.map(function (r) { return r.repo; }))).sort();
      sel.innerHTML = '<option value="">All repos</option>' + repos.map(function (r) {
        return '<option value="' + esc(r) + '"' + (r === cur ? " selected" : "") + ">" + esc(r) + "</option>";
      }).join("");
      drawTimeline();
    }).catch(function () { $("timeline").innerHTML = '<div class="empty">Timeline unavailable</div>'; });
  }
  $("repoSel").addEventListener("change", drawTimeline);
  window.addEventListener("resize", function () { if (tlRows.length) drawTimeline(); });

  /* ================= schedule ================= */
  function every(fn, ms) { fn(); setInterval(fn, ms); }
  loadFleet().then(function () { every(loadEvents, 2000); });
  every(loadFleet, 6000);
  every(loadStats, 5000);
  every(loadAlerts, 5000);
  every(loadLatency, 10000);
  every(loadMttr, 10000);
  every(loadInsights, 15000);
  every(loadTimeline, 60000);
  if (REDUCED) requestAnimationFrame(frame);
})();
