/**
 * @license
 * Copyright 2019 Google LLC
 * SPDX-License-Identifier: BSD-3-Clause
 */
const N = globalThis, W = N.ShadowRoot && (N.ShadyCSS === void 0 || N.ShadyCSS.nativeShadow) && "adoptedStyleSheets" in Document.prototype && "replace" in CSSStyleSheet.prototype, q = Symbol(), X = /* @__PURE__ */ new WeakMap();
let mt = class {
  constructor(t, r, s) {
    if (this._$cssResult$ = !0, s !== q) throw Error("CSSResult is not constructable. Use `unsafeCSS` or `css` instead.");
    this.cssText = t, this.t = r;
  }
  get styleSheet() {
    let t = this.o;
    const r = this.t;
    if (W && t === void 0) {
      const s = r !== void 0 && r.length === 1;
      s && (t = X.get(r)), t === void 0 && ((this.o = t = new CSSStyleSheet()).replaceSync(this.cssText), s && X.set(r, t));
    }
    return t;
  }
  toString() {
    return this.cssText;
  }
};
const At = (e) => new mt(typeof e == "string" ? e : e + "", void 0, q), kt = (e, ...t) => {
  const r = e.length === 1 ? e[0] : t.reduce((s, n, i) => s + ((o) => {
    if (o._$cssResult$ === !0) return o.cssText;
    if (typeof o == "number") return o;
    throw Error("Value passed to 'css' function must be a 'css' function result: " + o + ". Use 'unsafeCSS' to pass non-literal values, but take care to ensure page security.");
  })(n) + e[i + 1], e[0]);
  return new mt(r, e, q);
}, Et = (e, t) => {
  if (W) e.adoptedStyleSheets = t.map((r) => r instanceof CSSStyleSheet ? r : r.styleSheet);
  else for (const r of t) {
    const s = document.createElement("style"), n = N.litNonce;
    n !== void 0 && s.setAttribute("nonce", n), s.textContent = r.cssText, e.appendChild(s);
  }
}, Q = W ? (e) => e : (e) => e instanceof CSSStyleSheet ? ((t) => {
  let r = "";
  for (const s of t.cssRules) r += s.cssText;
  return At(r);
})(e) : e;
/**
 * @license
 * Copyright 2017 Google LLC
 * SPDX-License-Identifier: BSD-3-Clause
 */
