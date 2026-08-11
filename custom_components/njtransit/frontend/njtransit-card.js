/**
 * @license
 * Copyright 2019 Google LLC
 * SPDX-License-Identifier: BSD-3-Clause
 */
const M = globalThis, B = M.ShadowRoot && (M.ShadyCSS === void 0 || M.ShadyCSS.nativeShadow) && "adoptedStyleSheets" in Document.prototype && "replace" in CSSStyleSheet.prototype, q = Symbol(), Y = /* @__PURE__ */ new WeakMap();
let pt = class {
  constructor(t, r, s) {
    if (this._$cssResult$ = !0, s !== q) throw Error("CSSResult is not constructable. Use `unsafeCSS` or `css` instead.");
    this.cssText = t, this.t = r;
  }
  get styleSheet() {
    let t = this.o;
    const r = this.t;
    if (B && t === void 0) {
      const s = r !== void 0 && r.length === 1;
      s && (t = Y.get(r)), t === void 0 && ((this.o = t = new CSSStyleSheet()).replaceSync(this.cssText), s && Y.set(r, t));
    }
    return t;
  }
  toString() {
    return this.cssText;
  }
};
const wt = (e) => new pt(typeof e == "string" ? e : e + "", void 0, q), xt = (e, ...t) => {
  const r = e.length === 1 ? e[0] : t.reduce((s, n, i) => s + ((o) => {
    if (o._$cssResult$ === !0) return o.cssText;
    if (typeof o == "number") return o;
    throw Error("Value passed to 'css' function must be a 'css' function result: " + o + ". Use 'unsafeCSS' to pass non-literal values, but take care to ensure page security.");
  })(n) + e[i + 1], e[0]);
  return new pt(r, e, q);
}, At = (e, t) => {
  if (B) e.adoptedStyleSheets = t.map((r) => r instanceof CSSStyleSheet ? r : r.styleSheet);
  else for (const r of t) {
    const s = document.createElement("style"), n = M.litNonce;
    n !== void 0 && s.setAttribute("nonce", n), s.textContent = r.cssText, e.appendChild(s);
  }
}, X = B ? (e) => e : (e) => e instanceof CSSStyleSheet ? ((t) => {
  let r = "";
  for (const s of t.cssRules) r += s.cssText;
  return wt(r);
})(e) : e;
/**
 * @license
 * Copyright 2017 Google LLC
 * SPDX-License-Identifier: BSD-3-Clause
 */
