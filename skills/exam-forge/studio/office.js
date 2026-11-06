/* The pixel office: floor, desks, glass room, bots and task bubbles, drawn in code. */
(function () {
  const W = 360, H = 262, CELL = 4;
  const COLS = W / CELL, ROWS = H / CELL;
  const SPEED = 42; // units per second
  const FRAME_MS = 30; // about 30 frames a second: a small pixel canvas gains nothing from more, and costs twice the CPU
  const CATCH_UP_MS = 1000; // a longer pause means nobody was watching
  const reduced = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const EDITOR_COLS = [32, 76, 120], ROWS_Y = [108, 154, 200, 246], EDITOR_ROWS = ROWS_Y;
  const REVIEW_COLS = [240, 284, 328], REVIEW_ROWS = ROWS_Y;
  const SEAT_DX = -5;
  const ROOM = { x: 126, y: 6, w: 108, h: 70, door: 34 };
  const CHIEF_DESK = { x: 160, y: 50 }, SCREEN_DESK = { x: 180, y: 158 };
  const PRINTER = { x: 268, y: 70 };
  const DISPLAY = { x: 14, y: 8, w: 96, h: 42 };
  const BOARD_SPOT = { x: 62, y: 66 }, PRINT_SPOT = { x: 268, y: 84 };
  const COMPUTER = { x: 192, y: 26, w: 32, h: 21 };
  const THEMES = {
    day: {
      wall: "#e9d9b4", trim: "#c6a877", shadow: "#a98a5e", floorA: "#d8b583", floorB: "#cfaa78", seam: "#bb9566", ink: "#2a2533",
      deskTop: "#c8935f", desk: "#a9744a", deskDark: "#7a5233", pc: "#d7d3c4", pcDark: "#8f8b80", off: "#343a52", key: "#efe9d9", tag: "#fff3d6",
      glass: "rgba(150,225,240,.2)", glassEdge: "#5fb6cc", carpet: "#9fb7c9", sky: "#8fd6ee", cloud: "#ffffff", frame: "#8a6b47",
      board: "#fbf8ec", boardEdge: "#6e7890", track: "#d9d4c4", bubble: "#fffdf0", bubbleInk: "#2a2533", shelf: "#8a5f3c", rug: "#b9d4c8",
    },
    night: {
      wall: "#2f3158", trim: "#25264a", shadow: "#1b1c38", floorA: "#383a64", floorB: "#323459", seam: "#2a2c4e", ink: "#f4f0e4",
      deskTop: "#7c6488", desk: "#5e4a6c", deskDark: "#423450", pc: "#6f7396", pcDark: "#474a68", off: "#141627", key: "#8d91b0", tag: "#f4f0e4",
      glass: "rgba(90,170,235,.14)", glassEdge: "#73c8ee", carpet: "#3f5478", sky: "#141a44", cloud: "#f6e8a8", frame: "#1b1c38",
      board: "#20233f", boardEdge: "#8d91b0", track: "#3b3e62", bubble: "#fffdf0", bubbleInk: "#2a2533", shelf: "#3c2f46", rug: "#34506a",
    },
  };

  class Office {
    constructor(canvas) {
      this.c = canvas;
      canvas.width = W; canvas.height = H;
      this.g = canvas.getContext("2d");
      this.g.imageSmoothingEnabled = false;
      this.night = true; this.speech = true; this.phrase = () => ""; this.mark = null;
      this.actors = []; this.desks = []; this.fx = []; this.confetti = []; this.talkers = 0; this.talkT = 1;
      this.board = []; this.boardTitle = ""; this.printing = 0; this.last = 0; this.tick = 0; this.seed = 1;
      this.setup({ screener: "haiku", chief_editor: "opus" }, 3, 3);
      requestAnimationFrame((t) => this.frame(t));
    }

    setup(team, nEditors, nReviewers) {
      this.team = team;
      this.actors = []; this.desks = []; this.fx = []; this.confetti = []; this.mark = null;
      nEditors = Math.max(1, Math.min(nEditors, 12));
      nReviewers = Math.max(1, Math.min(nReviewers, 12));
      this.editors = []; this.reviewers = [];
      const addDesk = (x, y, tag, wide) => { const d = { x, y, tag, wide, stack: 0 }; this.desks.push(d); return d; };
      const addActor = (role, desk, seed, index, n) => {
        const a = {
          role, n, desk, look: Sprites.makeLook(seed, role, index), x: this.seatX(desk), y: desk.y - 3, dir: "down", pose: "sit",
          q: [], work: null, carrying: false, speech: null, idle: 5 + Math.random() * 14, cheer: 0, task: null, log: [],
        };
        desk.owner = a;
        this.actors.push(a); return a;
      };
      for (let i = 0; i < nEditors; i++) {
        const d = addDesk(EDITOR_COLS[i % 3], EDITOR_ROWS[Math.floor(i / 3)], "E" + (i + 1));
        this.editors.push(addActor("editor", d, this.seed * 31 + i * 7 + 1, i + this.seed, i + 1));
      }
      for (let i = 0; i < nReviewers; i++) {
        const d = addDesk(REVIEW_COLS[i % 3], REVIEW_ROWS[Math.floor(i / 3)], "R" + (i + 1));
        this.reviewers.push(addActor("reviewer", d, this.seed * 37 + i * 11 + 101, i + this.seed, i + 1));
      }
      this.screener = team.screener ? addActor("screener", addDesk(SCREEN_DESK.x, SCREEN_DESK.y, "S"), this.seed * 41 + 201, 0, 0) : null;
      this.chief = addActor("chief", addDesk(CHIEF_DESK.x, CHIEF_DESK.y, "CE", true), this.seed * 43 + 301, 0, 0);
      // every workstation is always in the office; unused ones stay dark
      for (const cols of [EDITOR_COLS, REVIEW_COLS]) {
        for (const y of ROWS_Y) for (const x of cols) {
          if (!this.desks.some((d) => d.x === x && d.y === y)) addDesk(x, y, "");
        }
      }
      const homes = [{ x: 180, y: 206 }, { x: 180, y: 232 }];
      this.drones = ["cat", "bunny"].map((kind, i) => ({
        role: "drone", kind, drone: true, desk: homes[i], x: homes[i].x, y: homes[i].y, look: { height: 0 },
        dir: "down", pose: "stand", q: [], work: null, carrying: false, speech: null, idle: 2 + i * 3, cheer: 0,
      }));
      this.actors.push(...this.drones);
      this.blocked = this.buildGrid();
      this.bg = null;
    }

    reseed() { this.seed += 1; }
    setNight(n) { this.night = n; this.bg = null; }
    staff() { return [this.chief, ...this.editors].concat(this.screener ? [this.screener] : [], this.reviewers); }

    /* ---------- pathfinding ---------- */
    buildGrid() {
      const grid = new Uint8Array(COLS * ROWS);
      const block = (x, y, w, h) => {
        for (let cy = Math.floor((y - 2) / CELL); cy <= Math.floor((y + h + 2) / CELL); cy++)
          for (let cx = Math.floor((x - 2) / CELL); cx <= Math.floor((x + w + 2) / CELL); cx++)
            if (cx >= 0 && cy >= 0 && cx < COLS && cy < ROWS) grid[cy * COLS + cx] = 1;
      };
      const r = ROOM, gap = (r.w - r.door) / 2;
      block(0, 0, r.x - 2, 54); block(r.x + r.w + 2, 0, W, 54); block(r.x, 0, r.w, r.y);
      block(r.x - 2, r.y, 1, r.h); block(r.x + r.w, r.y, 1, r.h);
      block(r.x, r.y + r.h, gap - 3, 1); block(r.x + r.w - gap + 3, r.y + r.h, gap - 3, 1);
      this.desks.forEach((d) => { const hw = d.wide ? 22 : 17; block(d.x - hw, d.y, hw * 2, 7); });
      block(COMPUTER.x, COMPUTER.y, COMPUTER.w, COMPUTER.h + 5);
      block(PRINTER.x - 11, PRINTER.y - 8, 22, 12);
      return grid;
    }

    free(cx, cy) { return cx >= 0 && cy >= 0 && cx < COLS && cy < ROWS && !this.blocked[cy * COLS + cx]; }

    nearest(cx, cy) {
      for (let r = 0; r < 12; r++)
        for (let dy = -r; dy <= r; dy++)
          for (let dx = -r; dx <= r; dx++)
            if (this.free(cx + dx, cy + dy)) return [cx + dx, cy + dy];
      return [cx, cy];
    }

    route(fx, fy, tx, ty) {
      const [sx, sy] = this.nearest(Math.round(fx / CELL), Math.round(fy / CELL));
      const [gx, gy] = this.nearest(Math.round(tx / CELL), Math.round(ty / CELL));
      const prev = new Int32Array(COLS * ROWS).fill(-1);
      const start = sy * COLS + sx, goal = gy * COLS + gx;
      prev[start] = start;
      const queue = [start];
      for (let i = 0; i < queue.length && prev[goal] < 0; i++) {
        const cur = queue[i], cx = cur % COLS, cy = (cur - cx) / COLS;
        for (const [dx, dy] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
          const nx = cx + dx, ny = cy + dy, n = ny * COLS + nx;
          if (this.free(nx, ny) && prev[n] < 0) { prev[n] = cur; queue.push(n); }
        }
      }
      const pts = [{ x: tx, y: ty }];
      if (prev[goal] < 0) return pts;
      let walk = goal;
      while (walk !== start) {
        const cx = walk % COLS, cy = (walk - cx) / COLS;
        pts.unshift({ x: cx * CELL, y: cy * CELL });
        walk = prev[walk];
      }
      return pts;
    }

    /* ---------- commands ---------- */
    go(a, x, y) { a.q.push({ walk: { x, y } }); }
    wait(a, s) { a.q.push({ wait: s }); }
    run(a, fn) { a.q.push({ fn }); }
    seatX(d) { return d.wide ? d.x : d.x + SEAT_DX; }
    home(a) { this.go(a, this.seatX(a.desk), a.desk.y - 3); this.run(a, () => { a.pose = "sit"; a.dir = "down"; }); }
    say(a, text, s) { if (a && text) a.speech = { text, t: s || 2.6 }; }
    float(x, y, text, color) { this.fx.push({ x, y, text, color, t: 1.8 }); }

    deliver(a, target, after) {
      a.work = null;
      this.run(a, () => { a.carrying = true; });
      this.go(a, target.x, target.y + 13);
      this.run(a, () => { a.carrying = false; a.dir = "up"; target.stack = Math.min(target.stack + 1, 5); if (after) after(); });
      this.wait(a, 0.4);
      this.home(a);
    }

    cheerAll(text) {
      this.staff().forEach((a) => { a.work = null; a.cheer = 3.2; a.q = []; });
      this.say(this.chief, text, 3.2);
      if (reduced()) return;
      for (let i = 0; i < 80; i++) {
        this.confetti.push({
          x: 80 + Math.random() * 200, y: 60 + Math.random() * 30, vx: (Math.random() - 0.5) * 70, vy: -40 - Math.random() * 50,
          c: ["#3b82f6", "#22c55e", "#f97316", "#e5484d", "#f6d24a", "#ec4899"][i % 6], t: 2.5 + Math.random(),
        });
      }
    }

    settle() {
      this.actors.forEach((a) => {
        if (a.drone) return;
        a.work = null; a.q = []; a.carrying = false; a.x = this.seatX(a.desk); a.y = a.desk.y - 3; a.pose = "sit"; a.dir = "down"; a.speech = null;
      });
    }

    locate(a) { this.mark = { a, t: 5 }; }

    /* canvas click: what is under this point */
    hit(px, py) {
      const { x, y, w, h } = COMPUTER;
      if (px >= x - 3 && px <= x + w + 3 && py >= y - 3 && py <= y + h + 3) return { computer: true };
      const a = this.staff().find((b) => Math.abs(px - b.x) < b.look.r + 2 && py > b.y - b.look.r * 2 - 4 && py < b.y + 3);
      return a ? { actor: a } : null;
    }

    /* ---------- loop ---------- */
    wake() {
      if (this.asleep) { this.asleep = false; requestAnimationFrame((t) => this.frame(t)); }
    }

    frame(t) {
      if (!this.c.clientWidth) { this.asleep = true; return; }  // on another tab the canvas has no size
      requestAnimationFrame((n) => this.frame(n));
      const gap = t - this.last;
      if (gap < FRAME_MS) return;
      const dt = Math.min(gap / 1000 || 0, 0.1);
      this.last = t; this.tick += dt;
      if (gap > CATCH_UP_MS) this.catchUp(gap / 1000); else this.update(dt);
      this.draw();
    }

    /* Nothing updates on a hidden tab, so the end of what happened meanwhile is applied at once instead of
       replayed late. Commands run in their order, so a.work and the desk stacks end as they would have. */
    catchUp(seconds) {
      for (let n = 0; n < 10000 && this.actors.some((a) => a.q.length); n++) {
        for (const a of this.actors) {
          const cmd = a.q.shift();
          if (cmd && cmd.walk) { a.x = cmd.walk.x; a.y = cmd.walk.y; a.pose = "stand"; } else if (cmd && cmd.fn) cmd.fn();
        }
      }
      this.update(seconds);
    }

    chatter(dt) {
      if ((this.talkT -= dt) > 0 || !this.speech) return;
      this.talkT = 1.1;
      const talking = this.staff().filter((a) => a.speech);
      if (talking.length >= 3) return;
      const busy = this.staff().filter((a) => a.work && !a.speech && !a.q.length
        && talking.every((b) => Math.abs(b.x - a.x) > 92 || Math.abs(b.y - a.y) > 30));
      if (!busy.length) return;
      const a = busy[Math.floor(Math.random() * busy.length)];
      this.say(a, this.phrase(a), 2.8);
    }

    update(dt) {
      this.chatter(dt);
      if (this.mark && (this.mark.t -= dt) <= 0) this.mark = null;
      for (const a of this.actors) {
        if (a.speech && (a.speech.t -= dt) <= 0) a.speech = null;
        if (a.cheer > 0) { a.cheer -= dt; a.pose = "cheer"; if (a.cheer <= 0) a.pose = "sit"; continue; }
        const cmd = a.q[0];
        if (cmd) {
          const speed = SPEED * (1 + Math.min(a.q.length, 8) * 0.25);
          if (cmd.walk) {
            if (reduced()) { a.x = cmd.walk.x; a.y = cmd.walk.y; a.q.shift(); continue; }
            if (!cmd.pts) cmd.pts = this.route(a.x, a.y, cmd.walk.x, cmd.walk.y);
            const p = cmd.pts[0];
            const dx = p.x - a.x, dy = p.y - a.y, dist = Math.hypot(dx, dy), move = speed * dt;
            a.pose = "walk";
            if (Math.abs(dx) > Math.abs(dy)) a.dir = dx > 0 ? "right" : "left"; else if (dist) a.dir = dy > 0 ? "down" : "up";
            if (dist <= move) { a.x = p.x; a.y = p.y; cmd.pts.shift(); if (!cmd.pts.length) { a.q.shift(); a.pose = "stand"; } }
            else { a.x += dx / dist * move; a.y += dy / dist * move; }
          } else if (cmd.wait != null) {
            if (a.pose === "walk") a.pose = "stand";
            if ((cmd.wait -= dt) <= 0) a.q.shift();
          } else { cmd.fn(); a.q.shift(); }
          continue;
        }
        if (a.work) { a.pose = a.work; a.dir = "down"; }
        else if (a.drone) {
          if (!reduced() && (a.idle -= dt) <= 0) {
            a.idle = 3 + Math.random() * 7;
            const cols = EDITOR_COLS.concat(REVIEW_COLS), col = cols[Math.floor(Math.random() * cols.length)];
            this.go(a, col + 22, 84 + Math.random() * 160);
            this.run(a, () => { a.pose = "stand"; });
          }
        } else if (!reduced() && (a.idle -= dt) <= 0) {
          a.idle = 14 + Math.random() * 26;
          const free = this.desks.filter((d) => !d.owner);
          const spot = free[Math.floor(Math.random() * free.length)];
          this.go(a, this.seatX(spot), spot.y - 3);
          this.run(a, () => { a.dir = "down"; a.pose = "sit"; });
          this.wait(a, 2 + Math.random() * 3);
          this.home(a);
        }
      }
      this.fx = this.fx.filter((f) => (f.t -= dt) > 0);
      this.confetti = this.confetti.filter((p) => {
        p.t -= dt; p.vy += 90 * dt; p.x += p.vx * dt; p.y += p.vy * dt; return p.t > 0 && p.y < H;
      });
      if (this.printing > 0) this.printing -= dt;
    }

    /* ---------- drawing ---------- */
    get P() { return THEMES[this.night ? "night" : "day"]; }
    rect(x, y, w, h, c, g) { g = g || this.g; g.fillStyle = c; g.fillRect(Math.round(x), Math.round(y), Math.round(w), Math.round(h)); }
    box(x, y, w, h, c, g) { // one-pixel outline
      this.rect(x, y, w, 1, c, g); this.rect(x, y + h - 1, w, 1, c, g); this.rect(x, y, 1, h, c, g); this.rect(x + w - 1, y, 1, h, c, g);
    }
    label(str, x, y, c) { Sprites.text(this.g, str, x, y, c); }

    background() {
      const cv = document.createElement("canvas");
      cv.width = W; cv.height = H;
      const g = cv.getContext("2d"), P = this.P, r = (x, y, w, h, c) => this.rect(x, y, w, h, c, g);
      // floor tiles
      for (let y = 56; y < H; y += 12) for (let x = (y % 24 ? 0 : -12); x < W; x += 24) {
        r(x, y, 24, 12, Math.floor(x / 24 + y / 12) % 2 ? P.floorA : P.floorB);
        r(x, y, 24, 1, P.seam); r(x, y, 1, 12, P.seam);
      }
      // wall
      r(0, 0, W, 50, P.wall); r(0, 50, W, 4, P.trim); r(0, 54, W, 2, P.shadow);
      // bookshelf
      r(300, 8, 52, 42, P.shelf);
      const books = ["#d65a4a", "#4a7fe6", "#e8c547", "#5aa469", "#8d5be0", "#e0953a"];
      for (let row = 0; row < 3; row++) {
        r(302, 21 + row * 14, 48, 1, P.shadow);
        for (let i = 0, x = 303; x < 348; i++) {
          const w = 2 + ((i * 7 + row * 3) % 3), h = 7 + ((i * 5 + row) % 4);
          r(x, 21 + row * 14 - h, w, h, books[(i + row * 2) % 6]); x += w + 1;
        }
      }
      // plants
      [[8, 74], [352, 74]].forEach(([x, y]) => {
        r(x - 4, y, 8, 7, "#a8603a"); r(x - 5, y, 10, 2, "#8f4e2c");
        [[-5, -7], [-1, -11], [3, -7], [-3, -4], [1, -5]].forEach(([dx, dy]) => r(x + dx, y + dy, 3, 6, "#4f9a5c"));
      });
      // glass room
      const q = ROOM, gap = (q.w - q.door) / 2;
      r(q.x, q.y, q.w, q.h, P.carpet);
      for (let y = q.y; y < q.y + q.h; y += 6) r(q.x, y, q.w, 1, P.glass);
      r(q.x, q.y, q.w, 2, P.glassEdge); r(q.x, q.y, 2, q.h, P.glassEdge); r(q.x + q.w - 2, q.y, 2, q.h, P.glassEdge);
      r(q.x, q.y + q.h - 2, gap, 2, P.glassEdge); r(q.x + q.w - gap, q.y + q.h - 2, gap, 2, P.glassEdge);
      // rug under the screener
      r(SCREEN_DESK.x - 28, SCREEN_DESK.y - 28, 56, 46, P.rug);
      this.box(SCREEN_DESK.x - 26, SCREEN_DESK.y - 26, 52, 42, P.seam, g);
      return cv;
    }

    desk(d) {
      const P = this.P, hw = d.wide ? 22 : 17, owner = d.owner;
      this.rect(d.x - hw + 1, d.y + 7, 2, 5, P.deskDark); this.rect(d.x + hw - 3, d.y + 7, 2, 5, P.deskDark);
      this.rect(d.x - hw, d.y, hw * 2, 5, P.deskTop); this.rect(d.x - hw, d.y + 4, hw * 2, 3, P.desk);
      this.rect(d.x - hw, d.y + 7, hw * 2, 1, P.deskDark);
      if (!d.wide) {
        const mx = d.x + 2, my = d.y - 11, mw = 14, mh = 10;
        this.rect(mx + 5, my + mh, 4, 1, P.pcDark); this.rect(mx + 3, my + mh + 1, 8, 1, P.pcDark);
        this.rect(mx, my, mw, mh, P.pc); this.box(mx, my, mw, mh, P.pcDark);
        this.rect(mx + 2, my + 2, mw - 4, mh - 4, P.off);
        if (owner && owner.work) {
          const scroll = reduced() ? 0 : Math.floor(this.tick * 3) % 3;
          [0, 1, 2].forEach((i) => this.rect(mx + 3, my + 3 + ((i + scroll) % 3) * 1.5, 7 - ((i + scroll) % 3) * 2, 1, owner.look.color));
        } else if (owner && owner.pose === "sit") this.rect(mx + 3, my + 3, 2, 1, P.key);
        this.rect(d.x - 10, d.y + 1, 9, 2, P.key); this.rect(d.x - 10, d.y + 2, 9, 1, P.pcDark);
      }
      for (let i = 0; i < d.stack; i++) this.rect(d.x - hw + 2, d.y + 1 - i, 6, 1, i % 2 ? "#e9e4d2" : "#fffdf0");
      if (d.tag) this.label(d.tag, d.x - Sprites.textWidth(d.tag) / 2, d.y + 9, P.ink);
    }

    computer() {
      const P = this.P, { x, y, w, h } = COMPUTER, blink = reduced() || Math.floor(this.tick * 2) % 2 === 0;
      this.rect(x + w / 2 - 2, y + h, 4, 2, P.pcDark); this.rect(x + w / 2 - 8, y + h + 2, 16, 2, P.pcDark);
      this.rect(x, y, w, h, P.pc); this.box(x, y, w, h, P.pcDark);
      this.rect(x + 2, y + 2, w - 4, h - 4, P.off);
      this.staff().slice(0, 12).forEach((a, i) => {
        this.rect(x + 4 + (i % 4) * 6, y + 4 + Math.floor(i / 4) * 4, 4, 2, a.work ? a.look.color : P.pcDark);
      });
      if (blink) { // corner brackets invite a click
        const c = P.glassEdge;
        [[x - 3, y - 3, 1, 1], [x + w + 2, y - 3, -1, 1], [x - 3, y + h + 2, 1, -1], [x + w + 2, y + h + 2, -1, -1]].forEach(([cx, cy, dx, dy]) => {
          this.rect(Math.min(cx, cx + dx * 3), cy, 4, 1, c); this.rect(cx, Math.min(cy, cy + dy * 3), 1, 4, c);
        });
      }
    }

    printer() {
      const P = this.P, { x, y } = PRINTER;
      this.rect(x - 11, y - 12, 22, 16, P.pc); this.box(x - 11, y - 12, 22, 16, P.pcDark);
      this.rect(x - 8, y - 8, 16, 2, P.off);
      this.rect(x + 6, y - 1, 2, 2, this.printing > 0 ? "#4fd071" : "#e5484d");
      if (this.printing > 0) { const h = Math.floor(this.tick * 8) % 7; this.rect(x - 5, y - 6 + h, 10, 7 - h, "#fffdf0"); }
    }

    display() {
      const P = this.P, { x, y, w, h } = DISPLAY;
      this.rect(x - 2, y - 2, w + 4, h + 4, P.boardEdge); this.rect(x, y, w, h, P.board);
      this.rect(x + 30, y + h + 2, 36, 2, P.boardEdge);
      this.label(this.boardTitle.slice(0, 15), x + 3, y + 3, this.night ? P.ink : P.bubbleInk);
      this.board.slice(0, 12).forEach((b, i) => {
        const col = i % 2, by = y + 14 + Math.floor(i / 2) * 4, bx = x + 3 + col * 46;
        this.rect(bx, by, 43, 2, P.track);
        const color = b.stage === "done" ? "#4fd071" : b.stage === "fix" ? "#e5484d" : b.stage === "review" ? "#4a7fe6" : "#f0a33a";
        this.rect(bx, by, Math.max(2, 43 * b.share), 2, color);
      });
    }

    bubble(a) {
      const P = this.P, text = a.speech.text, w = Sprites.textWidth(text) + 6, h = 11;
      const x = Math.round(Math.max(1, Math.min(W - w - 1, a.x - w / 2))), y = Math.max(1, Math.round(a.y - a.look.r * 2 - 16));
      this.rect(x + 1, y, w - 2, h, P.bubble); this.rect(x, y + 1, w, h - 2, P.bubble);
      this.rect(x + 1, y - 1, w - 2, 1, P.bubbleInk); this.rect(x + 1, y + h, w - 2, 1, P.bubbleInk);
      this.rect(x - 1, y + 1, 1, h - 2, P.bubbleInk); this.rect(x + w, y + 1, 1, h - 2, P.bubbleInk);
      const tx = Math.round(a.x);
      this.rect(tx - 2, y + h, 4, 1, P.bubble); this.rect(tx - 1, y + h + 1, 2, 1, P.bubble); this.rect(tx - 1, y + h + 2, 2, 1, P.bubbleInk);
      this.label(text, x + 3, y + 2, P.bubbleInk);
    }

    draw() {
      const g = this.g;
      if (!this.bg) this.bg = this.background();
      g.drawImage(this.bg, 0, 0);
      this.windows();
      this.display();
      const items = [];
      this.desks.forEach((d) => items.push({ y: d.y + 4, draw: () => this.desk(d) }));
      items.push({ y: COMPUTER.y + COMPUTER.h, draw: () => this.computer() });
      items.push({ y: PRINTER.y + 6, draw: () => this.printer() });
      this.actors.forEach((a) => items.push({
        y: a.y,
        draw: () => (a.drone
          ? Sprites.drawPet(g, a.x, a.y, a.kind, { dir: a.dir, pose: a.pose })
          : Sprites.drawPerson(g, a.x, a.y, a.look, { dir: a.dir, pose: a.pose, carrying: a.carrying })),
      }));
      items.sort((p, q) => p.y - q.y).forEach((i) => i.draw());
      if (this.mark) {
        const a = this.mark.a, R = a.look.r + 5, c = this.P.glassEdge, X = Math.round(a.x), Y = Math.round(a.y) - a.look.r;
        [[-1, -1], [1, -1], [-1, 1], [1, 1]].forEach(([dx, dy]) => {
          this.rect(X + dx * R - (dx > 0 ? 3 : 0), Y + dy * R, 4, 1, c); this.rect(X + dx * R, Y + dy * R - (dy > 0 ? 3 : 0), 1, 4, c);
        });
      }
      if (this.speech) this.actors.forEach((a) => a.speech && this.bubble(a));
      this.fx.forEach((f) => {
        const y = Math.round(f.y - (1.8 - f.t) * 9), w = Sprites.textWidth(f.text) + 6, x = Math.round(f.x - w / 2);
        this.rect(x, y, w, 11, f.color); this.rect(x + 1, y + 1, w - 2, 9, "#1a1c2c");
        this.label(f.text, x + 3, y + 2, f.color);
      });
      this.confetti.forEach((p) => this.rect(p.x, p.y, 2, 2, p.c));
    }

    windows() {
      const P = this.P;
      [240, 268].forEach((x, i) => {
        this.rect(x - 2, 6, 24, 34, P.frame); this.rect(x, 8, 20, 30, P.sky);
        this.rect(x + 9, 8, 2, 30, P.frame); this.rect(x, 22, 20, 2, P.frame);
        if (this.night) { this.rect(x + 4, 12, 1, 1, "#fff"); this.rect(x + 14, 28, 1, 1, "#fff"); if (i) this.rect(x + 12, 11, 5, 5, P.cloud); }
        else { const c = Math.floor(this.tick) % 2; this.rect(x + 2 + c, 13, 6, 2, P.cloud); this.rect(x + 12, 29, 6, 2, P.cloud); }
      });
    }
  }

  Office.spots = { BOARD_SPOT, PRINT_SPOT, PRINTER, W, H };
  window.Office = Office;
})();
