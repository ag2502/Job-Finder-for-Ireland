/* The floating CV.

   What was read from someone's CV, drawn onto a sheet of paper and hung in WebGL on the
   profile. It floats, leans toward the pointer, and bends like paper when it is dragged,
   thrown or scrolled past, then springs back to rest. The idea is borrowed from
   portfolio sites that hang their work on curved planes which flex with movement; here
   the thing on the plane is the searcher's own CV, which makes it feel like theirs.

   Progressive by design. The sheet is also in the page as plain HTML (`.paper`), which
   is what shows without WebGL, with reduced motion, and until this has drawn. The canvas
   is decorative: every fact on it is written out in full beside it. */
(function () {
  'use strict';

  var reduced = window.matchMedia('(prefers-reduced-motion: reduce)');
  var fine = window.matchMedia('(pointer: fine)');
  var live = null;

  // ------------------------------------------------------------ the paper, in 2D
  // Laid out in design units on a 600 x 848 sheet (A4's proportions) and scaled to
  // whatever resolution the texture needs.
  var PW = 600, PH = 848, M = 52;

  function token(name, fallback) {
    var v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return v || fallback;
  }

  function roundRect(g, x, y, w, h, r) {
    g.beginPath();
    g.moveTo(x + r, y);
    g.arcTo(x + w, y, x + w, y + h, r);
    g.arcTo(x + w, y + h, x, y + h, r);
    g.arcTo(x, y + h, x, y, r);
    g.arcTo(x, y, x + w, y, r);
    g.closePath();
  }

  function fit(g, text, width) {
    if (g.measureText(text).width <= width) return text;
    while (text.length > 1 && g.measureText(text + '…').width > width) text = text.slice(0, -1);
    return text + '…';
  }

  // Word-wrap `text` into at most `max` lines; returns the y after the last line.
  function wrap(g, text, x, y, width, lh, max) {
    var words = String(text).split(/\s+/), line = '', lines = [];
    for (var i = 0; i < words.length; i++) {
      var next = line ? line + ' ' + words[i] : words[i];
      if (g.measureText(next).width > width && line) { lines.push(line); line = words[i]; }
      else line = next;
    }
    if (line) lines.push(line);
    if (lines.length > max) {
      lines = lines.slice(0, max);
      lines[max - 1] = fit(g, lines[max - 1] + '…', width);
    }
    lines.forEach(function (l, k) { g.fillText(l, x, y + k * lh); });
    return y + (lines.length - 1) * lh;
  }

  function label(g, text, x, y) {
    g.font = '700 12.5px Geist, system-ui, sans-serif';
    g.fillStyle = token('--ink-3', '#5c626d');
    // Canvas has no letter-spacing everywhere yet, so the tracking is drawn by hand.
    var cx = x;
    for (var i = 0; i < text.length; i++) {
      g.fillText(text[i], cx, y);
      cx += g.measureText(text[i]).width + 1.6;
    }
  }

  // Tags laid out in rows; returns the y of the bottom of the last row.
  function tags(g, items, x, y, width, style, maxY) {
    var h = style.h, cx = x, cy = y;
    for (var i = 0; i < items.length; i++) {
      g.font = style.font;
      var text = items[i].label || items[i];
      var w = Math.min(width, g.measureText(text).width + style.pad * 2 + (style.dot ? 18 : 0));
      if (cx + w > x + width) { cx = x; cy += h + style.gap; }
      if (cy + h > maxY) break;
      var look = style.look(items[i], i);
      roundRect(g, cx, cy, w, h, style.r);
      g.fillStyle = look.fill; g.fill();
      if (look.stroke) { g.lineWidth = 1.5; g.strokeStyle = look.stroke; g.stroke(); }
      var tx = cx + style.pad;
      if (style.dot) {
        g.beginPath(); g.arc(tx + 5, cy + h / 2, 5, 0, Math.PI * 2);
        g.fillStyle = look.stroke; g.fill();
        tx += 18;
      }
      g.fillStyle = look.ink;
      g.textBaseline = 'middle';
      g.fillText(fit(g, text, w - style.pad * 2), tx, cy + h / 2 + 1);
      g.textBaseline = 'alphabetic';
      cx += w + style.gap;
    }
    return cy + h;
  }

  function drawPaper(data, width) {
    var height = Math.round(width * PH / PW);
    var c = document.createElement('canvas');
    c.width = width; c.height = height;
    var g = c.getContext('2d');
    g.scale(width / PW, height / PH);

    var ink = token('--ink', '#15171c'), ink2 = token('--ink-2', '#454b56'),
        ink3 = token('--ink-3', '#5c626d'), line = token('--line', '#dde1e7');

    g.fillStyle = '#fff';
    g.fillRect(0, 0, PW, PH);
    // A touch of tooth, so the sheet reads as paper rather than a white rectangle.
    var paper = g.createLinearGradient(0, 0, PW, PH);
    paper.addColorStop(0, 'rgba(255,255,255,0)');
    paper.addColorStop(1, 'rgba(236,238,242,.55)');
    g.fillStyle = paper; g.fillRect(0, 0, PW, PH);

    // The file, as a header strip.
    g.font = '500 14.5px "Geist Mono", ui-monospace, monospace';
    g.fillStyle = ink3;
    var meta = (data.kind + ' · ' + data.size).toUpperCase();
    var metaW = g.measureText(meta).width;
    g.fillText(fit(g, data.name, PW - M * 2 - metaW - 24), M, 66);
    g.fillText(meta, PW - M - metaW, 66);
    g.fillStyle = line; g.fillRect(M, 86, PW - M * 2, 1.5);

    // What the CV reads as, as the headline.
    var y;
    if (data.summary) {
      g.font = '700 30px Geist, system-ui, sans-serif';
      g.fillStyle = ink;
      y = wrap(g, data.summary, M, 138, PW - M * 2, 37, 4);
    } else {
      g.font = '750 42px Geist, system-ui, sans-serif';
      g.fillStyle = ink;
      g.fillText('Curriculum vitae', M, 146);
      y = 146;
    }
    if (data.years !== null && data.years !== undefined) {
      g.font = '500 17px Geist, system-ui, sans-serif';
      g.fillStyle = ink2;
      y += 34;
      g.fillText('About ' + data.years + (data.years === 1 ? ' year' : ' years') +
        (data.seniority ? ' · ' + data.seniority + ' level' : ''), M, y);
    }

    if (data.fields && data.fields.length) {
      y += 48; label(g, 'WHERE IT POINTS', M, y); y += 14;
      y = tags(g, data.fields, M, y, PW - M * 2, {
        h: 36, pad: 14, gap: 9, r: 18, dot: true,
        font: '600 15.5px Geist, system-ui, sans-serif',
        look: function (f) {
          var col = token('--g-' + f.group, '#2b74f0');
          return { fill: '#fff', stroke: col, ink: ink };
        }
      }, 620);
    }

    if (data.skills && data.skills.length) {
      y += 40; label(g, 'SKILLS WE PICKED UP', M, y); y += 14;
      var sticker = token('--sticker', '#ffd84a'), wash = token('--aqua-wash', '#edf3ff'),
          deep = token('--aqua-deep', '#1a55c2');
      y = tags(g, data.skills, M, y, PW - M * 2, {
        h: 30, pad: 11, gap: 8, r: 8,
        font: '500 14.5px "Geist Mono", ui-monospace, monospace',
        look: function (s, i) {
          return i < 4 ? { fill: sticker, ink: '#3d3000' } : { fill: wash, stroke: 'rgba(43,116,240,.25)', ink: deep };
        }
      }, 700);
    }

    // The rest of the page, as the grey lines a document has at a glance.
    g.fillStyle = '#eceef2';
    var widths = [1, .92, .97, .64, 1, .88, .72];
    for (var k = 0, ly = Math.max(y + 36, 560); ly < 752 && k < widths.length; k++, ly += 20) {
      roundRect(g, M, ly, (PW - M * 2) * widths[k], 8, 4); g.fill();
    }

    g.fillStyle = line; g.fillRect(M, 776, PW - M * 2, 1.5);
    g.font = '500 13.5px "Geist Mono", ui-monospace, monospace';
    g.fillStyle = ink3;
    var foot = (data.words ? data.words.toLocaleString('en-IE') + ' words' : 'read') +
      (data.added ? ' · read ' + data.added : '');
    g.fillText(foot, M, 808);

    // A rubber stamp, slightly crooked, the way a real one lands.
    var green = token('--sorted', '#1c9a4a');
    g.save();
    g.translate(PW - M - 74, 800);
    g.rotate(-0.13);
    g.globalAlpha = .85;
    roundRect(g, -70, -26, 140, 46, 9);
    g.lineWidth = 3; g.strokeStyle = green; g.stroke();
    g.font = '800 21px Geist, system-ui, sans-serif';
    g.fillStyle = green; g.textAlign = 'center'; g.textBaseline = 'middle';
    g.fillText('SORTED ✓', 0, -2);
    g.restore();
    return c;
  }

  // ------------------------------------------------------------------ WebGL
  var VS = [
    'attribute vec2 aPos;',
    'uniform vec2 uSize;',
    'uniform vec3 uRot;',
    'uniform vec3 uOff;',
    'uniform vec2 uBend;',
    'uniform float uTime;',
    'uniform float uAspect;',
    'uniform float uFocal;',
    'varying vec2 vUv;',
    'varying vec3 vNormal;',
    'mat3 rotX(float a){float c=cos(a),s=sin(a);return mat3(1.,0.,0.,0.,c,s,0.,-s,c);}',
    'mat3 rotY(float a){float c=cos(a),s=sin(a);return mat3(c,0.,-s,0.,1.,0.,s,0.,c);}',
    'mat3 rotZ(float a){float c=cos(a),s=sin(a);return mat3(c,s,0.,-s,c,0.,0.,0.,1.);}',
    'void main(){',
    '  vUv = vec2(aPos.x + .5, .5 - aPos.y);',
    '  vec3 p = vec3(aPos * uSize, 0.);',
    // Bent like a sheet held at its middle: a curve across the width and one down the
    // height, plus a slow ripple that keeps it from ever looking pinned.
    '  float wave = sin(aPos.y * 5.5 + uTime * 1.3) * .006 + sin(aPos.x * 4. - uTime * .9) * .004;',
    '  p.z += uBend.x * aPos.x * aPos.x * uSize.x + uBend.y * aPos.y * aPos.y * uSize.y + wave * uSize.y;',
    '  vec3 n = normalize(vec3(-2. * uBend.x * aPos.x, -2. * uBend.y * aPos.y, 1.));',
    '  mat3 r = rotZ(uRot.z) * rotY(uRot.y) * rotX(uRot.x);',
    '  p = r * p + uOff;',
    '  vNormal = r * n;',
    '  float z = uFocal - p.z;',
    '  gl_Position = vec4(p.x * uFocal / (z * uAspect), p.y * uFocal / z, (z - uFocal) * .1, 1.);',
    '}'
  ].join('\n');

  var FS = [
    'precision mediump float;',
    'uniform sampler2D uTex;',
    'uniform vec2 uLight;',
    // Its own uniform, not uTime: the two stages default to different precisions, and
    // a uniform shared between them must match, which some drivers refuse to link.
    'uniform float uGrain;',
    'varying vec2 vUv;',
    'varying vec3 vNormal;',
    'float hash(vec2 p){return fract(sin(dot(p, vec2(12.9898, 78.233))) * 43758.5453);}',
    'void main(){',
    '  vec3 col = texture2D(uTex, vUv).rgb;',
    '  vec3 n = normalize(vNormal);',
    '  vec3 l = normalize(vec3(uLight, 1.2));',
    '  float d = dot(n, l);',
    // Soft light from where the pointer is, and a narrow sheen where the sheet faces it.
    '  col *= mix(.9, 1.03, clamp(d, 0., 1.));',
    '  col += pow(max(d, 0.), 40.) * .10;',
    // Film grain, very faint, so the flat white has some tooth.
    '  col += (hash(gl_FragCoord.xy + uGrain * 91.) - .5) * .025;',
    '  gl_FragColor = vec4(col, 1.);',
    '}'
  ].join('\n');

  function compile(gl, type, src) {
    var s = gl.createShader(type);
    gl.shaderSource(s, src); gl.compileShader(s);
    if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s));
    return s;
  }

  // A grid of vertices across the sheet, so it can bend smoothly.
  function grid(cols, rows) {
    var pos = [], idx = [];
    for (var j = 0; j <= rows; j++)
      for (var i = 0; i <= cols; i++) pos.push(i / cols - .5, .5 - j / rows);
    for (j = 0; j < rows; j++)
      for (i = 0; i < cols; i++) {
        var a = j * (cols + 1) + i, b = a + 1, c = a + cols + 1, d = c + 1;
        idx.push(a, c, b, b, c, d);
      }
    return { pos: new Float32Array(pos), idx: new Uint16Array(idx) };
  }

  function fontsReady() {
    if (!document.fonts || !document.fonts.load) return Promise.resolve();
    var wait = Promise.all([
      document.fonts.load('700 30px Geist'), document.fonts.load('500 15px "Geist Mono"')
    ]).catch(function () {});
    return Promise.race([wait, new Promise(function (r) { setTimeout(r, 1500); })]);
  }

  function Sheet(stage) {
    this.stage = stage;
    this.canvas = stage.querySelector('.cvstage__gl');
    this.shadow = stage.querySelector('.cvstage__shadow');
    this.cursor = stage.querySelector('.cvstage__cursor');
    this.data = JSON.parse(stage.getAttribute('data-cv'));
    this.fresh = !!stage.closest('.is-fresh');
    this.running = false; this.visible = true; this.dead = false;
    // Where things are, and where they are heading.
    this.s = {
      rx: 0, ry: 0, rz: 0, tx: 0, ty: 0,         // tilt, and the tilt the pointer wants
      ox: 0, oy: 0, vx: 0, vy: 0,                // drag offset and its velocity
      bx: 0, by: 0, bvx: 0, bvy: 0,              // bend, and how fast it is changing
      lx: -.35, ly: .45,                         // where the light comes from
      drag: null, t0: performance.now()
    };
    // Arriving: from below, tipped back and bent, then springing to rest.
    if (this.fresh) { this.s.oy = -1.4; this.s.rx = 1.1; this.s.by = .5; this.s.rz = -.3; }
    else { this.s.oy = -.25; this.s.rx = .35; this.s.by = .2; }
  }

  Sheet.prototype.init = function () {
    var gl = this.canvas.getContext('webgl', { antialias: true, alpha: true, premultipliedAlpha: true });
    if (!gl) throw new Error('no webgl');
    this.gl = gl;
    var prog = gl.createProgram();
    gl.attachShader(prog, compile(gl, gl.VERTEX_SHADER, VS));
    gl.attachShader(prog, compile(gl, gl.FRAGMENT_SHADER, FS));
    gl.linkProgram(prog);
    if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(prog));
    gl.useProgram(prog);
    this.prog = prog;

    var mesh = grid(24, 34);
    this.count = mesh.idx.length;
    gl.bindBuffer(gl.ARRAY_BUFFER, gl.createBuffer());
    gl.bufferData(gl.ARRAY_BUFFER, mesh.pos, gl.STATIC_DRAW);
    var loc = gl.getAttribLocation(prog, 'aPos');
    gl.enableVertexAttribArray(loc);
    gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);
    gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, gl.createBuffer());
    gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, mesh.idx, gl.STATIC_DRAW);

    this.u = {};
    ['uSize', 'uRot', 'uOff', 'uBend', 'uTime', 'uGrain', 'uAspect', 'uFocal', 'uTex', 'uLight'].forEach(function (n) {
      this.u[n] = gl.getUniformLocation(prog, n);
    }, this);

    this.tex = gl.createTexture();
    gl.enable(gl.DEPTH_TEST);
    gl.clearColor(0, 0, 0, 0);

    var self = this;
    this.canvas.addEventListener('webglcontextlost', function (e) {
      e.preventDefault(); self.fail();
    });
    this.resize();
  };

  Sheet.prototype.paint = function () {
    // The texture is sized to how big the sheet is drawn, a little over, and no bigger:
    // a much larger one is shrunk on the GPU without mipmaps and the text shimmers.
    var gl = this.gl;
    var want = Math.min(1600, Math.max(600, Math.round(this.sheetPx * 1.35)));
    if (want === this.texW) return;
    this.texW = want;
    var paper = drawPaper(this.data, want);
    gl.bindTexture(gl.TEXTURE_2D, this.tex);
    gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, false);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, paper);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
  };

  Sheet.prototype.resize = function () {
    var r = this.stage.getBoundingClientRect();
    var dpr = Math.min(window.devicePixelRatio || 1, 2);
    this.w = r.width; this.h = r.height;
    this.canvas.width = Math.round(r.width * dpr);
    this.canvas.height = Math.round(r.height * dpr);
    this.gl.viewport(0, 0, this.canvas.width, this.canvas.height);
    // The camera sees 2 units of height at the sheet's resting depth. The sheet takes
    // most of that, less on a narrow stage so a lean never clips its corners.
    this.focal = 3.2;
    var aspect = r.width / r.height;
    // The stage grows with the column beside it; the sheet stops at a size a sheet of
    // paper on a desk would be, so a long reading does not blow it up to poster size.
    var hgt = Math.min(1.62, aspect * 2 * .78 * PH / PW, 560 / (r.height / 2));
    this.size = [hgt * PW / PH, hgt];
    this.aspect = aspect;
    this.unit = r.height / 2;            // CSS px per world unit at rest
    this.sheetPx = hgt * this.unit * dpr;
    this.paint();
  };

  Sheet.prototype.bind = function () {
    var self = this, s = this.s, stage = this.stage, canvas = this.canvas;

    this.onMove = function (e) {
      var r = stage.getBoundingClientRect();
      var px = (e.clientX - r.left) / r.width - .5, py = (e.clientY - r.top) / r.height - .5;
      // Lean toward the pointer anywhere near the stage; further away, drift back.
      var near = Math.abs(px) < 1.2 && Math.abs(py) < 1.2;
      s.tx = near ? py * .55 : 0;
      s.ty = near ? px * .75 : 0;
      s.lx = near ? px * 1.6 : -.35;
      s.ly = near ? -py * 1.6 : .45;
      if (self.cursor && fine.matches) {
        var inside = px > -.5 && px < .5 && py > -.5 && py < .5;
        self.cursor.style.transform = 'translate(' + (e.clientX - r.left) + 'px,' + (e.clientY - r.top) + 'px)';
        stage.classList.toggle('is-hovering', inside && !s.drag);
      }
    };
    window.addEventListener('pointermove', this.onMove, { passive: true });

    canvas.addEventListener('pointerdown', function (e) {
      if (e.button !== 0) return;
      s.drag = { x: e.clientX, y: e.clientY, ox: s.ox, oy: s.oy, lx: e.clientX, ly: e.clientY, t: performance.now() };
      canvas.setPointerCapture(e.pointerId);
      stage.classList.add('is-held');
      stage.classList.remove('is-hovering');
      if (e.pointerType === 'mouse') e.preventDefault();
    });
    canvas.addEventListener('pointermove', function (e) {
      var d = s.drag;
      if (!d) return;
      var now = performance.now(), dt = Math.max(8, now - d.t);
      // Rubber-banded: the further it is pulled, the harder it pulls back.
      var dx = (e.clientX - d.x) / self.unit, dy = -(e.clientY - d.y) / self.unit;
      s.ox = d.ox + dx / (1 + Math.abs(dx) * .9);
      s.oy = d.oy + dy / (1 + Math.abs(dy) * .9);
      var vx = (e.clientX - d.lx) / dt, vy = (e.clientY - d.ly) / dt;
      // Paper trails its own motion: it bends away from the direction it is moved.
      s.bvx += (-vx * .09 - s.bx) * .35;
      s.bvy += (vy * .06 - s.by) * .35;
      s.rz += (-vx * .05 - s.rz) * .2;
      d.vx = vx; d.vy = vy;
      d.lx = e.clientX; d.ly = e.clientY; d.t = now;
    });
    function release(e) {
      var d = s.drag;
      if (!d) return;
      s.drag = null;
      // Let go mid-swing and it carries on a little before the spring brings it home.
      s.vx = (d.vx || 0) * 16 / self.unit * .5;
      s.vy = -(d.vy || 0) * 16 / self.unit * .5;
      stage.classList.remove('is-held');
      stage.classList.add('was-moved');
      try { canvas.releasePointerCapture(e.pointerId); } catch (err) { /* already gone */ }
    }
    canvas.addEventListener('pointerup', release);
    canvas.addEventListener('pointercancel', release);
    canvas.addEventListener('pointerleave', function () { stage.classList.remove('is-hovering'); });

    // Scrolling past flexes it too, the way a sheet in a moving hand would.
    var lastY = window.scrollY;
    this.onScroll = function () {
      var dy = window.scrollY - lastY; lastY = window.scrollY;
      s.bvy += Math.max(-.06, Math.min(.06, dy * .0016));
    };
    window.addEventListener('scroll', this.onScroll, { passive: true });

    this.onResize = function () { if (!self.dead) self.resize(); };
    window.addEventListener('resize', this.onResize, { passive: true });

    if ('IntersectionObserver' in window) {
      this.io = new IntersectionObserver(function (es) {
        self.visible = es[0].isIntersecting;
        if (self.visible) self.start();
      });
      this.io.observe(stage);
    }
    this.onVis = function () { if (!document.hidden) self.start(); };
    document.addEventListener('visibilitychange', this.onVis);
  };

  Sheet.prototype.step = function (now) {
    var s = this.s, t = (now - s.t0) / 1000;
    // Springs: stiffness pulls toward rest, damping bleeds off the swing.
    if (!s.drag) {
      s.vx += (-s.ox * .055) ; s.vy += (-s.oy * .055);
      s.vx *= .86; s.vy *= .86;
      s.ox += s.vx; s.oy += s.vy;
    }
    s.bvx += -s.bx * .08; s.bvy += -s.by * .08;
    s.bvx *= .84; s.bvy *= .84;
    s.bx += s.bvx; s.by += s.bvy;

    // Idle float: a slow bob and sway, so it is never quite still.
    var bob = Math.sin(t * 1.1) * .018, sway = Math.sin(t * .63) * .035;
    var tiltX = s.tx + Math.sin(t * .8) * .03, tiltY = s.ty + sway;
    s.rx += (tiltX - s.rx) * .07;
    s.ry += (tiltY - s.ry) * .07;
    s.rz += (Math.sin(t * .5) * .02 - s.rz) * .06;

    var gl = this.gl, u = this.u;
    gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
    gl.uniform2f(u.uSize, this.size[0], this.size[1]);
    gl.uniform3f(u.uRot, s.rx, s.ry, s.rz);
    gl.uniform3f(u.uOff, s.ox, s.oy + bob, Math.abs(s.ox) * -.15);
    gl.uniform2f(u.uBend, s.bx, s.by);
    gl.uniform1f(u.uTime, t);
    gl.uniform1f(u.uGrain, t % 1);
    gl.uniform1f(u.uAspect, this.aspect);
    gl.uniform1f(u.uFocal, this.focal);
    gl.uniform2f(u.uLight, s.lx, s.ly);
    gl.uniform1i(u.uTex, 0);
    gl.drawElements(gl.TRIANGLES, this.count, gl.UNSIGNED_SHORT, 0);

    // The shadow on the desk: tighter and darker as the sheet comes down to it.
    if (this.shadow) {
      var lift = .5 + (s.oy + bob) * .6;
      this.shadow.style.transform = 'translate(' + (s.ox * this.unit).toFixed(1) + 'px,0) scale(' +
        (1 - lift * .18).toFixed(3) + ',' + (1 - lift * .1).toFixed(3) + ')';
      this.shadow.style.opacity = Math.max(.25, Math.min(1, 1 - lift * .5)).toFixed(3);
    }
  };

  Sheet.prototype.start = function () {
    if (this.running || this.dead) return;
    this.running = true;
    var self = this;
    (function frame(now) {
      if (self.dead || !self.visible || document.hidden) { self.running = false; return; }
      self.step(now);
      requestAnimationFrame(frame);
    })(performance.now());
  };

  Sheet.prototype.fail = function () {
    this.destroy();
    this.stage.classList.remove('is-gl');
  };

  Sheet.prototype.destroy = function () {
    this.dead = true;
    window.removeEventListener('pointermove', this.onMove);
    window.removeEventListener('scroll', this.onScroll);
    window.removeEventListener('resize', this.onResize);
    document.removeEventListener('visibilitychange', this.onVis);
    if (this.io) this.io.disconnect();
  };

  function mount(root) {
    if (live && !document.body.contains(live.stage)) { live.destroy(); live = null; }
    var stage = (root || document).querySelector ? (root || document).querySelector('[data-cvstage]') : null;
    if (!stage || stage._sheet || reduced.matches) return;
    var sheet = new Sheet(stage);
    stage._sheet = sheet;
    fontsReady().then(function () {
      if (!document.body.contains(stage)) return;
      try {
        sheet.init();
      } catch (err) {
        // No WebGL, or a driver that will not run it: the HTML sheet stays, which is
        // the whole fallback. Said once in the console so it is not a silent mystery.
        if (window.console) console.info('cv sheet: using the plain sheet (' + err.message + ')');
        return;
      }
      stage.classList.add('is-gl');
      sheet.bind();
      sheet.start();
      live = sheet;
    });
  }

  mount(document);
  // htmx swaps the panel after an upload or removal; mount whatever arrives.
  document.addEventListener('htmx:load', function (e) { mount(e.target.parentNode || e.target); });
})();