const { is: Et, defineProperty: St, getOwnPropertyDescriptor: kt, getOwnPropertyNames: Ct, getOwnPropertySymbols: Tt, getPrototypeOf: Pt } = Object, H = globalThis, Q = H.trustedTypes, Ot = Q ? Q.emptyScript : "", Ut = H.reactiveElementPolyfillSupport, k = (e, t) => e, j = { toAttribute(e, t) {
  switch (t) {
    case Boolean:
      e = e ? Ot : null;
      break;
    case Object:
    case Array:
      e = e == null ? e : JSON.stringify(e);
  }
  return e;
}, fromAttribute(e, t) {
  let r = e;
  switch (t) {
    case Boolean:
      r = e !== null;
      break;
    case Number:
      r = e === null ? null : Number(e);
      break;
    case Object:
    case Array:
      try {
        r = JSON.parse(e);
      } catch {
        r = null;
      }
  }
  return r;
} }, W = (e, t) => !Et(e, t), tt = { attribute: !0, type: String, converter: j, reflect: !1, useDefault: !1, hasChanged: W };
Symbol.metadata ??= Symbol("metadata"), H.litPropertyMetadata ??= /* @__PURE__ */ new WeakMap();
let y = class extends HTMLElement {
  static addInitializer(t) {
    this._$Ei(), (this.l ??= []).push(t);
  }
  static get observedAttributes() {
    return this.finalize(), this._$Eh && [...this._$Eh.keys()];
  }
  static createProperty(t, r = tt) {
    if (r.state && (r.attribute = !1), this._$Ei(), this.prototype.hasOwnProperty(t) && ((r = Object.create(r)).wrapped = !0), this.elementProperties.set(t, r), !r.noAccessor) {
      const s = Symbol(), n = this.getPropertyDescriptor(t, s, r);
      n !== void 0 && St(this.prototype, t, n);
    }
  }
  static getPropertyDescriptor(t, r, s) {
    const { get: n, set: i } = kt(this.prototype, t) ?? { get() {
      return this[r];
    }, set(o) {
      this[r] = o;
    } };
    return { get: n, set(o) {
      const l = n?.call(this);
      i?.call(this, o), this.requestUpdate(t, l, s);
    }, configurable: !0, enumerable: !0 };
  }
  static getPropertyOptions(t) {
    return this.elementProperties.get(t) ?? tt;
  }
  static _$Ei() {
    if (this.hasOwnProperty(k("elementProperties"))) return;
    const t = Pt(this);
    t.finalize(), t.l !== void 0 && (this.l = [...t.l]), this.elementProperties = new Map(t.elementProperties);
  }
  static finalize() {
    if (this.hasOwnProperty(k("finalized"))) return;
    if (this.finalized = !0, this._$Ei(), this.hasOwnProperty(k("properties"))) {
      const r = this.properties, s = [...Ct(r), ...Tt(r)];
      for (const n of s) this.createProperty(n, r[n]);
    }
    const t = this[Symbol.metadata];
    if (t !== null) {
      const r = litPropertyMetadata.get(t);
      if (r !== void 0) for (const [s, n] of r) this.elementProperties.set(s, n);
    }
    this._$Eh = /* @__PURE__ */ new Map();
    for (const [r, s] of this.elementProperties) {
      const n = this._$Eu(r, s);
      n !== void 0 && this._$Eh.set(n, r);
    }
    this.elementStyles = this.finalizeStyles(this.styles);
  }
  static finalizeStyles(t) {
    const r = [];
    if (Array.isArray(t)) {
      const s = new Set(t.flat(1 / 0).reverse());
      for (const n of s) r.unshift(X(n));
    } else t !== void 0 && r.push(X(t));
    return r;
  }
  static _$Eu(t, r) {
    const s = r.attribute;
    return s === !1 ? void 0 : typeof s == "string" ? s : typeof t == "string" ? t.toLowerCase() : void 0;
  }
  constructor() {
    super(), this._$Ep = void 0, this.isUpdatePending = !1, this.hasUpdated = !1, this._$Em = null, this._$Ev();
  }
  _$Ev() {
    this._$ES = new Promise((t) => this.enableUpdating = t), this._$AL = /* @__PURE__ */ new Map(), this._$E_(), this.requestUpdate(), this.constructor.l?.forEach((t) => t(this));
  }
  addController(t) {
    (this._$EO ??= /* @__PURE__ */ new Set()).add(t), this.renderRoot !== void 0 && this.isConnected && t.hostConnected?.();
  }
  removeController(t) {
    this._$EO?.delete(t);
  }
  _$E_() {
    const t = /* @__PURE__ */ new Map(), r = this.constructor.elementProperties;
    for (const s of r.keys()) this.hasOwnProperty(s) && (t.set(s, this[s]), delete this[s]);
    t.size > 0 && (this._$Ep = t);
  }
  createRenderRoot() {
    const t = this.shadowRoot ?? this.attachShadow(this.constructor.shadowRootOptions);
    return At(t, this.constructor.elementStyles), t;
  }
  connectedCallback() {
    this.renderRoot ??= this.createRenderRoot(), this.enableUpdating(!0), this._$EO?.forEach((t) => t.hostConnected?.());
  }
  enableUpdating(t) {
  }
  disconnectedCallback() {
    this._$EO?.forEach((t) => t.hostDisconnected?.());
  }
  attributeChangedCallback(t, r, s) {
    this._$AK(t, s);
  }
  _$ET(t, r) {
    const s = this.constructor.elementProperties.get(t), n = this.constructor._$Eu(t, s);
    if (n !== void 0 && s.reflect === !0) {
      const i = (s.converter?.toAttribute !== void 0 ? s.converter : j).toAttribute(r, s.type);
      this._$Em = t, i == null ? this.removeAttribute(n) : this.setAttribute(n, i), this._$Em = null;
    }
  }
  _$AK(t, r) {
    const s = this.constructor, n = s._$Eh.get(t);
    if (n !== void 0 && this._$Em !== n) {
      const i = s.getPropertyOptions(n), o = typeof i.converter == "function" ? { fromAttribute: i.converter } : i.converter?.fromAttribute !== void 0 ? i.converter : j;
      this._$Em = n;
      const l = o.fromAttribute(r, i.type);
      this[n] = l ?? this._$Ej?.get(n) ?? l, this._$Em = null;
    }
  }
  requestUpdate(t, r, s, n = !1, i) {
    if (t !== void 0) {
      const o = this.constructor;
      if (n === !1 && (i = this[t]), s ??= o.getPropertyOptions(t), !((s.hasChanged ?? W)(i, r) || s.useDefault && s.reflect && i === this._$Ej?.get(t) && !this.hasAttribute(o._$Eu(t, s)))) return;
      this.C(t, r, s);
    }
    this.isUpdatePending === !1 && (this._$ES = this._$EP());
  }
  C(t, r, { useDefault: s, reflect: n, wrapped: i }, o) {
    s && !(this._$Ej ??= /* @__PURE__ */ new Map()).has(t) && (this._$Ej.set(t, o ?? r ?? this[t]), i !== !0 || o !== void 0) || (this._$AL.has(t) || (this.hasUpdated || s || (r = void 0), this._$AL.set(t, r)), n === !0 && this._$Em !== t && (this._$Eq ??= /* @__PURE__ */ new Set()).add(t));
  }
  async _$EP() {
    this.isUpdatePending = !0;
    try {
      await this._$ES;
    } catch (r) {
      Promise.reject(r);
    }
    const t = this.scheduleUpdate();
    return t != null && await t, !this.isUpdatePending;
  }
  scheduleUpdate() {
    return this.performUpdate();
  }
  performUpdate() {
    if (!this.isUpdatePending) return;
    if (!this.hasUpdated) {
      if (this.renderRoot ??= this.createRenderRoot(), this._$Ep) {
        for (const [n, i] of this._$Ep) this[n] = i;
        this._$Ep = void 0;
      }
      const s = this.constructor.elementProperties;
      if (s.size > 0) for (const [n, i] of s) {
        const { wrapped: o } = i, l = this[n];
        o !== !0 || this._$AL.has(n) || l === void 0 || this.C(n, void 0, i, l);
      }
    }
    let t = !1;
    const r = this._$AL;
    try {
      t = this.shouldUpdate(r), t ? (this.willUpdate(r), this._$EO?.forEach((s) => s.hostUpdate?.()), this.update(r)) : this._$EM();
    } catch (s) {
      throw t = !1, this._$EM(), s;
    }
    t && this._$AE(r);
  }
  willUpdate(t) {
  }
  _$AE(t) {
    this._$EO?.forEach((r) => r.hostUpdated?.()), this.hasUpdated || (this.hasUpdated = !0, this.firstUpdated(t)), this.updated(t);
  }
  _$EM() {
    this._$AL = /* @__PURE__ */ new Map(), this.isUpdatePending = !1;
  }
  get updateComplete() {
    return this.getUpdateComplete();
  }
  getUpdateComplete() {
    return this._$ES;
  }
  shouldUpdate(t) {
    return !0;
  }
  update(t) {
    this._$Eq &&= this._$Eq.forEach((r) => this._$ET(r, this[r])), this._$EM();
  }
  updated(t) {
  }
  firstUpdated(t) {
  }
};
y.elementStyles = [], y.shadowRootOptions = { mode: "open" }, y[k("elementProperties")] = /* @__PURE__ */ new Map(), y[k("finalized")] = /* @__PURE__ */ new Map(), Ut?.({ ReactiveElement: y }), (H.reactiveElementVersions ??= []).push("2.1.2");
/**
 * @license
 * Copyright 2017 Google LLC
 * SPDX-License-Identifier: BSD-3-Clause
 */
