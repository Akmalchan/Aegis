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

  /* ================= incident ================= */
  var LABEL = { status_set: "Status set", issue_opened: "Issue opened", pr_opened: "Fix PR opened", pr_reviewed: "PR reviewed",
    verified: "Fix verified", verify_failed: "Fix rejected", issue_closed: "Issue closed", dismissed: "Dismissed", denied: "Blocked by policy" };
  function paintIncident(events) {
    var scans = events.filter(function (e) { return e.kind === "scan" && /^aegis-/.test(e.agent || ""); });
    if (!scans.length) return;
    var anchor = scans.find(function (e) { return e.verdict === "unsafe" && Date.now() / 1000 - e.ts < 1800; }) || scans[0];
    var evs = events.filter(function (e) { return e.repo === anchor.repo && e.ts >= anchor.ts - 1; }).sort(function (a, b) { return a.ts - b.ts; });
    var groups = [], idx = {};
    evs.forEach(function (e, i) {
      var key = e.kind === "scan" ? (i === 0 ? "scan0" : "rescan") : e.kind;
      if (idx[key] == null) { idx[key] = groups.length; groups.push({ kind: key, e: e, last: e, n: 0, refs: [] }); }
      var g = groups[idx[key]]; g.last = e; g.n++; if (/^\d+$/.test(e.ref || "")) g.refs.push("#" + e.ref);
    });
    var v = anchor.verdict === "unsafe" ? "unsafe" : "safe";
    setHTML($("incTitle"), esc(short(anchor.repo)) + '<span class="v ' + v + '">' + v + "</span>");
    $("incHint").textContent = agentName(anchor.agent) + " · " + ago(anchor.ts);
    var html = groups.map(function (g, i) {
      var label, meta = "", cls = "done";
      if (g.kind === "scan0") { label = "Push scanned"; meta = (g.e.n_findings || 0) + " findings · " + fmtMs(g.e.total_ms); if (v === "unsafe") cls += " bad"; }
      else if (g.kind === "rescan") { label = "Re-scanned"; meta = g.n + "×"; }
      else { label = (LABEL[g.kind] || g.kind) + (g.n > 1 ? " ×" + g.n : ""); meta = g.refs.slice(0, 3).join(" "); if (g.kind === "issue_opened" || g.kind === "denied") cls += " bad"; }
      if (i === groups.length - 1) cls += " last";
      return '<li class="' + cls + '"><span class="n">' + (i + 1) + "</span><span>" + esc(label) + ' <span class="m">' + esc(meta) +
        '</span></span><span class="m">+' + fmtDur(g.last.ts - anchor.ts) + "</span></li>";
    }).join("");
    var closed = groups.some(function (g) { return g.kind === "issue_closed" || g.kind === "verified"; });
    if (!closed && v === "unsafe") html += '<li><span class="n">' + (groups.length + 1) + "</span><span>Waiting for the fix</span><span></span></li>";
    setHTML($("pipeline"), html);
    var span = evs[evs.length - 1].ts - anchor.ts;
    setHTML($("incTotal"), closed ? "<span>Detect → fix → verify</span><b>" + fmtDur(span) + "</b>"
      : v === "unsafe" ? "<span>Since push</span><b>" + fmtDur(Date.now() / 1000 - anchor.ts) + "</b>"
      : "<span>Clean push, scanned in</span><b>" + fmtMs(anchor.total_ms) + "</b>");
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
  function loadEvents() {
    return getJSON("/api/events?n=80").then(function (d) {
      var events = (d.events || []).filter(function (e) { return e.repo && !/^local:|selftest/.test(e.repo); });
      var fresh = lastTs == null ? [] : events.filter(function (e) { return e.ts > lastTs; });
      if (events.length) lastTs = Math.max(lastTs || 0, events[0].ts);
      setHTML($("feed"), events.slice(0, 30).map(function (e) {
        var k = kindOf(e), bits = [agentName(e.agent), short(e.repo)];
        if (e.kind === "scan") bits.push(e.n_findings + " findings", fmtMs(e.total_ms));
        else if (/^\d+$/.test(e.ref || "")) bits.push("#" + e.ref);
        return '<li class="' + (fresh.indexOf(e) >= 0 ? "new" : "") + '"><span class="t">' + ago(e.ts) + '</span><span class="k ' + k.c + '">' +
          esc(k.l) + '</span><span class="d">' + esc(bits.join(" · ")) + "</span></li>";
      }).join("") || '<li><span class="empty">Waiting for the first push</span></li>');
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

  /* ================= posture area chart ================= */
  var tl = [];
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
    $("tlNote").textContent = keys[0].slice(0, 4) + " – " + end.slice(0, 4) + " · by commit date";
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
  every(loadPatrol, 1500);
})();
