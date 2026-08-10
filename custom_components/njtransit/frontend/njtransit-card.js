/**
 * @license
 * Copyright 2019 Google LLC
 * SPDX-License-Identifier: BSD-3-Clause
 */
const M = globalThis, L = M.ShadowRoot && (M.ShadyCSS === void 0 || M.ShadyCSS.nativeShadow) && "adoptedStyleSheets" in Document.prototype && "replace" in CSSStyleSheet.prototype, B = Symbol(), G = /* @__PURE__ */ new WeakMap();
let dt = class {
  constructor(t, s, r) {
    if (this._$cssResult$ = !0, r !== B) throw Error("CSSResult is not constructable. Use `unsafeCSS` or `css` instead.");
    this.cssText = t, this.t = s;
  }
  get styleSheet() {
    let t = this.o;
    const s = this.t;
    if (L && t === void 0) {
      const r = s !== void 0 && s.length === 1;
      r && (t = G.get(s)), t === void 0 && ((this.o = t = new CSSStyleSheet()).replaceSync(this.cssText), r && G.set(s, t));
    }
    return t;
  }
  toString() {
    return this.cssText;
  }
};
const yt = (e) => new dt(typeof e == "string" ? e : e + "", void 0, B), bt = (e, ...t) => {
  const s = e.length === 1 ? e[0] : t.reduce((r, n, i) => r + ((o) => {
    if (o._$cssResult$ === !0) return o.cssText;
    if (typeof o == "number") return o;
    throw Error("Value passed to 'css' function must be a 'css' function result: " + o + ". Use 'unsafeCSS' to pass non-literal values, but take care to ensure page security.");
  })(n) + e[i + 1], e[0]);
  return new dt(s, e, B);
}, At = (e, t) => {
  if (L) e.adoptedStyleSheets = t.map((s) => s instanceof CSSStyleSheet ? s : s.styleSheet);
  else for (const s of t) {
    const r = document.createElement("style"), n = M.litNonce;
    n !== void 0 && r.setAttribute("nonce", n), r.textContent = s.cssText, e.appendChild(r);
  }
}, X = L ? (e) => e : (e) => e instanceof CSSStyleSheet ? ((t) => {
  let s = "";
  for (const r of t.cssRules) s += r.cssText;
  return yt(s);
})(e) : e;
/**
 * @license
 * Copyright 2017 Google LLC
 * SPDX-License-Identifier: BSD-3-Clause
 */
