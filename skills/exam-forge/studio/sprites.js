/* Pixel bots rasterized in code at low resolution. No image files. */
(function () {
  const COLORS = ["#f0722e", "#57b8a5", "#8b5cf6", "#3b82f6", "#ec4899", "#f59f3a", "#5cc67a", "#8c6340", "#7d7d84", "#e5484d"];
  const SHAPES = ["circle", "drop", "tri", "cloud", "box", "squircle", "pill", "bean"];
  const EYES = ["tall", "tilt", "dash", "small", "wide"];
  const TILT = { box: -0.07, squircle: 0.32, bean: 0.12 };
  const EYE_Y = { drop: 0.16, tri: 0.3 };
  const still = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* index keeps teammates visibly different */
  function makeLook(seed, role, index) {
    const i = index || 0, phase = (seed % 97) / 7;
    if (role === "chief") return { role, shape: "circle", color: "#f4f4f5", r: 14, eyes: "tilt", ink: "#111113", outline: true, crown: true, phase, height: 0 };
    if (role === "screener") return { role, shape: "cloud", color: "#5cc67a", r: 13, eyes: "tall", phase, height: 0 };
    const offset = role === "reviewer" ? 4 : 0;
    return {
      role, shape: SHAPES[(i + offset + 1) % SHAPES.length], color: COLORS[(i + offset) % COLORS.length], r: 13,
      eyes: EYES[(i + offset) % EYES.length], phase, height: 0,
    };
  }

  /* unit-sized body around 0,0; bottom sits near y = 1 */
  function bodyPath(g, shape) {
    g.beginPath();
    const blob = (list) => list.forEach(([x, y, s]) => { g.moveTo(x + s, y); g.arc(x, y, s, 0, Math.PI * 2); });
    switch (shape) {
      case "drop":
        g.moveTo(-0.13, -1.1);
        g.quadraticCurveTo(0, -1.26, 0.13, -1.1);
        g.bezierCurveTo(0.4, -0.78, 1, -0.4, 1, 0.14);
        g.bezierCurveTo(1, 0.72, 0.55, 1, 0, 1);
        g.bezierCurveTo(-0.55, 1, -1, 0.72, -1, 0.14);
        g.bezierCurveTo(-1, -0.4, -0.4, -0.78, -0.13, -1.1);
        break;
      case "tri":
        g.moveTo(0, -1.08);
        g.bezierCurveTo(0.26, -1.08, 0.38, -0.84, 0.52, -0.56);
        g.bezierCurveTo(0.72, -0.16, 1.18, 0.5, 1.18, 0.74);
        g.bezierCurveTo(1.18, 1, 0.92, 1, 0.62, 1);
        g.lineTo(-0.62, 1);
        g.bezierCurveTo(-0.92, 1, -1.18, 1, -1.18, 0.74);
        g.bezierCurveTo(-1.18, 0.5, -0.72, -0.16, -0.52, -0.56);
        g.bezierCurveTo(-0.38, -0.84, -0.26, -1.08, 0, -1.08);
        break;
      case "cloud": blob([[-0.56, 0.22, 0.62], [0.56, 0.22, 0.64], [-0.3, -0.34, 0.58], [0.34, -0.3, 0.6], [0, 0.32, 0.66]]); break;
      case "bean": blob([[-0.34, -0.34, 0.74], [0.34, 0.26, 0.74], [0.4, -0.36, 0.5], [-0.4, 0.42, 0.52]]); break;
      case "box": g.roundRect(-1, -0.96, 2, 1.94, 0.24); break;
      case "squircle": g.roundRect(-0.84, -0.84, 1.68, 1.68, 0.42); break;
      case "pill": g.roundRect(-1.22, -0.76, 2.44, 1.52, 0.76); break;
      default: g.arc(0, 0, 1, 0, Math.PI * 2);
    }
    g.closePath();
  }

  function eye(g, x, y, w, h, angle) {
    g.save(); g.translate(x, y); g.rotate(angle);
    g.beginPath(); g.roundRect(-w / 2, -h / 2, w, h, Math.min(w, h) / 2); g.fill();
    g.restore();
  }

  function drawEyes(g, l, lookX, blink, happy) {
    const y = (EYE_Y[l.shape] || -0.04), x = lookX * 0.2;
    g.fillStyle = l.ink || "#fff"; g.strokeStyle = l.ink || "#fff";
    if (happy) {
      g.lineWidth = 0.13; g.lineCap = "round";
      [-0.34, 0.34].forEach((ex) => { g.beginPath(); g.arc(ex + x, y + 0.1, 0.2, Math.PI * 1.1, Math.PI * 1.9); g.stroke(); });
      return;
    }
    if (blink) { eye(g, -0.34 + x, y, 0.3, 0.09, 0); eye(g, 0.34 + x, y, 0.3, 0.09, 0); return; }
    switch (l.eyes) {
      case "tilt": eye(g, -0.3 + x, y, 0.19, 0.5, 0.42); eye(g, 0.3 + x, y - 0.05, 0.19, 0.5, 0.42); break;
      case "dash": eye(g, -0.32 + x, y - 0.02, 0.48, 0.16, 0.22); eye(g, 0.32 + x, y + 0.1, 0.48, 0.16, 0.22); break;
      case "small": eye(g, -0.2 + x, y + 0.2, 0.15, 0.3, 0); eye(g, 0.2 + x, y + 0.2, 0.15, 0.3, 0); break;
      case "wide": eye(g, -0.28 + x, y, 0.3, 0.46, 0); eye(g, 0.28 + x, y, 0.3, 0.46, 0); break;
      default: eye(g, -0.26 + x, y, 0.24, 0.56, 0); eye(g, 0.26 + x, y, 0.24, 0.56, 0);
    }
  }

  function darken(hex, k) {
    const n = parseInt(hex.slice(1), 16);
    return [(n >> 16) * k | 0, ((n >> 8) & 255) * k | 0, (n & 255) * k | 0];
  }

  /* Rasterize a body at low resolution: no smoothing, one dark outline, one shade band. */
  const cache = new Map();
  function sprite(l, sy, lookX, blink, happy, back) {
    const key = [l.shape, l.color, l.eyes, l.r, sy, lookX, blink, happy, back].join("|");
    if (cache.has(key)) return cache.get(key);
    const S = Math.ceil(l.r * 3.4), sx = 2 - sy;
    const paint = (fn) => {
      const c = document.createElement("canvas"); c.width = c.height = S;
      const g = c.getContext("2d", { willReadFrequently: true });
      g.translate(S / 2, S / 2); g.rotate(TILT[l.shape] || 0); g.scale(l.r * sx, l.r * sy);
      fn(g);
      return g.getImageData(0, 0, S, S).data;
    };
    const body = paint((g) => { bodyPath(g, l.shape); g.fill(); });
    const eyes = back ? null : paint((g) => { g.fillStyle = "#000"; const look = { ...l, ink: "#000" }; drawEyes(g, look, lookX, blink, happy); });
    const on = (i) => body[i * 4 + 3] > 120;
    let bottom = 0, top = S;
    for (let i = 0; i < S * S; i++) if (on(i)) { bottom = Math.max(bottom, Math.floor(i / S)); top = Math.min(top, Math.floor(i / S)); }
    const out = document.createElement("canvas"); out.width = out.height = S;
    const og = out.getContext("2d"), img = og.createImageData(S, S);
    const base = darken(l.color, 1), edge = darken(l.color, 0.55), low = darken(l.color, 0.82);
    const ink = l.ink ? darken(l.ink, 1) : [255, 255, 255];
    for (let y = 0; y < S; y++) for (let x = 0; x < S; x++) {
      const i = y * S + x;
      if (!on(i)) continue;
      const border = x === 0 || y === 0 || x === S - 1 || y === S - 1 || !on(i - 1) || !on(i + 1) || !on(i - S) || !on(i + S);
      let c = border ? edge : y >= bottom - 2 ? low : base;
      if (eyes && eyes[i * 4 + 3] > 110 && !border) c = ink;
      img.data.set([c[0], c[1], c[2], 255], i * 4);
    }
    og.putImageData(img, 0, 0);
    const s = { canvas: out, S, bottom, top };
    cache.set(key, s);
    return s;
  }

  /* x, y are the ground point. pose: stand | walk | sit | type | read | cheer */
  function drawPerson(g, x, y, l, opt) {
    const { dir = "down", pose = "stand", carrying = false } = opt || {};
    const t = still() ? 0 : performance.now() / 1000 + l.phase;
    const wave = (speed) => Math.abs(Math.sin(t * speed));
    let lift = 0, sy = 1;
    if (pose === "cheer") { const p = wave(7); lift = Math.round(p * 10); sy = p < 0.3 ? 0.88 : 1; }
    else if (pose === "walk") { const p = wave(8); lift = Math.round(p * 8); sy = p < 0.25 ? 0.86 : p > 0.75 ? 1.1 : 1; }
    else if (pose === "type") { lift = Math.round(wave(5) * 2); }
    else if (pose === "read") { lift = Math.round(wave(2.2)); }
    else { lift = Math.round(wave(1.6)); }
    const lookX = dir === "left" ? -1 : dir === "right" ? 1 : (pose === "read" ? Math.round(Math.sin(t * 1.3)) : 0);
    const s = sprite(l, sy, lookX, !still() && (t % 4.3) < 0.13, pose === "cheer", dir === "up");
    const px = Math.round(x), py = Math.round(y);
    const half = Math.max(3, Math.round(l.r * 0.7 - lift * 0.3));
    g.fillStyle = "rgba(10,10,30,.28)";
    g.fillRect(px - half, py, half * 2, 1); g.fillRect(px - half + 2, py + 1, half * 2 - 4, 1);
    const left = px - Math.floor(s.S / 2), top = py - s.bottom - lift;
    g.drawImage(s.canvas, left, top);
    if (l.crown) {
      const cx = px, by = top + s.top + 1, gold = "#f6c343", dark = "#b9791f", gem = "#e5484d";
      g.fillStyle = dark; g.fillRect(cx - 7, by - 1, 14, 2);
      g.fillStyle = gold; g.fillRect(cx - 6, by - 3, 12, 2);
      [-6, -1, 4].forEach((dx) => { g.fillRect(cx + dx, by - 6, 2, 3); });
      g.fillRect(cx - 4, by - 4, 2, 1); g.fillRect(cx + 2, by - 4, 2, 1);
      g.fillStyle = dark; [-6, -1, 4].forEach((dx) => g.fillRect(cx + dx + 1, by - 6, 1, 3));
      g.fillStyle = gem; g.fillRect(cx - 1, by - 3, 2, 2);
    }
    const cy = top + s.bottom - l.r;
    if (carrying) {
      g.fillStyle = "#fffdf0"; g.fillRect(px - 5, cy + 4, 10, 8);
      g.fillStyle = "#8d93a8"; g.fillRect(px - 3, cy + 6, 6, 1); g.fillRect(px - 3, cy + 8, 4, 1);
    }
    if (pose === "read") {
      g.fillStyle = "#fffdf0"; g.fillRect(px - 6, cy + 5, 12, 7);
      g.fillStyle = "#8d93a8"; g.fillRect(px, cy + 5, 1, 7);
    }
  }

  /* Small office helpers */
  function drawPet(g, x, y, kind, opt) {
    const look = { shape: kind === "bunny" ? "drop" : "circle", color: kind === "bunny" ? "#57b8a5" : "#f59f3a", r: 5, eyes: "tall", phase: 1.5, height: 0 };
    drawPerson(g, x, y, look, opt);
  }

  /* Bitmap text from the Studio's own font */
  const plain = (c) => c.normalize("NFD").charAt(0);
  function text(g, str, x, y, color) {
    g.fillStyle = color;
    let cx = Math.round(x);
    for (const ch of String(str)) {
      const rows = PIXEL_FONT[ch] || PIXEL_FONT[plain(ch)] || PIXEL_FONT["?"];
      rows.forEach((bits, r) => {
        for (let c = 0; c < 5; c++) if (bits & (16 >> c)) g.fillRect(cx + c, Math.round(y) + r, 1, 1);
      });
      cx += 6;
    }
  }
  const textWidth = (str) => [...String(str)].length * 6 - 1;

  window.Sprites = { makeLook, drawPerson, drawPet, text, textWidth };
})();
