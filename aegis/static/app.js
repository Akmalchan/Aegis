/* AEGIS dashboard. Plain JS, no dependencies. Every API value goes through esc() before innerHTML. */
(function () {
  "use strict";

  var $ = function (id) { return document.getElementById(id); };
  var REDUCED = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var NS = "http://www.w3.org/2000/svg";

  function esc(v) {
    return String(v == null ? "" : v).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function getJSON(url) {
    return fetch(url, { cache: "no-store" }).then(function (r) { if (!r.ok) throw new Error(url); return r.json(); });
  }
  function short(repo) { return String(repo || "").split("/").pop(); }
  function agentName(a) { return String(a || "").replace(/^aegis-/, "").replace("sentinel-", "Sentinel "); }
  function ruleShort(r) { var p = String(r || "").split("."); return (p[p.length - 1] || r).replace(/^js-/, "js · "); }
  function fmtInt(n) { return Math.round(n || 0).toLocaleString("en-US"); }
  function fmtK(n) { return n >= 10000 ? (n / 1000).toFixed(1).replace(/\.0$/, "") + "k" : fmtInt(n); }
  function fmtMs(ms) { if (!ms && ms !== 0) return "–"; return ms < 1000 ? Math.round(ms) + " ms" : (ms / 1000).toFixed(1) + " s"; }
  function fmtDur(s) {
    if (s < 90) return Math.round(s) + "s";
    if (s < 5400) return Math.round(s / 60) + " min";
    return (s / 3600).toFixed(1) + " h";
  }
  function ago(ts) {
    var s = Math.max(0, Date.now() / 1000 - ts);
    if (s < 60) return Math.round(s) + "s ago";
    if (s < 3600) return Math.round(s / 60) + "m ago";
    if (s < 86400) return Math.round(s / 3600) + "h ago";
    return Math.round(s / 86400) + "d ago";
  }
  function setHTML(node, html) {
    if (node._h === html) return false;
    node._h = html; node.innerHTML = html;
    return true;
  }
  function svgEl(tag, attrs, parent) {
    var e = document.createElementNS(NS, tag);
    for (var k in attrs) e.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(e);
    return e;
  }

  /* ================= hero: WebGL smoke ================= */
  var FRAG = [
    "precision mediump float;",
    "uniform vec2 r; uniform float t; uniform vec3 pc; uniform float ps; uniform vec2 pp;",
    "float h(vec2 p){return fract(sin(dot(p,vec2(127.1,311.7)))*43758.5453);}",
    "float n(vec2 p){vec2 i=floor(p),f=fract(p);f=f*f*(3.-2.*f);",
    " return mix(mix(h(i),h(i+vec2(1.,0.)),f.x),mix(h(i+vec2(0.,1.)),h(i+vec2(1.,1.)),f.x),f.y);}",
    "float fbm(vec2 p){float v=0.,a=.5;for(int i=0;i<5;i++){v+=a*n(p);p=p*2.02+vec2(1.7,9.2);a*=.5;}return v;}",
    "void main(){",
    " vec2 uv=gl_FragCoord.xy/r; float ar=r.x/r.y; vec2 p=uv*vec2(ar,1.)*1.9; float tt=t*.05;",
    " vec2 q=vec2(fbm(p+tt),fbm(p+vec2(5.2,1.3)-tt));",
    " vec2 w=vec2(fbm(p+3.*q+vec2(1.7,9.2)+tt*1.4),fbm(p+3.*q+vec2(8.3,2.8)-tt));",
    " float f=fbm(p+2.6*w);",
    " float smoke=smoothstep(.38,.98,f)*(.35+.75*uv.x);",
    " float line=smoothstep(.028,0.,abs(f-.6))*smoothstep(.25,.85,uv.x)*(.6+.4*sin(t*.6+uv.y*4.));",
    " vec3 col=vec3(smoke*.62)+vec3(1.,.86,.22)*line;",
    " float d=distance(uv*vec2(ar,1.),pp*vec2(ar,1.));",
    " float ring=exp(-pow((d-(1.-ps)*1.6)/.09,2.))*ps;",
    " col+=pc*(ring*.9+ps*.10);",
    " col*=1.-.6*length(uv-vec2(.62,.5));",
    " gl_FragColor=vec4(col,1.);}"
  ].join("\n");
  var VERT = "attribute vec2 a;void main(){gl_Position=vec4(a,0.,1.);}";
  var heroes = [];
  function initFlow(canvas) {
    var gl = canvas.getContext("webgl", { antialias: false, premultipliedAlpha: false });
    if (!gl) return null;
    function sh(type, src) { var s = gl.createShader(type); gl.shaderSource(s, src); gl.compileShader(s); return s; }
    var prog = gl.createProgram();
    gl.attachShader(prog, sh(gl.VERTEX_SHADER, VERT));
    gl.attachShader(prog, sh(gl.FRAGMENT_SHADER, FRAG));
    gl.linkProgram(prog);
    if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) return null;
    gl.useProgram(prog);
    var buf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
    var loc = gl.getAttribLocation(prog, "a");
    gl.enableVertexAttribArray(loc);
    gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);
    var u = {};
    ["r", "t", "pc", "ps", "pp"].forEach(function (k) { u[k] = gl.getUniformLocation(prog, k); });
    var hero = { canvas: canvas, gl: gl, u: u, pulse: { c: [1, 1, 1], s: 0, p: [0.7, 0.5] } };
    heroes.push(hero);
    return hero;
  }
  function sizeHero(h) {
    var scale = 0.5 * Math.min(window.devicePixelRatio || 1, 2);
    var w = Math.max(1, Math.round(h.canvas.clientWidth * scale)), ht = Math.max(1, Math.round(h.canvas.clientHeight * scale));
    if (h.canvas.width !== w || h.canvas.height !== ht) { h.canvas.width = w; h.canvas.height = ht; h.gl.viewport(0, 0, w, ht); }
  }
  var t0 = performance.now(), lastF = 0;
  function loop(now) {
    requestAnimationFrame(loop);
    if (now - lastF < 15) return;
    lastF = now;
    heroes.forEach(function (h) {
      if (!h.canvas.offsetParent) return;  // hidden page
      sizeHero(h);
      var gl = h.gl, u = h.u, p = h.pulse;
      p.s = Math.max(0, p.s - 0.012);
      gl.uniform2f(u.r, h.canvas.width, h.canvas.height);
      gl.uniform1f(u.t, REDUCED ? 8 : (now - t0) / 1000);
      gl.uniform3f(u.pc, p.c[0], p.c[1], p.c[2]);
      gl.uniform1f(u.ps, p.s);
      gl.uniform2f(u.pp, p.p[0], p.p[1]);
      gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
    });
  }
  var heroLive = initFlow($("flow"));
  initFlow($("flow2"));
  requestAnimationFrame(loop);
  function pulse(rgb) {
    if (!heroLive) return;
    heroLive.pulse = { c: rgb, s: 1, p: [0.72, 0.42] };
  }
  var RED = [1, 0.29, 0.24], LIME = [1, 0.86, 0.22];

  /* ================= routing ================= */
  function route() {
    var page = (location.hash || "#live").slice(1) === "how" ? "how" : "live";
    ["live", "how"].forEach(function (p) { $("page-" + p).hidden = p !== page; });
    document.querySelectorAll(".tab").forEach(function (a) { a.classList.toggle("on", a.dataset.page === page); });
    window.scrollTo({ top: 0 });
  }
  window.addEventListener("hashchange", route);
  route();

  /* ================= KPIs ================= */
  var shown = {};
  function countTo(id, target, fmt) {
    var node = $(id), from = shown[id] || 0;
    shown[id] = target;
    if (REDUCED || from === target) { node.textContent = fmt(target); return; }
    var s = performance.now();
    (function step(now) {
      var k = Math.min(1, (now - s) / 1000), e = 1 - Math.pow(1 - k, 3);
      node.textContent = fmt(from + (target - from) * e);
      if (k < 1) requestAnimationFrame(step);
    })(s);
  }
  function setStatus(ok, text) { $("status").className = "status " + (ok ? "ok" : "bad"); $("statusText").textContent = text; }
  function loadStats() {
    return getJSON("/api/stats").then(function (s) {
      countTo("kAgents", s.agents, fmtInt);
      countTo("kRepos", s.repos, fmtInt);
      countTo("kFindings", s.findings, fmtInt);
      $("howFindings").textContent = fmtK(s.findings);
      setStatus(true, s.ch ? "Fleet online" : "Online");
    }).catch(function () { setStatus(false, "Offline"); });
  }
  function loadLatency() {
    return getJSON("/api/latency").then(function (d) {
      var rows = (d.agents || []).filter(function (a) { return /^aegis-/.test(a.agent); });
      var n = rows.reduce(function (s, a) { return s + a.scans; }, 0);
      var p50 = n ? rows.reduce(function (s, a) { return s + a.p50_ms * a.scans; }, 0) / n : 0;
      $("kScan").textContent = p50 ? fmtMs(p50) : "–";
      var svg = $("latency"), sig = JSON.stringify(rows);
      if (svg._sig === sig) return;
      svg._sig = sig; svg.innerHTML = "";
      if (!rows.length) { svgEl("text", { x: 0, y: 20 }, svg).textContent = "No agent scans yet"; return; }
      var max = Math.max.apply(null, rows.map(function (a) { return a.p95_ms; })) || 1;
      var bh = Math.min(22, 110 / rows.length / 2.4);
      rows.forEach(function (a, i) {
        var y = 8 + i * (bh * 2 + 26);
        svgEl("text", { x: 0, y: y + 8 }, svg).textContent = agentName(a.agent) + " · " + a.scans + " scans";
        var w95 = 220 * a.p95_ms / max, w50 = 220 * a.p50_ms / max;
        svgEl("rect", { x: 0, y: y + 16, width: w95, height: bh, rx: bh / 2, fill: "#ffe03d" }, svg);
        svgEl("rect", { x: 0, y: y + 16, width: w50, height: bh, rx: bh / 2, fill: "#0a0a0a" }, svg);
        svgEl("text", { x: w95 + 8, y: y + 16 + bh * 0.72, "class": "v" }, svg).textContent = fmtMs(a.p50_ms) + " / " + fmtMs(a.p95_ms);
      });
    }).catch(function () {});
  }
  function loadMttr() {
    return getJSON("/api/mttr").then(function (d) {
      var n = 0, sum = 0;
      (d.repos || []).forEach(function (r) { n += r.closed; sum += r.mttr_h * r.closed; });
      $("kMttr").textContent = n ? fmtDur(sum / n * 3600) : "–";
    }).catch(function () {});
  }

  /* ================= fleet map ================= */
  var map = $("map"), nodes = { agents: {}, repos: {}, edges: {} }, repoAgent = {}, fleetSig = "";
  function buildMap(agents) {
    map.innerHTML = "";
    nodes = { agents: {}, repos: {}, edges: {} };
    var ge = svgEl("g", {}, map), gn = svgEl("g", {}, map);
    var band = 400 / Math.max(1, agents.length);
    agents.forEach(function (a, ai) {
      var ay = band * ai + band / 2, ax = 96, repos = a.repos || [];
      repos.forEach(function (r, ri) {
        var ry = ay + (ri - (repos.length - 1) / 2) * 40;
        nodes.edges[r.repo] = svgEl("path", { "class": "edge", d: "M" + (ax + 38) + " " + ay + " C 230 " + ay + ", 240 " + ry + ", 320 " + ry }, ge);
        var g = svgEl("g", { "class": "repo", transform: "translate(320 " + (ry - 16) + ")" }, gn);
        svgEl("rect", { width: 300, height: 32, rx: 16 }, g);
        svgEl("text", { x: 16, y: 21 }, g).textContent = short(r.repo);
        g._cnt = svgEl("text", { "class": "cnt", x: 286, y: 20.5, "text-anchor": "end" }, g);
        nodes.repos[r.repo] = g;
        repoAgent[r.repo] = a.agent;
      });
      var ga = svgEl("g", { "class": "agent", transform: "translate(" + ax + " " + ay + ")" }, gn);
      svgEl("circle", { "class": "halo", r: 36 }, ga);
      svgEl("circle", { "class": "core", r: 38 }, ga);
      svgEl("text", { y: 2 }, ga).textContent = agentName(a.agent).replace("Sentinel ", "S-");
      ga._s = svgEl("text", { "class": "s", y: 18 }, ga);
      ga._s.textContent = "idle";
      nodes.agents[a.agent] = ga;
    });
  }
  function loadFleet() {
    return getJSON("/api/fleet").then(function (d) {
      var agents = d.agents || [], n = 0;
      fleetAgents = {};
      agents.forEach(function (a) { fleetAgents[a.agent] = (a.repos || []).map(function (r) { return r.repo; }); });
      var sig = agents.map(function (a) { return a.agent + (a.repos || []).map(function (r) { return r.repo; }).join(); }).join("|");
      if (sig !== fleetSig) { fleetSig = sig; buildMap(agents); }
      agents.forEach(function (a) {
        (a.repos || []).forEach(function (r) {
          n++;
          var g = nodes.repos[r.repo];
          if (g) g._cnt.innerHTML = '<tspan class="o">' + fmtInt(r.open) + ' open</tspan>   <tspan class="f">' + fmtInt(r.resolved) + " fixed</tspan>";
        });
      });
      $("fleetHint").textContent = agents.length + " agents · " + n + " repos";
    }).catch(function () {});
  }
  function isHot(e) { return (e.kind === "scan" && e.verdict === "unsafe") || e.kind === "issue_opened" || e.kind === "denied"; }
  function isGood(e) { return (e.kind === "scan" && e.verdict === "safe") || e.kind === "issue_closed" || e.kind === "verified"; }
  function paintMap(events) {
    var now = Date.now() / 1000, st = {}, last = {};
    events.slice().reverse().forEach(function (e) {
      if (now - e.ts > 300) return;
      if (isHot(e) || e.kind === "pr_opened") st[e.repo] = "hot"; else if (isGood(e)) st[e.repo] = "cool";
      var ag = e.agent || repoAgent[e.repo];
      if (ag) last[ag] = Math.max(last[ag] || 0, e.ts);
    });
    Object.keys(nodes.repos).forEach(function (r) {
      nodes.repos[r].setAttribute("class", "repo " + (st[r] || ""));
      nodes.edges[r].setAttribute("class", "edge " + (st[r] || ""));
    });
    var anyAwake = false;
    Object.keys(nodes.agents).forEach(function (a) {
      var age = now - (last[a] || 0), awake = age < 45;
      anyAwake = anyAwake || awake;
      nodes.agents[a].setAttribute("class", "agent" + (awake ? " awake" : ""));
      nodes.agents[a]._s.textContent = awake ? "working" : last[a] ? ago(last[a]) : "idle";
    });
    $("heroState").textContent = anyAwake ? "Agent working now" : "Fleet on watch";
  }

  /* ================= latest push: 8-step pipeline (port of Andrii's stepper) ================= */
  function ghUrl(repo, kind, ref) {
    if (!repo || !ref || /^local:/.test(repo)) return "";
    var base = "https://github.com/" + repo;
    return kind === "issue" ? base + "/issues/" + ref : kind === "pr" ? base + "/pull/" + ref : kind === "commit" ? base + "/commit/" + ref : base;
  }
  function link(url, text) { return url ? '<a href="' + esc(url) + '" target="_blank" rel="noopener">' + esc(text) + "</a>" : esc(text); }
  var LABEL = { status_set: "Status set", issue_opened: "Issue opened", pr_opened: "Fix PR opened", pr_reviewed: "PR reviewed",
    verified: "Fix verified", verify_failed: "Fix rejected", issue_closed: "Issue closed", dismissed: "Dismissed", denied: "Blocked by policy",
    fix_proposed: "Fix proposed", fix_failed: "Fix failed", handoff_ok: "Handoff ok", handoff_rejected: "Handoff rejected" };
  function paintIncident(events) {
    var scans = events.filter(function (e) { return e.kind === "scan" && e.repo; });
    if (!scans.length) return;
    var now = Date.now() / 1000;
    var anchor = scans.find(function (e) { return e.verdict === "unsafe" && now - e.ts < 1800; }) || scans[0];
    var repo = anchor.repo;
    var wake = events.find(function (e) { return e.kind === "wake" && e.repo === repo && e.ts <= anchor.ts + 1 && anchor.ts - e.ts < 120; });
    var t0 = wake ? wake.ts : anchor.ts;
    var evs = events.filter(function (e) { return e.repo === repo && e.ts >= t0 - 1; }).sort(function (a, b) { return a.ts - b.ts; });
    function first(kind, pred) { return evs.find(function (e) { return e.kind === kind && (!pred || pred(e)); }); }
    function last(kind, pred) { var r = null; evs.forEach(function (e) { if (e.kind === kind && (!pred || pred(e))) r = e; }); return r; }
    var sha = anchor.sha || (wake && wake.sha) || "";
    var issue = first("issue_opened"), issueClosed = last("issue_closed"), dismissed = first("dismissed");
    var fix = last("fix_proposed"), fixFail = last("fix_failed"), ver = last("verified"), verFail = last("verify_failed");
    var pr = first("pr_opened"), rescan = evs.filter(function (e) { return e.kind === "scan" && e !== anchor; }).pop();
    var green = last("status_set", function (e) { return e.state === "success" && e.ts > anchor.ts; });
    var unsafe = anchor.verdict === "unsafe", steps = [];
    steps.push({ label: "Push", ev: wake || anchor, cls: "done", link: sha ? link(ghUrl(repo, "commit", sha), sha.slice(0, 7)) : "" });
    steps.push({ label: "Semgrep scan", ev: anchor, cls: "done " + (unsafe ? "bad" : ""), meta: (anchor.n_findings || 0) + " finding" + (anchor.n_findings === 1 ? "" : "s") + " · " + fmtMs(anchor.ms || anchor.total_ms) });
    if (!unsafe) {
      steps.push({ label: "Validated", cls: "done", ev: anchor, meta: "clean" });
      steps.push({ label: "Commit green", cls: green ? "done" : "", ev: green, meta: green ? "✓" : "–" });
    } else {
      var validated = issue || fix || dismissed;
      steps.push({ label: "Validated", cls: validated ? "done" : "active", ev: validated, meta: dismissed && !issue ? "false positive" : validated ? "triage confirmed" : "triage running…" });
      var fx = fix || fixFail;
      steps.push({ label: "Fix", cls: fix ? "done" : fixFail ? "done bad" : validated ? "active" : "", ev: fx,
        meta: fx ? [fx.model === "semgrep-rule-fix" ? "rule autofix" : fx.model || "llm", fixFail && !fix ? "failed" : ""].filter(Boolean).join(" · ") : validated ? "writing patch…" : "–" });
      var vr = ver || verFail;
      steps.push({ label: "Re-check", cls: ver ? "done" : verFail ? "done bad" : fx ? "active" : "", ev: vr, meta: vr ? (ver ? "verified" : "rejected") : fx ? "verifying…" : "–" });
      steps.push({ label: "Issue", cls: issue ? "done" + (issueClosed ? "" : " bad") : "", ev: issue, meta: issue ? (issueClosed ? "closed" : "open") : "–", link: issue ? link(ghUrl(repo, "issue", issue.ref), "#" + issue.ref) : "" });
      steps.push({ label: "Decision", cls: pr ? "done" + (verFail ? " bad" : "") : fixFail && !fix ? "done bad" : "", ev: pr || (fixFail && !fix ? fixFail : null),
        meta: pr ? (ver ? "PR verified" : verFail ? "PR rejected" : "PR open") : fixFail && !fix ? "no safe patch" : "–", link: pr ? link(ghUrl(repo, "pr", pr.ref), "PR #" + pr.ref) : "" });
      var merged = issueClosed || (green && rescan && rescan.verdict === "safe");
      steps.push({ label: "Merged & green", cls: merged ? "done" : pr ? "active" : "", ev: merged ? (issueClosed || green) : null, meta: merged ? "rescan clean" : pr ? "waiting for merge…" : "–" });
    }
    var v = unsafe ? "unsafe" : "safe";
    setHTML($("incTitle"), esc(short(repo)) + '<span class="v ' + v + '">' + v + "</span>");
    $("incHint").textContent = agentName(anchor.agent) + " · " + ago(t0);
    var lastDone = -1;
    steps.forEach(function (st, i) { if (/done/.test(st.cls)) lastDone = i; });
    setHTML($("pipeline"), steps.map(function (st, i) {
      var cls = st.cls + (i === lastDone ? " last" : "");
      return '<li class="' + cls + '"><span class="n">' + (i + 1) + "</span><span>" + esc(st.label) + ' <span class="m">' + esc(st.meta || "") + (st.link ? " " + st.link : "") +
        '</span></span><span class="m">' + (st.ev ? "+" + fmtDur(Math.max(0, st.ev.ts - t0)) : "") + "</span></li>";
    }).join(""));
    var done = issueClosed || ver, end = evs[evs.length - 1].ts;
    setHTML($("incTotal"), done ? "<span>Detect → fix → verify</span><b>" + fmtDur(end - t0) + "</b>"
      : unsafe ? "<span>Since push</span><b>" + fmtDur(now - t0) + "</b>" : "<span>Clean push, scanned in</span><b>" + fmtMs(anchor.total_ms) + "</b>");
  }

  /* ================= Guild agents + handoff guard (port of Andrii's control room) ================= */
  var ROLE_AGENTS = { "aegis-triage": "validates findings", "aegis-remediator": "writes the fix", "aegis-verifier": "proves the fix", "aegis-warden": "fleet cron · insights",
    "aegis-rulesmith": "learns new rules", "aegis-reporter": "writes the report", "aegis-onboarder": "adds repos" };
  var fleetAgents = {}, knownSessions = null;
  function paintCards(d) {
    var byAgent = {};
    (d.agents || []).forEach(function (a) { byAgent[a.agent] = a; });
    var names = Object.keys(fleetAgents).concat(Object.keys(ROLE_AGENTS));
    Object.keys(byAgent).forEach(function (n) { if (names.indexOf(n) < 0) names.push(n); });
    var html = "", lastGroup = "";
    names.forEach(function (name) {
      var group = fleetAgents[name] ? "Sentinels · one per 3 repos" : "Role agents";
      if (group !== lastGroup) { html += '<div class="grp">' + esc(group) + "</div>"; lastGroup = group; }
      var a = byAgent[name], cur = a && (a.current || a.latest), st = a ? a.state : "never";
      var cls = st === "working" ? "working" : (cur && cur.status === "failed") ? "failed" : a ? "done" : "never";
      var sub = fleetAgents[name] ? fleetAgents[name].map(short).join(", ") : (ROLE_AGENTS[name] || "");
      var status = st === "working" ? "working" : cur ? (cur.status === "failed" ? "failed " : "") + ago(cur.created_at) : "idle";
      var tool = "";
      if (cur) {
        var t = cur.last_tool_call;
        tool = (t ? esc(t.name) : esc(cur.note || cur.event || "")) + (cur.repo ? " · " + esc(short(cur.repo)) : "") + link(cur.session_url, "session ↗");
      }
      html += '<div class="ag ' + cls + '"><div class="top"><span class="led"></span><b>' + esc(agentName(name).replace(/^aegis-/, "")) + '</b><span class="st">' + esc(status) +
        "</span></div>" + (sub ? "<small>" + esc(sub) + "</small>" : "") + (tool ? '<div class="tool">' + tool + "</div>" : "") + "</div>";
    });
    setHTML($("cards"), html || '<div class="empty">No agents</div>');
  }
  function loadGuild() {
    return getJSON("/api/guild").then(function (d) {
      if (!d.ok) { $("guildHint").textContent = "Guild · offline on this host"; paintCards({ agents: [] }); return; }
      var sessions = d.sessions || [], working = sessions.filter(function (s) { return s.status === "working"; }).length;
      $("guildHint").textContent = "Guild · live · " + working + " awake · " + sessions.length + " sessions";
      paintCards(d);
      if (knownSessions) {
        var fresh = sessions.filter(function (s) { return !knownSessions[s.id] && Date.now() / 1000 - s.created_at < 120; });
        if (fresh.length) { pulse(LIME); toast(false, "Agent woke up", agentName(fresh[0].agent) + (fresh[0].repo ? " · " + short(fresh[0].repo) : "")); }
      }
      knownSessions = knownSessions || {};
      sessions.forEach(function (s) { knownSessions[s.id] = 1; });
    }).catch(function () { $("guildHint").textContent = "Guild · offline"; });
  }
  function loadHandoffs() {
    return getJSON("/api/handoffs").then(function (d) {
      var rows = (d.handoffs || []).slice(0, 7);
      $("hoHint").textContent = rows.length ? rows.length + " handoffs checked by Semgrep" : "Agent → agent handoffs, checked by Semgrep";
      setHTML($("handoffs"), rows.length ? rows.map(function (h) {
        var m = /^(.+?)->(.+?):(.+)$/.exec(String(h.ref || ""));
        var from = h.agent || (m && m[1]) || "?", to = h.to_agent || (m && m[2]) || "?", what = h.artifact || (m && m[3]) || "";
        var rejected = h.kind === "handoff_rejected" || h.verdict === "rejected" || h.ok === false;
        return '<li class="' + (rejected ? "rejected" : "ok") + '"><span class="ha">' + esc(agentName(from)) + " <b>→</b> " + esc(agentName(to)) + (what ? " · " + esc(what) : "") +
          '</span><span class="hv">' + (rejected ? "rejected" : "ok") + '</span><span class="hm">' + ago(h.ts) + " · " + esc(short(h.repo)) +
          (h.reason ? " · " + esc(h.reason) : "") + (h.rule_id ? " · " + esc(ruleShort(h.rule_id)) : "") + "</span></li>";
      }).join("") : '<li class="empty">No handoffs yet</li>');
    }).catch(function () {});
  }

  /* ================= feed + reactions ================= */
  var lastTs = null, toastTimer = null;
  function kindOf(e) {
    if (e.kind === "scan") return { c: e.verdict === "unsafe" ? "bad" : "good", l: e.verdict === "unsafe" ? "Unsafe" : "Safe" };
    var raw = LABEL[e.kind] || String(e.kind).replace(/_/g, " ");
    var l = raw.charAt(0).toUpperCase() + raw.slice(1).replace(" opened", "");
    var bad = isHot(e) || /fail|denied|reject/.test(e.kind);
    return { c: bad ? "bad" : isGood(e) ? "good" : "act", l: l };
  }
  function toast(bad, k, t) {
    var n = $("toast");
    n.className = "toast on" + (bad ? " bad" : "");
    $("toastK").textContent = k; $("toastT").textContent = t;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { n.className = "toast" + (bad ? " bad" : ""); }, 3800);
  }
  function react(fresh) {
    var top = null;
    fresh.forEach(function (e) {
      var r = { scan: 3, denied: 4, pr_opened: 2, verified: 2 }[e.kind] || 0;
      if (r && (!top || r > top.r)) top = { e: e, r: r };
    });
    var bad = fresh.some(isHot);
    pulse(bad ? RED : LIME);
    if (!top) return;
    var e = top.e, who = agentName(e.agent) + " · " + short(e.repo);
    if (e.kind === "scan") toast(e.verdict === "unsafe", e.verdict === "unsafe" ? e.n_findings + " findings" : "Clean", who);
    else if (e.kind === "pr_opened") toast(false, "Fix PR #" + e.ref, who);
    else if (e.kind === "verified") toast(false, "Verified", who);
    else if (e.kind === "denied") toast(true, "Blocked", who);
  }
  /* ---- fleet chat: every agent action as a message, newest at the bottom ---- */
  var AV = { "sentinel-01": "#0a0a0a", "sentinel-02": "#3a3a3a", "sentinel-03": "#6b6b6b", warden: "#ff4a3d", reporter: "#2f6fed", onboarder: "#2fbf71" };
  function chatText(e) {
    var r = "<b>" + esc(short(e.repo)) + "</b>", ref = /^\d+$/.test(e.ref || "") ? " #" + esc(e.ref) : "";
    switch (e.kind) {
      case "scan": return e.verdict === "unsafe"
        ? "Push to " + r + " is unsafe. " + e.n_findings + " finding" + (e.n_findings === 1 ? "" : "s") + " in " + fmtMs(e.total_ms) + "."
        : "Scanned " + r + ". Clean in " + fmtMs(e.total_ms) + ".";
      case "issue_opened": return "Opened Issue" + ref + " on " + r + ".";
      case "issue_closed": return "Closed Issue" + ref + " on " + r + ". The finding is gone.";
      case "pr_opened": return "Proposed a fix for " + r + ": PR" + ref + ".";
      case "pr_reviewed": return "Reviewed PR" + ref + " on " + r + ".";
      case "verified": return "Re-scanned the fix on " + r + ". Verified.";
      case "verify_failed": return "The fix on " + r + " didn't hold. Rejected it.";
      case "status_set": return "Set the commit status on " + r + ".";
      case "dismissed": return "Marked a finding on " + r + " as a false positive.";
      case "denied": return "Tried to touch " + r + ". Blocked by policy.";
      default: var k = String(e.kind).replace(/_/g, " "); return esc(k.charAt(0).toUpperCase() + k.slice(1)) + " on " + r + ref + ".";
    }
  }
  function renderChat(events, fresh) {
    var box = $("feed"), list = events.slice(0, 40).reverse();
    var atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 40;
    var changed = setHTML(box, list.map(function (e) {
      var who = agentName(e.agent).replace("Sentinel ", "Sentinel "), key = String(e.agent || "").replace(/^aegis-/, "");
      var init = /sentinel-(\d+)/.test(key) ? "S" + key.slice(-1) : key.charAt(0).toUpperCase();
      var k = kindOf(e);
      return '<li class="msg' + (fresh.indexOf(e) >= 0 ? " new" : "") + '"><span class="av" style="background:' + (AV[key] || "#999") + '">' + esc(init) +
        '</span><div class="mb"><div class="mh"><b>' + esc(who) + '</b><span class="tag ' + k.c + '">' + esc(k.l) + '</span><time>' +
        clock(e.ts) + '</time></div><p>' + chatText(e) + "</p></div></li>";
    }).join("") || '<li class="empty">Waiting for the first push</li>');
    if (changed && (atBottom || fresh.length || !box._seen)) { box.scrollTop = box.scrollHeight; box._seen = true; }
    $("chatCount").textContent = events.length + " recent";
  }
  function loadEvents() {
    return getJSON("/api/events?n=80").then(function (d) {
      var events = (d.events || []).filter(function (e) { return e.repo && !/^local:|selftest/.test(e.repo); });
      var fresh = lastTs == null ? [] : events.filter(function (e) { return e.ts > lastTs; });
      if (events.length) lastTs = Math.max(lastTs || 0, events[0].ts);
      renderChat(events, fresh);
      paintMap(events);
      paintIncident(events);
      if (fresh.length) {
        react(fresh); loadFleet();
        fresh.slice().reverse().forEach(function (e) {
          var k = kindOf(e), txt = "fleet  " + agentName(e.agent) + " · " + short(e.repo) + " · " + k.l.toLowerCase() +
            (e.kind === "scan" ? " · " + e.n_findings + " findings" : /^\d+$/.test(e.ref || "") ? " #" + e.ref : "");
          termQueue.push({ ts: e.ts, level: "fleet", text: txt });
        });
      }
    }).catch(function () {});
  }

  /* ================= alerts ================= */
  function loadAlerts() {
    return getJSON("/api/alerts?hours=48").then(function (d) {
      var a = d.alerts || [];
      $("alerts").hidden = !a.length;
      setHTML($("alerts"), a.slice(0, 2).map(function (x) {
        return x.type === "injection"
          ? '<div class="alert"><span class="tag">Attack caught</span><span class="msg">' + esc(short(x.repo)) + " told the agent to approve itself. Flagged. <code>" + esc(x.path) + ":" + esc(x.line) + "</code></span></div>"
          : '<div class="alert"><span class="tag">Blocked</span><span class="msg">' + esc(agentName(x.agent)) + " tried " + esc(short(x.repo)) + ". Outside its fence.</span></div>";
      }).join(""));
    }).catch(function () {});
  }

  /* ================= breakdown charts ================= */
  var SEV = { ERROR: "#0a0a0a", WARNING: "#ffe03d", INFO: "#c9c9c4" };
  function loadBreakdown() {
    return getJSON("/api/breakdown").then(function (d) {
      var sev = d.severity || [], total = sev.reduce(function (s, x) { return s + x.n; }, 0);
      var svg = $("donut"), sig = JSON.stringify(sev);
      if (svg._sig === sig) { drawBars(); return; }
      svg._sig = sig; svg.innerHTML = "";
      var R = 62, C = 2 * Math.PI * R, off = 0;
      svgEl("circle", { cx: 80, cy: 80, r: R, fill: "none", stroke: "#f2f2f0", "stroke-width": 18 }, svg);
      sev.forEach(function (x) {
        var len = total ? C * x.n / total : 0;
        svgEl("circle", { cx: 80, cy: 80, r: R, fill: "none", stroke: SEV[x.severity] || "#ccc", "stroke-width": 18,
          "stroke-dasharray": Math.max(0, len - 3) + " " + C, "stroke-dashoffset": -off, transform: "rotate(-90 80 80)", "stroke-linecap": "round" }, svg);
        off += len;
      });
      svgEl("text", { x: 80, y: 84, "class": "c-t" }, svg).textContent = fmtK(total);
      svgEl("text", { x: 80, y: 100, "class": "c-s" }, svg).textContent = "findings";
      setHTML($("donutLegend"), sev.map(function (x) {
        return '<div><i style="background:' + (SEV[x.severity] || "#ccc") + '"></i>' + esc(x.severity.charAt(0) + x.severity.slice(1).toLowerCase()) +
          "<b>" + Math.round(100 * x.n / (total || 1)) + "%</b></div>";
      }).join(""));
      drawBars();
      function drawBars() {
      function bars(id, rows, label) {
        var max = rows.length ? rows[0].n : 1;
        setHTML($(id), rows.slice(0, 6).map(function (r) {
          return '<li><div class="row"><span>' + esc(label(r)) + "</span><b>" + fmtK(r.n) + '</b></div><div class="bar"><i style="width:' +
            Math.max(2, 100 * r.n / max).toFixed(1) + '%"></i></div></li>';
        }).join("") || '<li class="empty">No data</li>');
      }
      bars("rules", d.rules || [], function (r) { return ruleShort(r.rule_id); });
      bars("repos", d.repos || [], function (r) { return r.repo; });
      }
    }).catch(function () {});
  }
  function loadInsights() {
    return getJSON("/api/insights?hours=24").then(function (d) {
      var reo = d.reopened || [], noisy = d.noisy_rules || [];
      setHTML($("reopened"), reo.slice(0, 5).map(function (r) {
        return "<li><span>" + esc(ruleShort(r.rule_id)) + "<small>" + esc(short(r.repo)) + '</small></span><b class="x">×' + fmtInt(r.times) + "</b></li>";
      }).join("") || '<li class="empty">Nothing reopened</li>');
      setHTML($("noisy"), noisy.slice(0, 5).map(function (r) {
        return "<li><span>" + esc(ruleShort(r.rule_id)) + "<small>" + fmtInt(r.dismissed) + "/" + fmtInt(r.filed) + '</small></span><b>' + Math.round(100 * r.dismiss_rate) + "%</b></li>";
      }).join("") || '<li class="empty">No triage yet</li>');
    }).catch(function () {});
  }

  /* ================= fix funnel + agent watch (ClickHouse MVs) ================= */
  var STAGE = { detected: "Detected", issue_opened: "Issue opened", pr_opened: "Fix PR opened", verified: "Re-scan verified", issue_closed: "Issue closed" };
  function loadFunnel() {
    return getJSON("/api/funnel?hours=720").then(function (d) {
      var st = d.stages || [], max = st.length ? st[0].n || 1 : 1;
      $("funnelMs").textContent = d.ch ? "· " + fmtMs(d.query_ms) : "";
      setHTML($("funnel"), st.map(function (s) {
        var t = s.median_s_from_prev == null ? "" : " · +" + fmtDur(s.median_s_from_prev);
        return '<li><div class="row"><span>' + esc(STAGE[s.stage] || s.stage) + "</span><b>" + fmtInt(s.n) + esc(t) +
          '</b></div><div class="bar"><i style="width:' + Math.max(2, 100 * s.n / max).toFixed(1) + '%"></i></div></li>';
      }).join("") || '<li class="empty">No live findings yet</li>');
    }).catch(function () {});
  }
  function loadWatch() {
    return getJSON("/api/anomalies?minutes=60").then(function (d) {
      var a = d.anomalies || [];
      $("watchMs").textContent = d.ch ? "· " + fmtMs(d.query_ms) : "";
      setHTML($("watch"), a.slice(0, 6).map(function (x) {
        return "<li><span>" + esc(agentName(x.agent)) + "<small>" + esc(x.kind) + " · " + esc(x.reason) + '</small></span><b class="x">' + fmtInt(x.recent) + "/min</b></li>";
      }).join("") || '<li class="empty">' + (d.ch ? "All agents nominal" : "ClickHouse offline") + "</li>");
    }).catch(function () {});
  }

  /* ================= posture area chart ================= */
  var tl = [], tlMs = 0;
  function drawTimeline() {
    var repo = $("repoSel").value, byM = {};
    tl.forEach(function (r) {
      if (repo && r.repo !== repo) return;
      var m = String(r.week).slice(0, 7);
      byM[m] = byM[m] || { ERROR: 0, WARNING: 0, INFO: 0 };
      byM[m][r.severity in byM[m] ? r.severity : "INFO"] += r.n;
    });
    var keys = Object.keys(byM).sort();
    if (!keys.length) { $("timeline").innerHTML = '<div class="empty">No history yet</div>'; return; }
    var months = [], y = +keys[0].slice(0, 4), m = +keys[0].slice(5, 7), end = keys[keys.length - 1];
    while (true) {
      var k = y + "-" + String(m).padStart(2, "0");
      months.push(k);
      if (k >= end) break;
      if (++m > 12) { m = 1; y++; }
    }
    function smooth(arr) { return arr.map(function (_, i) { var a = arr.slice(Math.max(0, i - 2), i + 3); return a.reduce(function (s, v) { return s + v; }, 0) / a.length; }); }
    var err = smooth(months.map(function (k) { return byM[k] ? byM[k].ERROR : 0; }));
    var wrn = smooth(months.map(function (k) { return byM[k] ? byM[k].WARNING + byM[k].INFO : 0; }));
    var VW = 1200, VH = 260, top = 10, bot = 230, max = 1;
    err.forEach(function (v, i) { max = Math.max(max, v + wrn[i]); });
    var X = function (i) { return months.length < 2 ? 0 : i * VW / (months.length - 1); };
    var Y = function (v) { return bot - (bot - top) * v / max; };
    function area(lo, hi) {
      var d = "M0 " + Y(lo[0] + hi[0]).toFixed(1);
      hi.forEach(function (v, i) { d += " L" + X(i).toFixed(1) + " " + Y(lo[i] + v).toFixed(1); });
      for (var i = lo.length - 1; i >= 0; i--) d += " L" + X(i).toFixed(1) + " " + Y(lo[i]).toFixed(1);
      return d + " Z";
    }
    var zero = err.map(function () { return 0; });
    var svg = '<svg viewBox="0 0 ' + VW + " " + VH + '" preserveAspectRatio="none">';
    [0.5, 1].forEach(function (f) { svg += '<line class="gl" x1="0" x2="' + VW + '" y1="' + Y(max * f) + '" y2="' + Y(max * f) + '"/>'; });
    svg += '<path d="' + area(zero, err) + '" fill="#0a0a0a"/>';
    svg += '<path d="' + area(err, wrn) + '" fill="#ffe03d"/>';
    var step = months.length > 180 ? 4 : months.length > 72 ? 2 : 1;
    months.forEach(function (k, i) {
      if (k.slice(5) === "01" && +k.slice(0, 4) % step === 0) svg += '<text class="ax" x="' + Math.min(VW - 30, X(i) + 4) + '" y="' + (VH - 6) + '">' + k.slice(0, 4) + "</text>";
    });
    svg += '<line class="cur" id="cur" x1="-10" x2="-10" y1="' + top + '" y2="' + bot + '"/></svg>';
    var box = $("timeline");
    box.innerHTML = svg;
    var total = months.reduce(function (s, k) { return s + (byM[k] ? byM[k].ERROR + byM[k].WARNING + byM[k].INFO : 0); }, 0);
    $("tlBig").textContent = fmtInt(total);
    $("tlNote").textContent = keys[0].slice(0, 4) + " – " + end.slice(0, 4) + " · by commit date" + (tlMs ? " · " + fmtMs(tlMs) : "");
    box.onmousemove = function (ev) {
      var r = box.getBoundingClientRect(), i = Math.round((ev.clientX - r.left) / r.width * (months.length - 1));
      i = Math.max(0, Math.min(months.length - 1, i));
      var c = $("cur"); c.setAttribute("x1", X(i)); c.setAttribute("x2", X(i));
      var v = byM[months[i]] || { ERROR: 0, WARNING: 0, INFO: 0 }, tip = $("tip");
      tip.hidden = false; tip.style.left = ev.clientX + 14 + "px"; tip.style.top = ev.clientY - 12 + "px";
      tip.textContent = months[i] + " · " + v.ERROR + " error · " + (v.WARNING + v.INFO) + " warning";
    };
    box.onmouseleave = function () { $("tip").hidden = true; var c = $("cur"); c.setAttribute("x1", -10); c.setAttribute("x2", -10); };
  }
  function loadTimeline() {
    return getJSON("/api/timeline?weeks=1100&bucket=month").then(function (d) {
      tlMs = d.ms || 0;
      var sig = JSON.stringify(d.rows || []);
      if (sig === loadTimeline._sig) return;
      loadTimeline._sig = sig;
      tl = d.rows || [];
      var sel = $("repoSel"), cur = sel.value;
      var repos = Array.from(new Set(tl.map(function (r) { return r.repo; }))).sort();
      sel.innerHTML = '<option value="">All repos</option>' + repos.map(function (r) {
        return '<option value="' + esc(r) + '"' + (r === cur ? " selected" : "") + ">" + esc(r) + "</option>";
      }).join("");
      drawTimeline();
    }).catch(function () {});
  }
  $("repoSel").addEventListener("change", drawTimeline);

  /* ================= patrol terminal ================= */
  var termQueue = [], pSeq = 0, term = $("term");
  function clock(ts) { return new Date(ts * 1000).toLocaleTimeString("en-US", { hour12: false }); }
  var hp = $("hpList");
  function heroLine(l) {
    var html;
    if (l.level === "hit" || l.level === "warn") {
      html = '<span class="d ' + l.level + '"></span><span class="t">' + esc(String(l.rule || "").replace(/^js-/, "js · ")) +
        "<small>" + esc(l.path || "") + ":" + esc(l.line || "") + '</small></span><span class="r">' + esc(l.repo || "") + "</span>";
    } else if (l.level === "ok") {
      html = '<span class="d ok"></span><span class="t">Clean</span><span class="r">' + esc(l.repo || "") + "</span>";
    } else if (l.level === "fleet") {
      html = '<span class="d fleet"></span><span class="t">' + esc(l.text.replace(/^fleet\s+/, "")) + '</span><span class="r">fleet</span>';
    } else return;
    var e = hp.querySelector(".hp-empty"); if (e) e.remove();
    var li = document.createElement("li");
    li.innerHTML = html;
    hp.insertBefore(li, hp.firstChild);
    while (hp.children.length > 6) hp.removeChild(hp.lastChild);
  }
  function pushLine(l) {
    heroLine(l);
    var li = document.createElement("li");
    li.className = "l-" + l.level;
    li.innerHTML = '<span class="ts">' + clock(l.ts) + "</span><span>" + esc(l.text) + "</span>";
    var prev = term.querySelector(".cursor");
    if (prev) prev.classList.remove("cursor");
    li.lastChild.classList.add("cursor");
    term.appendChild(li);
    while (term.children.length > 26) term.removeChild(term.firstChild);
  }
  setInterval(function () {  // drip lines out so the log reads like a live stream, not page refreshes
    if (!termQueue.length) return;
    var burst = termQueue.length > 20 ? 4 : 1;
    while (burst-- && termQueue.length) pushLine(termQueue.shift());
  }, 140);
  function loadPatrol() {
    return getJSON("/api/stream?after=" + pSeq).then(function (d) {
      var lines = d.lines || [];
      if (!pSeq && lines.length > 22) lines = lines.slice(-22);
      lines.forEach(function (l) { pSeq = Math.max(pSeq, l.seq); termQueue.push(l); });
      var st = d.stats || {};
      $("patrolPill").textContent = d.on ? "Live" : "Off";
      if (st.started) $("patrolUp").textContent = "aegis patrol · up " + fmtDur(Date.now() / 1000 - st.started);
      countTo("pRepos", st.repos || 0, fmtInt);
      countTo("pFind", st.findings || 0, fmtInt);
      countTo("pSecrets", st.secrets || 0, fmtInt);
      countTo("hpRepos", st.repos || 0, fmtInt);
      countTo("hpFind", st.findings || 0, fmtInt);
      countTo("hpSec", st.secrets || 0, fmtInt);
      if (st.started) $("hpSub").textContent = "Live · up " + fmtDur(Date.now() / 1000 - st.started);
      $("pRate").textContent = st.repos ? Math.round(100 * (st.dirty || 0) / st.repos) + "%" : "0%";
      var rules = st.rules || [], max = rules.length ? rules[0][1] : 1;
      setHTML($("pRules"), rules.map(function (r) {
        return '<li><div class="row"><span>' + esc(ruleShort(r[0])) + "</span><b>" + fmtInt(r[1]) + '</b></div><div class="bar"><i style="width:' +
          Math.max(3, 100 * r[1] / max).toFixed(1) + '%"></i></div></li>';
      }).join("") || '<li class="empty">Warming up</li>');
    }).catch(function () {});
  }

  /* ================= schedule ================= */
  function every(fn, ms) { fn(); setInterval(fn, ms); }
  loadFleet().then(function () { every(loadEvents, 2000); });
  every(loadFleet, 6000);
  every(loadStats, 5000);
  every(loadAlerts, 5000);
  every(loadLatency, 10000);
  every(loadMttr, 10000);
  every(loadBreakdown, 30000);
  every(loadInsights, 15000);
  every(loadTimeline, 60000);
  every(loadFunnel, 15000);
  every(loadWatch, 10000);
  every(loadPatrol, 1500);
  every(loadGuild, 4000);
  every(loadHandoffs, 5000);
})();
