/* Studio logic: polls the local server, feeds the office, renders panels and settings. */
(function () {
  const $ = (id) => document.getElementById(id);
  const TOKEN = document.querySelector('meta[name="forge-token"]').content;
  const store = {
    get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* private mode */ } },
  };
  const ACTIVE_MS = 15 * 60 * 1000;
  const office = new Office($("office"));
  const st = {
    lang: store.get("forge.lang") || (navigator.language || "en").toLowerCase().startsWith("tr") ? "tr" : "en",
    theme: store.get("forge.theme") || "auto",
    selected: "auto", runId: null, mode: "demo", since: 0, runs: [], settings: null,
  };
  const url = new URLSearchParams(location.search);
  st.lang = url.get("lang") || store.get("forge.lang") || st.lang;
  st.theme = url.get("theme") || st.theme;
  let job = freshJob();
  let demoTimer = null;

  function freshJob() {
    return { team: {}, chunks: {}, order: [], plan: 0, rewrites: 0, feed: [], phase: "phase_idle", cost: null, title: "" };
  }

  const T = (k) => I18N[st.lang][k] ?? k;
  const fill = (k, d) => T(k).replace(/\{(\w+)\}/g, (_, n) => d[n] ?? "");
  const clock = (t) => { const m = /T(\d\d:\d\d)/.exec(t || ""); return m ? m[1] : ""; };

  /* ---------- job state ---------- */
  function chunkOf(e) {
    const key = `${e.pdf}/${e.chunk}`;
    if (!job.chunks[key]) { job.chunks[key] = { key, pdf: e.pdf, chunk: e.chunk, questions: 0, verified: 0, stage: "write" }; job.order.push(key); }
    return job.chunks[key];
  }
  const index = (e) => job.order.indexOf(`${e.pdf}/${e.chunk}`);
  const editor = (e) => office.editors[Math.max(index(e), 0) % office.editors.length];
  const reviewer = (e) => office.reviewers[Math.max(index(e), 0) % office.reviewers.length];
  const chunkNo = (e) => index(e) + 1 || e.chunk;

  function setupTeam(e) {
    const chunks = e.pdfs.reduce((n, p) => n + p.chunks, 0);
    job.team = e.team; job.plan = e.pdfs.reduce((n, p) => n + p.questions, 0);
    job.title = e.pdfs.map((p) => p.stem).join(" + ");
    e.pdfs.forEach((p) => { for (let c = 1; c <= p.chunks; c++) chunkOf({ pdf: p.stem, chunk: c }); });
    office.reseed();
    office.setup(e.team, chunks, chunks);
    return chunks;
  }

  const labelOf = (a) => {
    const name = T({ editor: "role_editor", reviewer: "role_reviewer", screener: "role_screener", chief: "role_chief" }[a.role]);
    return a.n ? `${name} ${a.n}` : name;
  };

  function remember(a, entry) { if (a) { a.log.unshift(entry); a.log.length = Math.min(a.log.length, 40); } }
  function setTask(a, key, data) { if (a) a.task = key ? { key, data } : null; }

  function post(e, entry, actor) {
    const item = { t: e.t, ...entry };
    job.feed.unshift(item);
    job.feed.length = Math.min(job.feed.length, 120);
    remember(actor, item);
  }

  function handle(e, instant) {
    const anim = !instant;
    switch (e.type) {
      case "plan": {
        const chunks = setupTeam(e);
        job.phase = "phase_plan";
        post(e, { key: "ev_plan", data: { questions: job.plan, pdfs: e.pdfs.length, chunks } }, office.chief);
        if (anim) { const a = office.chief, s = Office.spots.BOARD_SPOT; office.go(a, s.x, s.y); office.wait(a, 1.6); office.home(a); }
        break;
      }
      case "agent_start": {
        const c = chunkOf(e), round = e.round || 1;
        const data = { n: 0, chunk: chunkNo(e), round, pdf: e.pdf };
        if (e.role === "editor") {
          const a = editor(e); data.n = a.n; a.work = "type"; a.model = e.model;
          c.stage = round > 1 ? "fix" : "write"; job.phase = c.stage === "fix" ? "phase_fix" : "phase_writing";
          setTask(a, round > 1 ? "task_fixing" : "task_writing", data);
          post(e, { key: round > 1 ? "ev_editor_fix" : "ev_editor", data }, a);
        } else if (e.role === "screener") {
          const a = office.screener;
          if (a) { a.work = "read"; a.model = e.model; }
          c.stage = "review"; job.phase = "phase_review";
          setTask(a, "task_screening", data);
          post(e, { key: "ev_screener", data }, a);
        } else if (e.role === "reviewer") {
          const a = reviewer(e); data.n = a.n; a.work = "read"; a.model = e.model;
          c.stage = "review"; job.phase = "phase_review";
          setTask(a, "task_reviewing", data);
          post(e, { key: "ev_reviewer", data }, a);
        } else if (e.role === "chief-editor") {
          const a = office.chief; a.work = "read"; a.model = e.model; job.phase = "phase_audit";
          setTask(a, "task_auditing", data);
          post(e, { key: "ev_chief", data }, a);
        }
        break;
      }
      case "check": {
        const c = chunkOf(e), a = editor(e), data = { chunk: chunkNo(e), questions: e.questions, errors: e.errors };
        if (e.ok) {
          if (String(e.file || "").startsWith("chunk-")) c.questions = e.questions;
          post(e, { key: "ev_check_ok", data, tone: "good" }, a);
          setTask(a, null);
          if (anim) office.deliver(a, office.screener ? office.screener.desk : reviewer(e).desk); else a.work = null;
        } else {
          post(e, { key: "ev_check_bad", data, tone: "bad" }, a);
          if (anim) office.say(a, fill("say_failed", { n: e.errors }));
        }
        break;
      }
      case "screen": case "review": case "audit": {
        const c = chunkOf(e), failed = (e.failed || []).length;
        const data = { chunk: chunkNo(e), passed: e.passed, failed };
        const who = e.type === "screen" ? office.screener : e.type === "review" ? reviewer(e) : office.chief;
        if (e.type === "review") c.verified += e.passed;
        if (e.type === "audit") c.verified = Math.max(0, c.verified - failed);
        const bad = failed > 0;
        c.stage = bad ? "fix" : e.type === "screen" ? "review" : "done";
        if (e.type !== "screen" && !bad && c.verified < c.questions) c.stage = "review";
        post(e, { key: "ev_" + e.type, data, tone: bad ? "bad" : "good" }, who);
        (e.failed || []).slice(0, 4).forEach((f) => post(e, { reason: f, tone: "bad" }, who));
        if (bad) job.phase = "phase_fix";
        setTask(who, null);
        if (who) {
          if (anim) {
            office.float(who.x, who.y - 26, bad ? T("fail") : T("pass"), bad ? "#ef4444" : "#22c55e");
            office.say(who, fill(bad ? "say_failed" : "say_passed", { n: bad ? failed : e.passed }));
            if (who !== office.chief || bad) office.deliver(who, bad ? editor(e).desk : (e.type === "screen" ? reviewer(e).desk : office.chief.desk));
            else who.work = null;
          } else who.work = null;
        }
        break;
      }
      case "audit_sample":
        post(e, { key: "ev_audit_sample", data: { chunk: chunkNo(e), questions: e.questions } }, office.chief);
        break;
      case "replace":
        job.rewrites += (e.ids || []).length;
        post(e, { key: "ev_replace", data: { chunk: chunkNo(e), count: (e.ids || []).length } }, editor(e));
        if (anim) editor(e).work = null;
        break;
      case "merge": {
        const a = office.chief;
        job.phase = "phase_merge";
        setTask(a, "task_merging", {});
        post(e, { key: "ev_merge", data: { questions: e.questions }, tone: "good" }, a);
        Object.values(job.chunks).forEach((c) => { c.stage = "done"; });
        if (anim) { const s = Office.spots.PRINT_SPOT; a.work = null; office.go(a, s.x, s.y); office.run(a, () => { a.dir = "up"; office.printing = 3; }); office.wait(a, 2.6); office.home(a); }
        break;
      }
      case "render":
        job.phase = "phase_done";
        setTask(office.chief, "task_done", {});
        post(e, { key: "ev_render", data: { name: String(e.out || "").split(/[\\/]/).pop() }, tone: "good" }, office.chief);
        if (anim) { office.printing = 3; office.float(Office.spots.PRINTER.x, 30, T("printed"), "#22c55e"); office.cheerAll(T("say_printed")); }
        break;
    }
  }

  office.phrase = (a) => {
    if (!a.task) return "";
    const key = a.task.key === "task_fixing" ? "say_fix" : a.role === "chief" ? "say_chief" : "say_" + a.role;
    const list = T(key);
    return Array.isArray(list) ? list[Math.floor(Math.random() * list.length)].replace("{chunk}", a.task.data.chunk ?? "") : "";
  };

  /* ---------- panels ---------- */
  function render() {
    const cs = job.order.map((k) => job.chunks[k]);
    const counted = cs.filter((c) => c.questions);
    const total = counted.length === cs.length && cs.length ? counted.reduce((n, c) => n + c.questions, 0) : job.plan;
    const verified = cs.reduce((n, c) => n + c.verified, 0);
    const pct = total ? Math.min(100, Math.round(verified / total * 100)) : 0;
    $("phase").textContent = T(job.phase);
    $("bar").style.width = pct + "%";
    document.querySelector(".progress").setAttribute("aria-valuenow", pct);
    $("s-verified").textContent = `${verified} / ${total}`;
    $("s-rewrites").textContent = job.rewrites;
    $("s-chunks").textContent = `${cs.filter((c) => c.stage === "done").length} / ${cs.length}`;
    $("s-cost").textContent = job.cost && job.cost.agents ? "$" + job.cost.total.usd.toFixed(2) : "–";
    office.boardTitle = job.title;
    office.board = cs.map((c) => ({ stage: c.stage, share: c.stage === "done" ? 1 : c.questions ? Math.max(0.08, c.verified / c.questions) : 0.08 }));
    const team = job.team, nE = office.editors.length, nR = office.reviewers.length;
    const rows = [["role_chief", 1, team.chief_editor || T("off")], ["role_editor", nE, team.editor],
      ["role_screener", team.screener ? 1 : 0, team.screener || T("off")], ["role_reviewer", nR, team.reviewer]];
    $("team").innerHTML = "";
    rows.forEach(([k, n, model]) => {
      const li = document.createElement("li"), b = document.createElement("b"), s = document.createElement("span");
      b.textContent = n > 1 ? `${T(k)} × ${n}` : T(k); s.textContent = model || "–";
      li.append(b, s); $("team").append(li);
    });
    const feed = $("feed");
    feed.innerHTML = "";
    job.feed.slice(0, 60).forEach((f) => {
      const li = document.createElement("li"), time = document.createElement("time"), span = document.createElement("span");
      li.className = f.tone || ""; time.textContent = clock(f.t);
      span.textContent = entryText(f);
      li.append(time, span); feed.append(li);
    });
    const banner = $("banner");
    banner.hidden = false;
    banner.textContent = st.mode === "demo" ? T("demo_banner") : st.mode === "live" ? T("live_banner") : T("past_banner");
  }

  function renderRunSelect() {
    const sel = $("run"), keep = st.selected;
    sel.innerHTML = "";
    const add = (v, label) => { const o = document.createElement("option"); o.value = v; o.textContent = label; sel.append(o); };
    add("auto", st.runs.length ? (st.runs[0] && isActive(st.runs[0]) ? `● ${st.runs[0].pdfs.join(" + ")}` : T("demo_option")) : T("demo_option"));
    st.runs.forEach((r) => add(r.id, `${(r.started || "").slice(5, 16).replace("T", " ")} · ${r.pdfs.join(" + ")}`));
    sel.value = [...sel.options].some((o) => o.value === keep) ? keep : "auto";
  }

  function applyStaticText() {
    document.documentElement.lang = st.lang;
    document.querySelectorAll("[data-i18n]").forEach((n) => { n.textContent = T(n.dataset.i18n); });
    $("lang").textContent = st.lang === "tr" ? "EN" : "TR";
    document.querySelectorAll("[data-theme-choice]").forEach((b) => {
      b.setAttribute("aria-checked", String(b.dataset.themeChoice === st.theme));
    });
    $("office").setAttribute("aria-label", T("canvas_label"));
    office.labels = { pass: T("pass"), fail: T("fail"), printed: T("printed") };
    renderRunSelect();
    renderSettings();
    render();
  }

  /* ---------- team console ---------- */
  const dlg = $("console");
  let selected = 0, consoleTimer = null, faceAnim = 0;

  const statusOf = (a) => (a.q.length ? "status_moving" : a.work ? "status_working" : "status_idle");
  const taskText = (a) => {
    if (a.task) return fill(a.task.key, a.task.data);
    return a.role === "chief" && job.plan && job.phase !== "phase_done" ? T("task_coordinating") : T("task_idle");
  };
  const entryText = (f) => (f.reason
    ? fill("ev_reason", { id: f.reason.id, reason: T("reasons")[f.reason.reason] || f.reason.reason })
    : fill(f.key, f.data || {}));

  function face(canvas, a) {
    const g = canvas.getContext("2d");
    g.imageSmoothingEnabled = false;
    g.clearRect(0, 0, canvas.width, canvas.height);
    Sprites.drawPerson(g, canvas.width / 2, canvas.height - 5, a.look, { pose: a.pose === "walk" ? "walk" : a.work || "stand" });
  }

  function renderConsole() {
    const staff = office.staff();
    selected = Math.min(selected, staff.length - 1);
    const a = staff[selected];
    $("console-count").textContent = fill("agents_active", { active: staff.filter((x) => x.work).length, total: staff.length });
    const list = $("agents");
    if (list.children.length !== staff.length) {
      list.innerHTML = "";
      staff.forEach((x, i) => {
        const li = document.createElement("li"), c = document.createElement("canvas"), box = document.createElement("div");
        li.setAttribute("role", "option"); li.id = "agent-" + i;
        c.width = c.height = 44; box.append(document.createElement("b"), document.createElement("span"));
        li.append(c, box);
        li.onclick = () => { selected = i; renderConsole(); };
        list.append(li);
      });
    }
    staff.forEach((x, i) => {
      const li = list.children[i];
      li.setAttribute("aria-selected", String(i === selected));
      li.querySelector("b").textContent = labelOf(x);
      li.querySelector("span").textContent = taskText(x);
      face(li.querySelector("canvas"), x);
    });
    list.setAttribute("aria-activedescendant", "agent-" + selected);
    $("agent-name").textContent = labelOf(a);
    $("agent-task").textContent = taskText(a);
    $("agent-status").textContent = T(statusOf(a));
    const role = { editor: "editor", reviewer: "reviewer", screener: "screener", chief: "chief_editor" }[a.role];
    $("agent-model").textContent = a.model || job.team[role] || office.team[role] || T("none");
    $("agent-chunk").textContent = a.task && a.task.data.chunk ? `#${a.task.data.chunk}` : T("none");
    const log = $("agent-log");
    log.innerHTML = "";
    (a.log.length ? a.log : [{ key: "no_activity" }]).slice(0, 30).forEach((f) => {
      const li = document.createElement("li"), time = document.createElement("time"), span = document.createElement("span");
      li.className = f.tone || ""; time.textContent = clock(f.t); span.textContent = entryText(f);
      li.append(time, span); log.append(li);
    });
  }

  function animateFace() {
    if (!dlg.open) return;
    const a = office.staff()[selected];
    if (a) face($("agent-face"), a);
    faceAnim = requestAnimationFrame(animateFace);
  }

  function openConsole(a) {
    const i = a ? office.staff().indexOf(a) : 0;
    selected = Math.max(i, 0);
    $("agents").innerHTML = "";
    dlg.showModal();
    renderConsole();
    $("agents").focus();
    consoleTimer = setInterval(renderConsole, 1000);
    animateFace();
  }

  function step(by) {
    const n = office.staff().length;
    selected = (selected + by + n) % n;
    renderConsole();
    const li = $("agents").children[selected];
    if (li) li.scrollIntoView({ block: "nearest" });
  }

  dlg.addEventListener("close", () => { clearInterval(consoleTimer); cancelAnimationFrame(faceAnim); });
  $("console-close").onclick = () => dlg.close();
  $("agent-prev").onclick = () => step(-1);
  $("agent-next").onclick = () => step(1);
  $("agent-locate").onclick = () => { office.locate(office.staff()[selected]); dlg.close(); };
  $("agents").addEventListener("keydown", (ev) => {
    if (ev.key === "ArrowDown") { ev.preventDefault(); step(1); }
    if (ev.key === "ArrowUp") { ev.preventDefault(); step(-1); }
  });
  $("open-console").onclick = () => openConsole();
  const canvasPoint = (ev) => {
    const r = $("office").getBoundingClientRect();
    return [(ev.clientX - r.left) / r.width * Office.spots.W, (ev.clientY - r.top) / r.height * Office.spots.H];
  };
  $("office").addEventListener("click", (ev) => {
    const hit = office.hit(...canvasPoint(ev));
    if (hit) openConsole(hit.actor);
  });
  $("office").addEventListener("mousemove", (ev) => {
    $("office").classList.toggle("can-open", !!office.hit(...canvasPoint(ev)));
  });

  /* ---------- polling ---------- */
  const isActive = (r) => r.last && Date.now() - new Date(r.last).getTime() < ACTIVE_MS && r.books < 1;
  const getJson = (u) => fetch(u, { cache: "no-store" }).then((r) => r.json());

  function stopDemo() { if (demoTimer) { clearTimeout(demoTimer); demoTimer = null; } }

  async function loadRun(id, mode) {
    stopDemo();
    st.runId = id; st.mode = mode; st.since = 0; job = freshJob();
    const d = await getJson(`/api/events?run=${id}&since=0`);
    const recent = Date.now() - 20000;
    d.events.forEach((e) => handle(e, mode !== "live" || new Date(e.t).getTime() < recent));
    st.since = d.total;
    if (mode !== "live") { office.settle(); office.staff().forEach((a) => { if (a.task && a.task.key !== "task_done") a.task = null; }); }
    refreshCost();
    render();
  }

  async function refreshCost() {
    if (!st.runId) return;
    try { job.cost = (await getJson(`/api/cost?run=${st.runId}`)).cost; render(); } catch (e) { /* server gone */ }
  }

  async function pollRuns() {
    try {
      st.runs = (await getJson("/api/runs")).runs;
      renderRunSelect();
      if (st.selected === "auto") {
        const r = st.runs[0];
        if (r && isActive(r)) { if (st.runId !== r.id || st.mode !== "live") await loadRun(r.id, "live"); }
        else if (st.mode !== "demo") startDemo();
      }
    } catch (e) { /* server gone */ }
  }

  async function pollEvents() {
    if (st.mode === "live" && st.runId) {
      try {
        const d = await getJson(`/api/events?run=${st.runId}&since=${st.since}`);
        if (d.events.length) { d.events.forEach((e) => handle(e, false)); st.since = d.total; render(); }
      } catch (e) { /* server gone */ }
    }
  }

  /* ---------- demo ---------- */
  function demoScript() {
    const team = { editor: "sonnet", screener: "haiku", reviewer: "sonnet", chief_editor: "opus" };
    const pdfs = [{ stem: "cell-biology", pages: 42, chunks: 2, questions: 42 }, { stem: "genetics", pages: 38, chunks: 2, questions: 38 }];
    const list = [["cell-biology", 1, 21], ["cell-biology", 2, 21], ["genetics", 1, 19], ["genetics", 2, 19]];
    const s = [[0.8, { type: "plan", team, pdfs }]];
    list.forEach(([pdf, chunk]) => s.push([0.5, { type: "agent_start", role: "editor", pdf, chunk }]));
    list.forEach(([pdf, chunk, q], i) => {
      s.push([3 + i, { type: "check", pdf, chunk, ok: true, questions: q, errors: 0, file: `chunk-${chunk}.json` }]);
      s.push([2.5, { type: "agent_start", role: "screener", pdf, chunk }]);
      const flagged = i % 2 ? [] : [{ id: `p${chunk}-q0${i + 3}`, reason: i ? "difficulty" : "giveaway" }];
      s.push([3, { type: "screen", pdf, chunk, passed: q - flagged.length, failed: flagged }]);
      if (flagged.length) {
        s.push([2, { type: "agent_start", role: "editor", pdf, chunk, round: 2 }]);
        s.push([3, { type: "replace", pdf, chunk, ids: flagged.map((f) => f.id) }]);
      }
      s.push([2, { type: "agent_start", role: "reviewer", pdf, chunk }]);
    });
    list.forEach(([pdf, chunk, q], i) => {
      const failed = i === 1 ? [{ id: `p${chunk}-q11`, reason: "multi_correct" }] : [];
      s.push([3.5, { type: "review", pdf, chunk, passed: q - failed.length, failed }]);
      if (failed.length) {
        s.push([2.5, { type: "agent_start", role: "editor", pdf, chunk, round: 2 }]);
        s.push([3, { type: "replace", pdf, chunk, ids: failed.map((f) => f.id) }]);
        s.push([1.5, { type: "agent_start", role: "reviewer", pdf, chunk, round: 2 }]);
        s.push([3, { type: "review", pdf, chunk, passed: failed.length, failed: [] }]);
      }
    });
    list.slice(0, 2).forEach(([pdf, chunk]) => {
      s.push([1.5, { type: "audit_sample", pdf, chunk, questions: 9 }]);
      s.push([1, { type: "agent_start", role: "chief-editor", pdf, chunk }]);
      s.push([4, { type: "audit", pdf, chunk, passed: 9, failed: [] }]);
    });
    s.push([2, { type: "merge", questions: 80, parts: 2 }]);
    s.push([3.5, { type: "render", questions: 80, out: "biology-final-questions.pdf" }]);
    return s;
  }

  function startDemo() {
    stopDemo();
    st.mode = "demo"; st.runId = null; job = freshJob(); render();
    const script = demoScript();
    let i = 0;
    const next = () => {
      if (i >= script.length) { demoTimer = setTimeout(startDemo, 9000); return; }
      const [delay, e] = script[i++];
      demoTimer = setTimeout(() => {
        handle({ ...e, t: new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 19) }, false);
        render(); next();
      }, delay * 1000);
    };
    next();
  }

  /* ---------- settings ---------- */
  async function saveSetting(key, value, stateEl, reset) {
    stateEl.querySelector("span").textContent = "";
    const res = await fetch("/api/settings", {
      method: "POST", headers: { "Content-Type": "application/json", "X-Forge-Token": TOKEN },
      body: JSON.stringify(reset ? { key, reset: true } : { key, value }),
    });
    const d = await res.json();
    const msg = stateEl.querySelector("span");
    if (d.ok) {
      st.settings = d;
      office.speech = d.values.studio_speech_bubbles;
      msg.className = "ok"; msg.textContent = T("saved");
      if (reset) renderSettings();
    } else { msg.className = "err"; msg.textContent = d.error; }
  }

  const getValue = (obj, key) => key.split(".").reduce((o, p) => (o ? o[p] : undefined), obj);

  function control(spec, value, onChange) {
    const id = "f-" + spec.key.replace(/\W/g, "-");
    if (spec.type === "enum") {
      const sel = document.createElement("select"); sel.id = id;
      spec.values.forEach((v) => {
        const o = document.createElement("option"); o.value = JSON.stringify(v);
        const labels = spec.value_labels && spec.value_labels[st.lang];
        o.textContent = labels ? labels[v] : (v === "" ? "—" : v);
        sel.append(o);
      });
      sel.value = JSON.stringify(value);
      sel.onchange = () => onChange(JSON.parse(sel.value));
      return sel;
    }
    if (spec.type === "bool") {
      const i = document.createElement("input"); i.type = "checkbox"; i.id = id; i.checked = !!value;
      i.onchange = () => onChange(i.checked); return i;
    }
    if (spec.type === "mix") {
      const box = document.createElement("div"); box.className = "mix";
      const inputs = {};
      ["easy", "medium", "hard"].forEach((k) => {
        const l = document.createElement("label"); l.textContent = T(k);
        const i = document.createElement("input"); i.type = "number"; i.step = "0.05"; i.min = 0; i.max = 1; i.value = value[k];
        inputs[k] = i; l.append(i); box.append(l);
        i.onchange = () => onChange({ easy: +inputs.easy.value, medium: +inputs.medium.value, hard: +inputs.hard.value });
      });
      return box;
    }
    if (spec.type === "json") {
      const t = document.createElement("textarea"); t.id = id; t.value = JSON.stringify(value, null, 1);
      t.onchange = () => { try { onChange(JSON.parse(t.value)); } catch (e) { onChange(null); } };
      return t;
    }
    const i = document.createElement("input"); i.id = id;
    i.type = spec.type === "int" || spec.type === "float" ? "number" : "text";
    if (spec.type === "float") i.step = "0.05";
    i.value = spec.type === "int_list" ? value.join(", ") : value;
    i.onchange = () => {
      if (spec.type === "int") onChange(parseInt(i.value, 10));
      else if (spec.type === "float") onChange(parseFloat(i.value));
      else if (spec.type === "int_list") onChange(i.value.split(",").map((x) => parseInt(x, 10)));
      else onChange(i.value);
    };
    return i;
  }

  function renderSettings() {
    const form = $("settings"), d = st.settings;
    form.innerHTML = "";
    if (!d) return;
    Object.entries(d.schema.groups).forEach(([gid, names]) => {
      const fs = document.createElement("fieldset"), lg = document.createElement("legend");
      lg.textContent = names[st.lang]; fs.append(lg);
      d.schema.settings.filter((s) => s.group === gid).forEach((spec) => {
        const row = document.createElement("div"); row.className = "field";
        const left = document.createElement("div"), label = document.createElement("label"), help = document.createElement("p");
        label.textContent = spec.label[st.lang]; label.htmlFor = "f-" + spec.key.replace(/\W/g, "-");
        help.textContent = spec.help[st.lang]; left.append(label, help);
        const right = document.createElement("div"); right.className = "control";
        const state = document.createElement("div"); state.className = "state";
        const msg = document.createElement("span"), reset = document.createElement("button");
        reset.type = "button"; reset.textContent = T("reset");
        reset.onclick = () => saveSetting(spec.key, null, state, true);
        state.append(msg, reset);
        right.append(control(spec, getValue(d.values, spec.key), (v) => saveSetting(spec.key, v, state, false)), state);
        row.append(left, right); fs.append(row);
      });
      form.append(fs);
    });
  }

  /* ---------- wiring ---------- */
  function applyTheme() {
    const root = document.documentElement;
    if (st.theme === "auto") root.removeAttribute("data-theme"); else root.dataset.theme = st.theme;
    const dark = st.theme === "dark" || (st.theme === "auto" && matchMedia("(prefers-color-scheme: dark)").matches);
    office.setNight(dark);
  }
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", applyTheme);

  document.querySelectorAll("[data-tab]").forEach((b) => {
    b.onclick = () => {
      document.querySelectorAll("[data-tab]").forEach((x) => x.setAttribute("aria-selected", String(x === b)));
      $("office-tab").hidden = b.dataset.tab !== "office";
      $("settings-tab").hidden = b.dataset.tab !== "settings";
    };
  });
  $("lang").onclick = () => { st.lang = st.lang === "tr" ? "en" : "tr"; store.set("forge.lang", st.lang); applyStaticText(); };
  document.querySelectorAll("[data-theme-choice]").forEach((b) => {
    b.onclick = () => { st.theme = b.dataset.themeChoice; store.set("forge.theme", st.theme); applyTheme(); applyStaticText(); };
  });
  $("run").onchange = async (ev) => {
    st.selected = ev.target.value;
    if (st.selected === "auto") { st.mode = ""; await pollRuns(); return; }
    const r = st.runs.find((x) => x.id === st.selected);
    await loadRun(st.selected, r && isActive(r) ? "live" : "past");
  };

  (async function start() {
    applyTheme();
    try { st.settings = await getJson("/api/settings"); office.speech = st.settings.values.studio_speech_bubbles; } catch (e) { /* shown empty */ }
    applyStaticText();
    await pollRuns();
    if (st.mode === "demo" && !demoTimer) startDemo();
    setInterval(pollEvents, 1500);
    setInterval(pollRuns, 5000);
    setInterval(refreshCost, 20000);
  })();
})();