const V = globalThis, et = (e) => e, R = V.trustedTypes, rt = R ? R.createPolicy("lit-html", { createHTML: (e) => e }) : void 0, ft = "$lit$", g = `lit$${Math.random().toFixed(9).slice(2)}$`, mt = "?" + g, Nt = `<${mt}>`, v = document, C = () => v.createComment(""), T = (e) => e === null || typeof e != "object" && typeof e != "function", K = Array.isArray, Mt = (e) => K(e) || typeof e?.[Symbol.iterator] == "function", z = `[ 	
\f\r]`, E = /<(?:(!--|\/[^a-zA-Z])|(\/?[a-zA-Z][^>\s]*)|(\/?$))/g, st = /-->/g, nt = />/g, $ = RegExp(`>|${z}(?:([^\\s"'>=/]+)(${z}*=${z}*(?:[^ 	
\f\r"'\`<>=]|("|')|))|$)`, "g"), it = /'/g, ot = /"/g, gt = /^(?:script|style|textarea|title)$/i, jt = (e) => (t, ...r) => ({ _$litType$: e, strings: t, values: r }), p = jt(1), b = Symbol.for("lit-noChange"), h = Symbol.for("lit-nothing"), at = /* @__PURE__ */ new WeakMap(), _ = v.createTreeWalker(v, 129);
function $t(e, t) {
  if (!K(e) || !e.hasOwnProperty("raw")) throw Error("invalid template strings array");
  return rt !== void 0 ? rt.createHTML(t) : t;
}
const Rt = (e, t) => {
  const r = e.length - 1, s = [];
  let n, i = t === 2 ? "<svg>" : t === 3 ? "<math>" : "", o = E;
  for (let l = 0; l < r; l++) {
    const a = e[l];
    let u, c, d = -1, f = 0;
    for (; f < a.length && (o.lastIndex = f, c = o.exec(a), c !== null); ) f = o.lastIndex, o === E ? c[1] === "!--" ? o = st : c[1] !== void 0 ? o = nt : c[2] !== void 0 ? (gt.test(c[2]) && (n = RegExp("</" + c[2], "g")), o = $) : c[3] !== void 0 && (o = $) : o === $ ? c[0] === ">" ? (o = n ?? E, d = -1) : c[1] === void 0 ? d = -2 : (d = o.lastIndex - c[2].length, u = c[1], o = c[3] === void 0 ? $ : c[3] === '"' ? ot : it) : o === ot || o === it ? o = $ : o === st || o === nt ? o = E : (o = $, n = void 0);
    const m = o === $ && e[l + 1].startsWith("/>") ? " " : "";
    i += o === E ? a + Nt : d >= 0 ? (s.push(u), a.slice(0, d) + ft + a.slice(d) + g + m) : a + g + (d === -2 ? l : m);
  }
  return [$t(e, i + (e[r] || "<?>") + (t === 2 ? "</svg>" : t === 3 ? "</math>" : "")), s];
};
class P {
  constructor({ strings: t, _$litType$: r }, s) {
    let n;
    this.parts = [];
    let i = 0, o = 0;
    const l = t.length - 1, a = this.parts, [u, c] = Rt(t, r);
    if (this.el = P.createElement(u, s), _.currentNode = this.el.content, r === 2 || r === 3) {
      const d = this.el.content.firstChild;
      d.replaceWith(...d.childNodes);
    }
    for (; (n = _.nextNode()) !== null && a.length < l; ) {
      if (n.nodeType === 1) {
        if (n.hasAttributes()) for (const d of n.getAttributeNames()) if (d.endsWith(ft)) {
          const f = c[o++], m = n.getAttribute(d).split(g), N = /([.?@])?(.*)/.exec(f);
          a.push({ type: 1, index: i, name: N[2], strings: m, ctor: N[1] === "." ? Dt : N[1] === "?" ? It : N[1] === "@" ? zt : D }), n.removeAttribute(d);
        } else d.startsWith(g) && (a.push({ type: 6, index: i }), n.removeAttribute(d));
        if (gt.test(n.tagName)) {
          const d = n.textContent.split(g), f = d.length - 1;
          if (f > 0) {
            n.textContent = R ? R.emptyScript : "";
            for (let m = 0; m < f; m++) n.append(d[m], C()), _.nextNode(), a.push({ type: 2, index: ++i });
            n.append(d[f], C());
          }
        }
      } else if (n.nodeType === 8) if (n.data === mt) a.push({ type: 2, index: i });
      else {
        let d = -1;
        for (; (d = n.data.indexOf(g, d + 1)) !== -1; ) a.push({ type: 7, index: i }), d += g.length - 1;
      }
      i++;
    }
  }
  static createElement(t, r) {
    const s = v.createElement("template");
    return s.innerHTML = t, s;
  }
}
function x(e, t, r = e, s) {
  if (t === b) return t;
  let n = s !== void 0 ? r._$Co?.[s] : r._$Cl;
  const i = T(t) ? void 0 : t._$litDirective$;
  return n?.constructor !== i && (n?._$AO?.(!1), i === void 0 ? n = void 0 : (n = new i(e), n._$AT(e, r, s)), s !== void 0 ? (r._$Co ??= [])[s] = n : r._$Cl = n), n !== void 0 && (t = x(e, n._$AS(e, t.values), n, s)), t;
}
class Ht {
  constructor(t, r) {
    this._$AV = [], this._$AN = void 0, this._$AD = t, this._$AM = r;
  }
  get parentNode() {
    return this._$AM.parentNode;
  }
  get _$AU() {
    return this._$AM._$AU;
  }
  u(t) {
    const { el: { content: r }, parts: s } = this._$AD, n = (t?.creationScope ?? v).importNode(r, !0);
    _.currentNode = n;
    let i = _.nextNode(), o = 0, l = 0, a = s[0];
    for (; a !== void 0; ) {
      if (o === a.index) {
        let u;
        a.type === 2 ? u = new U(i, i.nextSibling, this, t) : a.type === 1 ? u = new a.ctor(i, a.name, a.strings, this, t) : a.type === 6 && (u = new Lt(i, this, t)), this._$AV.push(u), a = s[++l];
      }
      o !== a?.index && (i = _.nextNode(), o++);
    }
    return _.currentNode = v, n;
  }
  p(t) {
    let r = 0;
    for (const s of this._$AV) s !== void 0 && (s.strings !== void 0 ? (s._$AI(t, s, r), r += s.strings.length - 2) : s._$AI(t[r])), r++;
  }
}
class U {
  get _$AU() {
    return this._$AM?._$AU ?? this._$Cv;
  }
  constructor(t, r, s, n) {
    this.type = 2, this._$AH = h, this._$AN = void 0, this._$AA = t, this._$AB = r, this._$AM = s, this.options = n, this._$Cv = n?.isConnected ?? !0;
  }
  get parentNode() {
    let t = this._$AA.parentNode;
    const r = this._$AM;
    return r !== void 0 && t?.nodeType === 11 && (t = r.parentNode), t;
  }
  get startNode() {
    return this._$AA;
  }
  get endNode() {
    return this._$AB;
  }
  _$AI(t, r = this) {
    t = x(this, t, r), T(t) ? t === h || t == null || t === "" ? (this._$AH !== h && this._$AR(), this._$AH = h) : t !== this._$AH && t !== b && this._(t) : t._$litType$ !== void 0 ? this.$(t) : t.nodeType !== void 0 ? this.T(t) : Mt(t) ? this.k(t) : this._(t);
  }
  O(t) {
    return this._$AA.parentNode.insertBefore(t, this._$AB);
  }
  T(t) {
    this._$AH !== t && (this._$AR(), this._$AH = this.O(t));
  }
  _(t) {
    this._$AH !== h && T(this._$AH) ? this._$AA.nextSibling.data = t : this.T(v.createTextNode(t)), this._$AH = t;
  }
  $(t) {
    const { values: r, _$litType$: s } = t, n = typeof s == "number" ? this._$AC(t) : (s.el === void 0 && (s.el = P.createElement($t(s.h, s.h[0]), this.options)), s);
    if (this._$AH?._$AD === n) this._$AH.p(r);
    else {
      const i = new Ht(n, this), o = i.u(this.options);
      i.p(r), this.T(o), this._$AH = i;
    }
  }
  _$AC(t) {
    let r = at.get(t.strings);
    return r === void 0 && at.set(t.strings, r = new P(t)), r;
  }
  k(t) {
    K(this._$AH) || (this._$AH = [], this._$AR());
    const r = this._$AH;
    let s, n = 0;
    for (const i of t) n === r.length ? r.push(s = new U(this.O(C()), this.O(C()), this, this.options)) : s = r[n], s._$AI(i), n++;
    n < r.length && (this._$AR(s && s._$AB.nextSibling, n), r.length = n);
  }
  _$AR(t = this._$AA.nextSibling, r) {
    for (this._$AP?.(!1, !0, r); t !== this._$AB; ) {
      const s = et(t).nextSibling;
      et(t).remove(), t = s;
    }
  }
  setConnected(t) {
    this._$AM === void 0 && (this._$Cv = t, this._$AP?.(t));
  }
}
class D {
  get tagName() {
    return this.element.tagName;
  }
  get _$AU() {
    return this._$AM._$AU;
  }
  constructor(t, r, s, n, i) {
    this.type = 1, this._$AH = h, this._$AN = void 0, this.element = t, this.name = r, this._$AM = n, this.options = i, s.length > 2 || s[0] !== "" || s[1] !== "" ? (this._$AH = Array(s.length - 1).fill(new String()), this.strings = s) : this._$AH = h;
  }
  _$AI(t, r = this, s, n) {
    const i = this.strings;
    let o = !1;
    if (i === void 0) t = x(this, t, r, 0), o = !T(t) || t !== this._$AH && t !== b, o && (this._$AH = t);
    else {
      const l = t;
      let a, u;
      for (t = i[0], a = 0; a < i.length - 1; a++) u = x(this, l[s + a], r, a), u === b && (u = this._$AH[a]), o ||= !T(u) || u !== this._$AH[a], u === h ? t = h : t !== h && (t += (u ?? "") + i[a + 1]), this._$AH[a] = u;
    }
    o && !n && this.j(t);
  }
  j(t) {
    t === h ? this.element.removeAttribute(this.name) : this.element.setAttribute(this.name, t ?? "");
  }
}
class Dt extends D {
  constructor() {
    super(...arguments), this.type = 3;
  }
  j(t) {
    this.element[this.name] = t === h ? void 0 : t;
  }
}
class It extends D {
  constructor() {
    super(...arguments), this.type = 4;
  }
  j(t) {
    this.element.toggleAttribute(this.name, !!t && t !== h);
  }
}
class zt extends D {
  constructor(t, r, s, n, i) {
    super(t, r, s, n, i), this.type = 5;
  }
  _$AI(t, r = this) {
    if ((t = x(this, t, r, 0) ?? h) === b) return;
    const s = this._$AH, n = t === h && s !== h || t.capture !== s.capture || t.once !== s.once || t.passive !== s.passive, i = t !== h && (s === h || n);
    n && this.element.removeEventListener(this.name, this, s), i && this.element.addEventListener(this.name, this, t), this._$AH = t;
  }
  handleEvent(t) {
    typeof this._$AH == "function" ? this._$AH.call(this.options?.host ?? this.element, t) : this._$AH.handleEvent(t);
  }
}
class Lt {
  constructor(t, r, s) {
    this.element = t, this.type = 6, this._$AN = void 0, this._$AM = r, this.options = s;
  }
  get _$AU() {
    return this._$AM._$AU;
  }
  _$AI(t) {
    x(this, t);
  }
}
const Bt = V.litHtmlPolyfillSupport;
Bt?.(P, U), (V.litHtmlVersions ??= []).push("3.3.3");
const qt = (e, t, r) => {
  const s = r?.renderBefore ?? t;
  let n = s._$litPart$;
  if (n === void 0) {
    const i = r?.renderBefore ?? null;
    s._$litPart$ = n = new U(t.insertBefore(C(), i), i, void 0, r ?? {});
  }
  return n._$AI(e), n;
};
/**
 * @license
 * Copyright 2017 Google LLC
 * SPDX-License-Identifier: BSD-3-Clause
 */