const { is: xt, defineProperty: wt, getOwnPropertyDescriptor: Et, getOwnPropertyNames: St, getOwnPropertySymbols: Ct, getPrototypeOf: Tt } = Object, j = globalThis, Q = j.trustedTypes, Pt = Q ? Q.emptyScript : "", kt = j.reactiveElementPolyfillSupport, C = (e, t) => e, R = { toAttribute(e, t) {
  switch (t) {
    case Boolean:
      e = e ? Pt : null;
      break;
    case Object:
    case Array:
      e = e == null ? e : JSON.stringify(e);
  }
  return e;
}, fromAttribute(e, t) {
  let s = e;
  switch (t) {
    case Boolean:
      s = e !== null;
      break;
    case Number:
      s = e === null ? null : Number(e);
      break;
    case Object:
    case Array:
      try {
        s = JSON.parse(e);
      } catch {
        s = null;
      }
  }
  return s;
} }, q = (e, t) => !xt(e, t), Y = { attribute: !0, type: String, converter: R, reflect: !1, useDefault: !1, hasChanged: q };
Symbol.metadata ??= Symbol("metadata"), j.litPropertyMetadata ??= /* @__PURE__ */ new WeakMap();
let b = class extends HTMLElement {
  static addInitializer(t) {
    this._$Ei(), (this.l ??= []).push(t);
  }
  static get observedAttributes() {
    return this.finalize(), this._$Eh && [...this._$Eh.keys()];
  }
  static createProperty(t, s = Y) {
    if (s.state && (s.attribute = !1), this._$Ei(), this.prototype.hasOwnProperty(t) && ((s = Object.create(s)).wrapped = !0), this.elementProperties.set(t, s), !s.noAccessor) {
      const r = Symbol(), n = this.getPropertyDescriptor(t, r, s);
      n !== void 0 && wt(this.prototype, t, n);
    }
  }
  static getPropertyDescriptor(t, s, r) {
    const { get: n, set: i } = Et(this.prototype, t) ?? { get() {
      return this[s];
    }, set(o) {
      this[s] = o;
    } };
    return { get: n, set(o) {
      const l = n?.call(this);
      i?.call(this, o), this.requestUpdate(t, l, r);
    }, configurable: !0, enumerable: !0 };
  }
  static getPropertyOptions(t) {
    return this.elementProperties.get(t) ?? Y;
  }
  static _$Ei() {
    if (this.hasOwnProperty(C("elementProperties"))) return;
    const t = Tt(this);
    t.finalize(), t.l !== void 0 && (this.l = [...t.l]), this.elementProperties = new Map(t.elementProperties);
  }
  static finalize() {
    if (this.hasOwnProperty(C("finalized"))) return;
    if (this.finalized = !0, this._$Ei(), this.hasOwnProperty(C("properties"))) {
      const s = this.properties, r = [...St(s), ...Ct(s)];
      for (const n of r) this.createProperty(n, s[n]);
    }
    const t = this[Symbol.metadata];
    if (t !== null) {
      const s = litPropertyMetadata.get(t);
      if (s !== void 0) for (const [r, n] of s) this.elementProperties.set(r, n);
    }
    this._$Eh = /* @__PURE__ */ new Map();
    for (const [s, r] of this.elementProperties) {
      const n = this._$Eu(s, r);
      n !== void 0 && this._$Eh.set(n, s);
    }
    this.elementStyles = this.finalizeStyles(this.styles);
  }
  static finalizeStyles(t) {
    const s = [];
    if (Array.isArray(t)) {
      const r = new Set(t.flat(1 / 0).reverse());
      for (const n of r) s.unshift(X(n));
    } else t !== void 0 && s.push(X(t));
    return s;
  }
  static _$Eu(t, s) {
    const r = s.attribute;
    return r === !1 ? void 0 : typeof r == "string" ? r : typeof t == "string" ? t.toLowerCase() : void 0;
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
    const t = /* @__PURE__ */ new Map(), s = this.constructor.elementProperties;
    for (const r of s.keys()) this.hasOwnProperty(r) && (t.set(r, this[r]), delete this[r]);
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
  attributeChangedCallback(t, s, r) {
    this._$AK(t, r);
  }
  _$ET(t, s) {
    const r = this.constructor.elementProperties.get(t), n = this.constructor._$Eu(t, r);
    if (n !== void 0 && r.reflect === !0) {
      const i = (r.converter?.toAttribute !== void 0 ? r.converter : R).toAttribute(s, r.type);
      this._$Em = t, i == null ? this.removeAttribute(n) : this.setAttribute(n, i), this._$Em = null;
    }
  }
  _$AK(t, s) {
    const r = this.constructor, n = r._$Eh.get(t);
    if (n !== void 0 && this._$Em !== n) {
      const i = r.getPropertyOptions(n), o = typeof i.converter == "function" ? { fromAttribute: i.converter } : i.converter?.fromAttribute !== void 0 ? i.converter : R;
      this._$Em = n;
      const l = o.fromAttribute(s, i.type);
      this[n] = l ?? this._$Ej?.get(n) ?? l, this._$Em = null;
    }
  }
  requestUpdate(t, s, r, n = !1, i) {
    if (t !== void 0) {
      const o = this.constructor;
      if (n === !1 && (i = this[t]), r ??= o.getPropertyOptions(t), !((r.hasChanged ?? q)(i, s) || r.useDefault && r.reflect && i === this._$Ej?.get(t) && !this.hasAttribute(o._$Eu(t, r)))) return;
      this.C(t, s, r);
    }
    this.isUpdatePending === !1 && (this._$ES = this._$EP());
  }
  C(t, s, { useDefault: r, reflect: n, wrapped: i }, o) {
    r && !(this._$Ej ??= /* @__PURE__ */ new Map()).has(t) && (this._$Ej.set(t, o ?? s ?? this[t]), i !== !0 || o !== void 0) || (this._$AL.has(t) || (this.hasUpdated || r || (s = void 0), this._$AL.set(t, s)), n === !0 && this._$Em !== t && (this._$Eq ??= /* @__PURE__ */ new Set()).add(t));
  }
  async _$EP() {
    this.isUpdatePending = !0;
    try {
      await this._$ES;
    } catch (s) {
      Promise.reject(s);
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
      const r = this.constructor.elementProperties;
      if (r.size > 0) for (const [n, i] of r) {
        const { wrapped: o } = i, l = this[n];
        o !== !0 || this._$AL.has(n) || l === void 0 || this.C(n, void 0, i, l);
      }
    }
    let t = !1;
    const s = this._$AL;
    try {
      t = this.shouldUpdate(s), t ? (this.willUpdate(s), this._$EO?.forEach((r) => r.hostUpdate?.()), this.update(s)) : this._$EM();
    } catch (r) {
      throw t = !1, this._$EM(), r;
    }
    t && this._$AE(s);
  }
  willUpdate(t) {
  }
  _$AE(t) {
    this._$EO?.forEach((s) => s.hostUpdated?.()), this.hasUpdated || (this.hasUpdated = !0, this.firstUpdated(t)), this.updated(t);
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
    this._$Eq &&= this._$Eq.forEach((s) => this._$ET(s, this[s])), this._$EM();
  }
  updated(t) {
  }
  firstUpdated(t) {
  }
};
b.elementStyles = [], b.shadowRootOptions = { mode: "open" }, b[C("elementProperties")] = /* @__PURE__ */ new Map(), b[C("finalized")] = /* @__PURE__ */ new Map(), kt?.({ ReactiveElement: b }), (j.reactiveElementVersions ??= []).push("2.1.2");
/**
 * @license
 * Copyright 2017 Google LLC
 * SPDX-License-Identifier: BSD-3-Clause
 */
const W = globalThis, tt = (e) => e, H = W.trustedTypes, et = H ? H.createPolicy("lit-html", { createHTML: (e) => e }) : void 0, ut = "$lit$", _ = `lit$${Math.random().toFixed(9).slice(2)}$`, pt = "?" + _, Ot = `<${pt}>`, v = document, T = () => v.createComment(""), P = (e) => e === null || typeof e != "object" && typeof e != "function", K = Array.isArray, Ut = (e) => K(e) || typeof e?.[Symbol.iterator] == "function", z = `[ 	
\f\r]`, E = /<(?:(!--|\/[^a-zA-Z])|(\/?[a-zA-Z][^>\s]*)|(\/?$))/g, st = /-->/g, rt = />/g, m = RegExp(`>|${z}(?:([^\\s"'>=/]+)(${z}*=${z}*(?:[^ 	
\f\r"'\`<>=]|("|')|))|$)`, "g"), nt = /'/g, it = /"/g, ft = /^(?:script|style|textarea|title)$/i, Nt = (e) => (t, ...s) => ({ _$litType$: e, strings: t, values: s }), p = Nt(1), y = Symbol.for("lit-noChange"), h = Symbol.for("lit-nothing"), ot = /* @__PURE__ */ new WeakMap(), g = v.createTreeWalker(v, 129);
function $t(e, t) {
  if (!K(e) || !e.hasOwnProperty("raw")) throw Error("invalid template strings array");
  return et !== void 0 ? et.createHTML(t) : t;
}
const Mt = (e, t) => {
  const s = e.length - 1, r = [];
  let n, i = t === 2 ? "<svg>" : t === 3 ? "<math>" : "", o = E;
  for (let l = 0; l < s; l++) {
    const a = e[l];
    let d, u, c = -1, f = 0;
    for (; f < a.length && (o.lastIndex = f, u = o.exec(a), u !== null); ) f = o.lastIndex, o === E ? u[1] === "!--" ? o = st : u[1] !== void 0 ? o = rt : u[2] !== void 0 ? (ft.test(u[2]) && (n = RegExp("</" + u[2], "g")), o = m) : u[3] !== void 0 && (o = m) : o === m ? u[0] === ">" ? (o = n ?? E, c = -1) : u[1] === void 0 ? c = -2 : (c = o.lastIndex - u[2].length, d = u[1], o = u[3] === void 0 ? m : u[3] === '"' ? it : nt) : o === it || o === nt ? o = m : o === st || o === rt ? o = E : (o = m, n = void 0);
    const $ = o === m && e[l + 1].startsWith("/>") ? " " : "";
    i += o === E ? a + Ot : c >= 0 ? (r.push(d), a.slice(0, c) + ut + a.slice(c) + _ + $) : a + _ + (c === -2 ? l : $);
  }
  return [$t(e, i + (e[s] || "<?>") + (t === 2 ? "</svg>" : t === 3 ? "</math>" : "")), r];
};
class k {
  constructor({ strings: t, _$litType$: s }, r) {
    let n;
    this.parts = [];
    let i = 0, o = 0;
    const l = t.length - 1, a = this.parts, [d, u] = Mt(t, s);
    if (this.el = k.createElement(d, r), g.currentNode = this.el.content, s === 2 || s === 3) {
      const c = this.el.content.firstChild;
      c.replaceWith(...c.childNodes);
    }
    for (; (n = g.nextNode()) !== null && a.length < l; ) {
      if (n.nodeType === 1) {
        if (n.hasAttributes()) for (const c of n.getAttributeNames()) if (c.endsWith(ut)) {
          const f = u[o++], $ = n.getAttribute(c).split(_), N = /([.?@])?(.*)/.exec(f);
          a.push({ type: 1, index: i, name: N[2], strings: $, ctor: N[1] === "." ? Ht : N[1] === "?" ? jt : N[1] === "@" ? Dt : D }), n.removeAttribute(c);
        } else c.startsWith(_) && (a.push({ type: 6, index: i }), n.removeAttribute(c));
        if (ft.test(n.tagName)) {
          const c = n.textContent.split(_), f = c.length - 1;
          if (f > 0) {
            n.textContent = H ? H.emptyScript : "";
            for (let $ = 0; $ < f; $++) n.append(c[$], T()), g.nextNode(), a.push({ type: 2, index: ++i });
            n.append(c[f], T());
          }
        }
      } else if (n.nodeType === 8) if (n.data === pt) a.push({ type: 2, index: i });
      else {
        let c = -1;
        for (; (c = n.data.indexOf(_, c + 1)) !== -1; ) a.push({ type: 7, index: i }), c += _.length - 1;
      }
      i++;
    }
  }
  static createElement(t, s) {
    const r = v.createElement("template");
    return r.innerHTML = t, r;
  }
}
function x(e, t, s = e, r) {
  if (t === y) return t;
  let n = r !== void 0 ? s._$Co?.[r] : s._$Cl;
  const i = P(t) ? void 0 : t._$litDirective$;
  return n?.constructor !== i && (n?._$AO?.(!1), i === void 0 ? n = void 0 : (n = new i(e), n._$AT(e, s, r)), r !== void 0 ? (s._$Co ??= [])[r] = n : s._$Cl = n), n !== void 0 && (t = x(e, n._$AS(e, t.values), n, r)), t;
}
class Rt {
  constructor(t, s) {
    this._$AV = [], this._$AN = void 0, this._$AD = t, this._$AM = s;
  }
  get parentNode() {
    return this._$AM.parentNode;
  }
  get _$AU() {
    return this._$AM._$AU;
  }
  u(t) {
    const { el: { content: s }, parts: r } = this._$AD, n = (t?.creationScope ?? v).importNode(s, !0);
    g.currentNode = n;
    let i = g.nextNode(), o = 0, l = 0, a = r[0];
    for (; a !== void 0; ) {
      if (o === a.index) {
        let d;
        a.type === 2 ? d = new U(i, i.nextSibling, this, t) : a.type === 1 ? d = new a.ctor(i, a.name, a.strings, this, t) : a.type === 6 && (d = new It(i, this, t)), this._$AV.push(d), a = r[++l];
      }
      o !== a?.index && (i = g.nextNode(), o++);
    }
    return g.currentNode = v, n;
  }
  p(t) {
    let s = 0;
    for (const r of this._$AV) r !== void 0 && (r.strings !== void 0 ? (r._$AI(t, r, s), s += r.strings.length - 2) : r._$AI(t[s])), s++;
  }
}
class U {
  get _$AU() {
    return this._$AM?._$AU ?? this._$Cv;
  }
  constructor(t, s, r, n) {
    this.type = 2, this._$AH = h, this._$AN = void 0, this._$AA = t, this._$AB = s, this._$AM = r, this.options = n, this._$Cv = n?.isConnected ?? !0;
  }
  get parentNode() {
    let t = this._$AA.parentNode;
    const s = this._$AM;
    return s !== void 0 && t?.nodeType === 11 && (t = s.parentNode), t;
  }
  get startNode() {
    return this._$AA;
  }
  get endNode() {
    return this._$AB;
  }
  _$AI(t, s = this) {
    t = x(this, t, s), P(t) ? t === h || t == null || t === "" ? (this._$AH !== h && this._$AR(), this._$AH = h) : t !== this._$AH && t !== y && this._(t) : t._$litType$ !== void 0 ? this.$(t) : t.nodeType !== void 0 ? this.T(t) : Ut(t) ? this.k(t) : this._(t);
  }
  O(t) {
    return this._$AA.parentNode.insertBefore(t, this._$AB);
  }
  T(t) {
    this._$AH !== t && (this._$AR(), this._$AH = this.O(t));
  }
  _(t) {
    this._$AH !== h && P(this._$AH) ? this._$AA.nextSibling.data = t : this.T(v.createTextNode(t)), this._$AH = t;
  }
  $(t) {
    const { values: s, _$litType$: r } = t, n = typeof r == "number" ? this._$AC(t) : (r.el === void 0 && (r.el = k.createElement($t(r.h, r.h[0]), this.options)), r);
    if (this._$AH?._$AD === n) this._$AH.p(s);
    else {
      const i = new Rt(n, this), o = i.u(this.options);
      i.p(s), this.T(o), this._$AH = i;
    }
  }
  _$AC(t) {
    let s = ot.get(t.strings);
    return s === void 0 && ot.set(t.strings, s = new k(t)), s;
  }
  k(t) {
    K(this._$AH) || (this._$AH = [], this._$AR());
    const s = this._$AH;
    let r, n = 0;
    for (const i of t) n === s.length ? s.push(r = new U(this.O(T()), this.O(T()), this, this.options)) : r = s[n], r._$AI(i), n++;
    n < s.length && (this._$AR(r && r._$AB.nextSibling, n), s.length = n);
  }
  _$AR(t = this._$AA.nextSibling, s) {
    for (this._$AP?.(!1, !0, s); t !== this._$AB; ) {
      const r = tt(t).nextSibling;
      tt(t).remove(), t = r;
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
  constructor(t, s, r, n, i) {
    this.type = 1, this._$AH = h, this._$AN = void 0, this.element = t, this.name = s, this._$AM = n, this.options = i, r.length > 2 || r[0] !== "" || r[1] !== "" ? (this._$AH = Array(r.length - 1).fill(new String()), this.strings = r) : this._$AH = h;
  }
  _$AI(t, s = this, r, n) {
    const i = this.strings;
    let o = !1;
    if (i === void 0) t = x(this, t, s, 0), o = !P(t) || t !== this._$AH && t !== y, o && (this._$AH = t);
    else {
      const l = t;
      let a, d;
      for (t = i[0], a = 0; a < i.length - 1; a++) d = x(this, l[r + a], s, a), d === y && (d = this._$AH[a]), o ||= !P(d) || d !== this._$AH[a], d === h ? t = h : t !== h && (t += (d ?? "") + i[a + 1]), this._$AH[a] = d;
    }
    o && !n && this.j(t);
  }
  j(t) {
    t === h ? this.element.removeAttribute(this.name) : this.element.setAttribute(this.name, t ?? "");
  }
}
class Ht extends D {
  constructor() {
    super(...arguments), this.type = 3;
  }
  j(t) {
    this.element[this.name] = t === h ? void 0 : t;
  }
}
class jt extends D {
  constructor() {
    super(...arguments), this.type = 4;
  }
  j(t) {
    this.element.toggleAttribute(this.name, !!t && t !== h);
  }
}
class Dt extends D {
  constructor(t, s, r, n, i) {
    super(t, s, r, n, i), this.type = 5;
  }
  _$AI(t, s = this) {
    if ((t = x(this, t, s, 0) ?? h) === y) return;
    const r = this._$AH, n = t === h && r !== h || t.capture !== r.capture || t.once !== r.once || t.passive !== r.passive, i = t !== h && (r === h || n);
    n && this.element.removeEventListener(this.name, this, r), i && this.element.addEventListener(this.name, this, t), this._$AH = t;
  }
  handleEvent(t) {
    typeof this._$AH == "function" ? this._$AH.call(this.options?.host ?? this.element, t) : this._$AH.handleEvent(t);
  }
}
class It {
  constructor(t, s, r) {
    this.element = t, this.type = 6, this._$AN = void 0, this._$AM = s, this.options = r;
  }
  get _$AU() {
    return this._$AM._$AU;
  }
  _$AI(t) {
    x(this, t);
  }
}
const zt = W.litHtmlPolyfillSupport;
zt?.(k, U), (W.litHtmlVersions ??= []).push("3.3.3");
const Lt = (e, t, s) => {
  const r = s?.renderBefore ?? t;
  let n = r._$litPart$;
  if (n === void 0) {
    const i = s?.renderBefore ?? null;
    r._$litPart$ = n = new U(t.insertBefore(T(), i), i, void 0, s ?? {});
  }
  return n._$AI(e), n;
};
/**
 * @license
 * Copyright 2017 Google LLC
 * SPDX-License-Identifier: BSD-3-Clause
 */
const V = globalThis;
let A = class extends b {
  constructor() {
    super(...arguments), this.renderOptions = { host: this }, this._$Do = void 0;
  }
  createRenderRoot() {
    const t = super.createRenderRoot();
    return this.renderOptions.renderBefore ??= t.firstChild, t;
  }
  update(t) {
    const s = this.render();
    this.hasUpdated || (this.renderOptions.isConnected = this.isConnected), super.update(t), this._$Do = Lt(s, this.renderRoot, this.renderOptions);
  }
  connectedCallback() {
    super.connectedCallback(), this._$Do?.setConnected(!0);
  }
  disconnectedCallback() {
    super.disconnectedCallback(), this._$Do?.setConnected(!1);
  }
  render() {
    return y;
  }
};
A._$litElement$ = !0, A.finalized = !0, V.litElementHydrateSupport?.({ LitElement: A });
const Bt = V.litElementPolyfillSupport;
Bt?.({ LitElement: A });
(V.litElementVersions ??= []).push("4.2.2");
/**
 * @license
 * Copyright 2017 Google LLC
 * SPDX-License-Identifier: BSD-3-Clause
 */
const _t = (e) => (t, s) => {
  s !== void 0 ? s.addInitializer(() => {
    customElements.define(e, t);
  }) : customElements.define(e, t);
};
/**
 * @license
 * Copyright 2017 Google LLC
 * SPDX-License-Identifier: BSD-3-Clause
 */
const qt = { attribute: !0, type: String, converter: R, reflect: !1, hasChanged: q }, Wt = (e = qt, t, s) => {
  const { kind: r, metadata: n } = s;
  let i = globalThis.litPropertyMetadata.get(n);
  if (i === void 0 && globalThis.litPropertyMetadata.set(n, i = /* @__PURE__ */ new Map()), r === "setter" && ((e = Object.create(e)).wrapped = !0), i.set(s.name, e), r === "accessor") {
    const { name: o } = s;
    return { set(l) {
      const a = t.get.call(this);
      t.set.call(this, l), this.requestUpdate(o, a, e, !0, l);
    }, init(l) {
      return l !== void 0 && this.C(o, void 0, e, l), l;
    } };
  }
  if (r === "setter") {
    const { name: o } = s;
    return function(l) {
      const a = this[o];
      t.call(this, l), this.requestUpdate(o, a, e, !0, l);
    };
  }
  throw Error("Unsupported decorator location: " + r);
};
function J(e) {
  return (t, s) => typeof s == "object" ? Wt(e, t, s) : ((r, n, i) => {
    const o = n.hasOwnProperty(i);
    return n.constructor.createProperty(i, r), o ? Object.getOwnPropertyDescriptor(n, i) : void 0;
  })(e, t, s);
}
/**
 * @license
 * Copyright 2017 Google LLC
 * SPDX-License-Identifier: BSD-3-Clause
 */
function Z(e) {
  return J({ ...e, state: !0, attribute: !1 });
}
/**
 * @license
 * Copyright 2017 Google LLC
 * SPDX-License-Identifier: BSD-3-Clause
 */
const Kt = { ATTRIBUTE: 1 }, Vt = (e) => (...t) => ({ _$litDirective$: e, values: t });
class Jt {
  constructor(t) {
  }
  get _$AU() {
    return this._$AM._$AU;
  }
  _$AT(t, s, r) {
    this._$Ct = t, this._$AM = s, this._$Ci = r;
  }
  _$AS(t, s) {
    return this.update(t, s);
  }
  update(t, s) {
    return this.render(...s);
  }
}
/**
 * @license
 * Copyright 2018 Google LLC
 * SPDX-License-Identifier: BSD-3-Clause
 */
const Zt = Vt(class extends Jt {
  constructor(e) {
    if (super(e), e.type !== Kt.ATTRIBUTE || e.name !== "class" || e.strings?.length > 2) throw Error("`classMap()` can only be used in the `class` attribute and must be the only part in the attribute.");
  }
  render(e) {
    return " " + Object.keys(e).filter((t) => e[t]).join(" ") + " ";
  }
  update(e, [t]) {
    if (this.st === void 0) {
      this.st = /* @__PURE__ */ new Set(), e.strings !== void 0 && (this.nt = new Set(e.strings.join(" ").split(/\s/).filter((r) => r !== "")));
      for (const r in t) t[r] && !this.nt?.has(r) && this.st.add(r);
      return this.render(t);
    }
    const s = e.element.classList;
    for (const r of this.st) r in t || (s.remove(r), this.st.delete(r));
    for (const r in t) {
      const n = !!t[r];
      n === this.st.has(r) || this.nt?.has(r) || (n ? (s.add(r), this.st.add(r)) : (s.remove(r), this.st.delete(r)));
    }
    return y;
  }
}), Ft = /* @__PURE__ */ new Set(["unknown", "unavailable", "none", ""]), Gt = 10;
function Xt(e) {
  return e === void 0 || Ft.has(e);
}
function at(e) {
  const t = /_(\d+)$/.exec(e);
  return t ? Number(t[1]) : 0;
}
function Qt(e, t) {
  const s = Yt(e, t), r = te(e, t);
  return s && s.departures.length >= r.departures.length ? s : {
    departures: r.departures,
    favorite: r.favorite ?? s?.favorite ?? null,
    progress: r.progress ?? s?.progress ?? null
  };
}
function Yt(e, t) {
  const s = e.entities?.[t]?.device_id;
  if (!s)
    return null;
  const r = Object.values(e.entities).filter(
    (l) => l.device_id === s
  ), n = (l) => r.filter((a) => a.translation_key === l).map((a) => a.entity_id), i = n("departure").sort(
    (l, a) => at(l) - at(a)
  ), o = [...n("next_departure"), ...i];
  return o.length ? {
    departures: o,
    favorite: n("next_favorite")[0] ?? null,
    progress: n("stops_away")[0] ?? null
  } : null;
}
function te(e, t) {
  const s = t.replace(/_next_departure$/, ""), r = (i) => e.states[i] ? i : null, n = [t];
  for (let i = 2; i <= Gt; i++) {
    const o = r(`${s}_departure_${i}`);
    o && n.push(o);
  }
  return {
    departures: n,
    favorite: r(`${s}_next_favorite`),
    progress: r(`${s}_stops_away`)
  };
}
function S(e) {
  return typeof e == "string" && e !== "" ? e : null;
}
function mt(e, t) {
  const s = e.states[t];
  if (!s || Xt(s.state))
    return null;
  const r = new Date(s.state);
  if (Number.isNaN(r.getTime()))
    return null;
  const n = s.attributes ?? {}, i = n.delay_minutes, o = Array.isArray(n.cars) ? n.cars : [], l = Array.isArray(n.alerts) ? n.alerts.filter(
    (a) => typeof a == "string"
  ) : [];
  return {
    entityId: t,
    scheduled: r,
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
function ee(e, t) {
  return t.departures.map((s) => mt(e, s)).filter((s) => s !== null);
}
function se(e) {
  return e.some((t) => t.track !== null);
}
function re(e, t) {
  if (e)
    return { departure: e, tracking: !0, allCancelled: !1 };
  const s = t.find((r) => r.status !== "cancelled");
  return {
    departure: s ?? null,
    tracking: !1,
    allCancelled: !s && t.length > 0
  };
}
function lt(e, t) {
  return Math.round((e.getTime() - t.getTime()) / 6e4);
}
const gt = new Intl.DateTimeFormat("en-US", {
  hour: "numeric",
  minute: "2-digit",
  hour12: !0
});
function ne(e) {
  return gt.format(e);
}
function ie(e) {
  return gt.format(e).replace(/\s?[AP]M$/i, "");
}
function oe(e) {
  return e < 0 ? { value: "Departed", unit: !1 } : e === 0 ? { value: "Now", unit: !1 } : { value: String(e), unit: !0 };
}
const vt = 6, ct = 10, ae = 25;
function le(e, t, s) {
  return e.track ? { text: `Track ${e.track}`, tone: "accent" } : e.status === "cancelled" ? null : s ? t <= vt ? { text: "⚠️ Track overdue", tone: "bad" } : t <= ct ? { text: "Track due any minute", tone: "muted" } : t <= ae ? { text: `Track due in ~${t - (ct - 1)} min`, tone: "muted" } : { text: "Track not posted yet", tone: "muted" } : { text: "Track not posted", tone: "muted" };
}
function ce(e, t, s) {
  return e.track ? { text: e.track, tone: "accent" } : s && t <= vt && e.status !== "cancelled" ? { text: "⚠️", tone: "bad" } : { text: "—", tone: "muted" };
}
function ht(e) {
  return e.statusText ? e.status === "cancelled" ? { text: e.statusText, tone: "bad" } : e.delayMinutes ? { text: e.statusText, tone: "warn" } : { text: e.statusText, tone: "muted" } : null;
}
function he(e) {
  return e.crowding === "heavy" ? { text: "Busy", tone: "warn" } : e.crowding === "moderate" ? { text: "Filling up", tone: "muted" } : null;
}
const de = { light: 0, moderate: 1, heavy: 2 };
function ue(e) {
  let t = null, s = 1 / 0, r = -1 / 0;
  for (const n of e) {
    const i = n.crowding ? de[n.crowding] : void 0;
    i === void 0 || !n.position || (i < s && (s = i, t = n.position), r = Math.max(r, i));
  }
  return t !== null && r > s ? t : null;
}
var pe = Object.defineProperty, fe = Object.getOwnPropertyDescriptor, I = (e, t, s, r) => {
  for (var n = r > 1 ? void 0 : r ? fe(t, s) : t, i = e.length - 1, o; i >= 0; i--)
    (o = e[i]) && (n = (r ? o(t, s, n) : o(n)) || n);
  return r && n && pe(t, s, n), n;
};
const $e = 1e4;
let w = class extends A {
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
    return await Promise.resolve().then(() => be), document.createElement("njtransit-departures-editor");
  }
  /** Offer a working card the moment it is added from the picker. */
  static getStubConfig(e) {
    return { type: "custom:njtransit-departures", entity: Object.keys(e.states).find(
      (s) => s.endsWith("_next_departure")
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
    const e = Qt(this.hass, this._config.entity), t = ee(this.hass, e), s = e.favorite ? mt(this.hass, e.favorite) : null, { departure: r, tracking: n, allCancelled: i } = re(s, t), o = se(t);
    return p`
      <ha-card>
        ${this._config.title ? p`<h1 class="card-header">${this._config.title}</h1>` : h}
        ${r ? this._renderHero(r, {
      tracking: n,
      posting: o,
      progress: e.progress,
      favoriteEntity: e.favorite
    }) : this._renderEmpty(i)}
        ${t.length ? this._renderBoard(t, o) : h}
      </ha-card>
    `;
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
  _renderHero(e, t) {
    const { tracking: s, posting: r, progress: n, favoriteEntity: i } = t, o = lt(e.scheduled, this._now), { value: l, unit: a } = oe(o), d = [
      le(e, o, r),
      ht(e),
      he(e)
    ].filter((c) => c !== null), u = ue(e.cars);
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
          ${ne(e.scheduled)}
          ${e.trainId ? p`· Train ${e.trainId}` : h}
        </h2>
        <div class="pills">${d.map((c) => this._renderPill(c))}</div>
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
    const s = Number(t.state), r = t.attributes.next_stop;
    return p`
      <hr />
      <p class="progress">
        ${s === 0 ? "Arriving now" : p`<strong>${s}</strong> stops away`}
        ${typeof r == "string" && r ? p` · next ${r}` : h}
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
            ${e.map((s) => this._renderRow(s, t))}
          </tbody>
        </table>
      </div>
    `;
  }
  _renderRow(e, t) {
    const s = lt(e.scheduled, this._now), r = ht(e);
    return p`
      <tr @click=${() => this._moreInfo(e.entityId)}>
        <td>
          <strong>${ie(e.scheduled)}</strong>
          <span class="relative">${s > 0 ? `${s}m` : "now"}</span>
        </td>
        <td>${e.trainId}${e.favorite ? " ⭐" : ""}</td>
        <td>${this._renderPill(ce(e, s, t))}</td>
        <td>${r ? this._renderPill(r) : h}</td>
      </tr>
    `;
  }
  _renderPill(e) {
    return p`<span
      class=${Zt({ pill: !0, [e.tone]: !0 })}
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
w.styles = bt`
    :host {
      /* The board itself sends #00953b as the Morristown Line's colour. Other
         lines send their own, and the feed also uses that field to mark
         cancellations, so it is not a dependable line identity and the
         integration does not expose it -- override this to match yours. */
      --njtransit-accent: #00953b;
    }

    ha-card {
      border: none;
      border-top: 3px solid var(--njtransit-accent);
      border-radius: 16px;
      overflow: hidden;
      background: linear-gradient(
        168deg,
        color-mix(in srgb, var(--njtransit-accent) 10%, var(--card-background-color))
          0%,
        var(--card-background-color) 62%
      );
    }

    .card-header {
      margin: 0;
      padding: 14px 16px 0;
      font-size: 1.1rem;
      font-weight: 600;
    }

    .hero {
      padding: 16px 16px 18px;
      cursor: pointer;
    }

    .hero:focus-visible {
      outline: 2px solid var(--njtransit-accent);
      outline-offset: -2px;
    }

    .hero.empty {
      cursor: default;
    }

    .countdown {
      font-size: 3.5rem;
      font-weight: 800;
      line-height: 0.95;
      letter-spacing: -0.035em;
      font-variant-numeric: tabular-nums;
      color: var(--primary-text-color);
      margin-bottom: 2px;
    }

    /* The unit, kept small so the number carries the card. */
    .unit {
      font-size: 0.28em;
      font-weight: 600;
      letter-spacing: 0.1em;
      text-transform: uppercase;
      color: var(--secondary-text-color);
      margin-left: 0.3em;
      vertical-align: 0.6em;
    }

    h2 {
      margin: 0 0 14px;
      font-size: 0.78rem;
      font-weight: 600;
      letter-spacing: 0.1em;
      text-transform: uppercase;
      color: var(--secondary-text-color);
    }

    h3 {
      margin: 0 0 6px;
      font-size: 1.15rem;
      font-weight: 600;
    }

    p {
      margin: 0;
      line-height: 1.6;
    }

    .hint {
      margin-top: 10px;
      color: var(--secondary-text-color);
      font-style: italic;
    }

    hr {
      border: none;
      border-top: 1px solid var(--divider-color);
      margin: 14px 0 12px;
    }

    .pills {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
    }

    .pill {
      font-size: 0.8rem;
      font-weight: 700;
      letter-spacing: 0.015em;
      white-space: nowrap;
      padding: 4px 10px;
      border-radius: 999px;
      border: 1px solid transparent;
      background: var(--njtransit-accent);
      color: #fff;
    }

    .pill.bad {
      background: var(--error-color);
      color: #fff;
    }

    .pill.warn {
      background: var(--warning-color);
      color: #111;
    }

    .pill.muted {
      background: transparent;
      color: var(--secondary-text-color);
      border-color: var(--divider-color);
      font-weight: 600;
    }

    blockquote {
      margin: 12px 0 0;
      padding: 9px 12px;
      border-left: 3px solid var(--error-color);
      border-radius: 0 8px 8px 0;
      background: color-mix(in srgb, var(--error-color) 10%, transparent);
      font-size: 0.9rem;
      line-height: 1.5;
    }

    .board {
      padding: 0 16px 14px;
    }

    table {
      width: 100%;
      border-collapse: collapse;
      font-variant-numeric: tabular-nums;
    }

    th {
      padding: 0 4px 8px 0;
      border-bottom: 1px solid var(--divider-color);
      text-align: left;
      font-size: 0.65rem;
      font-weight: 700;
      letter-spacing: 0.12em;
      text-transform: uppercase;
      color: var(--secondary-text-color);
    }

    td {
      padding: 10px 4px 10px 0;
      border-bottom: 1px solid
        color-mix(in srgb, var(--divider-color) 45%, transparent);
      font-size: 0.98rem;
      white-space: nowrap;
    }

    tbody tr {
      cursor: pointer;
    }

    tbody tr:last-child td {
      border-bottom: none;
    }

    .relative {
      font-size: 0.8rem;
      color: var(--secondary-text-color);
      margin-left: 4px;
    }

    .board .pill {
      font-size: 0.75rem;
      padding: 3px 9px;
    }
  `;
I([
  J({ attribute: !1 })
], w.prototype, "hass", 2);
I([
  Z()
], w.prototype, "_config", 2);
I([
  Z()
], w.prototype, "_now", 2);
w = I([
  _t("njtransit-departures")
], w);
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
var me = Object.defineProperty, ge = Object.getOwnPropertyDescriptor, F = (e, t, s, r) => {
  for (var n = r > 1 ? void 0 : r ? ge(t, s) : t, i = e.length - 1, o; i >= 0; i--)
    (o = e[i]) && (n = (r ? o(t, s, n) : o(n)) || n);
  return r && n && me(t, s, n), n;
};
const ve = [
  {
    name: "entity",
    required: !0,
    selector: { entity: { integration: "njtransit", domain: "sensor" } }
  },
  { name: "title", selector: { text: {} } }
], ye = {
  entity: "Commute (any departure sensor)",
  title: "Heading (optional)"
};
let O = class extends A {
  setConfig(e) {
    this._config = e;
  }
  render() {
    return !this.hass || !this._config ? h : p`
      <ha-form
        .hass=${this.hass}
        .data=${this._config}
        .schema=${ve}
        .computeLabel=${(e) => ye[e.name] ?? e.name}
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
F([
  J({ attribute: !1 })
], O.prototype, "hass", 2);
F([
  Z()
], O.prototype, "_config", 2);
O = F([
  _t("njtransit-departures-editor")
], O);
const be = /* @__PURE__ */ Object.freeze(/* @__PURE__ */ Object.defineProperty({
  __proto__: null,
  get NJTransitDeparturesEditor() {
    return O;
  }
}, Symbol.toStringTag, { value: "Module" }));
