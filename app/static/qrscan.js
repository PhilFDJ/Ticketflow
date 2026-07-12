/* qrscan.js — a minimal, dependency-free QR decoder.
 *
 * WHY THIS EXISTS
 * The scanner used the BarcodeDetector API, which is Chrome-only: iOS Safari and
 * Firefox don't have it, so on an iPhone the camera scanner simply refused to
 * run. At a venue door that means manually typing every code. This provides a
 * fallback decoder so scanning works on any phone.
 *
 * It decodes QR codes from an ImageData frame. Scope is deliberately narrow —
 * it only has to read the QRs this app generates (byte mode, versions 1–10,
 * which covers the ticket URLs) — but it implements proper Reed–Solomon error
 * correction, so it still reads codes that are creased, printed, or partly
 * glared.
 */
(function (global) {
  "use strict";

  // ---- Galois field (GF(256)) for Reed-Solomon --------------------------
  var EXP = new Uint8Array(512), LOG = new Uint8Array(256);
  (function () {
    var x = 1;
    for (var i = 0; i < 255; i++) {
      EXP[i] = x; LOG[x] = i;
      x <<= 1;
      if (x & 0x100) x ^= 0x11d;           // QR's primitive polynomial
    }
    for (var j = 255; j < 512; j++) EXP[j] = EXP[j - 255];
  })();
  function gmul(a, b) { return (a === 0 || b === 0) ? 0 : EXP[LOG[a] + LOG[b]]; }
  function gdiv(a, b) { return EXP[(LOG[a] + 255 - LOG[b]) % 255]; }

  /* Reed-Solomon: correct up to ecLen/2 byte errors in place.
     Returns false if the block is beyond repair. */
  function rsCorrect(bytes, ecLen) {
    var n = bytes.length, i, j;

    // syndromes
    var synd = new Uint8Array(ecLen), hasErr = false;
    for (i = 0; i < ecLen; i++) {
      var s = 0;
      for (j = 0; j < n; j++) s = gmul(s, EXP[i]) ^ bytes[j];
      synd[i] = s;
      if (s !== 0) hasErr = true;
    }
    if (!hasErr) return true;               // clean block

    // Berlekamp-Massey → error locator polynomial
    var sigma = [1], old = [1];
    for (i = 0; i < ecLen; i++) {
      var delta = synd[i];
      for (j = 1; j < sigma.length; j++) delta ^= gmul(sigma[j], synd[i - j]);
      old.unshift(0);
      if (delta !== 0) {
        if (old.length > sigma.length) {
          var tmp = old.map(function (c) { return gmul(c, delta); });
          old = sigma.map(function (c) { return gdiv(c, delta); });
          sigma = tmp;
        } else {
          for (j = 0; j < old.length; j++) sigma[j] ^= gmul(old[j], delta);
        }
      }
    }
    var errCount = sigma.length - 1;
    if (errCount <= 0 || errCount * 2 > ecLen) return false;

    // Chien search → error positions
    var positions = [];
    for (i = 0; i < n; i++) {
      var val = 1, xInv = EXP[(255 - (i % 255)) % 255], acc = 0, p = 1;
      acc = sigma[0];
      for (j = 1; j < sigma.length; j++) {
        p = gmul(p, xInv);
        acc ^= gmul(sigma[j], p);
      }
      if (acc === 0) positions.push(n - 1 - i);
      void val;
    }
    if (positions.length !== errCount) return false;

    // Forney → error magnitudes
    for (var k = 0; k < positions.length; k++) {
      var pos = positions[k];
      var xi = EXP[(n - 1 - pos) % 255];
      var xiInv = EXP[(255 - LOG[xi]) % 255];

      // omega = (syndrome * sigma) mod x^ecLen
      var omega = 0, pw = 1;
      for (i = 0; i < ecLen; i++) {
        var term = 0;
        for (j = 0; j <= i && j < sigma.length; j++) term ^= gmul(sigma[j], synd[i - j]);
        omega ^= gmul(term, pw);
        pw = gmul(pw, xiInv);
      }
      // sigma'(x) — formal derivative: odd terms only
      var deriv = 0, pw2 = 1;
      for (j = 1; j < sigma.length; j += 2) {
        deriv ^= gmul(sigma[j], pw2);
        pw2 = gmul(pw2, gmul(xiInv, xiInv));
      }
      if (deriv === 0) return false;
      var mag = gmul(xi, gdiv(omega, deriv));
      bytes[pos] ^= mag;
    }

    // verify
    for (i = 0; i < ecLen; i++) {
      var s2 = 0;
      for (j = 0; j < n; j++) s2 = gmul(s2, EXP[i]) ^ bytes[j];
      if (s2 !== 0) return false;
    }
    return true;
  }

  // ---- EC block layout, versions 1..10, all 4 EC levels ------------------
  // [ecCodewordsPerBlock, group1Blocks, group1DataCodewords, group2Blocks, group2DataCodewords]
  var EC_TABLE = {
    1:  { L:[7,1,19,0,0],   M:[10,1,16,0,0],  Q:[13,1,13,0,0],  H:[17,1,9,0,0] },
    2:  { L:[10,1,34,0,0],  M:[16,1,28,0,0],  Q:[22,1,22,0,0],  H:[28,1,16,0,0] },
    3:  { L:[15,1,55,0,0],  M:[26,1,44,0,0],  Q:[18,2,17,0,0],  H:[22,2,13,0,0] },
    4:  { L:[20,1,80,0,0],  M:[18,2,32,0,0],  Q:[26,2,24,0,0],  H:[16,4,9,0,0] },
    5:  { L:[26,1,108,0,0], M:[24,2,43,0,0],  Q:[18,2,15,2,16], H:[22,2,11,2,12] },
    6:  { L:[18,2,68,0,0],  M:[16,4,27,0,0],  Q:[24,4,19,0,0],  H:[28,4,15,0,0] },
    7:  { L:[20,2,78,0,0],  M:[18,4,31,0,0],  Q:[18,2,14,4,15], H:[26,4,13,1,14] },
    8:  { L:[24,2,97,0,0],  M:[22,2,38,2,39], Q:[22,4,18,2,19], H:[26,4,14,2,15] },
    9:  { L:[30,2,116,0,0], M:[22,3,36,2,37], Q:[20,4,16,4,17], H:[24,4,12,4,13] },
    10: { L:[18,2,68,2,69], M:[26,4,43,1,44], Q:[24,6,19,2,20], H:[28,6,15,2,16] }
  };
  var EC_LEVELS = ["M", "L", "H", "Q"];      // indexed by the 2 format bits

  var ALIGN_POS = {
    1:[], 2:[6,18], 3:[6,22], 4:[6,26], 5:[6,30],
    6:[6,34], 7:[6,22,38], 8:[6,24,42], 9:[6,26,46], 10:[6,28,50]
  };

  function maskFn(m, i, j) {
    switch (m) {
      case 0: return (i + j) % 2 === 0;
      case 1: return i % 2 === 0;
      case 2: return j % 3 === 0;
      case 3: return (i + j) % 3 === 0;
      case 4: return (((i / 2) | 0) + ((j / 3) | 0)) % 2 === 0;
      case 5: return ((i * j) % 2) + ((i * j) % 3) === 0;
      case 6: return ((((i * j) % 2) + ((i * j) % 3)) % 2) === 0;
      case 7: return ((((i + j) % 2) + ((i * j) % 3)) % 2) === 0;
    }
    return false;
  }

  // ---- image → binary grid ----------------------------------------------
  function toGray(img) {
    var d = img.data, n = img.width * img.height, g = new Uint8ClampedArray(n);
    for (var i = 0; i < n; i++) {
      var o = i * 4;
      g[i] = (d[o] * 77 + d[o + 1] * 150 + d[o + 2] * 29) >> 8;
    }
    return g;
  }

  /* Adaptive threshold. A single global cutoff fails badly on phone photos
     (glare on one side of a printed ticket, screen glow on the other), so we
     threshold against a local block average. */
  function binarize(gray, w, h) {
    var BS = 16, out = new Uint8Array(w * h);
    var bw = Math.ceil(w / BS), bh = Math.ceil(h / BS);
    var means = new Float32Array(bw * bh);
    for (var by = 0; by < bh; by++) {
      for (var bx = 0; bx < bw; bx++) {
        var sum = 0, cnt = 0;
        for (var y = by * BS; y < Math.min((by + 1) * BS, h); y++) {
          for (var x = bx * BS; x < Math.min((bx + 1) * BS, w); x++) {
            sum += gray[y * w + x]; cnt++;
          }
        }
        means[by * bw + bx] = cnt ? sum / cnt : 128;
      }
    }
    for (var yy = 0; yy < h; yy++) {
      for (var xx = 0; xx < w; xx++) {
        var bxi = Math.min(bw - 1, (xx / BS) | 0), byi = Math.min(bh - 1, (yy / BS) | 0);
        // average the 3x3 neighbourhood of blocks — smooths lighting gradients
        var acc = 0, k = 0;
        for (var dy = -1; dy <= 1; dy++) {
          for (var dx = -1; dx <= 1; dx++) {
            var nx = bxi + dx, ny = byi + dy;
            if (nx >= 0 && nx < bw && ny >= 0 && ny < bh) { acc += means[ny * bw + nx]; k++; }
          }
        }
        var thr = (acc / k) - 6;             // small bias toward calling it dark
        out[yy * w + xx] = gray[yy * w + xx] < thr ? 1 : 0;   // 1 = dark module
      }
    }
    return out;
  }

  // Find the three finder patterns by scanning rows for the 1:1:3:1:1 signature.
  function findFinders(bin, w, h) {
    var cands = [];

    for (var y = 0; y < h; y++) {
      // Build run-lengths for this row: alternating runs of dark/light.
      var runs = [];
      var cur = bin[y * w], len = 1;
      for (var x = 1; x < w; x++) {
        var v = bin[y * w + x];
        if (v === cur) { len++; }
        else { runs.push({ v: cur, len: len, end: x }); cur = v; len = 1; }
      }
      runs.push({ v: cur, len: len, end: w });

      // Look for dark-light-dark-light-dark with ratios 1:1:3:1:1.
      for (var i = 0; i + 4 < runs.length; i++) {
        if (runs[i].v !== 1) continue;                     // must start dark
        var a = runs[i], b = runs[i + 1], c = runs[i + 2],
            d = runs[i + 3], e = runs[i + 4];
        if (b.v !== 0 || c.v !== 1 || d.v !== 0 || e.v !== 1) continue;

        var total = a.len + b.len + c.len + d.len + e.len;
        if (total < 7) continue;
        var unit = total / 7;
        var tol = unit * 0.5;                              // generous: phone cameras blur
        if (Math.abs(a.len - unit) > tol) continue;
        if (Math.abs(b.len - unit) > tol) continue;
        if (Math.abs(c.len - unit * 3) > tol * 1.5) continue;
        if (Math.abs(d.len - unit) > tol) continue;
        if (Math.abs(e.len - unit) > tol) continue;

        // centre of the middle (3-unit) run = centre of the finder
        var cx = c.end - c.len / 2;

        // Verify vertically through that centre — kills false positives from
        // barcode-ish patterns elsewhere in the frame.
        if (!vCheck(bin, w, h, Math.round(cx), y)) continue;

        cands.push({ x: cx, y: y, size: total });
      }
    }
    if (cands.length < 3) return null;

    // Cluster: the same finder is detected on many consecutive rows.
    var groups = [];
    for (var k = 0; k < cands.length; k++) {
      var p = cands[k], placed = false;
      for (var gi = 0; gi < groups.length; gi++) {
        var g = groups[gi];
        if (Math.abs(g.x - p.x) < g.size * 0.5 && Math.abs(g.y - p.y) < g.size * 0.7) {
          g.x = (g.x * g.n + p.x) / (g.n + 1);
          g.y = (g.y * g.n + p.y) / (g.n + 1);
          g.size = (g.size * g.n + p.size) / (g.n + 1);
          g.n++;
          placed = true;
          break;
        }
      }
      if (!placed) groups.push({ x: p.x, y: p.y, size: p.size, n: 1 });
    }

    groups = groups.filter(function (g) { return g.n >= 3; });
    if (groups.length < 3) return null;
    groups.sort(function (u, v2) { return v2.n - u.n; });
    return groups.slice(0, 3);
  }

  /* Confirm a 1:1:3:1:1 pattern vertically through (cx, cy) too.
     Walks outward from the dark core, alternating run by run. */
  function vCheck(bin, w, h, cx, cy) {
    if (cx < 0 || cx >= w) return false;
    function at(y) { return (y >= 0 && y < h) ? bin[y * w + cx] : 0; }
    if (!at(cy)) return false;

    // Extent of the dark core containing cy.
    var top = cy, bot = cy;
    while (top - 1 >= 0 && at(top - 1)) top--;
    while (bot + 1 < h && at(bot + 1)) bot++;
    var centre = bot - top + 1;

    // Light run immediately above, then dark above that.
    var y = top - 1, upLight = 0;
    while (y >= 0 && !at(y)) { upLight++; y--; }
    var upDark = 0;
    while (y >= 0 && at(y)) { upDark++; y--; }

    // Light run immediately below, then dark below that.
    y = bot + 1;
    var dnLight = 0;
    while (y < h && !at(y)) { dnLight++; y++; }
    var dnDark = 0;
    while (y < h && at(y)) { dnDark++; y++; }

    if (!upLight || !dnLight || !upDark || !dnDark) return false;

    var tot = centre + upLight + dnLight + upDark + dnDark;
    var u = tot / 7, tol = u * 0.6;
    return Math.abs(centre - u * 3) <= tol * 1.6
        && Math.abs(upLight - u) <= tol && Math.abs(dnLight - u) <= tol
        && Math.abs(upDark - u) <= tol && Math.abs(dnDark - u) <= tol;
  }

  // Work out which finder is the top-left corner, and orient the other two.
  function orient(p) {
    function d2(a, b) { var dx = a.x - b.x, dy = a.y - b.y; return dx * dx + dy * dy; }
    var d01 = d2(p[0], p[1]), d02 = d2(p[0], p[2]), d12 = d2(p[1], p[2]);
    var tl, a, b;
    if (d12 >= d01 && d12 >= d02) { tl = p[0]; a = p[1]; b = p[2]; }
    else if (d02 >= d01 && d02 >= d12) { tl = p[1]; a = p[0]; b = p[2]; }
    else { tl = p[2]; a = p[0]; b = p[1]; }
    // cross product decides which of a/b is "top-right" vs "bottom-left"
    var cross = (a.x - tl.x) * (b.y - tl.y) - (a.y - tl.y) * (b.x - tl.x);
    return cross < 0 ? { tl: tl, tr: b, bl: a } : { tl: tl, tr: a, bl: b };
  }

  function sampleGrid(bin, w, h, o, size) {
    var mod = o.tl.size / 7;
    // finder centres sit 3.5 modules in from each corner
    var ux = (o.tr.x - o.tl.x) / (size - 7), uy = (o.tr.y - o.tl.y) / (size - 7);
    var vx = (o.bl.x - o.tl.x) / (size - 7), vy = (o.bl.y - o.tl.y) / (size - 7);
    var ox = o.tl.x - 3.5 * ux - 3.5 * vx;
    var oy = o.tl.y - 3.5 * uy - 3.5 * vy;

    var g = new Uint8Array(size * size);
    for (var r = 0; r < size; r++) {
      for (var c = 0; c < size; c++) {
        // sample a 3x3 neighbourhood and take the majority — resists speckle
        var dark = 0, tot = 0;
        for (var sy = -1; sy <= 1; sy++) {
          for (var sx = -1; sx <= 1; sx++) {
            var px = Math.round(ox + (c + 0.5) * ux + (r + 0.5) * vx + sx * mod * 0.22);
            var py = Math.round(oy + (c + 0.5) * uy + (r + 0.5) * vy + sy * mod * 0.22);
            if (px < 0 || py < 0 || px >= w || py >= h) continue;
            dark += bin[py * w + px]; tot++;
          }
        }
        g[r * size + c] = (tot && dark * 2 > tot) ? 1 : 0;
      }
    }
    return g;
  }

  function readFormat(g, size) {
    // format info is duplicated; try the copy beside the top-left finder
    var bits = 0, i;
    for (i = 0; i <= 5; i++) bits = (bits << 1) | g[8 * size + i];
    bits = (bits << 1) | g[8 * size + 7];
    bits = (bits << 1) | g[8 * size + 8];
    bits = (bits << 1) | g[7 * size + 8];
    for (i = 5; i >= 0; i--) bits = (bits << 1) | g[i * size + 8];
    bits ^= 0x5412;                          // unmask
    return { ec: EC_LEVELS[(bits >> 13) & 3], mask: (bits >> 10) & 7 };
  }

  function functionMask(size, version) {
    var f = new Uint8Array(size * size), i, j;
    function block(r, c, hgt, wid) {
      for (var y = r; y < r + hgt; y++)
        for (var x = c; x < c + wid; x++)
          if (y >= 0 && x >= 0 && y < size && x < size) f[y * size + x] = 1;
    }
    block(0, 0, 9, 9);
    block(0, size - 8, 9, 8);
    block(size - 8, 0, 8, 9);
    for (i = 0; i < size; i++) { f[6 * size + i] = 1; f[i * size + 6] = 1; }  // timing
    var ap = ALIGN_POS[version] || [];
    for (i = 0; i < ap.length; i++) {
      for (j = 0; j < ap.length; j++) {
        var r = ap[i], c = ap[j];
        if ((r === 6 && c === 6) || (r === 6 && c === size - 7) || (r === size - 7 && c === 6)) continue;
        block(r - 2, c - 2, 5, 5);
      }
    }
    if (version >= 7) { block(size - 11, 0, 3, 6); block(0, size - 11, 6, 3); }
    return f;
  }

  function extractCodewords(g, size, version, mask) {
    var fn = functionMask(size, version);
    var bits = [], up = true;
    for (var col = size - 1; col > 0; col -= 2) {
      if (col === 6) col--;                  // skip the vertical timing line
      for (var k = 0; k < size; k++) {
        var row = up ? size - 1 - k : k;
        for (var c2 = 0; c2 < 2; c2++) {
          var cc = col - c2;
          if (fn[row * size + cc]) continue;
          var v = g[row * size + cc];
          if (maskFn(mask, row, cc)) v ^= 1;
          bits.push(v);
        }
      }
      up = !up;
    }
    var bytes = [];
    for (var i = 0; i + 7 < bits.length; i += 8) {
      var b = 0;
      for (var j = 0; j < 8; j++) b = (b << 1) | bits[i + j];
      bytes.push(b);
    }
    return bytes;
  }

  /* De-interleave the blocks, run error correction on each, and concatenate the
     data. QR interleaves codewords across blocks, so this order matters. */
  function deinterleave(codewords, version, ecLevel) {
    var spec = EC_TABLE[version] && EC_TABLE[version][ecLevel];
    if (!spec) return null;
    var ecLen = spec[0], g1 = spec[1], d1 = spec[2], g2 = spec[3], d2 = spec[4];

    var blocks = [];
    for (var i = 0; i < g1; i++) blocks.push({ data: new Uint8Array(d1), ec: new Uint8Array(ecLen) });
    for (var j = 0; j < g2; j++) blocks.push({ data: new Uint8Array(d2), ec: new Uint8Array(ecLen) });

    var p = 0, maxData = Math.max(d1, d2), k;
    for (k = 0; k < maxData; k++) {
      for (var b = 0; b < blocks.length; b++) {
        if (k < blocks[b].data.length) blocks[b].data[k] = codewords[p++];
      }
    }
    for (k = 0; k < ecLen; k++) {
      for (var b2 = 0; b2 < blocks.length; b2++) blocks[b2].ec[k] = codewords[p++];
    }

    var out = [];
    for (var b3 = 0; b3 < blocks.length; b3++) {
      var full = new Uint8Array(blocks[b3].data.length + ecLen);
      full.set(blocks[b3].data, 0);
      full.set(blocks[b3].ec, blocks[b3].data.length);
      if (!rsCorrect(full, ecLen)) return null;     // unrecoverable
      for (var m = 0; m < blocks[b3].data.length; m++) out.push(full[m]);
    }
    return out;
  }

  function decodeBytes(data, version) {
    var bitPos = 0;
    function read(n) {
      var v = 0;
      for (var i = 0; i < n; i++) {
        var byteI = (bitPos >> 3), bitI = 7 - (bitPos & 7);
        if (byteI >= data.length) return -1;
        v = (v << 1) | ((data[byteI] >> bitI) & 1);
        bitPos++;
      }
      return v;
    }
    var out = "";
    for (;;) {
      var mode = read(4);
      if (mode <= 0 || mode === -1) break;             // 0 = terminator
      if (mode === 4) {                                // byte mode (what we emit)
        var lenBits = version <= 9 ? 8 : 16;
        var len = read(lenBits);
        if (len < 0) break;
        var bytes = [];
        for (var i = 0; i < len; i++) {
          var b = read(8);
          if (b < 0) break;
          bytes.push(b);
        }
        try {
          out += new TextDecoder("utf-8").decode(new Uint8Array(bytes));
        } catch (e) {
          out += bytes.map(function (c) { return String.fromCharCode(c); }).join("");
        }
      } else if (mode === 1) {                          // numeric
        var n = read(version <= 9 ? 10 : 12), s = "";
        while (n > 0) {
          var take = Math.min(3, n);
          var bitsN = take === 3 ? 10 : take === 2 ? 7 : 4;
          var val = read(bitsN);
          s += String(val).padStart(take, "0");
          n -= take;
        }
        out += s;
      } else {
        break;                                          // other modes unused here
      }
    }
    return out;
  }

  /* Decode a QR from an ImageData. Returns the string, or null. */
  function decode(imageData) {
    var w = imageData.width, h = imageData.height;
    var bin = binarize(toGray(imageData), w, h);
    var finders = findFinders(bin, w, h);
    if (!finders) return null;
    var o = orient(finders);

    // Estimate the module size, then the version, from the finder spacing.
    var mod = o.tl.size / 7;
    var dist = Math.hypot(o.tr.x - o.tl.x, o.tr.y - o.tl.y);
    var est = dist / mod + 7;
    var version = Math.round((est - 17) / 4);
    // Try the best guess first, then neighbours — the estimate can be a version out.
    var tries = [version, version - 1, version + 1, version - 2, version + 2];
    for (var t = 0; t < tries.length; t++) {
      var v = tries[t];
      if (v < 1 || v > 10) continue;
      var size = v * 4 + 17;
      var g = sampleGrid(bin, w, h, o, size);
      var fmt;
      try { fmt = readFormat(g, size); } catch (e) { continue; }
      if (!fmt.ec) continue;
      var cw = extractCodewords(g, size, v, fmt.mask);
      var data = deinterleave(cw, v, fmt.ec);
      if (!data) continue;
      var text = decodeBytes(data, v);
      if (text) return text;
    }
    return null;
  }

  global.QRScan = { decode: decode };
})(window);