const J = globalThis;
let w = class extends y {
  constructor() {
    super(...arguments), this.renderOptions = { host: this }, this._$Do = void 0;
  }
  createRenderRoot() {
    const t = super.createRenderRoot();
    return this.renderOptions.renderBefore ??= t.firstChild, t;
  }
  update(t) {
    const r = this.render();
    this.hasUpdated || (this.renderOptions.isConnected = this.isConnected), super.update(t), this._$Do = qt(r, this.renderRoot, this.renderOptions);
  }
  connectedCallback() {
    super.connectedCallback(), this._$Do?.setConnected(!0);
  }
  disconnectedCallback() {
    super.disconnectedCallback(), this._$Do?.setConnected(!1);
  }
  render() {
    return b;
  }
};
w._$litElement$ = !0, w.finalized = !0, J.litElementHydrateSupport?.({ LitElement: w });
const Wt = J.litElementPolyfillSupport;
Wt?.({ LitElement: w });
(J.litElementVersions ??= []).push("4.2.2");
/**
 * @license
 * Copyright 2017 Google LLC
 * SPDX-License-Identifier: BSD-3-Clause
 */
const _t = (e) => (t, r) => {
  r !== void 0 ? r.addInitializer(() => {
    customElements.define(e, t);
  }) : customElements.define(e, t);
};
/**
 * @license
 * Copyright 2017 Google LLC
 * SPDX-License-Identifier: BSD-3-Clause
 */