const { is: St, defineProperty: Tt, getOwnPropertyDescriptor: Ct, getOwnPropertyNames: Pt, getOwnPropertySymbols: Mt, getPrototypeOf: Ot } = Object, H = globalThis, tt = H.trustedTypes, Ut = tt ? tt.emptyScript : "", Nt = H.reactiveElementPolyfillSupport, S = (e, t) => e, j = { toAttribute(e, t) {
  switch (t) {
    case Boolean:
      e = e ? Ut : null;
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
} }, V = (e, t) => !St(e, t), et = { attribute: !0, type: String, converter: j, reflect: !1, useDefault: !1, hasChanged: V };
Symbol.metadata ??= Symbol("metadata"), H.litPropertyMetadata ??= /* @__PURE__ */ new WeakMap();
let w = class extends HTMLElement {
  static addInitializer(t) {
    this._$Ei(), (this.l ??= []).push(t);
  }
  static get observedAttributes() {
    return this.finalize(), this._$Eh && [...this._$Eh.keys()];
  }
  static createProperty(t, r = et) {
    if (r.state && (r.attribute = !1), this._$Ei(), this.prototype.hasOwnProperty(t) && ((r = Object.create(r)).wrapped = !0), this.elementProperties.set(t, r), !r.noAccessor) {
      const s = Symbol(), n = this.getPropertyDescriptor(t, s, r);
      n !== void 0 && Tt(this.prototype, t, n);
    }
  }
  static getPropertyDescriptor(t, r, s) {
    const { get: n, set: i } = Ct(this.prototype, t) ?? { get() {
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
    return this.elementProperties.get(t) ?? et;
  }
  static _$Ei() {
    if (this.hasOwnProperty(S("elementProperties"))) return;
    const t = Ot(this);
    t.finalize(), t.l !== void 0 && (this.l = [...t.l]), this.elementProperties = new Map(t.elementProperties);
  }
  static finalize() {
    if (this.hasOwnProperty(S("finalized"))) return;
    if (this.finalized = !0, this._$Ei(), this.hasOwnProperty(S("properties"))) {
      const r = this.properties, s = [...Pt(r), ...Mt(r)];
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
      for (const n of s) r.unshift(Q(n));
    } else t !== void 0 && r.push(Q(t));
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
    return Et(t, this.constructor.elementStyles), t;
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
      if (n === !1 && (i = this[t]), s ??= o.getPropertyOptions(t), !((s.hasChanged ?? V)(i, r) || s.useDefault && s.reflect && i === this._$Ej?.get(t) && !this.hasAttribute(o._$Eu(t, s)))) return;
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
w.elementStyles = [], w.shadowRootOptions = { mode: "open" }, w[S("elementProperties")] = /* @__PURE__ */ new Map(), w[S("finalized")] = /* @__PURE__ */ new Map(), Nt?.({ ReactiveElement: w }), (H.reactiveElementVersions ??= []).push("2.1.2");
/**
 * @license
 * Copyright 2017 Google LLC
 * SPDX-License-Identifier: BSD-3-Clause
 */
const K = globalThis, rt = (e) => e, R = K.trustedTypes, st = R ? R.createPolicy("lit-html", { createHTML: (e) => e }) : void 0, gt = "$lit$", g = `lit$${Math.random().toFixed(9).slice(2)}$`, $t = "?" + g, jt = `<${$t}>`, v = document, T = () => v.createComment(""), C = (e) => e === null || typeof e != "object" && typeof e != "function", J = Array.isArray, Rt = (e) => J(e) || typeof e?.[Symbol.iterator] == "function", z = `[ 	
\f\r]`, E = /<(?:(!--|\/[^a-zA-Z])|(\/?[a-zA-Z][^>\s]*)|(\/?$))/g, nt = /-->/g, it = />/g, $ = RegExp(`>|${z}(?:([^\\s"'>=/]+)(${z}*=${z}*(?:[^ 	
\f\r"'\`<>=]|("|')|))|$)`, "g"), ot = /'/g, at = /"/g, _t = /^(?:script|style|textarea|title)$/i, Ht = (e) => (t, ...r) => ({ _$litType$: e, strings: t, values: r }), p = Ht(1), b = Symbol.for("lit-noChange"), c = Symbol.for("lit-nothing"), lt = /* @__PURE__ */ new WeakMap(), _ = v.createTreeWalker(v, 129);
function vt(e, t) {
  if (!J(e) || !e.hasOwnProperty("raw")) throw Error("invalid template strings array");
  return st !== void 0 ? st.createHTML(t) : t;
}
const Dt = (e, t) => {
  const r = e.length - 1, s = [];
  let n, i = t === 2 ? "<svg>" : t === 3 ? "<math>" : "", o = E;
  for (let l = 0; l < r; l++) {
    const a = e[l];
    let h, d, u = -1, f = 0;
    for (; f < a.length && (o.lastIndex = f, d = o.exec(a), d !== null); ) f = o.lastIndex, o === E ? d[1] === "!--" ? o = nt : d[1] !== void 0 ? o = it : d[2] !== void 0 ? (_t.test(d[2]) && (n = RegExp("</" + d[2], "g")), o = $) : d[3] !== void 0 && (o = $) : o === $ ? d[0] === ">" ? (o = n ?? E, u = -1) : d[1] === void 0 ? u = -2 : (u = o.lastIndex - d[2].length, h = d[1], o = d[3] === void 0 ? $ : d[3] === '"' ? at : ot) : o === at || o === ot ? o = $ : o === nt || o === it ? o = E : (o = $, n = void 0);
    const m = o === $ && e[l + 1].startsWith("/>") ? " " : "";
    i += o === E ? a + jt : u >= 0 ? (s.push(h), a.slice(0, u) + gt + a.slice(u) + g + m) : a + g + (u === -2 ? l : m);
  }
  return [vt(e, i + (e[r] || "<?>") + (t === 2 ? "</svg>" : t === 3 ? "</math>" : "")), s];
};
class P {
  constructor({ strings: t, _$litType$: r }, s) {
    let n;
    this.parts = [];
    let i = 0, o = 0;
    const l = t.length - 1, a = this.parts, [h, d] = Dt(t, r);
    if (this.el = P.createElement(h, s), _.currentNode = this.el.content, r === 2 || r === 3) {
      const u = this.el.content.firstChild;
      u.replaceWith(...u.childNodes);
    }
    for (; (n = _.nextNode()) !== null && a.length < l; ) {
      if (n.nodeType === 1) {
        if (n.hasAttributes()) for (const u of n.getAttributeNames()) if (u.endsWith(gt)) {
          const f = d[o++], m = n.getAttribute(u).split(g), U = /([.?@])?(.*)/.exec(f);
          a.push({ type: 1, index: i, name: U[2], strings: m, ctor: U[1] === "." ? zt : U[1] === "?" ? Lt : U[1] === "@" ? Bt : D }), n.removeAttribute(u);
        } else u.startsWith(g) && (a.push({ type: 6, index: i }), n.removeAttribute(u));
        if (_t.test(n.tagName)) {
          const u = n.textContent.split(g), f = u.length - 1;
          if (f > 0) {
            n.textContent = R ? R.emptyScript : "";
            for (let m = 0; m < f; m++) n.append(u[m], T()), _.nextNode(), a.push({ type: 2, index: ++i });
            n.append(u[f], T());
          }
        }
      } else if (n.nodeType === 8) if (n.data === $t) a.push({ type: 2, index: i });
      else {
        let u = -1;
        for (; (u = n.data.indexOf(g, u + 1)) !== -1; ) a.push({ type: 7, index: i }), u += g.length - 1;
      }
      i++;
    }
  }
  static createElement(t, r) {
    const s = v.createElement("template");
    return s.innerHTML = t, s;
  }
}
function A(e, t, r = e, s) {
  if (t === b) return t;
  let n = s !== void 0 ? r._$Co?.[s] : r._$Cl;
  const i = C(t) ? void 0 : t._$litDirective$;
  return n?.constructor !== i && (n?._$AO?.(!1), i === void 0 ? n = void 0 : (n = new i(e), n._$AT(e, r, s)), s !== void 0 ? (r._$Co ??= [])[s] = n : r._$Cl = n), n !== void 0 && (t = A(e, n._$AS(e, t.values), n, s)), t;
}
class It {
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
        let h;
        a.type === 2 ? h = new O(i, i.nextSibling, this, t) : a.type === 1 ? h = new a.ctor(i, a.name, a.strings, this, t) : a.type === 6 && (h = new Wt(i, this, t)), this._$AV.push(h), a = s[++l];
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
class O {
  get _$AU() {
    return this._$AM?._$AU ?? this._$Cv;
  }
  constructor(t, r, s, n) {
    this.type = 2, this._$AH = c, this._$AN = void 0, this._$AA = t, this._$AB = r, this._$AM = s, this.options = n, this._$Cv = n?.isConnected ?? !0;
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
    t = A(this, t, r), C(t) ? t === c || t == null || t === "" ? (this._$AH !== c && this._$AR(), this._$AH = c) : t !== this._$AH && t !== b && this._(t) : t._$litType$ !== void 0 ? this.$(t) : t.nodeType !== void 0 ? this.T(t) : Rt(t) ? this.k(t) : this._(t);
  }
  O(t) {
    return this._$AA.parentNode.insertBefore(t, this._$AB);
  }
  T(t) {
    this._$AH !== t && (this._$AR(), this._$AH = this.O(t));
  }
  _(t) {
    this._$AH !== c && C(this._$AH) ? this._$AA.nextSibling.data = t : this.T(v.createTextNode(t)), this._$AH = t;
  }
  $(t) {
    const { values: r, _$litType$: s } = t, n = typeof s == "number" ? this._$AC(t) : (s.el === void 0 && (s.el = P.createElement(vt(s.h, s.h[0]), this.options)), s);
    if (this._$AH?._$AD === n) this._$AH.p(r);
    else {
      const i = new It(n, this), o = i.u(this.options);
      i.p(r), this.T(o), this._$AH = i;
    }
  }
  _$AC(t) {
    let r = lt.get(t.strings);
    return r === void 0 && lt.set(t.strings, r = new P(t)), r;
  }
  k(t) {
    J(this._$AH) || (this._$AH = [], this._$AR());
    const r = this._$AH;
    let s, n = 0;
    for (const i of t) n === r.length ? r.push(s = new O(this.O(T()), this.O(T()), this, this.options)) : s = r[n], s._$AI(i), n++;
    n < r.length && (this._$AR(s && s._$AB.nextSibling, n), r.length = n);
  }
  _$AR(t = this._$AA.nextSibling, r) {
    for (this._$AP?.(!1, !0, r); t !== this._$AB; ) {
      const s = rt(t).nextSibling;
      rt(t).remove(), t = s;
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
    this.type = 1, this._$AH = c, this._$AN = void 0, this.element = t, this.name = r, this._$AM = n, this.options = i, s.length > 2 || s[0] !== "" || s[1] !== "" ? (this._$AH = Array(s.length - 1).fill(new String()), this.strings = s) : this._$AH = c;
  }
  _$AI(t, r = this, s, n) {
    const i = this.strings;
    let o = !1;
    if (i === void 0) t = A(this, t, r, 0), o = !C(t) || t !== this._$AH && t !== b, o && (this._$AH = t);
    else {
      const l = t;
      let a, h;
      for (t = i[0], a = 0; a < i.length - 1; a++) h = A(this, l[s + a], r, a), h === b && (h = this._$AH[a]), o ||= !C(h) || h !== this._$AH[a], h === c ? t = c : t !== c && (t += (h ?? "") + i[a + 1]), this._$AH[a] = h;
    }
    o && !n && this.j(t);
  }
  j(t) {
    t === c ? this.element.removeAttribute(this.name) : this.element.setAttribute(this.name, t ?? "");
  }
}
class zt extends D {
  constructor() {
    super(...arguments), this.type = 3;
  }
  j(t) {
    this.element[this.name] = t === c ? void 0 : t;
  }
}
class Lt extends D {
  constructor() {
    super(...arguments), this.type = 4;
  }
  j(t) {
    this.element.toggleAttribute(this.name, !!t && t !== c);
  }
}
class Bt extends D {
  constructor(t, r, s, n, i) {
    super(t, r, s, n, i), this.type = 5;
  }
  _$AI(t, r = this) {
    if ((t = A(this, t, r, 0) ?? c) === b) return;
    const s = this._$AH, n = t === c && s !== c || t.capture !== s.capture || t.once !== s.once || t.passive !== s.passive, i = t !== c && (s === c || n);
    n && this.element.removeEventListener(this.name, this, s), i && this.element.addEventListener(this.name, this, t), this._$AH = t;
  }
  handleEvent(t) {
    typeof this._$AH == "function" ? this._$AH.call(this.options?.host ?? this.element, t) : this._$AH.handleEvent(t);
  }
}
class Wt {
  constructor(t, r, s) {
    this.element = t, this.type = 6, this._$AN = void 0, this._$AM = r, this.options = s;
  }
  get _$AU() {
    return this._$AM._$AU;
  }
  _$AI(t) {
    A(this, t);
  }
}
const qt = K.litHtmlPolyfillSupport;
qt?.(P, O), (K.litHtmlVersions ??= []).push("3.3.3");
const Vt = (e, t, r) => {
  const s = r?.renderBefore ?? t;
  let n = s._$litPart$;
  if (n === void 0) {
    const i = r?.renderBefore ?? null;
    s._$litPart$ = n = new O(t.insertBefore(T(), i), i, void 0, r ?? {});
  }
  return n._$AI(e), n;
};
/**
 * @license
 * Copyright 2017 Google LLC
 * SPDX-License-Identifier: BSD-3-Clause
 */
const F = globalThis;
let x = class extends w {
  constructor() {
    super(...arguments), this.renderOptions = { host: this }, this._$Do = void 0;
  }
  createRenderRoot() {
    const t = super.createRenderRoot();
    return this.renderOptions.renderBefore ??= t.firstChild, t;
  }
  update(t) {
    const r = this.render();
    this.hasUpdated || (this.renderOptions.isConnected = this.isConnected), super.update(t), this._$Do = Vt(r, this.renderRoot, this.renderOptions);
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
x._$litElement$ = !0, x.finalized = !0, F.litElementHydrateSupport?.({ LitElement: x });
const Kt = F.litElementPolyfillSupport;
Kt?.({ LitElement: x });
(F.litElementVersions ??= []).push("4.2.2");
/**
 * @license
 * Copyright 2017 Google LLC
 * SPDX-License-Identifier: BSD-3-Clause
 */
const bt = (e) => (t, r) => {
  r !== void 0 ? r.addInitializer(() => {
    customElements.define(e, t);
  }) : customElements.define(e, t);
};
/**
 * @license
 * Copyright 2017 Google LLC
 * SPDX-License-Identifier: BSD-3-Clause
 */
const Jt = { attribute: !0, type: String, converter: j, reflect: !1, hasChanged: V }, Ft = (e = Jt, t, r) => {
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
function G(e) {
  return (t, r) => typeof r == "object" ? Ft(e, t, r) : ((s, n, i) => {
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
  return G({ ...e, state: !0, attribute: !1 });
}
/**
 * @license
 * Copyright 2017 Google LLC
 * SPDX-License-Identifier: BSD-3-Clause
 */
const Gt = { ATTRIBUTE: 1 }, Zt = (e) => (...t) => ({ _$litDirective$: e, values: t });
class Yt {
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
const ct = Zt(class extends Yt {
  constructor(e) {
    if (super(e), e.type !== Gt.ATTRIBUTE || e.name !== "class" || e.strings?.length > 2) throw Error("`classMap()` can only be used in the `class` attribute and must be the only part in the attribute.");
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
}), Xt = /* @__PURE__ */ new Set(["unknown", "unavailable", "none", ""]), Qt = 10;
function te(e) {
  return e === void 0 || Xt.has(e);
}
function ht(e) {
  const t = /_(\d+)$/.exec(e);
  return t ? Number(t[1]) : 0;
}
function ee(e, t) {
  const r = re(e, t), s = se(e, t);
  return r && r.departures.length >= s.departures.length ? r : {
    departures: s.departures,
    favorite: s.favorite ?? r?.favorite ?? null,
    progress: s.progress ?? r?.progress ?? null
  };
}
function re(e, t) {
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
function se(e, t) {
  const r = t.replace(/_next_departure$/, ""), s = (i) => e.states[i] ? i : null, n = [t];
  for (let i = 2; i <= Qt; i++) {
    const o = s(`${r}_departure_${i}`);
    o && n.push(o);
  }
  return {
    departures: n,
    favorite: s(`${r}_next_favorite`),
    progress: s(`${r}_stops_away`)
  };
}
function y(e) {
  return typeof e == "string" && e !== "" ? e : null;
}
function yt(e, t) {
  const r = e.states[t];
  if (!r || te(r.state))
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
    trainId: y(n.train_id),
    favorite: n.favorite === !0,
    track: y(n.track),
    signaledTrack: y(n.signaled_track),
    status: y(n.status),
    statusText: y(n.status_text),
    delayMinutes: typeof i == "number" ? i : null,
    crowding: y(n.crowding),
    cars: o,
    alerts: l
  };
}
function ne(e, t, r) {
  if (!e.states[t])
    return "missing";
  const s = r.departures.map((n) => e.states[n]?.state).filter((n) => n !== void 0);
  return s.length > 0 && s.every((n) => n === "unavailable") ? "unavailable" : "ok";
}
function ie(e, t) {
  return t.departures.map((r) => yt(e, r)).filter((r) => r !== null);
}
function oe(e) {
  return e.some((t) => t.track !== null);
}
function ae(e, t) {
  if (e)
    return { departure: e, tracking: !0, allCancelled: !1 };
  const r = t.find((s) => s.status !== "cancelled");
  return {
    departure: r ?? null,
    tracking: !1,
    allCancelled: !r && t.length > 0
  };
}
function L(e) {
  return e.delayMinutes !== null && e.delayMinutes > 0 && e.status !== "departed" ? new Date(
    e.scheduled.getTime() + e.delayMinutes * 6e4
  ) : e.scheduled;
}
function B(e, t) {
  return Math.round((e.getTime() - t.getTime()) / 6e4);
}
const wt = new Intl.DateTimeFormat("en-US", {
  hour: "numeric",
  minute: "2-digit",
  hour12: !0
});
function dt(e) {
  return wt.format(e);
}
function le(e) {
  return wt.format(e).replace(/\s?[AP]M$/i, "");
}
function ce(e) {
  return e < 0 ? { value: "Departed", unit: !1 } : e === 0 ? { value: "Now", unit: !1 } : { value: String(e), unit: !0 };
}
const xt = 6, ut = 10, he = 25;
function de(e, t, r) {
  return e.track ? { text: `Track ${e.track}`, tone: "accent" } : e.status === "cancelled" ? null : e.signaledTrack ? { text: `Track ${e.signaledTrack} (likely)`, tone: "accent" } : r ? t <= xt ? { text: "⚠️ Track overdue", tone: "bad" } : t <= ut ? { text: "Track due any minute", tone: "muted" } : t <= he ? { text: `Track due in ~${t - (ut - 1)} min`, tone: "muted" } : { text: "Track not posted yet", tone: "muted" } : { text: "Track not posted", tone: "muted" };
}
function ue(e, t, r) {
  return e.track ? {
    text: e.track,
    tone: "accent",
    label: `Track ${e.track}`
  } : e.status === "cancelled" ? { text: "—", tone: "muted", label: "Track not posted" } : e.signaledTrack ? {
    text: `${e.signaledTrack}?`,
    tone: "accent",
    label: `Track ${e.signaledTrack} (likely)`
  } : r && t <= xt ? { text: "⚠️", tone: "bad", label: "Track overdue" } : { text: "—", tone: "muted", label: "Track not posted" };
}
function pt(e) {
  return e.statusText ? e.status === "cancelled" ? { text: e.statusText, tone: "bad" } : e.delayMinutes ? { text: e.statusText, tone: "warn" } : { text: e.statusText, tone: "muted" } : null;
}
function pe(e) {
  return e.crowding === "heavy" ? { text: "Busy", tone: "warn" } : e.crowding === "moderate" ? { text: "Filling up", tone: "muted" } : null;
}
const ft = { muted: 0, accent: 1, warn: 2, bad: 3 };
function fe(e) {
  let t = "accent";
  for (const r of e)
    r && ft[r.tone] > ft[t] && (t = r.tone);
  return t;
}
const me = { light: 0, moderate: 1, heavy: 2 };
function ge(e) {
  let t = null, r = 1 / 0, s = -1 / 0;
  for (const n of e) {
    const i = n.crowding ? me[n.crowding] : void 0;
    i === void 0 || !n.position || (i < r && (r = i, t = n.position), s = Math.max(s, i));
  }
  return t !== null && s > r ? t : null;
}
var $e = Object.defineProperty, _e = Object.getOwnPropertyDescriptor, I = (e, t, r, s) => {
  for (var n = s > 1 ? void 0 : s ? _e(t, r) : t, i = e.length - 1, o; i >= 0; i--)
    (o = e[i]) && (n = (s ? o(t, r, n) : o(n)) || n);
  return s && n && $e(t, r, n), n;
};
const ve = 1e4;
let k = class extends x {
  constructor() {
    super(...arguments), this._now = /* @__PURE__ */ new Date();
  }
  setConfig(e) {
    if (!e)
      throw new Error("Invalid configuration");
    this._config = e;
  }
  getCardSize() {
    return 8;
  }
  static async getConfigElement() {
    return await Promise.resolve().then(() => ke), document.createElement("njtransit-departures-editor");
  }
  /** Offer a working card the moment it is added from the picker. */
  static getStubConfig(e) {
    return { type: "custom:njtransit-departures", entity: Object.keys(e.states).find(
      (r) => r.endsWith("_next_departure")
    ) ?? "" };
  }
  connectedCallback() {
    super.connectedCallback(), this._timer && clearInterval(this._timer), this._now = /* @__PURE__ */ new Date(), this._timer = setInterval(() => {
      this._now = /* @__PURE__ */ new Date();
    }, ve);
  }
  disconnectedCallback() {
    super.disconnectedCallback(), this._timer && (clearInterval(this._timer), this._timer = void 0);
  }
  render() {
    if (!this.hass || !this._config)
      return c;
    const e = this._config.title ? p`<h1 class="card-header">${this._config.title}</h1>` : c;
    if (!this._config.entity)
      return p`
        <ha-card class="muted">
          ${e}
          ${this._renderMessage(
        "Select a commute",
        "Choose a departure sensor in the card configuration."
      )}
        </ha-card>
      `;
    const t = ee(this.hass, this._config.entity), r = ne(this.hass, this._config.entity, t);
    if (r === "missing")
      return p`
        <ha-card class="warn">
          ${e}
          ${this._renderMessage(
        "Entity not found",
        "Check the card configuration and select a commute departure sensor."
      )}
        </ha-card>
      `;
    if (r === "unavailable")
      return p`
        <ha-card class="warn">
          ${e}
          ${this._renderMessage(
        "Departure board unavailable",
        "Unable to reach NJ Transit. Check the integration or your connection."
      )}
        </ha-card>
      `;
    const s = ie(this.hass, t), n = t.favorite ? yt(this.hass, t.favorite) : null, { departure: i, tracking: o, allCancelled: l } = ae(n, s), a = oe(s), h = i ? this._pillsFor(i, a) : [], d = i ? fe(h) : l ? "bad" : "muted";
    return p`
      <ha-card class=${ct({ [d]: !0 })}>
        ${e}
        ${i ? this._renderHero(i, h, {
      tracking: o,
      posting: a,
      progress: t.progress,
      favoriteEntity: t.favorite
    }) : this._renderEmpty(l)}
        ${s.length ? this._renderBoard(s, a) : c}
      </ha-card>
    `;
  }
  /** Everything the hero has to say about this train, in reading order. */
  _pillsFor(e, t) {
    const r = B(L(e), this._now);
    return [
      de(e, r, t),
      pt(e),
      pe(e)
    ].filter((s) => s !== null);
  }
  _renderMessage(e, t) {
    return p`
      <div class="hero empty">
        <h2>${e}</h2>
        <p>${t}</p>
      </div>
    `;
  }
  _renderEmpty(e) {
    return this._renderMessage(
      "Nothing on the board",
      e ? "Every upcoming departure is cancelled." : "No departures in the next couple of hours."
    );
  }
  _renderHero(e, t, r) {
    const { tracking: s, progress: n, favoriteEntity: i } = r, o = B(L(e), this._now), { value: l, unit: a } = e.status === "cancelled" ? { value: "Cancelled", unit: !1 } : ce(o), h = ge(e.cars);
    return p`
      <div class="hero">
        <div
          class="hero-main"
          role="button"
          tabindex="0"
          aria-label=${this._departureLabel(e)}
          @click=${() => this._moreInfo(e.entityId)}
          @keydown=${(d) => this._activate(d, e.entityId)}
        >
          <div class="countdown">
            ${l}${a ? p`<span class="unit">min</span>` : c}
          </div>
          <h2>
            <span class="clock">${dt(e.scheduled)}</span>
            ${e.trainId ? p`<span class="train">Train ${e.trainId}</span>` : c}
          </h2>
          <div class="pills">${t.map((d) => this._renderPill(d))}</div>
          ${h ? p`<p class="hint">${be(h)} cars are emptier</p>` : c}
          ${s ? this._renderProgress(n) : c}
        </div>
        ${e.alerts.map(
      (d) => p`<div class="alert" role="note">${d}</div>`
    )}
        ${s ? c : this._renderWaiting(i)}
      </div>
    `;
  }
  _renderProgress(e) {
    const t = e ? this.hass.states[e] : void 0;
    if (!t || !/^\d+$/.test(t.state))
      return c;
    const r = Number(t.state), s = t.attributes.next_stop;
    return p`
      <hr />
      <p class="progress">
        ${r === 0 ? "Arriving now" : p`<strong>${r}</strong> ${r === 1 ? "stop" : "stops"} away`}
        ${typeof s == "string" && s ? p` · next ${s}` : c}
      </p>
    `;
  }
  /**
   * Why the card is showing someone else's train.
   *
   * Without this the fallback is indistinguishable from the favourite, and
   * the countdown at the top is for a service the reader was never going to
   * board. Riders who have not configured any favourites are not nagged.
   */
  _renderWaiting(e) {
    const t = e ? this.hass.states[e]?.attributes.favorites : void 0;
    return !Array.isArray(t) || t.length === 0 ? c : p`
      <p class="hint">
        Waiting for ${t.join(", ")} — not on the board yet.
      </p>
    `;
  }
  _renderBoard(e, t) {
    return p`
      <div class="board">
        <table>
          <thead>
            <tr>
              <th scope="col">Departs</th>
              <th scope="col">Train</th>
              <th scope="col"><abbr title="Track">Trk</abbr></th>
              <th scope="col"><span class="sr-only">Status</span></th>
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
    const r = B(L(e), this._now), s = pt(e), n = r > 0 ? `${r}m` : r === 0 ? "now" : "dep";
    return p`
      <tr
        tabindex="0"
        aria-label=${this._departureLabel(e)}
        @click=${() => this._moreInfo(e.entityId)}
        @keydown=${(i) => this._activate(i, e.entityId)}
      >
        <td>
          <strong>${le(e.scheduled)}</strong>
          <span class="relative">${n}</span>
        </td>
        <td>
          ${e.trainId}${e.favorite ? p`<span aria-hidden="true"> ⭐</span
                ><span class="sr-only"> (favourite)</span>` : c}
        </td>
        <td>${this._renderPill(ue(e, r, t))}</td>
        <td>${s ? this._renderPill(s) : c}</td>
      </tr>
    `;
  }
  _renderPill(e) {
    return p`<span
      class=${ct({ pill: !0, [e.tone]: !0 })}
      aria-label=${e.label ?? c}
      title=${e.label ?? c}
      >${e.text}</span
    >`;
  }
  _departureLabel(e) {
    const t = [];
    return e.trainId && t.push(`Train ${e.trainId}`), t.push(dt(e.scheduled)), e.favorite && t.push("favourite"), e.track ? t.push(`Track ${e.track}`) : e.signaledTrack && e.status !== "cancelled" && t.push(`Track ${e.signaledTrack} (likely)`), e.statusText ? t.push(e.statusText) : e.status === "cancelled" && t.push("Cancelled"), t.join(", ");
  }
  _activate(e, t) {
    (e.key === "Enter" || e.key === " ") && (e.preventDefault(), this._moreInfo(t));
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
k.styles = kt`
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
      /* Secondary text mixed toward primary so text over the 13% corner wash
         clears WCAG AA (4.5:1) in every mood across both light and dark HA
         themes (lowest is 4.56:1 in dark warn, 8.26:1+ in light). */
      --njt-secondary: color-mix(
        in oklab,
        var(--secondary-text-color) 50%,
        var(--primary-text-color)
      );
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
    .alert {
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
      /* Focus outline mixed with primary text so WCAG 1.4.11 (3:1) clears in
         all moods including light-theme warn (4.66:1 over wash). */
      --njt-focus: color-mix(
        in oklab,
        var(--mood) 55%,
        var(--primary-text-color)
      );
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
    }

    .hero-main {
      cursor: pointer;
      -webkit-tap-highlight-color: transparent;
      border-radius: 10px;
    }

    .hero-main:focus-visible {
      outline: 2px solid var(--njt-focus);
      outline-offset: 4px;
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
      color: var(--njt-secondary);
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

    .hero.empty h2 {
      display: block;
      margin: 0 0 6px;
      font-size: 1.15rem;
      font-weight: 650;
      letter-spacing: -0.01em;
    }

    .clock {
      color: var(--primary-text-color);
      font-weight: 650;
    }

    .train {
      color: var(--njt-secondary);
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

    p {
      margin: 0;
      line-height: 1.55;
    }

    .hint {
      margin-top: 12px;
      color: var(--njt-secondary);
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

    .alert {
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
      overflow-x: auto;
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

    abbr {
      text-decoration: none;
    }

    .sr-only {
      position: absolute;
      width: 1px;
      height: 1px;
      padding: 0;
      margin: -1px;
      overflow: hidden;
      clip: rect(0, 0, 0, 0);
      white-space: nowrap;
      border: 0;
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

    tbody tr:focus-visible {
      outline: 2px solid var(--njt-focus);
      outline-offset: -2px;
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

      th {
        padding-right: 4px;
      }

      td {
        font-size: 0.9rem;
        padding: 9px 4px 9px 0;
      }

      .board .pill {
        font-size: 0.72rem;
        padding: 2px 7px;
      }

      .relative {
        display: none;
      }
    }
  `;
I([
  G({ attribute: !1 })
], k.prototype, "hass", 2);
I([
  Z()
], k.prototype, "_config", 2);
I([
  Z()
], k.prototype, "_now", 2);
k = I([
  bt("njtransit-departures")
], k);
function be(e) {
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
var ye = Object.defineProperty, we = Object.getOwnPropertyDescriptor, Y = (e, t, r, s) => {
  for (var n = s > 1 ? void 0 : s ? we(t, r) : t, i = e.length - 1, o; i >= 0; i--)
    (o = e[i]) && (n = (s ? o(t, r, n) : o(n)) || n);
  return s && n && ye(t, r, n), n;
};
const xe = [
  {
    name: "entity",
    required: !0,
    selector: {
      entity: {
        integration: "njtransit",
        domain: "sensor",
        device_class: "timestamp"
      }
    }
  },
  { name: "title", selector: { text: {} } }
], Ae = {
  entity: "Commute (any departure sensor)",
  title: "Heading (optional)"
};
let M = class extends x {
  setConfig(e) {
    this._config = e;
  }
  render() {
    return !this.hass || !this._config ? c : p`
      <ha-form
        .hass=${this.hass}
        .data=${this._config}
        .schema=${xe}
        .computeLabel=${(e) => Ae[e.name] ?? e.name}
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
Y([
  G({ attribute: !1 })
], M.prototype, "hass", 2);
Y([
  Z()
], M.prototype, "_config", 2);
M = Y([
  bt("njtransit-departures-editor")
], M);
const ke = /* @__PURE__ */ Object.freeze(/* @__PURE__ */ Object.defineProperty({
  __proto__: null,
  get NJTransitDeparturesEditor() {
    return M;
  }
}, Symbol.toStringTag, { value: "Module" }));
