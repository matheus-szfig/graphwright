// pagestate.js — page observation, evaluated inside a CDP isolated world (one per frame).
//
// Exposes globalThis.__ps in THAT world only. The page's own scripts cannot see it:
// no DOM attributes are written, no main-world globals are created. Element ids live in
// a WeakMap and are stable for the lifetime of the frame.
//
// API:
//   __ps.extract(opts, prefix) -> raw frame observation (merged in Python)
//   __ps.signature()           -> cheap string hash of "what matters" on the page
//   __ps.box(id)               -> geometry + occlusion for one element (action layer)
//   __ps.frameOffset()         -> this frame's <iframe> rect in its parent, or null
(() => {
  if (globalThis.__ps) return;

  const registry = new WeakMap(); // Element -> id
  const byId = new Map();         // id -> WeakRef<Element>
  let counter = 0;
  let framePrefix = "";

  const INTERACTIVE_SEL = [
    "a[href]", "button", "input", "select", "textarea", "summary",
    "[role=button]", "[role=link]", "[role=textbox]", "[role=combobox]", "[role=checkbox]",
    "[role=radio]", "[role=tab]", "[role=menuitem]", "[role=option]", "[role=switch]",
    "[onclick]", '[tabindex]:not([tabindex="-1"])', '[contenteditable=""]', '[contenteditable="true"]',
  ].join(",");
  const HEADING_SEL = "h1,h2,h3,h4,[role=heading]";
  // ARIA live regions plus the class names server-rendered apps commonly use for
  // messages (Bootstrap, JSF/PrimeFaces/RichFaces, ASP.NET validation, i18n variants).
  const ALERT_SEL = [
    "[role=alert]", "[role=status]", "[aria-live=polite]", "[aria-live=assertive]",
    ".alert", ".error", ".erro", ".mensagem", ".msg", ".warning", ".aviso", ".feedback",
    ".ui-messages", ".ui-message", ".ui-growl-message", ".rich-messages", ".rf-msgs",
    ".validation-summary-errors", ".field-validation-error", ".alert-danger", ".alert-warning",
    "#mensagemRetorno", ".mensagemExibindo", ".mensagemErro", ".mensagem-erro", ".msgErro",
  ].join(",");
  const LANDMARK_SEL = [
    "main", "[role=main]", "form", "nav", "[role=navigation]", "header", "[role=banner]",
    "footer", "[role=contentinfo]", "aside", "[role=complementary]", "section[aria-label]",
    "fieldset", "table", "[role=dialog]", "dialog", "[role=tabpanel]",
  ].join(",");

  // --- helpers ---------------------------------------------------------------------

  const norm = (s) => (s == null ? "" : String(s).replace(/\s+/g, " ").trim());
  const clip = (s, n) => (s.length <= n ? s : s.slice(0, n - 1) + "…");
  const textOf = (el, n = 80) => clip(norm(el.textContent), n);

  function idFor(el) {
    let id = registry.get(el);
    if (!id) {
      id = framePrefix + "e" + (++counter);
      registry.set(el, id);
      byId.set(id, new WeakRef(el));
    }
    return id;
  }

  function roleOf(el) {
    const explicit = el.getAttribute("role");
    if (explicit) return explicit.split(/\s+/)[0].toLowerCase();
    const t = el.tagName.toLowerCase();
    switch (t) {
      case "a": return el.hasAttribute("href") ? "link" : "generic";
      case "button":
      case "summary": return "button";
      case "select": return "combobox";
      case "textarea": return "textbox";
      case "table": return "table";
      case "img": return "img";
      case "input": {
        const ty = (el.getAttribute("type") || "text").toLowerCase();
        if (ty === "hidden") return null;
        if (ty === "button" || ty === "submit" || ty === "reset" || ty === "image") return "button";
        if (ty === "checkbox") return "checkbox";
        if (ty === "radio") return "radio";
        if (ty === "file") return "fileinput";
        if (ty === "range") return "slider";
        return "textbox";
      }
    }
    if (/^h[1-6]$/.test(t)) return "heading";
    if (el.isContentEditable) return "textbox";
    if (el.hasAttribute("onclick") || el.hasAttribute("tabindex")) return "clickable";
    return "generic";
  }

  function visible(el) {
    if (!el.isConnected) return false;
    if (typeof el.checkVisibility === "function" &&
        !el.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true })) return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  }

  function inViewport(r) {
    return r.bottom > 0 && r.right > 0 && r.top < innerHeight && r.left < innerWidth;
  }

  // Approximate accessible name. Not the full AccName spec — tuned for legacy portals
  // where labels are often plain text in the previous table cell (see nearby()).
  function accName(el) {
    const aria = norm(el.getAttribute("aria-label"));
    if (aria) return clip(aria, 80);
    const lb = el.getAttribute("aria-labelledby");
    if (lb) {
      const s = norm(lb.split(/\s+/).map((id) => {
        const n = el.ownerDocument.getElementById(id);
        return n ? n.textContent : "";
      }).join(" "));
      if (s) return clip(s, 80);
    }
    const t = el.tagName.toLowerCase();
    if (t === "input" || t === "select" || t === "textarea") {
      if (el.id) {
        const lab = el.ownerDocument.querySelector(`label[for="${CSS.escape(el.id)}"]`);
        if (lab) { const s = labelText(lab); if (s) return s; }
      }
      // A wrapping <label> names the control only if it wraps just this one control;
      // JSF/RichFaces radio groups wrap the whole group in one label.
      const wrap = el.closest("label");
      if (wrap && wrap.querySelectorAll("input,select,textarea").length <= 1) {
        const s = textOf(wrap); if (s) return s;
      }
      const ty = (el.getAttribute("type") || "").toLowerCase();
      if (ty === "checkbox" || ty === "radio") {
        const s = siblingLabel(el); if (s) return s;
      }
      if ((ty === "button" || ty === "submit" || ty === "reset") && el.value) return clip(norm(el.value), 80);
      if (ty === "image" && el.alt) return clip(norm(el.alt), 80);
      if (el.placeholder) return clip(norm(el.placeholder), 80);
      if (el.title) return clip(norm(el.title), 80);
      return "";
    }
    if (t === "img") return clip(norm(el.alt || el.title), 80);
    const txt = textOf(el);
    if (txt) return txt;
    const img = el.querySelector("img[alt]");
    if (img) return clip(norm(img.alt), 80);
    return clip(norm(el.title), 80);
  }

  const hasControl = (n) => n.nodeType === 1 && (n.matches(INTERACTIVE_SEL) || !!n.querySelector(INTERACTIVE_SEL));
  const hasWord = (s) => /[\p{L}\p{N}]/u.test(s);

  // A <label for=X> may wrap other controls — some JSF apps put a radio group inside
  // the text field's label. Then X is named by the label's first text piece only.
  function labelText(lab) {
    if (!lab.querySelector(INTERACTIVE_SEL)) return textOf(lab);
    for (const n of lab.childNodes) {
      if (hasControl(n)) break;
      const s = norm(n.nodeType === 3 ? n.nodeValue : n.textContent);
      if (hasWord(s)) return clip(s, 80);
    }
    return "";
  }

  // Radio/checkbox with no label of its own: its container alternates text and controls.
  // Decide once per container whether text comes before or after each control
  // (label-first: "Unified <input> Free <input>"; label-after: "<input> Unified"),
  // then take the adjacent text on that side.
  function siblingLabel(el) {
    const parent = el.parentElement;
    if (!parent) return "";
    const seq = [];
    for (const n of parent.childNodes) {
      if (n.nodeType === 3) { const s = norm(n.nodeValue); if (hasWord(s)) seq.push({ ctl: false, s, n }); }
      else if (n.nodeType === 1) {
        if (hasControl(n)) seq.push({ ctl: true, n });
        else { const s = textOf(n); if (hasWord(s)) seq.push({ ctl: false, s, n }); }
      }
    }
    const i = seq.findIndex((x) => x.n === el);
    if (i < 0) return "";
    const textFirst = !seq[0].ctl;
    const pick = textFirst ? seq[i - 1] : seq[i + 1];
    return pick && !pick.ctl ? clip(pick.s, 80) : "";
  }

  // Text physically near the element: previous table cell (legacy layouts), preceding
  // siblings/text nodes, the parent's own text, the enclosing fieldset legend.
  function nearby(el, ownName) {
    const out = [];
    // Must contain a letter or digit: skips separators such as "-", "|", ":" and "*".
    const push = (s) => {
      s = norm(s);
      if (s && s !== ownName && s.length <= 60 && /[\p{L}\p{N}]/u.test(s) && !out.includes(s)) out.push(s);
    };

    const cell = el.closest("td,th");
    if (cell) {
      let p = cell.previousElementSibling;
      let hops = 0;
      while (p && hops < 2 && out.length < 1) { push(textOf(p, 60)); p = p.previousElementSibling; hops++; }
    }
    let node = el.previousSibling, hops = 0;
    while (node && hops < 4 && out.length < 2) {
      if (node.nodeType === 3) push(node.nodeValue);
      else if (node.nodeType === 1 && !node.matches(INTERACTIVE_SEL)) push(node.textContent);
      node = node.previousSibling; hops++;
    }
    if (!out.length && el.parentElement) {
      push(Array.from(el.parentElement.childNodes).filter((n) => n.nodeType === 3).map((n) => n.nodeValue).join(" "));
    }
    const fs = el.closest("fieldset");
    if (fs) { const lg = fs.querySelector(":scope > legend"); if (lg) push(lg.textContent); }
    return out.slice(0, 3);
  }

  function landmarkOf(el) {
    const lm = el.parentElement && el.parentElement.closest(LANDMARK_SEL);
    if (!lm) return null;
    const t = lm.tagName.toLowerCase();
    const kind = lm.getAttribute("role") || t;
    let label = lm.getAttribute("aria-label") || "";
    if (!label && t === "fieldset") { const lg = lm.querySelector(":scope > legend"); if (lg) label = lg.textContent; }
    if (!label && t === "table" && lm.caption) label = lm.caption.textContent;
    if (!label) label = lm.id || lm.getAttribute("name") || "";
    label = clip(norm(label), 30);
    return label ? `${kind}#${label}` : kind;
  }

  function states(el, role) {
    const s = [];
    if (el.disabled || el.getAttribute("aria-disabled") === "true") s.push("disabled");
    if ((role === "checkbox" || role === "radio") && (el.checked || el.getAttribute("aria-checked") === "true")) s.push("checked");
    if (el.required || el.getAttribute("aria-required") === "true") s.push("required");
    if (el.readOnly) s.push("readonly");
    const exp = el.getAttribute("aria-expanded"); if (exp) s.push(exp === "true" ? "expanded" : "collapsed");
    if (el.getAttribute("aria-selected") === "true") s.push("selected");
    if (el.getAttribute("aria-current")) s.push("current");
    return s;
  }

  function valueOf(el, role) {
    const t = el.tagName.toLowerCase();
    if (t === "select") {
      const o = el.selectedOptions && el.selectedOptions[0];
      return o ? clip(norm(o.textContent), 40) : null;
    }
    if (role === "checkbox" || role === "radio" || role === "button") return null;
    if (t === "input" || t === "textarea") {
      const ty = (el.getAttribute("type") || "").toLowerCase();
      if (!el.value) return null;
      return ty === "password" ? "•••" : clip(norm(el.value), 40);
    }
    if (el.isContentEditable) { const s = textOf(el, 40); return s || null; }
    return null;
  }

  function optionsOf(el) {
    if (el.tagName.toLowerCase() !== "select") return [];
    return Array.from(el.options).slice(0, 24).map((o) => clip(norm(o.textContent), 40)).filter(Boolean);
  }

  function tableShape(t) {
    const headerCells = t.querySelectorAll("thead th, thead td, tr:first-child th");
    const cells = headerCells.length ? headerCells : (t.rows[0] ? t.rows[0].cells : []);
    const headers = Array.from(cells).slice(0, 12).map((c) => clip(norm(c.textContent), 30)).filter(Boolean);
    const rows = t.tBodies.length ? Array.from(t.tBodies).reduce((n, b) => n + b.rows.length, 0) : t.rows.length;
    const name = clip(norm((t.caption && t.caption.textContent) || t.getAttribute("aria-label") || t.id || ""), 40) || null;
    return { id: idFor(t), name, headers, rows };
  }

  function isDataTable(t) {
    if (!t.rows || t.rows.length < 2) return false;
    if (t.querySelector("table")) return false; // layout table wrapping others
    let maxCells = 0;
    for (let i = 0; i < Math.min(t.rows.length, 5); i++) maxCells = Math.max(maxCells, t.rows[i].cells.length);
    return maxCells >= 2;
  }

  function fnv1a(str) {
    let h = 0x811c9dc5;
    for (let i = 0; i < str.length; i++) { h ^= str.charCodeAt(i); h = Math.imul(h, 0x01000193); }
    return (h >>> 0).toString(16).padStart(8, "0");
  }

  // --- API ------------------------------------------------------------------------

  function extract(opts, prefix) {
    const t0 = performance.now();
    opts = Object.assign({ maxElements: 400, includeHidden: false }, opts || {});
    if (typeof prefix === "string") framePrefix = prefix;

    const candidates = document.querySelectorAll(INTERACTIVE_SEL);
    const elements = [];
    let hidden = 0;
    for (const el of candidates) {
      const role = roleOf(el);
      if (!role) { hidden++; continue; }
      const vis = visible(el);
      if (!vis) { hidden++; if (!opts.includeHidden) continue; }
      const r = el.getBoundingClientRect();
      const name = accName(el);
      const rec = {
        id: idFor(el), role, tag: el.tagName.toLowerCase(), name,
        dom_id: el.id || null, // the HTML id attribute — stable on legacy portals, usable in recognize
        value: valueOf(el, role),
        href: role === "link" ? clip(norm(el.getAttribute("href")), 120) || null : null,
        options: optionsOf(el),
        states: states(el, role),
        landmark: landmarkOf(el),
        nearby: nearby(el, name),
        bbox: { x: Math.round(r.left), y: Math.round(r.top), w: Math.round(r.width), h: Math.round(r.height) },
        in_viewport: inViewport(r),
      };
      elements.push(rec);
      if (elements.length >= opts.maxElements) break;
    }

    const headings = Array.from(document.querySelectorAll(HEADING_SEL)).filter(visible).slice(0, 20).map((h) => ({
      level: Number(h.getAttribute("aria-level")) || Number((h.tagName.match(/\d/) || [2])[0]),
      text: textOf(h, 100),
    })).filter((h) => h.text);

    const alerts = [];
    for (const a of document.querySelectorAll(ALERT_SEL)) {
      if (!visible(a)) continue;
      const s = clip(norm(a.innerText), 240);
      if (s && !alerts.includes(s)) alerts.push(s);
      if (alerts.length >= 10) break;
    }

    const tables = Array.from(document.querySelectorAll("table")).filter((t) => visible(t) && isDataTable(t)).slice(0, 20).map(tableShape);

    return {
      url: location.href,
      title: document.title,
      lang: document.documentElement.lang || null,
      ready: document.readyState,
      viewport: { w: innerWidth, h: innerHeight, scroll_y: Math.round(scrollY) },
      elements, headings, alerts, tables,
      stats: { candidates: candidates.length, hidden, kept: elements.length, extract_ms: Math.round((performance.now() - t0) * 10) / 10 },
    };
  }

  function signature() {
    const main = document.querySelector("main,[role=main]") || document.body;
    const txt = main ? main.textContent.slice(0, 20000) : "";
    const n = document.querySelectorAll(INTERACTIVE_SEL).length;
    return fnv1a(location.href + "|" + document.title + "|" + n + "|" + txt);
  }

  function box(id) {
    const ref = byId.get(id);
    const el = ref && ref.deref();
    if (!el || !el.isConnected) return null;
    const r = el.getBoundingClientRect();
    const vis = visible(el);
    const inView = inViewport(r);
    let occluded = null;
    if (vis && inView) {
      const cx = Math.min(Math.max(r.left + r.width / 2, 0), innerWidth - 1);
      const cy = Math.min(Math.max(r.top + r.height / 2, 0), innerHeight - 1);
      const top = document.elementFromPoint(cx, cy);
      occluded = !!top && !(top === el || el.contains(top) || top.contains(el));
    }
    return {
      id, visible: vis, in_viewport: inView, occluded,
      disabled: !!(el.disabled || el.getAttribute("aria-disabled") === "true"),
      bbox: { x: r.left, y: r.top, w: r.width, h: r.height },
    };
  }

  // Runtime helpers for the action layer -------------------------------------------

  // Resolve a css/xpath strategy against the live DOM; returns registered ids so the
  // result is addressable like any observed element.
  function query(selector, max) {
    const out = [];
    let nodes;
    if (selector.startsWith("xpath=") || selector.startsWith("//")) {
      const expr = selector.replace(/^xpath=/, "");
      const r = document.evaluate(expr, document, null, XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null);
      nodes = []; for (let i = 0; i < r.snapshotLength; i++) nodes.push(r.snapshotItem(i));
    } else {
      nodes = document.querySelectorAll(selector);
    }
    for (const el of nodes) {
      if (!(el instanceof Element) || !visible(el)) continue;
      out.push(idFor(el));
      if (out.length >= (max || 20)) break;
    }
    return out;
  }

  // Pick a <select> option by visible text (pattern: /re/flags or substring).
  function selectOption(id, pattern) {
    const el = byId.get(id) && byId.get(id).deref();
    if (!el || el.tagName.toLowerCase() !== "select") return { ok: false, reason: "not a select" };
    const m = /^\/(.*)\/([ims]*)$/.exec(pattern);
    const re = m ? new RegExp(m[1], m[2]) : null;
    const want = pattern.toLowerCase();
    const opt = Array.from(el.options).find((o) => {
      const t = norm(o.textContent);
      return re ? re.test(t) : t.toLowerCase().includes(want);
    });
    if (!opt) return { ok: false, reason: "no option matches", options: optionsOf(el) };
    if (el.value !== opt.value) {
      el.value = opt.value;
      el.dispatchEvent(new Event("input", { bubbles: true }));
      el.dispatchEvent(new Event("change", { bubbles: true }));
    }
    return { ok: true, text: norm(opt.textContent) };
  }

  function scrollIntoView(id) {
    const el = byId.get(id) && byId.get(id).deref();
    if (!el) return false;
    el.scrollIntoView({ block: "center", inline: "nearest" });
    return true;
  }

  function valueOfId(id) {
    const el = byId.get(id) && byId.get(id).deref();
    return el ? (el.value != null ? String(el.value) : norm(el.textContent)) : null;
  }

  // Text of one element (full, not clipped) — what `extract.fields` reads.
  function textOfId(id) {
    const el = byId.get(id) && byId.get(id).deref();
    if (!el) return null;
    if (el.value != null && el.tagName.toLowerCase() !== "button") return String(el.value);
    return norm(el.innerText != null ? el.innerText : el.textContent);
  }

  // Rows of a data table as objects keyed by header text (or c0, c1… without
  // headers) — what `extract.tables` reads. Header rows are skipped.
  function tableRows(id, max) {
    const t = byId.get(id) && byId.get(id).deref();
    if (!t || t.tagName.toLowerCase() !== "table") return null;
    const headerCells = t.querySelectorAll("thead th, thead td");
    let headers = Array.from(headerCells).map((c) => norm(c.textContent));
    const rows = [];
    for (const tr of Array.from(t.rows)) {
      if (tr.parentElement && tr.parentElement.tagName.toLowerCase() === "thead") continue;
      const cells = Array.from(tr.cells);
      if (!headers.length && cells.every((c) => c.tagName.toLowerCase() === "th")) {
        headers = cells.map((c) => norm(c.textContent));
        continue;
      }
      const row = {};
      cells.forEach((c, i) => { row[headers[i] || "c" + i] = norm(c.innerText != null ? c.innerText : c.textContent); });
      rows.push(row);
      if (rows.length >= max) break;
    }
    return { headers, rows };
  }

  function frameOffset() {
    try {
      const fe = window.frameElement;
      if (!fe) return null;
      const r = fe.getBoundingClientRect();
      return { x: r.left, y: r.top };
    } catch (_) { return null; } // cross-origin parent
  }

  globalThis.__ps = { extract, signature, box, frameOffset, query, selectOption, scrollIntoView, valueOfId, textOfId, tableRows };
})();