const Vt = { attribute: !0, type: String, converter: j, reflect: !1, hasChanged: W }, Kt = (e = Vt, t, r) => {
  const { kind: s, metadata: n } = r;
  let i = globalThis.litPropertyMetadata.get(n);
  if (i === void 0 && globalThis.litPropertyMetadata.set(n, i = /* @__PURE__ */ new Map()), s === "setter" && ((e = Object.create(e)).wrapped = !0), i.set(r.name, e), s === "accessor") {
    const { name: o } = r;
    return { set(l) {
      const a = t.get.call(this);
      t.set.call(this, l), this.requestUpdate(o, a, e, !0, l);
    }, init(l) {
      return l !== void 0 && this.C(o, void 0, e, l), l;
    } };
  }
  if (s === "setter") {
    const { name: o } = r;
    return function(l) {
      const a = this[o];
      t.call(this, l), this.requestUpdate(o, a, e, !0, l);
    };
  }
  throw Error("Unsupported decorator location: " + s);
};
function F(e) {
  return (t, r) => typeof r == "object" ? Kt(e, t, r) : ((s, n, i) => {
    const o = n.hasOwnProperty(i);
    return n.constructor.createProperty(i, s), o ? Object.getOwnPropertyDescriptor(n, i) : void 0;
  })(e, t, r);
}
/**
 * @license
 * Copyright 2017 Google LLC
 * SPDX-License-Identifier: BSD-3-Clause
 */
function Z(e) {
  return F({ ...e, state: !0, attribute: !1 });
}
/**
 * @license
 * Copyright 2017 Google LLC
 * SPDX-License-Identifier: BSD-3-Clause
 */
const Jt = { ATTRIBUTE: 1 }, Ft = (e) => (...t) => ({ _$litDirective$: e, values: t });
class Zt {
  constructor(t) {
  }
  get _$AU() {
    return this._$AM._$AU;
  }
  _$AT(t, r, s) {
    this._$Ct = t, this._$AM = r, this._$Ci = s;
  }
  _$AS(t, r) {
    return this.update(t, r);
  }
  update(t, r) {
    return this.render(...r);
  }
}
/**
 * @license
 * Copyright 2018 Google LLC
 * SPDX-License-Identifier: BSD-3-Clause
 */
const lt = Ft(class extends Zt {
  constructor(e) {
    if (super(e), e.type !== Jt.ATTRIBUTE || e.name !== "class" || e.strings?.length > 2) throw Error("`classMap()` can only be used in the `class` attribute and must be the only part in the attribute.");
  }
  render(e) {
    return " " + Object.keys(e).filter((t) => e[t]).join(" ") + " ";
  }
  update(e, [t]) {
    if (this.st === void 0) {
      this.st = /* @__PURE__ */ new Set(), e.strings !== void 0 && (this.nt = new Set(e.strings.join(" ").split(/\s/).filter((s) => s !== "")));
      for (const s in t) t[s] && !this.nt?.has(s) && this.st.add(s);
      return this.render(t);
    }
    const r = e.element.classList;
    for (const s of this.st) s in t || (r.remove(s), this.st.delete(s));
    for (const s in t) {
      const n = !!t[s];
      n === this.st.has(s) || this.nt?.has(s) || (n ? (r.add(s), this.st.add(s)) : (r.remove(s), this.st.delete(s)));
    }
    return b;
  }
}), Gt = /* @__PURE__ */ new Set(["unknown", "unavailable", "none", ""]), Yt = 10;
function Xt(e) {
  return e === void 0 || Gt.has(e);
}
function ht(e) {
  const t = /_(\d+)$/.exec(e);
  return t ? Number(t[1]) : 0;
}
function Qt(e, t) {
  const r = te(e, t), s = ee(e, t);
  return r && r.departures.length >= s.departures.length ? r : {
    departures: s.departures,
    favorite: s.favorite ?? r?.favorite ?? null,
    progress: s.progress ?? r?.progress ?? null
  };
}
function te(e, t) {
  const r = e.entities?.[t]?.device_id;
  if (!r)
    return null;
  const s = Object.values(e.entities).filter(
    (l) => l.device_id === r
  ), n = (l) => s.filter((a) => a.translation_key === l).map((a) => a.entity_id), i = n("departure").sort(
    (l, a) => ht(l) - ht(a)
  ), o = [...n("next_departure"), ...i];
  return o.length ? {
    departures: o,
    favorite: n("next_favorite")[0] ?? null,
    progress: n("stops_away")[0] ?? null
  } : null;
}
function ee(e, t) {
  const r = t.replace(/_next_departure$/, ""), s = (i) => e.states[i] ? i : null, n = [t];
  for (let i = 2; i <= Yt; i++) {
    const o = s(`${r}_departure_${i}`);
    o && n.push(o);
  }
  return {
    departures: n,
    favorite: s(`${r}_next_favorite`),
    progress: s(`${r}_stops_away`)
  };
}
function S(e) {
  return typeof e == "string" && e !== "" ? e : null;
}
function vt(e, t) {
  const r = e.states[t];
  if (!r || Xt(r.state))
    return null;
  const s = new Date(r.state);
  if (Number.isNaN(s.getTime()))
    return null;
  const n = r.attributes ?? {}, i = n.delay_minutes, o = Array.isArray(n.cars) ? n.cars : [], l = Array.isArray(n.alerts) ? n.alerts.filter(
    (a) => typeof a == "string"
  ) : [];
  return {
    entityId: t,
    scheduled: s,
    trainId: S(n.train_id),
    favorite: n.favorite === !0,
    track: S(n.track),
    status: S(n.status),
    statusText: S(n.status_text),
    delayMinutes: typeof i == "number" ? i : null,
    crowding: S(n.crowding),
    cars: o,
    alerts: l
  };
}
function re(e, t) {
  return t.departures.map((r) => vt(e, r)).filter((r) => r !== null);
}
function se(e) {
  return e.some((t) => t.track !== null);
}
function ne(e, t) {
  if (e)
    return { departure: e, tracking: !0, allCancelled: !1 };
  const r = t.find((s) => s.status !== "cancelled");
  return {
    departure: r ?? null,
    tracking: !1,
    allCancelled: !r && t.length > 0
  };
}
function L(e, t) {
  return Math.round((e.getTime() - t.getTime()) / 6e4);
}
const bt = new Intl.DateTimeFormat("en-US", {
  hour: "numeric",
  minute: "2-digit",
  hour12: !0
});
function ie(e) {
  return bt.format(e);
}
function oe(e) {
  return bt.format(e).replace(/\s?[AP]M$/i, "");
}
function ae(e) {
  return e < 0 ? { value: "Departed", unit: !1 } : e === 0 ? { value: "Now", unit: !1 } : { value: String(e), unit: !0 };
}
const yt = 6, ct = 10, le = 25;
function he(e, t, r) {
  return e.track ? { text: `Track ${e.track}`, tone: "accent" } : e.status === "cancelled" ? null : r ? t <= yt ? { text: "⚠️ Track overdue", tone: "bad" } : t <= ct ? { text: "Track due any minute", tone: "muted" } : t <= le ? { text: `Track due in ~${t - (ct - 1)} min`, tone: "muted" } : { text: "Track not posted yet", tone: "muted" } : { text: "Track not posted", tone: "muted" };
}
function ce(e, t, r) {
  return e.track ? { text: e.track, tone: "accent" } : r && t <= yt && e.status !== "cancelled" ? { text: "⚠️", tone: "bad" } : { text: "—", tone: "muted" };
}
function dt(e) {
  return e.statusText ? e.status === "cancelled" ? { text: e.statusText, tone: "bad" } : e.delayMinutes ? { text: e.statusText, tone: "warn" } : { text: e.statusText, tone: "muted" } : null;
}
function de(e) {
  return e.crowding === "heavy" ? { text: "Busy", tone: "warn" } : e.crowding === "moderate" ? { text: "Filling up", tone: "muted" } : null;
}
const ut = { muted: 0, accent: 1, warn: 2, bad: 3 };
function ue(e) {
  let t = "accent";
  for (const r of e)
    r && ut[r.tone] > ut[t] && (t = r.tone);
  return t;
}
const pe = { light: 0, moderate: 1, heavy: 2 };
function fe(e) {
  let t = null, r = 1 / 0, s = -1 / 0;
  for (const n of e) {
    const i = n.crowding ? pe[n.crowding] : void 0;
    i === void 0 || !n.position || (i < r && (r = i, t = n.position), s = Math.max(s, i));
  }
  return t !== null && s > r ? t : null;
}
var me = Object.defineProperty, ge = Object.getOwnPropertyDescriptor, I = (e, t, r, s) => {
  for (var n = s > 1 ? void 0 : s ? ge(t, r) : t, i = e.length - 1, o; i >= 0; i--)
    (o = e[i]) && (n = (s ? o(t, r, n) : o(n)) || n);
  return s && n && me(t, r, n), n;
};
const $e = 1e4;
let A = class extends w {
  constructor() {
    super(...arguments), this._now = /* @__PURE__ */ new Date();
  }
  setConfig(e) {
    if (!e?.entity)
      throw new Error("Set `entity` to the commute's next departure sensor");
    this._config = e;
  }
  getCardSize() {
    return 8;
  }
  static async getConfigElement() {
    return await Promise.resolve().then(() => xe), document.createElement("njtransit-departures-editor");
  }
  /** Offer a working card the moment it is added from the picker. */
  static getStubConfig(e) {
    return { type: "custom:njtransit-departures", entity: Object.keys(e.states).find(
      (r) => r.endsWith("_next_departure")
    ) ?? "" };
  }
  connectedCallback() {
    super.connectedCallback(), this._timer = setInterval(() => {
      this._now = /* @__PURE__ */ new Date();
    }, $e);
  }
  disconnectedCallback() {
    super.disconnectedCallback(), this._timer && (clearInterval(this._timer), this._timer = void 0);
  }
  render() {
    if (!this.hass || !this._config?.entity)
      return h;
    const e = Qt(this.hass, this._config.entity), t = re(this.hass, e), r = e.favorite ? vt(this.hass, e.favorite) : null, { departure: s, tracking: n, allCancelled: i } = ne(r, t), o = se(t), l = s ? this._pillsFor(s, o) : [], a = s ? ue(l) : i ? "bad" : "muted";
    return p`
      <ha-card class=${lt({ [a]: !0 })}>
        ${this._config.title ? p`<h1 class="card-header">${this._config.title}</h1>` : h}
        ${s ? this._renderHero(s, l, {
      tracking: n,
      posting: o,
      progress: e.progress,
      favoriteEntity: e.favorite
    }) : this._renderEmpty(i)}
        ${t.length ? this._renderBoard(t, o) : h}
      </ha-card>
    `;
  }
  /** Everything the hero has to say about this train, in reading order. */
  _pillsFor(e, t) {
    const r = L(e.scheduled, this._now);
    return [
      he(e, r, t),
      dt(e),
      de(e)
    ].filter((s) => s !== null);
  }
  _renderEmpty(e) {
    return p`
      <div class="hero empty">
        <h3>Nothing on the board</h3>
        <p>
          ${e ? "Every upcoming departure is cancelled." : "No departures in the next couple of hours."}
        </p>
      </div>
    `;
  }
  _renderHero(e, t, r) {
    const { tracking: s, progress: n, favoriteEntity: i } = r, o = L(e.scheduled, this._now), { value: l, unit: a } = ae(o), u = fe(e.cars);
    return p`
      <div
        class="hero"
        role="button"
        tabindex="0"
        @click=${() => this._moreInfo(e.entityId)}
        @keydown=${(c) => {
      (c.key === "Enter" || c.key === " ") && (c.preventDefault(), this._moreInfo(e.entityId));
    }}
      >
        <div class="countdown">
          ${l}${a ? p`<span class="unit">min</span>` : h}
        </div>
        <h2>
          <span class="clock">${ie(e.scheduled)}</span>
          ${e.trainId ? p`<span class="train">Train ${e.trainId}</span>` : h}
        </h2>
        <div class="pills">${t.map((c) => this._renderPill(c))}</div>
        ${u ? p`<p class="hint">${_e(u)} cars are emptier</p>` : h}
        ${s ? this._renderProgress(n) : h}
        ${e.alerts.map(
      (c) => p`<blockquote>${c}</blockquote>`
    )}
        ${s ? h : this._renderWaiting(i)}
      </div>
    `;
  }
  _renderProgress(e) {
    const t = e ? this.hass.states[e] : void 0;
    if (!t || !/^\d+$/.test(t.state))
      return h;
    const r = Number(t.state), s = t.attributes.next_stop;
    return p`
      <hr />
      <p class="progress">
        ${r === 0 ? "Arriving now" : p`<strong>${r}</strong> stops away`}
        ${typeof s == "string" && s ? p` · next ${s}` : h}
      </p>
    `;
  }
  /**
   * Why the card is showing someone else's train.
   *
   * Without this the fallback is indistinguishable from the favourite, and
   * the countdown at the top is for a service the reader was never going to
   * board.
   */
  _renderWaiting(e) {
    const t = e ? this.hass.states[e]?.attributes.favorites : void 0;
    return Array.isArray(t) ? p`
      <p class="hint">
        ${t.length ? `Waiting for ${t.join(", ")} — not on the board yet.` : "No favourite set. Pick one in this commute's options."}
      </p>
    ` : h;
  }
  _renderBoard(e, t) {
    return p`
      <div class="board">
        <table>
          <thead>
            <tr>
              <th>Departs</th>
              <th>Train</th>
              <th>Trk</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            ${e.map((r) => this._renderRow(r, t))}
          </tbody>
        </table>
      </div>
    `;
  }
  _renderRow(e, t) {
    const r = L(e.scheduled, this._now), s = dt(e);
    return p`
      <tr @click=${() => this._moreInfo(e.entityId)}>
        <td>
          <strong>${oe(e.scheduled)}</strong>
          <span class="relative">${r > 0 ? `${r}m` : "now"}</span>
        </td>
        <td>${e.trainId}${e.favorite ? " ⭐" : ""}</td>
        <td>${this._renderPill(ce(e, r, t))}</td>
        <td>${s ? this._renderPill(s) : h}</td>
      </tr>
    `;
  }
  _renderPill(e) {
    return p`<span
      class=${lt({ pill: !0, [e.tone]: !0 })}
      >${e.text}</span
    >`;
  }
  _moreInfo(e) {
    this.dispatchEvent(
      new CustomEvent("hass-more-info", {
        detail: { entityId: e },
        bubbles: !0,
        composed: !0
      })
    );
  }
};
A.styles = xt`
    :host {
      /* The board itself sends #00953b as the Morristown Line's colour. Other
         lines send their own, and the feed also uses that field to mark
         cancellations, so it is not a dependable line identity and the
         integration does not expose it -- override this to match yours. */
      --njtransit-accent: #00953b;

      /* Set per card by the worst thing on it, and used for the surface tint,
         the hairline and the row highlight. See cardMood in pills.ts. */
      --mood: var(--njtransit-accent);

      --njt-radius: 18px;
      --njt-gutter: 18px;
      display: block;
      container-type: inline-size;
    }

    /* Every tinted surface in here is these three expressions, mixed in oklab
       so the result keeps its lightness whatever hue it is given. Mixing the
       foreground with the *theme's* text colour is what makes one stylesheet
       work in both: against a light theme it darkens toward black and against
       a dark one it lifts toward white, so a dark green that would be
       unreadable on near-black never has to be special-cased.

       --ink is how much of the tone survives into the text, and it is
       measured rather than chosen. At a straight 70% the amber pill came out
       at 3.33:1 against its own tint in a light theme -- failing WCAG AA for
       text this size -- while green and red sat at 4.2 in a dark one. Amber
       needs far more of the text colour than the others, because a light hue
       can only be darkened by borrowing from it. */
    .pill,
    blockquote {
      --ink: 58%;
      background: color-mix(in oklab, var(--tone) 14%, transparent);
      color: color-mix(
        in oklab,
        var(--tone) var(--ink),
        var(--primary-text-color)
      );
      box-shadow: inset 0 0 0 1px
        color-mix(in oklab, var(--tone) 28%, transparent);
    }

    ha-card {
      position: relative;
      border: none;
      border-radius: var(--njt-radius);
      overflow: hidden;
      background: var(--card-background-color);
    }

    ha-card.warn {
      --mood: var(--warning-color);
    }

    ha-card.bad {
      --mood: var(--error-color);
    }

    ha-card.muted {
      --mood: var(--secondary-text-color);
    }

    /* The tint, as a corner wash rather than a border. A 3px rule across the
       top reads as a status bar on a 2015 dashboard; this reads as the card
       being lit from somewhere. */
    ha-card::before {
      content: "";
      position: absolute;
      inset: 0;
      pointer-events: none;
      background: radial-gradient(
        95% 65% at 0% 0%,
        color-mix(in oklab, var(--mood) 13%, transparent),
        transparent 68%
      );
      transition: background 600ms ease;
    }

    /* What is left of the top border: a hairline that fades out rather than
       stopping, so it reads as an edge lit by the same source. */
    ha-card::after {
      content: "";
      position: absolute;
      inset: 0 0 auto;
      height: 2px;
      pointer-events: none;
      background: linear-gradient(
        90deg,
        var(--mood),
        color-mix(in oklab, var(--mood) 20%, transparent) 55%,
        transparent
      );
    }

    .card-header {
      position: relative;
      margin: 0;
      padding: 16px var(--njt-gutter) 0;
      font-size: 1.05rem;
      font-weight: 600;
      letter-spacing: -0.01em;
    }

    .hero {
      position: relative;
      padding: 18px var(--njt-gutter) 20px;
      cursor: pointer;
      -webkit-tap-highlight-color: transparent;
    }

    .hero.empty {
      cursor: default;
    }

    .hero:focus-visible {
      outline: 2px solid var(--mood);
      outline-offset: -3px;
      border-radius: var(--njt-radius);
    }

    .countdown {
      display: flex;
      align-items: baseline;
      gap: 0.12em;
      font-size: clamp(3rem, 17cqi, 4rem);
      font-weight: 750;
      line-height: 0.92;
      letter-spacing: -0.045em;
      font-variant-numeric: tabular-nums;
      /* Not the mood colour: a delayed train is still the number you are
         reading, and tinting it amber makes the one thing this card exists
         to show harder to read, not easier. */
      color: var(--primary-text-color);
      margin-bottom: 6px;
    }

    /* The unit, kept small so the number carries the card. */
    .unit {
      font-size: 0.26em;
      font-weight: 650;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: var(--secondary-text-color);
      transform: translateY(-0.55em);
    }

    h2 {
      display: flex;
      flex-wrap: wrap;
      align-items: baseline;
      gap: 0 8px;
      margin: 0 0 14px;
      font-size: 0.9rem;
      font-weight: 500;
      font-variant-numeric: tabular-nums;
    }

    .clock {
      color: var(--primary-text-color);
      font-weight: 650;
    }

    .train {
      color: var(--secondary-text-color);
    }

    /* Separator between the two, drawn rather than typed, so it never lands
       on its own line when the card is narrow. */
    .train::before {
      content: "";
      display: inline-block;
      width: 3px;
      height: 3px;
      margin-right: 8px;
      border-radius: 50%;
      background: currentColor;
      vertical-align: 0.22em;
      opacity: 0.55;
    }

    h3 {
      margin: 0 0 6px;
      font-size: 1.15rem;
      font-weight: 650;
      letter-spacing: -0.01em;
    }

    p {
      margin: 0;
      line-height: 1.55;
    }

    .hint {
      margin-top: 12px;
      color: var(--secondary-text-color);
      font-size: 0.9rem;
    }

    hr {
      border: none;
      border-top: 1px solid
        color-mix(in oklab, var(--divider-color) 60%, transparent);
      margin: 16px 0 12px;
    }

    .progress {
      font-size: 0.95rem;
    }

    .pills {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
    }

    .pill {
      --tone: var(--njtransit-accent);
      font-size: 0.78rem;
      font-weight: 650;
      letter-spacing: 0.005em;
      white-space: nowrap;
      padding: 4px 11px;
      border-radius: 999px;
    }

    .pill.bad {
      --tone: var(--error-color);
    }

    .pill.warn {
      --tone: var(--warning-color);
      --ink: 34%;
    }

    .pill.muted {
      --tone: var(--secondary-text-color);
      font-weight: 600;
    }

    blockquote {
      --tone: var(--error-color);
      margin: 14px 0 0;
      padding: 10px 13px;
      border-radius: 10px;
      font-size: 0.9rem;
      line-height: 1.5;
    }

    .board {
      position: relative;
      padding: 0 var(--njt-gutter) 8px;
    }

    table {
      width: 100%;
      border-collapse: collapse;
      font-variant-numeric: tabular-nums;
    }

    th {
      padding: 0 6px 8px 0;
      border-bottom: 1px solid
        color-mix(in oklab, var(--divider-color) 70%, transparent);
      text-align: left;
      font-size: 0.63rem;
      font-weight: 700;
      letter-spacing: 0.14em;
      text-transform: uppercase;
      color: var(--secondary-text-color);
    }

    td {
      padding: 11px 6px 11px 0;
      border-bottom: 1px solid
        color-mix(in oklab, var(--divider-color) 35%, transparent);
      font-size: 0.96rem;
      white-space: nowrap;
    }

    /* The row is the tap target, so the highlight has to reach the card edge
       rather than stopping at the table's padding. */
    tbody tr {
      cursor: pointer;
      -webkit-tap-highlight-color: transparent;
      box-shadow: 0 0 0 0 transparent;
      transition:
        background-color 120ms ease,
        box-shadow 120ms ease;
    }

    tbody tr:hover,
    tbody tr:active {
      background: color-mix(in oklab, var(--mood) 8%, transparent);
      box-shadow:
        calc(var(--njt-gutter) * -1) 0 0 0
          color-mix(in oklab, var(--mood) 8%, transparent),
        var(--njt-gutter) 0 0 0 color-mix(in oklab, var(--mood) 8%, transparent);
    }

    tbody tr:last-child td {
      border-bottom: none;
    }

    .relative {
      font-size: 0.8rem;
      color: var(--secondary-text-color);
      margin-left: 5px;
    }

    .board .pill {
      font-size: 0.74rem;
      padding: 3px 9px;
    }

    /* Anything worth a red pill is worth finding without reading. Slow and
       shallow: this sits next to a number someone is trying to read. */
    @media (prefers-reduced-motion: no-preference) {
      .pills .pill.bad {
        animation: attention 2.6s ease-in-out infinite;
      }
    }

    @keyframes attention {
      0%,
      100% {
        box-shadow: inset 0 0 0 1px
          color-mix(in oklab, var(--tone) 28%, transparent);
      }
      50% {
        box-shadow: inset 0 0 0 1px
          color-mix(in oklab, var(--tone) 70%, transparent);
      }
    }

    /* Narrow columns, and the phone this is really for. */
    @container (max-width: 330px) {
      :host {
        --njt-gutter: 14px;
      }

      td,
      th {
        font-size: 0.9rem;
      }

      .relative {
        display: none;
      }
    }
  `;
I([
  F({ attribute: !1 })
], A.prototype, "hass", 2);
I([
  Z()
], A.prototype, "_config", 2);
I([
  Z()
], A.prototype, "_now", 2);
A = I([
  _t("njtransit-departures")
], A);
function _e(e) {
  return e.charAt(0).toUpperCase() + e.slice(1);
}
window.customCards = window.customCards ?? [];
window.customCards.push({
  type: "njtransit-departures",
  name: "NJ Transit departures",
  description: "The next train out, why it might not be the one you want, and the board behind it.",
  preview: !0,
  documentationURL: "https://github.com/dknowles2/ha-njtransit"
});
console.info("%c NJ TRANSIT %c card loaded ", "font-weight:700", "");
var ve = Object.defineProperty, be = Object.getOwnPropertyDescriptor, G = (e, t, r, s) => {
  for (var n = s > 1 ? void 0 : s ? be(t, r) : t, i = e.length - 1, o; i >= 0; i--)
    (o = e[i]) && (n = (s ? o(t, r, n) : o(n)) || n);
  return s && n && ve(t, r, n), n;
};
const ye = [
  {
    name: "entity",
    required: !0,
    selector: { entity: { integration: "njtransit", domain: "sensor" } }
  },
  { name: "title", selector: { text: {} } }
], we = {
  entity: "Commute (any departure sensor)",
  title: "Heading (optional)"
};
let O = class extends w {
  setConfig(e) {
    this._config = e;
  }
  render() {
    return !this.hass || !this._config ? h : p`
      <ha-form
        .hass=${this.hass}
        .data=${this._config}
        .schema=${ye}
        .computeLabel=${(e) => we[e.name] ?? e.name}
        @value-changed=${this._changed}
      ></ha-form>
    `;
  }
  _changed(e) {
    this.dispatchEvent(
      new CustomEvent("config-changed", {
        detail: { config: e.detail.value },
        bubbles: !0,
        composed: !0
      })
    );
  }
};
G([
  F({ attribute: !1 })
], O.prototype, "hass", 2);
G([
  Z()
], O.prototype, "_config", 2);
O = G([
  _t("njtransit-departures-editor")
], O);
const xe = /* @__PURE__ */ Object.freeze(/* @__PURE__ */ Object.defineProperty({
  __proto__: null,
  get NJTransitDeparturesEditor() {
    return O;
  }
}, Symbol.toStringTag, { value: "Module" }));
