import { r as e } from "./rolldown-runtime.js";
import { Fragment as t, computed as n, createBlock as r, createCommentVNode as i, createElementBlock as a, createElementVNode as o, createPropsRestProxy as s, createTextVNode as c, createVNode as l, customRef as u, defineComponent as d, effectScope as f, getCurrentInstance as p, getCurrentScope as m, h, inject as g, isMemoSame as _, isRef as v, markRaw as y, mergeProps as b, nextTick as x, normalizeClass as S, normalizeProps as C, normalizeStyle as w, onBeforeMount as T, onBeforeUnmount as E, onMounted as D, onScopeDispose as O, onUnmounted as k, openBlock as A, provide as j, reactive as M, readonly as N, ref as P, renderList as F, renderSlot as I, resolveComponent as L, resolveDynamicComponent as R, shallowRef as z, toDisplayString as ee, toRef as B, toRefs as V, toValue as H, unref as U, useAttrs as W, useSlots as G, watch as K, watchEffect as te, withCtx as q } from "vue";
// node_modules/@vue-flow/core/dist/vue-flow-core.mjs
function ne(e) {
	return m() ? (O(e), !0) : !1;
}
function re(e) {
	return typeof e == "function" ? e() : U(e);
}
var ie = typeof window < "u" && typeof document < "u", ae = (e) => e !== void 0, oe = Object.prototype.toString, se = (e) => oe.call(e) === "[object Object]", ce = () => {};
function le(e, t) {
	function n(...n) {
		return new Promise((r, i) => {
			Promise.resolve(e(() => t.apply(this, n), {
				fn: t,
				thisArg: this,
				args: n
			})).then(r).catch(i);
		});
	}
	return n;
}
var ue = (e) => e();
function de(e = ue) {
	let t = P(!0);
	function n() {
		t.value = !1;
	}
	function r() {
		t.value = !0;
	}
	return {
		isActive: N(t),
		pause: n,
		resume: r,
		eventFilter: (...n) => {
			t.value && e(...n);
		}
	};
}
function fe(e, t = !1, n = "Timeout") {
	return new Promise((r, i) => {
		setTimeout(t ? () => i(n) : r, e);
	});
}
function pe(e, t, n = {}) {
	let { eventFilter: r = ue, ...i } = n;
	return K(e, le(r, t), i);
}
function me(e, t, n = {}) {
	let { eventFilter: r, ...i } = n, { eventFilter: a, pause: o, resume: s, isActive: c } = de(r);
	return {
		stop: pe(e, t, {
			...i,
			eventFilter: a
		}),
		pause: o,
		resume: s,
		isActive: c
	};
}
function he(e, t = {}) {
	if (!v(e)) return V(e);
	let n = Array.isArray(e.value) ? Array.from({ length: e.value.length }) : {};
	for (let r in e.value) n[r] = u(() => ({
		get() {
			return e.value[r];
		},
		set(n) {
			if (re(t.replaceRef) ?? !0) {
				if (Array.isArray(e.value)) {
					let t = [...e.value];
					t[r] = n, e.value = t;
				} else {
					let t = {
						...e.value,
						[r]: n
					};
					Object.setPrototypeOf(t, Object.getPrototypeOf(e.value)), e.value = t;
				}
			} else e.value[r] = n;
		}
	}));
	return n;
}
function ge(e, t = !1) {
	function n(n, { flush: r = "sync", deep: i = !1, timeout: a, throwOnTimeout: o } = {}) {
		let s = null, c = [new Promise((a) => {
			s = K(e, (e) => {
				n(e) !== t && (s?.(), a(e));
			}, {
				flush: r,
				deep: i,
				immediate: !0
			});
		})];
		return a != null && c.push(fe(a, o).then(() => re(e)).finally(() => s?.())), Promise.race(c);
	}
	function r(r, i) {
		if (!v(r)) return n((e) => e === r, i);
		let { flush: a = "sync", deep: o = !1, timeout: s, throwOnTimeout: c } = i ?? {}, l = null, u = [new Promise((n) => {
			l = K([e, r], ([e, r]) => {
				t !== (e === r) && (l?.(), n(e));
			}, {
				flush: a,
				deep: o,
				immediate: !0
			});
		})];
		return s != null && u.push(fe(s, c).then(() => re(e)).finally(() => (l?.(), re(e)))), Promise.race(u);
	}
	function i(e) {
		return n((e) => !!e, e);
	}
	function a(e) {
		return r(null, e);
	}
	function o(e) {
		return r(void 0, e);
	}
	function s(e) {
		return n(Number.isNaN, e);
	}
	function c(e, t) {
		return n((t) => {
			let n = Array.from(t);
			return n.includes(e) || n.includes(re(e));
		}, t);
	}
	function l(e) {
		return u(1, e);
	}
	function u(e = 1, t) {
		let r = -1;
		return n(() => (r += 1, r >= e), t);
	}
	return Array.isArray(re(e)) ? {
		toMatch: n,
		toContains: c,
		changed: l,
		changedTimes: u,
		get not() {
			return ge(e, !t);
		}
	} : {
		toMatch: n,
		toBe: r,
		toBeTruthy: i,
		toBeNull: a,
		toBeNaN: s,
		toBeUndefined: o,
		changed: l,
		changedTimes: u,
		get not() {
			return ge(e, !t);
		}
	};
}
function _e(e) {
	return ge(e);
}
function ve(e) {
	let t = re(e);
	return t?.$el ?? t;
}
var ye = ie ? window : void 0;
function be(...e) {
	let t, n, r, i;
	if (typeof e[0] == "string" || Array.isArray(e[0]) ? ([n, r, i] = e, t = ye) : [t, n, r, i] = e, !t) return ce;
	Array.isArray(n) || (n = [n]), Array.isArray(r) || (r = [r]);
	let a = [], o = () => {
		a.forEach((e) => e()), a.length = 0;
	}, s = (e, t, n, r) => (e.addEventListener(t, n, r), () => e.removeEventListener(t, n, r)), c = K(() => [ve(t), re(i)], ([e, t]) => {
		if (o(), !e) return;
		let i = se(t) ? { ...t } : t;
		a.push(...n.flatMap((t) => r.map((n) => s(e, t, n, i))));
	}, {
		immediate: !0,
		flush: "post"
	}), l = () => {
		c(), o();
	};
	return ne(l), l;
}
function xe(e) {
	return typeof e == "function" ? e : typeof e == "string" ? (t) => t.key === e : Array.isArray(e) ? (t) => e.includes(t.key) : () => !0;
}
function Se(...e) {
	let t, n, r = {};
	e.length === 3 ? (t = e[0], n = e[1], r = e[2]) : e.length === 2 ? typeof e[1] == "object" ? (t = !0, n = e[0], r = e[1]) : (t = e[0], n = e[1]) : (t = !0, n = e[0]);
	let { target: i = ye, eventName: a = "keydown", passive: o = !1, dedupe: s = !1 } = r, c = xe(t);
	return be(i, a, (e) => {
		e.repeat && re(s) || c(e) && n(e);
	}, o);
}
function Ce(e) {
	return JSON.parse(JSON.stringify(e));
}
function we(e, t, r, i = {}) {
	let { clone: a = !1, passive: o = !1, eventName: s, deep: c = !1, defaultValue: l, shouldEmit: u } = i, d = p(), f = r || d?.emit || (d?.$emit)?.bind(d) || (d?.proxy?.$emit)?.bind(d?.proxy), m = s;
	t || (t = "modelValue"), m = m || `update:${t.toString()}`;
	let h = (e) => a ? typeof a == "function" ? a(e) : Ce(e) : e, g = () => ae(e[t]) ? h(e[t]) : l, _ = (e) => {
		u ? u(e) && f(m, e) : f(m, e);
	};
	if (o) {
		let n = g(), r = P(n), i = !1;
		return K(() => e[t], (e) => {
			i || (i = !0, r.value = h(e), x(() => i = !1));
		}), K(r, (n) => {
			!i && (n !== e[t] || c) && _(n);
		}, { deep: c }), r;
	}
	return n({
		get() {
			return g();
		},
		set(e) {
			_(e);
		}
	});
}
var Te = { value: () => {} };
function Ee() {
	for (var e = 0, t = arguments.length, n = {}, r; e < t; ++e) {
		if (!(r = arguments[e] + "") || r in n || /[\s.]/.test(r)) throw Error("illegal type: " + r);
		n[r] = [];
	}
	return new De(n);
}
function De(e) {
	this._ = e;
}
function Oe(e, t) {
	return e.trim().split(/^|\s+/).map(function(e) {
		var n = "", r = e.indexOf(".");
		if (r >= 0 && (n = e.slice(r + 1), e = e.slice(0, r)), e && !t.hasOwnProperty(e)) throw Error("unknown type: " + e);
		return {
			type: e,
			name: n
		};
	});
}
De.prototype = Ee.prototype = {
	constructor: De,
	on: function(e, t) {
		var n = this._, r = Oe(e + "", n), i, a = -1, o = r.length;
		if (arguments.length < 2) {
			for (; ++a < o;) if ((i = (e = r[a]).type) && (i = ke(n[i], e.name))) return i;
		} else {
			if (t != null && typeof t != "function") throw Error("invalid callback: " + t);
			for (; ++a < o;) if (i = (e = r[a]).type) n[i] = Ae(n[i], e.name, t);
			else if (t == null) for (i in n) n[i] = Ae(n[i], e.name, null);
			return this;
		}
	},
	copy: function() {
		var e = {}, t = this._;
		for (var n in t) e[n] = t[n].slice();
		return new De(e);
	},
	call: function(e, t) {
		if ((i = arguments.length - 2) > 0) for (var n = Array(i), r = 0, i, a; r < i; ++r) n[r] = arguments[r + 2];
		if (!this._.hasOwnProperty(e)) throw Error("unknown type: " + e);
		for (a = this._[e], r = 0, i = a.length; r < i; ++r) a[r].value.apply(t, n);
	},
	apply: function(e, t, n) {
		if (!this._.hasOwnProperty(e)) throw Error("unknown type: " + e);
		for (var r = this._[e], i = 0, a = r.length; i < a; ++i) r[i].value.apply(t, n);
	}
};
function ke(e, t) {
	for (var n = 0, r = e.length, i; n < r; ++n) if ((i = e[n]).name === t) return i.value;
}
function Ae(e, t, n) {
	for (var r = 0, i = e.length; r < i; ++r) if (e[r].name === t) {
		e[r] = Te, e = e.slice(0, r).concat(e.slice(r + 1));
		break;
	}
	return n != null && e.push({
		name: t,
		value: n
	}), e;
}
var je = "http://www.w3.org/1999/xhtml", Me = {
	svg: "http://www.w3.org/2000/svg",
	xhtml: je,
	xlink: "http://www.w3.org/1999/xlink",
	xml: "http://www.w3.org/XML/1998/namespace",
	xmlns: "http://www.w3.org/2000/xmlns/"
};
function Ne(e) {
	var t = e += "", n = t.indexOf(":");
	return n >= 0 && (t = e.slice(0, n)) !== "xmlns" && (e = e.slice(n + 1)), Me.hasOwnProperty(t) ? {
		space: Me[t],
		local: e
	} : e;
}
function Pe(e) {
	return function() {
		var t = this.ownerDocument, n = this.namespaceURI;
		return n === je && t.documentElement.namespaceURI === je ? t.createElement(e) : t.createElementNS(n, e);
	};
}
function Fe(e) {
	return function() {
		return this.ownerDocument.createElementNS(e.space, e.local);
	};
}
function Ie(e) {
	var t = Ne(e);
	return (t.local ? Fe : Pe)(t);
}
function Le() {}
function Re(e) {
	return e == null ? Le : function() {
		return this.querySelector(e);
	};
}
function ze(e) {
	typeof e != "function" && (e = Re(e));
	for (var t = this._groups, n = t.length, r = Array(n), i = 0; i < n; ++i) for (var a = t[i], o = a.length, s = r[i] = Array(o), c, l, u = 0; u < o; ++u) (c = a[u]) && (l = e.call(c, c.__data__, u, a)) && ("__data__" in c && (l.__data__ = c.__data__), s[u] = l);
	return new On(r, this._parents);
}
function Be(e) {
	return e == null ? [] : Array.isArray(e) ? e : Array.from(e);
}
function Ve() {
	return [];
}
function He(e) {
	return e == null ? Ve : function() {
		return this.querySelectorAll(e);
	};
}
function Ue(e) {
	return function() {
		return Be(e.apply(this, arguments));
	};
}
function We(e) {
	e = typeof e == "function" ? Ue(e) : He(e);
	for (var t = this._groups, n = t.length, r = [], i = [], a = 0; a < n; ++a) for (var o = t[a], s = o.length, c, l = 0; l < s; ++l) (c = o[l]) && (r.push(e.call(c, c.__data__, l, o)), i.push(c));
	return new On(r, i);
}
function Ge(e) {
	return function() {
		return this.matches(e);
	};
}
function Ke(e) {
	return function(t) {
		return t.matches(e);
	};
}
var qe = Array.prototype.find;
function Je(e) {
	return function() {
		return qe.call(this.children, e);
	};
}
function Ye() {
	return this.firstElementChild;
}
function Xe(e) {
	return this.select(e == null ? Ye : Je(typeof e == "function" ? e : Ke(e)));
}
var Ze = Array.prototype.filter;
function Qe() {
	return Array.from(this.children);
}
function $e(e) {
	return function() {
		return Ze.call(this.children, e);
	};
}
function et(e) {
	return this.selectAll(e == null ? Qe : $e(typeof e == "function" ? e : Ke(e)));
}
function tt(e) {
	typeof e != "function" && (e = Ge(e));
	for (var t = this._groups, n = t.length, r = Array(n), i = 0; i < n; ++i) for (var a = t[i], o = a.length, s = r[i] = [], c, l = 0; l < o; ++l) (c = a[l]) && e.call(c, c.__data__, l, a) && s.push(c);
	return new On(r, this._parents);
}
function nt(e) {
	return Array(e.length);
}
function rt() {
	return new On(this._enter || this._groups.map(nt), this._parents);
}
function it(e, t) {
	this.ownerDocument = e.ownerDocument, this.namespaceURI = e.namespaceURI, this._next = null, this._parent = e, this.__data__ = t;
}
it.prototype = {
	constructor: it,
	appendChild: function(e) {
		return this._parent.insertBefore(e, this._next);
	},
	insertBefore: function(e, t) {
		return this._parent.insertBefore(e, t);
	},
	querySelector: function(e) {
		return this._parent.querySelector(e);
	},
	querySelectorAll: function(e) {
		return this._parent.querySelectorAll(e);
	}
};
function at(e) {
	return function() {
		return e;
	};
}
function ot(e, t, n, r, i, a) {
	for (var o = 0, s, c = t.length, l = a.length; o < l; ++o) (s = t[o]) ? (s.__data__ = a[o], r[o] = s) : n[o] = new it(e, a[o]);
	for (; o < c; ++o) (s = t[o]) && (i[o] = s);
}
function st(e, t, n, r, i, a, o) {
	var s, c, l = /* @__PURE__ */ new Map(), u = t.length, d = a.length, f = Array(u), p;
	for (s = 0; s < u; ++s) (c = t[s]) && (f[s] = p = o.call(c, c.__data__, s, t) + "", l.has(p) ? i[s] = c : l.set(p, c));
	for (s = 0; s < d; ++s) p = o.call(e, a[s], s, a) + "", (c = l.get(p)) ? (r[s] = c, c.__data__ = a[s], l.delete(p)) : n[s] = new it(e, a[s]);
	for (s = 0; s < u; ++s) (c = t[s]) && l.get(f[s]) === c && (i[s] = c);
}
function ct(e) {
	return e.__data__;
}
function lt(e, t) {
	if (!arguments.length) return Array.from(this, ct);
	var n = t ? st : ot, r = this._parents, i = this._groups;
	typeof e != "function" && (e = at(e));
	for (var a = i.length, o = Array(a), s = Array(a), c = Array(a), l = 0; l < a; ++l) {
		var u = r[l], d = i[l], f = d.length, p = ut(e.call(u, u && u.__data__, l, r)), m = p.length, h = s[l] = Array(m), g = o[l] = Array(m);
		n(u, d, h, g, c[l] = Array(f), p, t);
		for (var _ = 0, v = 0, y, b; _ < m; ++_) if (y = h[_]) {
			for (_ >= v && (v = _ + 1); !(b = g[v]) && ++v < m;);
			y._next = b || null;
		}
	}
	return o = new On(o, r), o._enter = s, o._exit = c, o;
}
function ut(e) {
	return typeof e == "object" && "length" in e ? e : Array.from(e);
}
function dt() {
	return new On(this._exit || this._groups.map(nt), this._parents);
}
function ft(e, t, n) {
	var r = this.enter(), i = this, a = this.exit();
	return typeof e == "function" ? (r = e(r), r && (r = r.selection())) : r = r.append(e + ""), t != null && (i = t(i), i && (i = i.selection())), n == null ? a.remove() : n(a), r && i ? r.merge(i).order() : i;
}
function pt(e) {
	for (var t = e.selection ? e.selection() : e, n = this._groups, r = t._groups, i = n.length, a = r.length, o = Math.min(i, a), s = Array(i), c = 0; c < o; ++c) for (var l = n[c], u = r[c], d = l.length, f = s[c] = Array(d), p, m = 0; m < d; ++m) (p = l[m] || u[m]) && (f[m] = p);
	for (; c < i; ++c) s[c] = n[c];
	return new On(s, this._parents);
}
function mt() {
	for (var e = this._groups, t = -1, n = e.length; ++t < n;) for (var r = e[t], i = r.length - 1, a = r[i], o; --i >= 0;) (o = r[i]) && (a && o.compareDocumentPosition(a) ^ 4 && a.parentNode.insertBefore(o, a), a = o);
	return this;
}
function ht(e) {
	e || (e = gt);
	function t(t, n) {
		return t && n ? e(t.__data__, n.__data__) : !t - !n;
	}
	for (var n = this._groups, r = n.length, i = Array(r), a = 0; a < r; ++a) {
		for (var o = n[a], s = o.length, c = i[a] = Array(s), l, u = 0; u < s; ++u) (l = o[u]) && (c[u] = l);
		c.sort(t);
	}
	return new On(i, this._parents).order();
}
function gt(e, t) {
	return e < t ? -1 : e > t ? 1 : e >= t ? 0 : NaN;
}
function _t() {
	var e = arguments[0];
	return arguments[0] = this, e.apply(null, arguments), this;
}
function vt() {
	return Array.from(this);
}
function yt() {
	for (var e = this._groups, t = 0, n = e.length; t < n; ++t) for (var r = e[t], i = 0, a = r.length; i < a; ++i) {
		var o = r[i];
		if (o) return o;
	}
	return null;
}
function bt() {
	let e = 0;
	for (let t of this) ++e;
	return e;
}
function xt() {
	return !this.node();
}
function St(e) {
	for (var t = this._groups, n = 0, r = t.length; n < r; ++n) for (var i = t[n], a = 0, o = i.length, s; a < o; ++a) (s = i[a]) && e.call(s, s.__data__, a, i);
	return this;
}
function Ct(e) {
	return function() {
		this.removeAttribute(e);
	};
}
function wt(e) {
	return function() {
		this.removeAttributeNS(e.space, e.local);
	};
}
function Tt(e, t) {
	return function() {
		this.setAttribute(e, t);
	};
}
function Et(e, t) {
	return function() {
		this.setAttributeNS(e.space, e.local, t);
	};
}
function Dt(e, t) {
	return function() {
		var n = t.apply(this, arguments);
		n == null ? this.removeAttribute(e) : this.setAttribute(e, n);
	};
}
function Ot(e, t) {
	return function() {
		var n = t.apply(this, arguments);
		n == null ? this.removeAttributeNS(e.space, e.local) : this.setAttributeNS(e.space, e.local, n);
	};
}
function kt(e, t) {
	var n = Ne(e);
	if (arguments.length < 2) {
		var r = this.node();
		return n.local ? r.getAttributeNS(n.space, n.local) : r.getAttribute(n);
	}
	return this.each((t == null ? n.local ? wt : Ct : typeof t == "function" ? n.local ? Ot : Dt : n.local ? Et : Tt)(n, t));
}
function At(e) {
	return e.ownerDocument && e.ownerDocument.defaultView || e.document && e || e.defaultView;
}
function jt(e) {
	return function() {
		this.style.removeProperty(e);
	};
}
function Mt(e, t, n) {
	return function() {
		this.style.setProperty(e, t, n);
	};
}
function Nt(e, t, n) {
	return function() {
		var r = t.apply(this, arguments);
		r == null ? this.style.removeProperty(e) : this.style.setProperty(e, r, n);
	};
}
function Pt(e, t, n) {
	return arguments.length > 1 ? this.each((t == null ? jt : typeof t == "function" ? Nt : Mt)(e, t, n ?? "")) : Ft(this.node(), e);
}
function Ft(e, t) {
	return e.style.getPropertyValue(t) || At(e).getComputedStyle(e, null).getPropertyValue(t);
}
function It(e) {
	return function() {
		delete this[e];
	};
}
function Lt(e, t) {
	return function() {
		this[e] = t;
	};
}
function Rt(e, t) {
	return function() {
		var n = t.apply(this, arguments);
		n == null ? delete this[e] : this[e] = n;
	};
}
function zt(e, t) {
	return arguments.length > 1 ? this.each((t == null ? It : typeof t == "function" ? Rt : Lt)(e, t)) : this.node()[e];
}
function Bt(e) {
	return e.trim().split(/^|\s+/);
}
function Vt(e) {
	return e.classList || new Ht(e);
}
function Ht(e) {
	this._node = e, this._names = Bt(e.getAttribute("class") || "");
}
Ht.prototype = {
	add: function(e) {
		this._names.indexOf(e) < 0 && (this._names.push(e), this._node.setAttribute("class", this._names.join(" ")));
	},
	remove: function(e) {
		var t = this._names.indexOf(e);
		t >= 0 && (this._names.splice(t, 1), this._node.setAttribute("class", this._names.join(" ")));
	},
	contains: function(e) {
		return this._names.indexOf(e) >= 0;
	}
};
function Ut(e, t) {
	for (var n = Vt(e), r = -1, i = t.length; ++r < i;) n.add(t[r]);
}
function Wt(e, t) {
	for (var n = Vt(e), r = -1, i = t.length; ++r < i;) n.remove(t[r]);
}
function Gt(e) {
	return function() {
		Ut(this, e);
	};
}
function Kt(e) {
	return function() {
		Wt(this, e);
	};
}
function qt(e, t) {
	return function() {
		(t.apply(this, arguments) ? Ut : Wt)(this, e);
	};
}
function Jt(e, t) {
	var n = Bt(e + "");
	if (arguments.length < 2) {
		for (var r = Vt(this.node()), i = -1, a = n.length; ++i < a;) if (!r.contains(n[i])) return !1;
		return !0;
	}
	return this.each((typeof t == "function" ? qt : t ? Gt : Kt)(n, t));
}
function Yt() {
	this.textContent = "";
}
function Xt(e) {
	return function() {
		this.textContent = e;
	};
}
function Zt(e) {
	return function() {
		var t = e.apply(this, arguments);
		this.textContent = t ?? "";
	};
}
function Qt(e) {
	return arguments.length ? this.each(e == null ? Yt : (typeof e == "function" ? Zt : Xt)(e)) : this.node().textContent;
}
function $t() {
	this.innerHTML = "";
}
function en(e) {
	return function() {
		this.innerHTML = e;
	};
}
function tn(e) {
	return function() {
		var t = e.apply(this, arguments);
		this.innerHTML = t ?? "";
	};
}
function nn(e) {
	return arguments.length ? this.each(e == null ? $t : (typeof e == "function" ? tn : en)(e)) : this.node().innerHTML;
}
function rn() {
	this.nextSibling && this.parentNode.appendChild(this);
}
function an() {
	return this.each(rn);
}
function on() {
	this.previousSibling && this.parentNode.insertBefore(this, this.parentNode.firstChild);
}
function sn() {
	return this.each(on);
}
function cn(e) {
	var t = typeof e == "function" ? e : Ie(e);
	return this.select(function() {
		return this.appendChild(t.apply(this, arguments));
	});
}
function ln() {
	return null;
}
function un(e, t) {
	var n = typeof e == "function" ? e : Ie(e), r = t == null ? ln : typeof t == "function" ? t : Re(t);
	return this.select(function() {
		return this.insertBefore(n.apply(this, arguments), r.apply(this, arguments) || null);
	});
}
function dn() {
	var e = this.parentNode;
	e && e.removeChild(this);
}
function fn() {
	return this.each(dn);
}
function pn() {
	var e = this.cloneNode(!1), t = this.parentNode;
	return t ? t.insertBefore(e, this.nextSibling) : e;
}
function mn() {
	var e = this.cloneNode(!0), t = this.parentNode;
	return t ? t.insertBefore(e, this.nextSibling) : e;
}
function hn(e) {
	return this.select(e ? mn : pn);
}
function gn(e) {
	return arguments.length ? this.property("__data__", e) : this.node().__data__;
}
function _n(e) {
	return function(t) {
		e.call(this, t, this.__data__);
	};
}
function vn(e) {
	return e.trim().split(/^|\s+/).map(function(e) {
		var t = "", n = e.indexOf(".");
		return n >= 0 && (t = e.slice(n + 1), e = e.slice(0, n)), {
			type: e,
			name: t
		};
	});
}
function yn(e) {
	return function() {
		var t = this.__on;
		if (t) {
			for (var n = 0, r = -1, i = t.length, a; n < i; ++n) a = t[n], (!e.type || a.type === e.type) && a.name === e.name ? this.removeEventListener(a.type, a.listener, a.options) : t[++r] = a;
			++r ? t.length = r : delete this.__on;
		}
	};
}
function bn(e, t, n) {
	return function() {
		var r = this.__on, i, a = _n(t);
		if (r) {
			for (var o = 0, s = r.length; o < s; ++o) if ((i = r[o]).type === e.type && i.name === e.name) {
				this.removeEventListener(i.type, i.listener, i.options), this.addEventListener(i.type, i.listener = a, i.options = n), i.value = t;
				return;
			}
		}
		this.addEventListener(e.type, a, n), i = {
			type: e.type,
			name: e.name,
			value: t,
			listener: a,
			options: n
		}, r ? r.push(i) : this.__on = [i];
	};
}
function xn(e, t, n) {
	var r = vn(e + ""), i, a = r.length, o;
	if (arguments.length < 2) {
		var s = this.node().__on;
		if (s) {
			for (var c = 0, l = s.length, u; c < l; ++c) for (i = 0, u = s[c]; i < a; ++i) if ((o = r[i]).type === u.type && o.name === u.name) return u.value;
		}
	} else {
		for (s = t ? bn : yn, i = 0; i < a; ++i) this.each(s(r[i], t, n));
		return this;
	}
}
function Sn(e, t, n) {
	var r = At(e), i = r.CustomEvent;
	typeof i == "function" ? i = new i(t, n) : (i = r.document.createEvent("Event"), n ? (i.initEvent(t, n.bubbles, n.cancelable), i.detail = n.detail) : i.initEvent(t, !1, !1)), e.dispatchEvent(i);
}
function Cn(e, t) {
	return function() {
		return Sn(this, e, t);
	};
}
function wn(e, t) {
	return function() {
		return Sn(this, e, t.apply(this, arguments));
	};
}
function Tn(e, t) {
	return this.each((typeof t == "function" ? wn : Cn)(e, t));
}
function* En() {
	for (var e = this._groups, t = 0, n = e.length; t < n; ++t) for (var r = e[t], i = 0, a = r.length, o; i < a; ++i) (o = r[i]) && (yield o);
}
var Dn = [null];
function On(e, t) {
	this._groups = e, this._parents = t;
}
function kn() {
	return new On([[document.documentElement]], Dn);
}
function An() {
	return this;
}
On.prototype = kn.prototype = {
	constructor: On,
	select: ze,
	selectAll: We,
	selectChild: Xe,
	selectChildren: et,
	filter: tt,
	data: lt,
	enter: rt,
	exit: dt,
	join: ft,
	merge: pt,
	selection: An,
	order: mt,
	sort: ht,
	call: _t,
	nodes: vt,
	node: yt,
	size: bt,
	empty: xt,
	each: St,
	attr: kt,
	style: Pt,
	property: zt,
	classed: Jt,
	text: Qt,
	html: nn,
	raise: an,
	lower: sn,
	append: cn,
	insert: un,
	remove: fn,
	clone: hn,
	datum: gn,
	on: xn,
	dispatch: Tn,
	[Symbol.iterator]: En
};
function jn(e) {
	return typeof e == "string" ? new On([[document.querySelector(e)]], [document.documentElement]) : new On([[e]], Dn);
}
function Mn(e) {
	let t;
	for (; t = e.sourceEvent;) e = t;
	return e;
}
function Nn(e, t) {
	if (e = Mn(e), t === void 0 && (t = e.currentTarget), t) {
		var n = t.ownerSVGElement || t;
		if (n.createSVGPoint) {
			var r = n.createSVGPoint();
			return r.x = e.clientX, r.y = e.clientY, r = r.matrixTransform(t.getScreenCTM().inverse()), [r.x, r.y];
		}
		if (t.getBoundingClientRect) {
			var i = t.getBoundingClientRect();
			return [e.clientX - i.left - t.clientLeft, e.clientY - i.top - t.clientTop];
		}
	}
	return [e.pageX, e.pageY];
}
var Pn = { passive: !1 }, Fn = {
	capture: !0,
	passive: !1
};
function In(e) {
	e.stopImmediatePropagation();
}
function Ln(e) {
	e.preventDefault(), e.stopImmediatePropagation();
}
function Rn(e) {
	var t = e.document.documentElement, n = jn(e).on("dragstart.drag", Ln, Fn);
	"onselectstart" in t ? n.on("selectstart.drag", Ln, Fn) : (t.__noselect = t.style.MozUserSelect, t.style.MozUserSelect = "none");
}
function zn(e, t) {
	var n = e.document.documentElement, r = jn(e).on("dragstart.drag", null);
	t && (r.on("click.drag", Ln, Fn), setTimeout(function() {
		r.on("click.drag", null);
	}, 0)), "onselectstart" in n ? r.on("selectstart.drag", null) : (n.style.MozUserSelect = n.__noselect, delete n.__noselect);
}
var Bn = (e) => () => e;
function Vn(e, { sourceEvent: t, subject: n, target: r, identifier: i, active: a, x: o, y: s, dx: c, dy: l, dispatch: u }) {
	Object.defineProperties(this, {
		type: {
			value: e,
			enumerable: !0,
			configurable: !0
		},
		sourceEvent: {
			value: t,
			enumerable: !0,
			configurable: !0
		},
		subject: {
			value: n,
			enumerable: !0,
			configurable: !0
		},
		target: {
			value: r,
			enumerable: !0,
			configurable: !0
		},
		identifier: {
			value: i,
			enumerable: !0,
			configurable: !0
		},
		active: {
			value: a,
			enumerable: !0,
			configurable: !0
		},
		x: {
			value: o,
			enumerable: !0,
			configurable: !0
		},
		y: {
			value: s,
			enumerable: !0,
			configurable: !0
		},
		dx: {
			value: c,
			enumerable: !0,
			configurable: !0
		},
		dy: {
			value: l,
			enumerable: !0,
			configurable: !0
		},
		_: { value: u }
	});
}
Vn.prototype.on = function() {
	var e = this._.on.apply(this._, arguments);
	return e === this._ ? this : e;
};
function Hn(e) {
	return !e.ctrlKey && !e.button;
}
function Un() {
	return this.parentNode;
}
function Wn(e, t) {
	return t ?? {
		x: e.x,
		y: e.y
	};
}
function Gn() {
	return navigator.maxTouchPoints || "ontouchstart" in this;
}
function Kn() {
	var e = Hn, t = Un, n = Wn, r = Gn, i = {}, a = Ee("start", "drag", "end"), o = 0, s, c, l, u, d = 0;
	function f(e) {
		e.on("mousedown.drag", p).filter(r).on("touchstart.drag", g).on("touchmove.drag", _, Pn).on("touchend.drag touchcancel.drag", v).style("touch-action", "none").style("-webkit-tap-highlight-color", "rgba(0,0,0,0)");
	}
	function p(n, r) {
		if (!u && e.call(this, n, r)) {
			var i = y(this, t.call(this, n, r), n, r, "mouse");
			i && (jn(n.view).on("mousemove.drag", m, Fn).on("mouseup.drag", h, Fn), Rn(n.view), In(n), l = !1, s = n.clientX, c = n.clientY, i("start", n));
		}
	}
	function m(e) {
		if (Ln(e), !l) {
			var t = e.clientX - s, n = e.clientY - c;
			l = t * t + n * n > d;
		}
		i.mouse("drag", e);
	}
	function h(e) {
		jn(e.view).on("mousemove.drag mouseup.drag", null), zn(e.view, l), Ln(e), i.mouse("end", e);
	}
	function g(n, r) {
		if (e.call(this, n, r)) for (var i = n.changedTouches, a = t.call(this, n, r), o = i.length, s = 0, c; s < o; ++s) (c = y(this, a, n, r, i[s].identifier, i[s])) && (In(n), c("start", n, i[s]));
	}
	function _(e) {
		for (var t = e.changedTouches, n = t.length, r = 0, a; r < n; ++r) (a = i[t[r].identifier]) && (Ln(e), a("drag", e, t[r]));
	}
	function v(e) {
		var t = e.changedTouches, n = t.length, r, a;
		for (u && clearTimeout(u), u = setTimeout(function() {
			u = null;
		}, 500), r = 0; r < n; ++r) (a = i[t[r].identifier]) && (In(e), a("end", e, t[r]));
	}
	function y(e, t, r, s, c, l) {
		var u = a.copy(), d = Nn(l || r, t), p, m, h;
		if ((h = n.call(e, new Vn("beforestart", {
			sourceEvent: r,
			target: f,
			identifier: c,
			active: o,
			x: d[0],
			y: d[1],
			dx: 0,
			dy: 0,
			dispatch: u
		}), s)) != null) return p = h.x - d[0] || 0, m = h.y - d[1] || 0, function n(r, a, l) {
			var g = d, _;
			switch (r) {
				case "start":
					i[c] = n, _ = o++;
					break;
				case "end": delete i[c], --o;
				case "drag": d = Nn(l || a, t), _ = o;
			}
			u.call(r, e, new Vn(r, {
				sourceEvent: a,
				subject: h,
				target: f,
				identifier: c,
				active: _,
				x: d[0] + p,
				y: d[1] + m,
				dx: d[0] - g[0],
				dy: d[1] - g[1],
				dispatch: u
			}), s);
		};
	}
	return f.filter = function(t) {
		return arguments.length ? (e = typeof t == "function" ? t : Bn(!!t), f) : e;
	}, f.container = function(e) {
		return arguments.length ? (t = typeof e == "function" ? e : Bn(e), f) : t;
	}, f.subject = function(e) {
		return arguments.length ? (n = typeof e == "function" ? e : Bn(e), f) : n;
	}, f.touchable = function(e) {
		return arguments.length ? (r = typeof e == "function" ? e : Bn(!!e), f) : r;
	}, f.on = function() {
		var e = a.on.apply(a, arguments);
		return e === a ? f : e;
	}, f.clickDistance = function(e) {
		return arguments.length ? (d = (e = +e) * e, f) : Math.sqrt(d);
	}, f;
}
function qn(e, t, n) {
	e.prototype = t.prototype = n, n.constructor = e;
}
function Jn(e, t) {
	var n = Object.create(e.prototype);
	for (var r in t) n[r] = t[r];
	return n;
}
function Yn() {}
var Xn = .7, Zn = 1 / Xn, Qn = "\\s*([+-]?\\d+)\\s*", $n = "\\s*([+-]?(?:\\d*\\.)?\\d+(?:[eE][+-]?\\d+)?)\\s*", er = "\\s*([+-]?(?:\\d*\\.)?\\d+(?:[eE][+-]?\\d+)?)%\\s*", tr = /^#([0-9a-f]{3,8})$/, nr = RegExp(`^rgb\\(${Qn},${Qn},${Qn}\\)$`), rr = RegExp(`^rgb\\(${er},${er},${er}\\)$`), ir = RegExp(`^rgba\\(${Qn},${Qn},${Qn},${$n}\\)$`), ar = RegExp(`^rgba\\(${er},${er},${er},${$n}\\)$`), or = RegExp(`^hsl\\(${$n},${er},${er}\\)$`), sr = RegExp(`^hsla\\(${$n},${er},${er},${$n}\\)$`), cr = {
	aliceblue: 15792383,
	antiquewhite: 16444375,
	aqua: 65535,
	aquamarine: 8388564,
	azure: 15794175,
	beige: 16119260,
	bisque: 16770244,
	black: 0,
	blanchedalmond: 16772045,
	blue: 255,
	blueviolet: 9055202,
	brown: 10824234,
	burlywood: 14596231,
	cadetblue: 6266528,
	chartreuse: 8388352,
	chocolate: 13789470,
	coral: 16744272,
	cornflowerblue: 6591981,
	cornsilk: 16775388,
	crimson: 14423100,
	cyan: 65535,
	darkblue: 139,
	darkcyan: 35723,
	darkgoldenrod: 12092939,
	darkgray: 11119017,
	darkgreen: 25600,
	darkgrey: 11119017,
	darkkhaki: 12433259,
	darkmagenta: 9109643,
	darkolivegreen: 5597999,
	darkorange: 16747520,
	darkorchid: 10040012,
	darkred: 9109504,
	darksalmon: 15308410,
	darkseagreen: 9419919,
	darkslateblue: 4734347,
	darkslategray: 3100495,
	darkslategrey: 3100495,
	darkturquoise: 52945,
	darkviolet: 9699539,
	deeppink: 16716947,
	deepskyblue: 49151,
	dimgray: 6908265,
	dimgrey: 6908265,
	dodgerblue: 2003199,
	firebrick: 11674146,
	floralwhite: 16775920,
	forestgreen: 2263842,
	fuchsia: 16711935,
	gainsboro: 14474460,
	ghostwhite: 16316671,
	gold: 16766720,
	goldenrod: 14329120,
	gray: 8421504,
	green: 32768,
	greenyellow: 11403055,
	grey: 8421504,
	honeydew: 15794160,
	hotpink: 16738740,
	indianred: 13458524,
	indigo: 4915330,
	ivory: 16777200,
	khaki: 15787660,
	lavender: 15132410,
	lavenderblush: 16773365,
	lawngreen: 8190976,
	lemonchiffon: 16775885,
	lightblue: 11393254,
	lightcoral: 15761536,
	lightcyan: 14745599,
	lightgoldenrodyellow: 16448210,
	lightgray: 13882323,
	lightgreen: 9498256,
	lightgrey: 13882323,
	lightpink: 16758465,
	lightsalmon: 16752762,
	lightseagreen: 2142890,
	lightskyblue: 8900346,
	lightslategray: 7833753,
	lightslategrey: 7833753,
	lightsteelblue: 11584734,
	lightyellow: 16777184,
	lime: 65280,
	limegreen: 3329330,
	linen: 16445670,
	magenta: 16711935,
	maroon: 8388608,
	mediumaquamarine: 6737322,
	mediumblue: 205,
	mediumorchid: 12211667,
	mediumpurple: 9662683,
	mediumseagreen: 3978097,
	mediumslateblue: 8087790,
	mediumspringgreen: 64154,
	mediumturquoise: 4772300,
	mediumvioletred: 13047173,
	midnightblue: 1644912,
	mintcream: 16121850,
	mistyrose: 16770273,
	moccasin: 16770229,
	navajowhite: 16768685,
	navy: 128,
	oldlace: 16643558,
	olive: 8421376,
	olivedrab: 7048739,
	orange: 16753920,
	orangered: 16729344,
	orchid: 14315734,
	palegoldenrod: 15657130,
	palegreen: 10025880,
	paleturquoise: 11529966,
	palevioletred: 14381203,
	papayawhip: 16773077,
	peachpuff: 16767673,
	peru: 13468991,
	pink: 16761035,
	plum: 14524637,
	powderblue: 11591910,
	purple: 8388736,
	rebeccapurple: 6697881,
	red: 16711680,
	rosybrown: 12357519,
	royalblue: 4286945,
	saddlebrown: 9127187,
	salmon: 16416882,
	sandybrown: 16032864,
	seagreen: 3050327,
	seashell: 16774638,
	sienna: 10506797,
	silver: 12632256,
	skyblue: 8900331,
	slateblue: 6970061,
	slategray: 7372944,
	slategrey: 7372944,
	snow: 16775930,
	springgreen: 65407,
	steelblue: 4620980,
	tan: 13808780,
	teal: 32896,
	thistle: 14204888,
	tomato: 16737095,
	turquoise: 4251856,
	violet: 15631086,
	wheat: 16113331,
	white: 16777215,
	whitesmoke: 16119285,
	yellow: 16776960,
	yellowgreen: 10145074
};
qn(Yn, pr, {
	copy(e) {
		return Object.assign(new this.constructor(), this, e);
	},
	displayable() {
		return this.rgb().displayable();
	},
	hex: lr,
	formatHex: lr,
	formatHex8: ur,
	formatHsl: dr,
	formatRgb: fr,
	toString: fr
});
function lr() {
	return this.rgb().formatHex();
}
function ur() {
	return this.rgb().formatHex8();
}
function dr() {
	return Er(this).formatHsl();
}
function fr() {
	return this.rgb().formatRgb();
}
function pr(e) {
	var t, n;
	return e = (e + "").trim().toLowerCase(), (t = tr.exec(e)) ? (n = t[1].length, t = parseInt(t[1], 16), n === 6 ? mr(t) : n === 3 ? new vr(t >> 8 & 15 | t >> 4 & 240, t >> 4 & 15 | t & 240, (t & 15) << 4 | t & 15, 1) : n === 8 ? hr(t >> 24 & 255, t >> 16 & 255, t >> 8 & 255, (t & 255) / 255) : n === 4 ? hr(t >> 12 & 15 | t >> 8 & 240, t >> 8 & 15 | t >> 4 & 240, t >> 4 & 15 | t & 240, ((t & 15) << 4 | t & 15) / 255) : null) : (t = nr.exec(e)) ? new vr(t[1], t[2], t[3], 1) : (t = rr.exec(e)) ? new vr(t[1] * 255 / 100, t[2] * 255 / 100, t[3] * 255 / 100, 1) : (t = ir.exec(e)) ? hr(t[1], t[2], t[3], t[4]) : (t = ar.exec(e)) ? hr(t[1] * 255 / 100, t[2] * 255 / 100, t[3] * 255 / 100, t[4]) : (t = or.exec(e)) ? Tr(t[1], t[2] / 100, t[3] / 100, 1) : (t = sr.exec(e)) ? Tr(t[1], t[2] / 100, t[3] / 100, t[4]) : cr.hasOwnProperty(e) ? mr(cr[e]) : e === "transparent" ? new vr(NaN, NaN, NaN, 0) : null;
}
function mr(e) {
	return new vr(e >> 16 & 255, e >> 8 & 255, e & 255, 1);
}
function hr(e, t, n, r) {
	return r <= 0 && (e = t = n = NaN), new vr(e, t, n, r);
}
function gr(e) {
	return e instanceof Yn || (e = pr(e)), e ? (e = e.rgb(), new vr(e.r, e.g, e.b, e.opacity)) : new vr();
}
function _r(e, t, n, r) {
	return arguments.length === 1 ? gr(e) : new vr(e, t, n, r ?? 1);
}
function vr(e, t, n, r) {
	this.r = +e, this.g = +t, this.b = +n, this.opacity = +r;
}
qn(vr, _r, Jn(Yn, {
	brighter(e) {
		return e = e == null ? Zn : Zn ** +e, new vr(this.r * e, this.g * e, this.b * e, this.opacity);
	},
	darker(e) {
		return e = e == null ? Xn : Xn ** +e, new vr(this.r * e, this.g * e, this.b * e, this.opacity);
	},
	rgb() {
		return this;
	},
	clamp() {
		return new vr(Cr(this.r), Cr(this.g), Cr(this.b), Sr(this.opacity));
	},
	displayable() {
		return -.5 <= this.r && this.r < 255.5 && -.5 <= this.g && this.g < 255.5 && -.5 <= this.b && this.b < 255.5 && 0 <= this.opacity && this.opacity <= 1;
	},
	hex: yr,
	formatHex: yr,
	formatHex8: br,
	formatRgb: xr,
	toString: xr
}));
function yr() {
	return `#${wr(this.r)}${wr(this.g)}${wr(this.b)}`;
}
function br() {
	return `#${wr(this.r)}${wr(this.g)}${wr(this.b)}${wr((isNaN(this.opacity) ? 1 : this.opacity) * 255)}`;
}
function xr() {
	let e = Sr(this.opacity);
	return `${e === 1 ? "rgb(" : "rgba("}${Cr(this.r)}, ${Cr(this.g)}, ${Cr(this.b)}${e === 1 ? ")" : `, ${e})`}`;
}
function Sr(e) {
	return isNaN(e) ? 1 : Math.max(0, Math.min(1, e));
}
function Cr(e) {
	return Math.max(0, Math.min(255, Math.round(e) || 0));
}
function wr(e) {
	return e = Cr(e), (e < 16 ? "0" : "") + e.toString(16);
}
function Tr(e, t, n, r) {
	return r <= 0 ? e = t = n = NaN : n <= 0 || n >= 1 ? e = t = NaN : t <= 0 && (e = NaN), new Or(e, t, n, r);
}
function Er(e) {
	if (e instanceof Or) return new Or(e.h, e.s, e.l, e.opacity);
	if (e instanceof Yn || (e = pr(e)), !e) return new Or();
	if (e instanceof Or) return e;
	e = e.rgb();
	var t = e.r / 255, n = e.g / 255, r = e.b / 255, i = Math.min(t, n, r), a = Math.max(t, n, r), o = NaN, s = a - i, c = (a + i) / 2;
	return s ? (o = t === a ? (n - r) / s + (n < r) * 6 : n === a ? (r - t) / s + 2 : (t - n) / s + 4, s /= c < .5 ? a + i : 2 - a - i, o *= 60) : s = c > 0 && c < 1 ? 0 : o, new Or(o, s, c, e.opacity);
}
function Dr(e, t, n, r) {
	return arguments.length === 1 ? Er(e) : new Or(e, t, n, r ?? 1);
}
function Or(e, t, n, r) {
	this.h = +e, this.s = +t, this.l = +n, this.opacity = +r;
}
qn(Or, Dr, Jn(Yn, {
	brighter(e) {
		return e = e == null ? Zn : Zn ** +e, new Or(this.h, this.s, this.l * e, this.opacity);
	},
	darker(e) {
		return e = e == null ? Xn : Xn ** +e, new Or(this.h, this.s, this.l * e, this.opacity);
	},
	rgb() {
		var e = this.h % 360 + (this.h < 0) * 360, t = isNaN(e) || isNaN(this.s) ? 0 : this.s, n = this.l, r = n + (n < .5 ? n : 1 - n) * t, i = 2 * n - r;
		return new vr(jr(e >= 240 ? e - 240 : e + 120, i, r), jr(e, i, r), jr(e < 120 ? e + 240 : e - 120, i, r), this.opacity);
	},
	clamp() {
		return new Or(kr(this.h), Ar(this.s), Ar(this.l), Sr(this.opacity));
	},
	displayable() {
		return (0 <= this.s && this.s <= 1 || isNaN(this.s)) && 0 <= this.l && this.l <= 1 && 0 <= this.opacity && this.opacity <= 1;
	},
	formatHsl() {
		let e = Sr(this.opacity);
		return `${e === 1 ? "hsl(" : "hsla("}${kr(this.h)}, ${Ar(this.s) * 100}%, ${Ar(this.l) * 100}%${e === 1 ? ")" : `, ${e})`}`;
	}
}));
function kr(e) {
	return e = (e || 0) % 360, e < 0 ? e + 360 : e;
}
function Ar(e) {
	return Math.max(0, Math.min(1, e || 0));
}
function jr(e, t, n) {
	return (e < 60 ? t + (n - t) * e / 60 : e < 180 ? n : e < 240 ? t + (n - t) * (240 - e) / 60 : t) * 255;
}
var Mr = (e) => () => e;
function Nr(e, t) {
	return function(n) {
		return e + n * t;
	};
}
function Pr(e, t, n) {
	return e **= +n, t = t ** +n - e, n = 1 / n, function(r) {
		return (e + r * t) ** +n;
	};
}
function Fr(e) {
	return (e = +e) == 1 ? Ir : function(t, n) {
		return n - t ? Pr(t, n, e) : Mr(isNaN(t) ? n : t);
	};
}
function Ir(e, t) {
	var n = t - e;
	return n ? Nr(e, n) : Mr(isNaN(e) ? t : e);
}
var Lr = function e(t) {
	var n = Fr(t);
	function r(e, t) {
		var r = n((e = _r(e)).r, (t = _r(t)).r), i = n(e.g, t.g), a = n(e.b, t.b), o = Ir(e.opacity, t.opacity);
		return function(t) {
			return e.r = r(t), e.g = i(t), e.b = a(t), e.opacity = o(t), e + "";
		};
	}
	return r.gamma = e, r;
}(1);
function Rr(e, t) {
	t || (t = []);
	var n = e ? Math.min(t.length, e.length) : 0, r = t.slice(), i;
	return function(a) {
		for (i = 0; i < n; ++i) r[i] = e[i] * (1 - a) + t[i] * a;
		return r;
	};
}
function zr(e) {
	return ArrayBuffer.isView(e) && !(e instanceof DataView);
}
function Br(e, t) {
	for (var n = t ? t.length : 0, r = e ? Math.min(n, e.length) : 0, i = Array(r), a = Array(n), o = 0; o < r; ++o) i[o] = Yr(e[o], t[o]);
	for (; o < n; ++o) a[o] = t[o];
	return function(e) {
		for (o = 0; o < r; ++o) a[o] = i[o](e);
		return a;
	};
}
function Vr(e, t) {
	var n = /* @__PURE__ */ new Date();
	return e = +e, t = +t, function(r) {
		return n.setTime(e * (1 - r) + t * r), n;
	};
}
function Hr(e, t) {
	return e = +e, t = +t, function(n) {
		return e * (1 - n) + t * n;
	};
}
function Ur(e, t) {
	var n = {}, r = {}, i;
	for (i in typeof e == "object" && e || (e = {}), typeof t == "object" && t || (t = {}), t) i in e ? n[i] = Yr(e[i], t[i]) : r[i] = t[i];
	return function(e) {
		for (i in n) r[i] = n[i](e);
		return r;
	};
}
var Wr = /[-+]?(?:\d+\.?\d*|\.?\d+)(?:[eE][-+]?\d+)?/g, Gr = new RegExp(Wr.source, "g");
function Kr(e) {
	return function() {
		return e;
	};
}
function qr(e) {
	return function(t) {
		return e(t) + "";
	};
}
function Jr(e, t) {
	var n = Wr.lastIndex = Gr.lastIndex = 0, r, i, a, o = -1, s = [], c = [];
	for (e += "", t += ""; (r = Wr.exec(e)) && (i = Gr.exec(t));) (a = i.index) > n && (a = t.slice(n, a), s[o] ? s[o] += a : s[++o] = a), (r = r[0]) === (i = i[0]) ? s[o] ? s[o] += i : s[++o] = i : (s[++o] = null, c.push({
		i: o,
		x: Hr(r, i)
	})), n = Gr.lastIndex;
	return n < t.length && (a = t.slice(n), s[o] ? s[o] += a : s[++o] = a), s.length < 2 ? c[0] ? qr(c[0].x) : Kr(t) : (t = c.length, function(e) {
		for (var n = 0, r; n < t; ++n) s[(r = c[n]).i] = r.x(e);
		return s.join("");
	});
}
function Yr(e, t) {
	var n = typeof t, r;
	return t == null || n === "boolean" ? Mr(t) : (n === "number" ? Hr : n === "string" ? (r = pr(t)) ? (t = r, Lr) : Jr : t instanceof pr ? Lr : t instanceof Date ? Vr : zr(t) ? Rr : Array.isArray(t) ? Br : typeof t.valueOf != "function" && typeof t.toString != "function" || isNaN(t) ? Ur : Hr)(e, t);
}
var Xr = 180 / Math.PI, Zr = {
	translateX: 0,
	translateY: 0,
	rotate: 0,
	skewX: 0,
	scaleX: 1,
	scaleY: 1
};
function Qr(e, t, n, r, i, a) {
	var o, s, c;
	return (o = Math.sqrt(e * e + t * t)) && (e /= o, t /= o), (c = e * n + t * r) && (n -= e * c, r -= t * c), (s = Math.sqrt(n * n + r * r)) && (n /= s, r /= s, c /= s), e * r < t * n && (e = -e, t = -t, c = -c, o = -o), {
		translateX: i,
		translateY: a,
		rotate: Math.atan2(t, e) * Xr,
		skewX: Math.atan(c) * Xr,
		scaleX: o,
		scaleY: s
	};
}
var $r;
function ei(e) {
	let t = new (typeof DOMMatrix == "function" ? DOMMatrix : WebKitCSSMatrix)(e + "");
	return t.isIdentity ? Zr : Qr(t.a, t.b, t.c, t.d, t.e, t.f);
}
function ti(e) {
	return e == null || ($r || ($r = document.createElementNS("http://www.w3.org/2000/svg", "g")), $r.setAttribute("transform", e), !(e = $r.transform.baseVal.consolidate())) ? Zr : (e = e.matrix, Qr(e.a, e.b, e.c, e.d, e.e, e.f));
}
function ni(e, t, n, r) {
	function i(e) {
		return e.length ? e.pop() + " " : "";
	}
	function a(e, r, i, a, o, s) {
		if (e !== i || r !== a) {
			var c = o.push("translate(", null, t, null, n);
			s.push({
				i: c - 4,
				x: Hr(e, i)
			}, {
				i: c - 2,
				x: Hr(r, a)
			});
		} else (i || a) && o.push("translate(" + i + t + a + n);
	}
	function o(e, t, n, a) {
		e === t ? t && n.push(i(n) + "rotate(" + t + r) : (e - t > 180 ? t += 360 : t - e > 180 && (e += 360), a.push({
			i: n.push(i(n) + "rotate(", null, r) - 2,
			x: Hr(e, t)
		}));
	}
	function s(e, t, n, a) {
		e === t ? t && n.push(i(n) + "skewX(" + t + r) : a.push({
			i: n.push(i(n) + "skewX(", null, r) - 2,
			x: Hr(e, t)
		});
	}
	function c(e, t, n, r, a, o) {
		if (e !== n || t !== r) {
			var s = a.push(i(a) + "scale(", null, ",", null, ")");
			o.push({
				i: s - 4,
				x: Hr(e, n)
			}, {
				i: s - 2,
				x: Hr(t, r)
			});
		} else (n !== 1 || r !== 1) && a.push(i(a) + "scale(" + n + "," + r + ")");
	}
	return function(t, n) {
		var r = [], i = [];
		return t = e(t), n = e(n), a(t.translateX, t.translateY, n.translateX, n.translateY, r, i), o(t.rotate, n.rotate, r, i), s(t.skewX, n.skewX, r, i), c(t.scaleX, t.scaleY, n.scaleX, n.scaleY, r, i), t = n = null, function(e) {
			for (var t = -1, n = i.length, a; ++t < n;) r[(a = i[t]).i] = a.x(e);
			return r.join("");
		};
	};
}
var ri = ni(ei, "px, ", "px)", "deg)"), ii = ni(ti, ", ", ")", ")"), ai = 1e-12;
function oi(e) {
	return ((e = Math.exp(e)) + 1 / e) / 2;
}
function si(e) {
	return ((e = Math.exp(e)) - 1 / e) / 2;
}
function ci(e) {
	return ((e = Math.exp(2 * e)) - 1) / (e + 1);
}
var li = function e(t, n, r) {
	function i(e, i) {
		var a = e[0], o = e[1], s = e[2], c = i[0], l = i[1], u = i[2], d = c - a, f = l - o, p = d * d + f * f, m, h;
		if (p < ai) h = Math.log(u / s) / t, m = function(e) {
			return [
				a + e * d,
				o + e * f,
				s * Math.exp(t * e * h)
			];
		};
		else {
			var g = Math.sqrt(p), _ = (u * u - s * s + r * p) / (2 * s * n * g), v = (u * u - s * s - r * p) / (2 * u * n * g), y = Math.log(Math.sqrt(_ * _ + 1) - _);
			h = (Math.log(Math.sqrt(v * v + 1) - v) - y) / t, m = function(e) {
				var r = e * h, i = oi(y), c = s / (n * g) * (i * ci(t * r + y) - si(y));
				return [
					a + c * d,
					o + c * f,
					s * i / oi(t * r + y)
				];
			};
		}
		return m.duration = h * 1e3 * t / Math.SQRT2, m;
	}
	return i.rho = function(t) {
		var n = Math.max(.001, +t), r = n * n;
		return e(n, r, r * r);
	}, i;
}(Math.SQRT2, 2, 4), ui = 0, di = 0, fi = 0, pi = 1e3, mi, hi, gi = 0, _i = 0, vi = 0, yi = typeof performance == "object" && performance.now ? performance : Date, bi = typeof window == "object" && window.requestAnimationFrame ? window.requestAnimationFrame.bind(window) : function(e) {
	setTimeout(e, 17);
};
function xi() {
	return _i || (bi(Si), _i = yi.now() + vi);
}
function Si() {
	_i = 0;
}
function Ci() {
	this._call = this._time = this._next = null;
}
Ci.prototype = wi.prototype = {
	constructor: Ci,
	restart: function(e, t, n) {
		if (typeof e != "function") throw TypeError("callback is not a function");
		n = (n == null ? xi() : +n) + (t == null ? 0 : +t), !this._next && hi !== this && (hi ? hi._next = this : mi = this, hi = this), this._call = e, this._time = n, ki();
	},
	stop: function() {
		this._call && (this._call = null, this._time = Infinity, ki());
	}
};
function wi(e, t, n) {
	var r = new Ci();
	return r.restart(e, t, n), r;
}
function Ti() {
	xi(), ++ui;
	for (var e = mi, t; e;) (t = _i - e._time) >= 0 && e._call.call(void 0, t), e = e._next;
	--ui;
}
function Ei() {
	_i = (gi = yi.now()) + vi, ui = di = 0;
	try {
		Ti();
	} finally {
		ui = 0, Oi(), _i = 0;
	}
}
function Di() {
	var e = yi.now(), t = e - gi;
	t > pi && (vi -= t, gi = e);
}
function Oi() {
	for (var e, t = mi, n, r = Infinity; t;) t._call ? (r > t._time && (r = t._time), e = t, t = t._next) : (n = t._next, t._next = null, t = e ? e._next = n : mi = n);
	hi = e, ki(r);
}
function ki(e) {
	ui || (di && (di = clearTimeout(di)), e - _i > 24 ? (e < Infinity && (di = setTimeout(Ei, e - yi.now() - vi)), fi && (fi = clearInterval(fi))) : (fi || (gi = yi.now(), fi = setInterval(Di, pi)), ui = 1, bi(Ei)));
}
function Ai(e, t, n) {
	var r = new Ci();
	return t = t == null ? 0 : +t, r.restart((n) => {
		r.stop(), e(n + t);
	}, t, n), r;
}
var ji = Ee("start", "end", "cancel", "interrupt"), Mi = [], Ni = 0, Pi = 1, Fi = 2, Ii = 3, Li = 4, Ri = 5, zi = 6;
function Bi(e, t, n, r, i, a) {
	var o = e.__transition;
	if (!o) e.__transition = {};
	else if (n in o) return;
	Wi(e, n, {
		name: t,
		index: r,
		group: i,
		on: ji,
		tween: Mi,
		time: a.time,
		delay: a.delay,
		duration: a.duration,
		ease: a.ease,
		timer: null,
		state: Ni
	});
}
function Vi(e, t) {
	var n = Ui(e, t);
	if (n.state > Ni) throw Error("too late; already scheduled");
	return n;
}
function Hi(e, t) {
	var n = Ui(e, t);
	if (n.state > Ii) throw Error("too late; already running");
	return n;
}
function Ui(e, t) {
	var n = e.__transition;
	if (!n || !(n = n[t])) throw Error("transition not found");
	return n;
}
function Wi(e, t, n) {
	var r = e.__transition, i;
	r[t] = n, n.timer = wi(a, 0, n.time);
	function a(e) {
		n.state = Pi, n.timer.restart(o, n.delay, n.time), n.delay <= e && o(e - n.delay);
	}
	function o(a) {
		var l, u, d, f;
		if (n.state !== Pi) return c();
		for (l in r) if (f = r[l], f.name === n.name) {
			if (f.state === Ii) return Ai(o);
			f.state === Li ? (f.state = zi, f.timer.stop(), f.on.call("interrupt", e, e.__data__, f.index, f.group), delete r[l]) : +l < t && (f.state = zi, f.timer.stop(), f.on.call("cancel", e, e.__data__, f.index, f.group), delete r[l]);
		}
		if (Ai(function() {
			n.state === Ii && (n.state = Li, n.timer.restart(s, n.delay, n.time), s(a));
		}), n.state = Fi, n.on.call("start", e, e.__data__, n.index, n.group), n.state === Fi) {
			for (n.state = Ii, i = Array(d = n.tween.length), l = 0, u = -1; l < d; ++l) (f = n.tween[l].value.call(e, e.__data__, n.index, n.group)) && (i[++u] = f);
			i.length = u + 1;
		}
	}
	function s(t) {
		for (var r = t < n.duration ? n.ease.call(null, t / n.duration) : (n.timer.restart(c), n.state = Ri, 1), a = -1, o = i.length; ++a < o;) i[a].call(e, r);
		n.state === Ri && (n.on.call("end", e, e.__data__, n.index, n.group), c());
	}
	function c() {
		for (var i in n.state = zi, n.timer.stop(), delete r[t], r) return;
		delete e.__transition;
	}
}
function Gi(e, t) {
	var n = e.__transition, r, i, a = !0, o;
	if (n) {
		for (o in t = t == null ? null : t + "", n) (r = n[o]).name === t ? (i = r.state > Fi && r.state < Ri, r.state = zi, r.timer.stop(), r.on.call(i ? "interrupt" : "cancel", e, e.__data__, r.index, r.group), delete n[o]) : a = !1;
		a && delete e.__transition;
	}
}
function Ki(e) {
	return this.each(function() {
		Gi(this, e);
	});
}
function qi(e, t) {
	var n, r;
	return function() {
		var i = Hi(this, e), a = i.tween;
		if (a !== n) {
			r = n = a;
			for (var o = 0, s = r.length; o < s; ++o) if (r[o].name === t) {
				r = r.slice(), r.splice(o, 1);
				break;
			}
		}
		i.tween = r;
	};
}
function Ji(e, t, n) {
	var r, i;
	if (typeof n != "function") throw Error();
	return function() {
		var a = Hi(this, e), o = a.tween;
		if (o !== r) {
			i = (r = o).slice();
			for (var s = {
				name: t,
				value: n
			}, c = 0, l = i.length; c < l; ++c) if (i[c].name === t) {
				i[c] = s;
				break;
			}
			c === l && i.push(s);
		}
		a.tween = i;
	};
}
function Yi(e, t) {
	var n = this._id;
	if (e += "", arguments.length < 2) {
		for (var r = Ui(this.node(), n).tween, i = 0, a = r.length, o; i < a; ++i) if ((o = r[i]).name === e) return o.value;
		return null;
	}
	return this.each((t == null ? qi : Ji)(n, e, t));
}
function Xi(e, t, n) {
	var r = e._id;
	return e.each(function() {
		var e = Hi(this, r);
		(e.value || (e.value = {}))[t] = n.apply(this, arguments);
	}), function(e) {
		return Ui(e, r).value[t];
	};
}
function Zi(e, t) {
	var n;
	return (typeof t == "number" ? Hr : t instanceof pr ? Lr : (n = pr(t)) ? (t = n, Lr) : Jr)(e, t);
}
function Qi(e) {
	return function() {
		this.removeAttribute(e);
	};
}
function $i(e) {
	return function() {
		this.removeAttributeNS(e.space, e.local);
	};
}
function ea(e, t, n) {
	var r, i = n + "", a;
	return function() {
		var o = this.getAttribute(e);
		return o === i ? null : o === r ? a : a = t(r = o, n);
	};
}
function ta(e, t, n) {
	var r, i = n + "", a;
	return function() {
		var o = this.getAttributeNS(e.space, e.local);
		return o === i ? null : o === r ? a : a = t(r = o, n);
	};
}
function na(e, t, n) {
	var r, i, a;
	return function() {
		var o, s = n(this), c;
		return s == null ? void this.removeAttribute(e) : (o = this.getAttribute(e), c = s + "", o === c ? null : o === r && c === i ? a : (i = c, a = t(r = o, s)));
	};
}
function ra(e, t, n) {
	var r, i, a;
	return function() {
		var o, s = n(this), c;
		return s == null ? void this.removeAttributeNS(e.space, e.local) : (o = this.getAttributeNS(e.space, e.local), c = s + "", o === c ? null : o === r && c === i ? a : (i = c, a = t(r = o, s)));
	};
}
function ia(e, t) {
	var n = Ne(e), r = n === "transform" ? ii : Zi;
	return this.attrTween(e, typeof t == "function" ? (n.local ? ra : na)(n, r, Xi(this, "attr." + e, t)) : t == null ? (n.local ? $i : Qi)(n) : (n.local ? ta : ea)(n, r, t));
}
function aa(e, t) {
	return function(n) {
		this.setAttribute(e, t.call(this, n));
	};
}
function oa(e, t) {
	return function(n) {
		this.setAttributeNS(e.space, e.local, t.call(this, n));
	};
}
function sa(e, t) {
	var n, r;
	function i() {
		var i = t.apply(this, arguments);
		return i !== r && (n = (r = i) && oa(e, i)), n;
	}
	return i._value = t, i;
}
function ca(e, t) {
	var n, r;
	function i() {
		var i = t.apply(this, arguments);
		return i !== r && (n = (r = i) && aa(e, i)), n;
	}
	return i._value = t, i;
}
function la(e, t) {
	var n = "attr." + e;
	if (arguments.length < 2) return (n = this.tween(n)) && n._value;
	if (t == null) return this.tween(n, null);
	if (typeof t != "function") throw Error();
	var r = Ne(e);
	return this.tween(n, (r.local ? sa : ca)(r, t));
}
function ua(e, t) {
	return function() {
		Vi(this, e).delay = +t.apply(this, arguments);
	};
}
function da(e, t) {
	return t = +t, function() {
		Vi(this, e).delay = t;
	};
}
function fa(e) {
	var t = this._id;
	return arguments.length ? this.each((typeof e == "function" ? ua : da)(t, e)) : Ui(this.node(), t).delay;
}
function pa(e, t) {
	return function() {
		Hi(this, e).duration = +t.apply(this, arguments);
	};
}
function ma(e, t) {
	return t = +t, function() {
		Hi(this, e).duration = t;
	};
}
function ha(e) {
	var t = this._id;
	return arguments.length ? this.each((typeof e == "function" ? pa : ma)(t, e)) : Ui(this.node(), t).duration;
}
function ga(e, t) {
	if (typeof t != "function") throw Error();
	return function() {
		Hi(this, e).ease = t;
	};
}
function _a(e) {
	var t = this._id;
	return arguments.length ? this.each(ga(t, e)) : Ui(this.node(), t).ease;
}
function va(e, t) {
	return function() {
		var n = t.apply(this, arguments);
		if (typeof n != "function") throw Error();
		Hi(this, e).ease = n;
	};
}
function ya(e) {
	if (typeof e != "function") throw Error();
	return this.each(va(this._id, e));
}
function ba(e) {
	typeof e != "function" && (e = Ge(e));
	for (var t = this._groups, n = t.length, r = Array(n), i = 0; i < n; ++i) for (var a = t[i], o = a.length, s = r[i] = [], c, l = 0; l < o; ++l) (c = a[l]) && e.call(c, c.__data__, l, a) && s.push(c);
	return new Ya(r, this._parents, this._name, this._id);
}
function xa(e) {
	if (e._id !== this._id) throw Error();
	for (var t = this._groups, n = e._groups, r = t.length, i = n.length, a = Math.min(r, i), o = Array(r), s = 0; s < a; ++s) for (var c = t[s], l = n[s], u = c.length, d = o[s] = Array(u), f, p = 0; p < u; ++p) (f = c[p] || l[p]) && (d[p] = f);
	for (; s < r; ++s) o[s] = t[s];
	return new Ya(o, this._parents, this._name, this._id);
}
function Sa(e) {
	return (e + "").trim().split(/^|\s+/).every(function(e) {
		var t = e.indexOf(".");
		return t >= 0 && (e = e.slice(0, t)), !e || e === "start";
	});
}
function Ca(e, t, n) {
	var r, i, a = Sa(t) ? Vi : Hi;
	return function() {
		var o = a(this, e), s = o.on;
		s !== r && (i = (r = s).copy()).on(t, n), o.on = i;
	};
}
function wa(e, t) {
	var n = this._id;
	return arguments.length < 2 ? Ui(this.node(), n).on.on(e) : this.each(Ca(n, e, t));
}
function Ta(e) {
	return function() {
		var t = this.parentNode;
		for (var n in this.__transition) if (+n !== e) return;
		t && t.removeChild(this);
	};
}
function Ea() {
	return this.on("end.remove", Ta(this._id));
}
function Da(e) {
	var t = this._name, n = this._id;
	typeof e != "function" && (e = Re(e));
	for (var r = this._groups, i = r.length, a = Array(i), o = 0; o < i; ++o) for (var s = r[o], c = s.length, l = a[o] = Array(c), u, d, f = 0; f < c; ++f) (u = s[f]) && (d = e.call(u, u.__data__, f, s)) && ("__data__" in u && (d.__data__ = u.__data__), l[f] = d, Bi(l[f], t, n, f, l, Ui(u, n)));
	return new Ya(a, this._parents, t, n);
}
function Oa(e) {
	var t = this._name, n = this._id;
	typeof e != "function" && (e = He(e));
	for (var r = this._groups, i = r.length, a = [], o = [], s = 0; s < i; ++s) for (var c = r[s], l = c.length, u, d = 0; d < l; ++d) if (u = c[d]) {
		for (var f = e.call(u, u.__data__, d, c), p, m = Ui(u, n), h = 0, g = f.length; h < g; ++h) (p = f[h]) && Bi(p, t, n, h, f, m);
		a.push(f), o.push(u);
	}
	return new Ya(a, o, t, n);
}
var ka = kn.prototype.constructor;
function Aa() {
	return new ka(this._groups, this._parents);
}
function ja(e, t) {
	var n, r, i;
	return function() {
		var a = Ft(this, e), o = (this.style.removeProperty(e), Ft(this, e));
		return a === o ? null : a === n && o === r ? i : i = t(n = a, r = o);
	};
}
function Ma(e) {
	return function() {
		this.style.removeProperty(e);
	};
}
function Na(e, t, n) {
	var r, i = n + "", a;
	return function() {
		var o = Ft(this, e);
		return o === i ? null : o === r ? a : a = t(r = o, n);
	};
}
function Pa(e, t, n) {
	var r, i, a;
	return function() {
		var o = Ft(this, e), s = n(this), c = s + "";
		return s ?? (c = s = (this.style.removeProperty(e), Ft(this, e))), o === c ? null : o === r && c === i ? a : (i = c, a = t(r = o, s));
	};
}
function Fa(e, t) {
	var n, r, i, a = "style." + t, o = "end." + a, s;
	return function() {
		var c = Hi(this, e), l = c.on, u = c.value[a] == null ? s || (s = Ma(t)) : void 0;
		(l !== n || i !== u) && (r = (n = l).copy()).on(o, i = u), c.on = r;
	};
}
function Ia(e, t, n) {
	var r = (e += "") == "transform" ? ri : Zi;
	return t == null ? this.styleTween(e, ja(e, r)).on("end.style." + e, Ma(e)) : typeof t == "function" ? this.styleTween(e, Pa(e, r, Xi(this, "style." + e, t))).each(Fa(this._id, e)) : this.styleTween(e, Na(e, r, t), n).on("end.style." + e, null);
}
function La(e, t, n) {
	return function(r) {
		this.style.setProperty(e, t.call(this, r), n);
	};
}
function Ra(e, t, n) {
	var r, i;
	function a() {
		var a = t.apply(this, arguments);
		return a !== i && (r = (i = a) && La(e, a, n)), r;
	}
	return a._value = t, a;
}
function za(e, t, n) {
	var r = "style." + (e += "");
	if (arguments.length < 2) return (r = this.tween(r)) && r._value;
	if (t == null) return this.tween(r, null);
	if (typeof t != "function") throw Error();
	return this.tween(r, Ra(e, t, n ?? ""));
}
function Ba(e) {
	return function() {
		this.textContent = e;
	};
}
function Va(e) {
	return function() {
		var t = e(this);
		this.textContent = t ?? "";
	};
}
function Ha(e) {
	return this.tween("text", typeof e == "function" ? Va(Xi(this, "text", e)) : Ba(e == null ? "" : e + ""));
}
function Ua(e) {
	return function(t) {
		this.textContent = e.call(this, t);
	};
}
function Wa(e) {
	var t, n;
	function r() {
		var r = e.apply(this, arguments);
		return r !== n && (t = (n = r) && Ua(r)), t;
	}
	return r._value = e, r;
}
function Ga(e) {
	var t = "text";
	if (arguments.length < 1) return (t = this.tween(t)) && t._value;
	if (e == null) return this.tween(t, null);
	if (typeof e != "function") throw Error();
	return this.tween(t, Wa(e));
}
function Ka() {
	for (var e = this._name, t = this._id, n = Xa(), r = this._groups, i = r.length, a = 0; a < i; ++a) for (var o = r[a], s = o.length, c, l = 0; l < s; ++l) if (c = o[l]) {
		var u = Ui(c, t);
		Bi(c, e, n, l, o, {
			time: u.time + u.delay + u.duration,
			delay: 0,
			duration: u.duration,
			ease: u.ease
		});
	}
	return new Ya(r, this._parents, e, n);
}
function qa() {
	var e, t, n = this, r = n._id, i = n.size();
	return new Promise(function(a, o) {
		var s = { value: o }, c = { value: function() {
			--i === 0 && a();
		} };
		n.each(function() {
			var n = Hi(this, r), i = n.on;
			i !== e && (t = (e = i).copy(), t._.cancel.push(s), t._.interrupt.push(s), t._.end.push(c)), n.on = t;
		}), i === 0 && a();
	});
}
var Ja = 0;
function Ya(e, t, n, r) {
	this._groups = e, this._parents = t, this._name = n, this._id = r;
}
function Xa() {
	return ++Ja;
}
var Za = kn.prototype;
Ya.prototype = {
	constructor: Ya,
	select: Da,
	selectAll: Oa,
	selectChild: Za.selectChild,
	selectChildren: Za.selectChildren,
	filter: ba,
	merge: xa,
	selection: Aa,
	transition: Ka,
	call: Za.call,
	nodes: Za.nodes,
	node: Za.node,
	size: Za.size,
	empty: Za.empty,
	each: Za.each,
	on: wa,
	attr: ia,
	attrTween: la,
	style: Ia,
	styleTween: za,
	text: Ha,
	textTween: Ga,
	remove: Ea,
	tween: Yi,
	delay: fa,
	duration: ha,
	ease: _a,
	easeVarying: ya,
	end: qa,
	[Symbol.iterator]: Za[Symbol.iterator]
};
function Qa(e) {
	return ((e *= 2) <= 1 ? e * e * e : (e -= 2) * e * e + 2) / 2;
}
var $a = {
	time: null,
	delay: 0,
	duration: 250,
	ease: Qa
};
function eo(e, t) {
	for (var n; !(n = e.__transition) || !(n = n[t]);) if (!(e = e.parentNode)) throw Error(`transition ${t} not found`);
	return n;
}
function to(e) {
	var t, n;
	e instanceof Ya ? (t = e._id, e = e._name) : (t = Xa(), (n = $a).time = xi(), e = e == null ? null : e + "");
	for (var r = this._groups, i = r.length, a = 0; a < i; ++a) for (var o = r[a], s = o.length, c, l = 0; l < s; ++l) (c = o[l]) && Bi(c, e, t, l, o, n || eo(c, t));
	return new Ya(r, this._parents, e, t);
}
kn.prototype.interrupt = Ki, kn.prototype.transition = to;
var no = (e) => () => e;
function ro(e, { sourceEvent: t, target: n, transform: r, dispatch: i }) {
	Object.defineProperties(this, {
		type: {
			value: e,
			enumerable: !0,
			configurable: !0
		},
		sourceEvent: {
			value: t,
			enumerable: !0,
			configurable: !0
		},
		target: {
			value: n,
			enumerable: !0,
			configurable: !0
		},
		transform: {
			value: r,
			enumerable: !0,
			configurable: !0
		},
		_: { value: i }
	});
}
function io(e, t, n) {
	this.k = e, this.x = t, this.y = n;
}
io.prototype = {
	constructor: io,
	scale: function(e) {
		return e === 1 ? this : new io(this.k * e, this.x, this.y);
	},
	translate: function(e, t) {
		return e === 0 & t === 0 ? this : new io(this.k, this.x + this.k * e, this.y + this.k * t);
	},
	apply: function(e) {
		return [e[0] * this.k + this.x, e[1] * this.k + this.y];
	},
	applyX: function(e) {
		return e * this.k + this.x;
	},
	applyY: function(e) {
		return e * this.k + this.y;
	},
	invert: function(e) {
		return [(e[0] - this.x) / this.k, (e[1] - this.y) / this.k];
	},
	invertX: function(e) {
		return (e - this.x) / this.k;
	},
	invertY: function(e) {
		return (e - this.y) / this.k;
	},
	rescaleX: function(e) {
		return e.copy().domain(e.range().map(this.invertX, this).map(e.invert, e));
	},
	rescaleY: function(e) {
		return e.copy().domain(e.range().map(this.invertY, this).map(e.invert, e));
	},
	toString: function() {
		return "translate(" + this.x + "," + this.y + ") scale(" + this.k + ")";
	}
};
var ao = new io(1, 0, 0);
io.prototype;
function oo(e) {
	e.stopImmediatePropagation();
}
function so(e) {
	e.preventDefault(), e.stopImmediatePropagation();
}
function co(e) {
	return (!e.ctrlKey || e.type === "wheel") && !e.button;
}
function lo() {
	var e = this;
	return e instanceof SVGElement ? (e = e.ownerSVGElement || e, e.hasAttribute("viewBox") ? (e = e.viewBox.baseVal, [[e.x, e.y], [e.x + e.width, e.y + e.height]]) : [[0, 0], [e.width.baseVal.value, e.height.baseVal.value]]) : [[0, 0], [e.clientWidth, e.clientHeight]];
}
function uo() {
	return this.__zoom || ao;
}
function fo(e) {
	return -e.deltaY * (e.deltaMode === 1 ? .05 : e.deltaMode ? 1 : .002) * (e.ctrlKey ? 10 : 1);
}
function po() {
	return navigator.maxTouchPoints || "ontouchstart" in this;
}
function mo(e, t, n) {
	var r = e.invertX(t[0][0]) - n[0][0], i = e.invertX(t[1][0]) - n[1][0], a = e.invertY(t[0][1]) - n[0][1], o = e.invertY(t[1][1]) - n[1][1];
	return e.translate(i > r ? (r + i) / 2 : Math.min(0, r) || Math.max(0, i), o > a ? (a + o) / 2 : Math.min(0, a) || Math.max(0, o));
}
function ho() {
	var e = co, t = lo, n = mo, r = fo, i = po, a = [0, Infinity], o = [[-Infinity, -Infinity], [Infinity, Infinity]], s = 250, c = li, l = Ee("start", "zoom", "end"), u, d, f, p = 500, m = 150, h = 0, g = 10;
	function _(e) {
		e.property("__zoom", uo).on("wheel.zoom", w, { passive: !1 }).on("mousedown.zoom", T).on("dblclick.zoom", E).filter(i).on("touchstart.zoom", D).on("touchmove.zoom", O).on("touchend.zoom touchcancel.zoom", k).style("-webkit-tap-highlight-color", "rgba(0,0,0,0)");
	}
	_.transform = function(e, t, n, r) {
		var i = e.selection ? e.selection() : e;
		i.property("__zoom", uo), e === i ? i.interrupt().each(function() {
			S(this, arguments).event(r).start().zoom(null, typeof t == "function" ? t.apply(this, arguments) : t).end();
		}) : x(e, t, n, r);
	}, _.scaleBy = function(e, t, n, r) {
		_.scaleTo(e, function() {
			return this.__zoom.k * (typeof t == "function" ? t.apply(this, arguments) : t);
		}, n, r);
	}, _.scaleTo = function(e, r, i, a) {
		_.transform(e, function() {
			var e = t.apply(this, arguments), a = this.__zoom, s = i == null ? b(e) : typeof i == "function" ? i.apply(this, arguments) : i, c = a.invert(s), l = typeof r == "function" ? r.apply(this, arguments) : r;
			return n(y(v(a, l), s, c), e, o);
		}, i, a);
	}, _.translateBy = function(e, r, i, a) {
		_.transform(e, function() {
			return n(this.__zoom.translate(typeof r == "function" ? r.apply(this, arguments) : r, typeof i == "function" ? i.apply(this, arguments) : i), t.apply(this, arguments), o);
		}, null, a);
	}, _.translateTo = function(e, r, i, a, s) {
		_.transform(e, function() {
			var e = t.apply(this, arguments), s = this.__zoom, c = a == null ? b(e) : typeof a == "function" ? a.apply(this, arguments) : a;
			return n(ao.translate(c[0], c[1]).scale(s.k).translate(typeof r == "function" ? -r.apply(this, arguments) : -r, typeof i == "function" ? -i.apply(this, arguments) : -i), e, o);
		}, a, s);
	};
	function v(e, t) {
		return t = Math.max(a[0], Math.min(a[1], t)), t === e.k ? e : new io(t, e.x, e.y);
	}
	function y(e, t, n) {
		var r = t[0] - n[0] * e.k, i = t[1] - n[1] * e.k;
		return r === e.x && i === e.y ? e : new io(e.k, r, i);
	}
	function b(e) {
		return [(+e[0][0] + +e[1][0]) / 2, (+e[0][1] + +e[1][1]) / 2];
	}
	function x(e, n, r, i) {
		e.on("start.zoom", function() {
			S(this, arguments).event(i).start();
		}).on("interrupt.zoom end.zoom", function() {
			S(this, arguments).event(i).end();
		}).tween("zoom", function() {
			var e = this, a = arguments, o = S(e, a).event(i), s = t.apply(e, a), l = r == null ? b(s) : typeof r == "function" ? r.apply(e, a) : r, u = Math.max(s[1][0] - s[0][0], s[1][1] - s[0][1]), d = e.__zoom, f = typeof n == "function" ? n.apply(e, a) : n, p = c(d.invert(l).concat(u / d.k), f.invert(l).concat(u / f.k));
			return function(e) {
				if (e === 1) e = f;
				else {
					var t = p(e), n = u / t[2];
					e = new io(n, l[0] - t[0] * n, l[1] - t[1] * n);
				}
				o.zoom(null, e);
			};
		});
	}
	function S(e, t, n) {
		return !n && e.__zooming || new C(e, t);
	}
	function C(e, n) {
		this.that = e, this.args = n, this.active = 0, this.sourceEvent = null, this.extent = t.apply(e, n), this.taps = 0;
	}
	C.prototype = {
		event: function(e) {
			return e && (this.sourceEvent = e), this;
		},
		start: function() {
			return ++this.active === 1 && (this.that.__zooming = this, this.emit("start")), this;
		},
		zoom: function(e, t) {
			return this.mouse && e !== "mouse" && (this.mouse[1] = t.invert(this.mouse[0])), this.touch0 && e !== "touch" && (this.touch0[1] = t.invert(this.touch0[0])), this.touch1 && e !== "touch" && (this.touch1[1] = t.invert(this.touch1[0])), this.that.__zoom = t, this.emit("zoom"), this;
		},
		end: function() {
			return --this.active === 0 && (delete this.that.__zooming, this.emit("end")), this;
		},
		emit: function(e) {
			var t = jn(this.that).datum();
			l.call(e, this.that, new ro(e, {
				sourceEvent: this.sourceEvent,
				target: _,
				type: e,
				transform: this.that.__zoom,
				dispatch: l
			}), t);
		}
	};
	function w(t, ...i) {
		if (!e.apply(this, arguments)) return;
		var s = S(this, i).event(t), c = this.__zoom, l = Math.max(a[0], Math.min(a[1], c.k * 2 ** r.apply(this, arguments))), u = Nn(t);
		if (s.wheel) (s.mouse[0][0] !== u[0] || s.mouse[0][1] !== u[1]) && (s.mouse[1] = c.invert(s.mouse[0] = u)), clearTimeout(s.wheel);
		else if (c.k === l) return;
		else s.mouse = [u, c.invert(u)], Gi(this), s.start();
		so(t), s.wheel = setTimeout(d, m), s.zoom("mouse", n(y(v(c, l), s.mouse[0], s.mouse[1]), s.extent, o));
		function d() {
			s.wheel = null, s.end();
		}
	}
	function T(t, ...r) {
		if (f || !e.apply(this, arguments)) return;
		var i = t.currentTarget, a = S(this, r, !0).event(t), s = jn(t.view).on("mousemove.zoom", d, !0).on("mouseup.zoom", p, !0), c = Nn(t, i), l = t.clientX, u = t.clientY;
		Rn(t.view), oo(t), a.mouse = [c, this.__zoom.invert(c)], Gi(this), a.start();
		function d(e) {
			if (so(e), !a.moved) {
				var t = e.clientX - l, r = e.clientY - u;
				a.moved = t * t + r * r > h;
			}
			a.event(e).zoom("mouse", n(y(a.that.__zoom, a.mouse[0] = Nn(e, i), a.mouse[1]), a.extent, o));
		}
		function p(e) {
			s.on("mousemove.zoom mouseup.zoom", null), zn(e.view, a.moved), so(e), a.event(e).end();
		}
	}
	function E(r, ...i) {
		if (e.apply(this, arguments)) {
			var a = this.__zoom, c = Nn(r.changedTouches ? r.changedTouches[0] : r, this), l = a.invert(c), u = a.k * (r.shiftKey ? .5 : 2), d = n(y(v(a, u), c, l), t.apply(this, i), o);
			so(r), s > 0 ? jn(this).transition().duration(s).call(x, d, c, r) : jn(this).call(_.transform, d, c, r);
		}
	}
	function D(t, ...n) {
		if (e.apply(this, arguments)) {
			var r = t.touches, i = r.length, a = S(this, n, t.changedTouches.length === i).event(t), o, s, c, l;
			for (oo(t), s = 0; s < i; ++s) c = r[s], l = Nn(c, this), l = [
				l,
				this.__zoom.invert(l),
				c.identifier
			], a.touch0 ? !a.touch1 && a.touch0[2] !== l[2] && (a.touch1 = l, a.taps = 0) : (a.touch0 = l, o = !0, a.taps = 1 + !!u);
			u && (u = clearTimeout(u)), o && (a.taps < 2 && (d = l[0], u = setTimeout(function() {
				u = null;
			}, p)), Gi(this), a.start());
		}
	}
	function O(e, ...t) {
		if (this.__zooming) {
			var r = S(this, t).event(e), i = e.changedTouches, a = i.length, s, c, l, u;
			for (so(e), s = 0; s < a; ++s) c = i[s], l = Nn(c, this), r.touch0 && r.touch0[2] === c.identifier ? r.touch0[0] = l : r.touch1 && r.touch1[2] === c.identifier && (r.touch1[0] = l);
			if (c = r.that.__zoom, r.touch1) {
				var d = r.touch0[0], f = r.touch0[1], p = r.touch1[0], m = r.touch1[1], h = (h = p[0] - d[0]) * h + (h = p[1] - d[1]) * h, g = (g = m[0] - f[0]) * g + (g = m[1] - f[1]) * g;
				c = v(c, Math.sqrt(h / g)), l = [(d[0] + p[0]) / 2, (d[1] + p[1]) / 2], u = [(f[0] + m[0]) / 2, (f[1] + m[1]) / 2];
			} else if (r.touch0) l = r.touch0[0], u = r.touch0[1];
			else return;
			r.zoom("touch", n(y(c, l, u), r.extent, o));
		}
	}
	function k(e, ...t) {
		if (this.__zooming) {
			var n = S(this, t).event(e), r = e.changedTouches, i = r.length, a, o;
			for (oo(e), f && clearTimeout(f), f = setTimeout(function() {
				f = null;
			}, p), a = 0; a < i; ++a) o = r[a], n.touch0 && n.touch0[2] === o.identifier ? delete n.touch0 : n.touch1 && n.touch1[2] === o.identifier && delete n.touch1;
			if (n.touch1 && !n.touch0 && (n.touch0 = n.touch1, delete n.touch1), n.touch0) n.touch0[1] = this.__zoom.invert(n.touch0[0]);
			else if (n.end(), n.taps === 2 && (o = Nn(o, this), Math.hypot(d[0] - o[0], d[1] - o[1]) < g)) {
				var s = jn(this).on("dblclick.zoom");
				s && s.apply(this, arguments);
			}
		}
	}
	return _.wheelDelta = function(e) {
		return arguments.length ? (r = typeof e == "function" ? e : no(+e), _) : r;
	}, _.filter = function(t) {
		return arguments.length ? (e = typeof t == "function" ? t : no(!!t), _) : e;
	}, _.touchable = function(e) {
		return arguments.length ? (i = typeof e == "function" ? e : no(!!e), _) : i;
	}, _.extent = function(e) {
		return arguments.length ? (t = typeof e == "function" ? e : no([[+e[0][0], +e[0][1]], [+e[1][0], +e[1][1]]]), _) : t;
	}, _.scaleExtent = function(e) {
		return arguments.length ? (a[0] = +e[0], a[1] = +e[1], _) : [a[0], a[1]];
	}, _.translateExtent = function(e) {
		return arguments.length ? (o[0][0] = +e[0][0], o[1][0] = +e[1][0], o[0][1] = +e[0][1], o[1][1] = +e[1][1], _) : [[o[0][0], o[0][1]], [o[1][0], o[1][1]]];
	}, _.constrain = function(e) {
		return arguments.length ? (n = e, _) : n;
	}, _.duration = function(e) {
		return arguments.length ? (s = +e, _) : s;
	}, _.interpolate = function(e) {
		return arguments.length ? (c = e, _) : c;
	}, _.on = function() {
		var e = l.on.apply(l, arguments);
		return e === l ? _ : e;
	}, _.clickDistance = function(e) {
		return arguments.length ? (h = (e = +e) * e, _) : Math.sqrt(h);
	}, _.tapDistance = function(e) {
		return arguments.length ? (g = +e, _) : g;
	}, _;
}
var J = /* @__PURE__ */ ((e) => (e.Left = "left", e.Top = "top", e.Right = "right", e.Bottom = "bottom", e))(J || {}), go = /* @__PURE__ */ ((e) => (e.Partial = "partial", e.Full = "full", e))(go || {}), _o = /* @__PURE__ */ ((e) => (e.Bezier = "default", e.SimpleBezier = "simple-bezier", e.Straight = "straight", e.Step = "step", e.SmoothStep = "smoothstep", e))(_o || {}), vo = /* @__PURE__ */ ((e) => (e.Strict = "strict", e.Loose = "loose", e))(vo || {}), yo = /* @__PURE__ */ ((e) => (e.Arrow = "arrow", e.ArrowClosed = "arrowclosed", e))(yo || {}), bo = /* @__PURE__ */ ((e) => (e.Free = "free", e.Vertical = "vertical", e.Horizontal = "horizontal", e))(bo || {}), xo = /* @__PURE__ */ ((e) => (e.TopLeft = "top-left", e.TopCenter = "top-center", e.TopRight = "top-right", e.BottomLeft = "bottom-left", e.BottomCenter = "bottom-center", e.BottomRight = "bottom-right", e))(xo || {}), So = [
	"INPUT",
	"SELECT",
	"TEXTAREA"
], Co = typeof document < "u" ? document : null;
function wo(e) {
	let t = e.composedPath?.call(e)?.[0] || e.target, n = typeof t?.hasAttribute == "function" && t.hasAttribute("contenteditable"), r = typeof t?.closest == "function" ? t.closest(".nokey") : null;
	return So.includes(t?.nodeName) || n || !!r;
}
function To(e) {
	return e.ctrlKey || e.metaKey || e.shiftKey || e.altKey;
}
function Eo(e, t, n, r) {
	let i = t.replace("+", "\n").replace("\n\n", "\n+").split("\n").map((e) => e.trim().toLowerCase());
	if (i.length === 1) return e.toLowerCase() === t.toLowerCase();
	r || n.add(e.toLowerCase());
	let a = i.every((e, t) => n.has(e) && Array.from(n.values())[t] === i[t]);
	return r && n.delete(e.toLowerCase()), a;
}
function Do(e, t) {
	return (n) => {
		if (!n.code && !n.key) return !1;
		let r = Oo(n.code, e);
		return Array.isArray(e) ? e.some((e) => Eo(n[r], e, t, n.type === "keyup")) : Eo(n[r], e, t, n.type === "keyup");
	};
}
function Oo(e, t) {
	return t.includes(e) ? "code" : "key";
}
function ko(e, t) {
	let r = n(() => H(t?.target) ?? Co), i = z(H(e) === !0), a = !1, o = /* @__PURE__ */ new Set(), s = l(H(e));
	K(() => H(e), (e, t) => {
		typeof t == "boolean" && typeof e != "boolean" && c(), s = l(e);
	}, { immediate: !0 }), be(["blur", "contextmenu"], c), Se((...e) => s(...e), (e) => {
		let n = H(t?.actInsideInputWithModifier) ?? !0, r = H(t?.preventDefault) ?? !1;
		if (a = To(e), (!a || a && !n) && wo(e)) return;
		let o = e.composedPath?.call(e)?.[0] || e.target, s = o?.nodeName === "BUTTON" || o?.nodeName === "A";
		r || !a && s || e.preventDefault(), i.value = !0;
	}, {
		eventName: "keydown",
		target: r
	}), Se((...e) => s(...e), (e) => {
		let n = H(t?.actInsideInputWithModifier) ?? !0;
		if (i.value) {
			if ((!a || a && !n) && wo(e)) return;
			a = !1, i.value = !1;
		}
	}, {
		eventName: "keyup",
		target: r
	});
	function c() {
		a = !1, o.clear(), i.value = H(e) === !0;
	}
	function l(e) {
		return e === null ? (c(), () => !1) : typeof e == "boolean" ? (c(), i.value = e, () => !1) : Array.isArray(e) || typeof e == "string" ? Do(e, o) : e;
	}
	return i;
}
var Ao = "vue-flow__node-desc", jo = "vue-flow__edge-desc", Mo = "vue-flow__aria-live", No = [
	"Enter",
	" ",
	"Escape"
], Po = {
	ArrowUp: {
		x: 0,
		y: -1
	},
	ArrowDown: {
		x: 0,
		y: 1
	},
	ArrowLeft: {
		x: -1,
		y: 0
	},
	ArrowRight: {
		x: 1,
		y: 0
	}
};
function Fo(e) {
	return {
		...e.computedPosition || {
			x: 0,
			y: 0
		},
		width: e.dimensions.width || 0,
		height: e.dimensions.height || 0
	};
}
function Io(e, t) {
	let n = Math.max(0, Math.min(e.x + e.width, t.x + t.width) - Math.max(e.x, t.x)), r = Math.max(0, Math.min(e.y + e.height, t.y + t.height) - Math.max(e.y, t.y));
	return Math.ceil(n * r);
}
function Lo(e) {
	return {
		width: e.offsetWidth,
		height: e.offsetHeight
	};
}
function Ro(e, t = 0, n = 1) {
	return Math.min(Math.max(e, t), n);
}
function zo(e, t) {
	return {
		x: Ro(e.x, t[0][0], t[1][0]),
		y: Ro(e.y, t[0][1], t[1][1])
	};
}
function Bo(e) {
	let t = e.getRootNode();
	return "elementFromPoint" in t ? t : window.document;
}
function Vo(e) {
	return e && typeof e == "object" && "id" in e && "source" in e && "target" in e;
}
function Ho(e) {
	return e && typeof e == "object" && "id" in e && "position" in e && !Vo(e);
}
function Uo(e) {
	return Ho(e) && "computedPosition" in e;
}
function Wo(e) {
	return !Number.isNaN(e) && Number.isFinite(e);
}
function Go(e) {
	return Wo(e.width) && Wo(e.height) && Wo(e.x) && Wo(e.y);
}
function Ko(e, t, n) {
	let r = {
		id: e.id.toString(),
		type: e.type ?? "default",
		dimensions: y({
			width: 0,
			height: 0
		}),
		computedPosition: y({
			z: 0,
			...e.position
		}),
		handleBounds: {
			source: [],
			target: []
		},
		draggable: void 0,
		selectable: void 0,
		connectable: void 0,
		focusable: void 0,
		selected: !1,
		dragging: !1,
		resizing: !1,
		initialized: !1,
		isParent: !1,
		position: {
			x: 0,
			y: 0
		},
		data: Q(e.data) ? e.data : {},
		events: y(Q(e.events) ? e.events : {})
	};
	return Object.assign(t ?? r, e, {
		id: e.id.toString(),
		parentNode: n
	});
}
function qo(e, t, n) {
	let r = {
		id: e.id.toString(),
		type: e.type ?? t?.type ?? "default",
		source: e.source.toString(),
		target: e.target.toString(),
		sourceHandle: e.sourceHandle?.toString(),
		targetHandle: e.targetHandle?.toString(),
		updatable: e.updatable ?? n?.updatable,
		selectable: e.selectable ?? n?.selectable,
		focusable: e.focusable ?? n?.focusable,
		data: Q(e.data) ? e.data : {},
		events: y(Q(e.events) ? e.events : {}),
		label: e.label ?? "",
		interactionWidth: e.interactionWidth ?? n?.interactionWidth,
		...n ?? {}
	};
	return Object.assign(t ?? r, e, { id: e.id.toString() });
}
function Jo(e, t, n, r) {
	let i = typeof e == "string" ? e : e.id, a = /* @__PURE__ */ new Set(), o = r === "source" ? "target" : "source";
	for (let e of n) e[o] === i && a.add(e[r]);
	return t.filter((e) => a.has(e.id));
}
function Yo(...e) {
	if (e.length === 3) {
		let [t, n, r] = e;
		return Jo(t, n, r, "target");
	}
	let [t, n] = e, r = typeof t == "string" ? t : t.id;
	return n.filter((e) => Vo(e) && e.source === r).map((e) => n.find((t) => Ho(t) && t.id === e.target));
}
function Xo(...e) {
	if (e.length === 3) {
		let [t, n, r] = e;
		return Jo(t, n, r, "source");
	}
	let [t, n] = e, r = typeof t == "string" ? t : t.id;
	return n.filter((e) => Vo(e) && e.target === r).map((e) => n.find((t) => Ho(t) && t.id === e.source));
}
function Zo({ source: e, sourceHandle: t, target: n, targetHandle: r }) {
	return `vueflow__edge-${e}${t ?? ""}-${n}${r ?? ""}`;
}
function Qo(e, t) {
	return t.some((t) => Vo(t) && t.source === e.source && t.target === e.target && (t.sourceHandle === e.sourceHandle || !t.sourceHandle && !e.sourceHandle) && (t.targetHandle === e.targetHandle || !t.targetHandle && !e.targetHandle));
}
function $o({ x: e, y: t }, { x: n, y: r, zoom: i }) {
	return {
		x: e * i + n,
		y: t * i + r
	};
}
function es({ x: e, y: t }, { x: n, y: r, zoom: i }, a = !1, o = [1, 1]) {
	let s = {
		x: (e - n) / i,
		y: (t - r) / i
	};
	return a ? Ws(s, o) : s;
}
function ts(e, t) {
	return {
		x: Math.min(e.x, t.x),
		y: Math.min(e.y, t.y),
		x2: Math.max(e.x2, t.x2),
		y2: Math.max(e.y2, t.y2)
	};
}
function ns({ x: e, y: t, width: n, height: r }) {
	return {
		x: e,
		y: t,
		x2: e + n,
		y2: t + r
	};
}
function rs({ x: e, y: t, x2: n, y2: r }) {
	return {
		x: e,
		y: t,
		width: n - e,
		height: r - t
	};
}
function is(e, t) {
	return rs(ts(ns(e), ns(t)));
}
function as(e) {
	let t = {
		x: Infinity,
		y: Infinity,
		x2: -Infinity,
		y2: -Infinity
	};
	for (let n = 0; n < e.length; n++) {
		let r = e[n];
		t = ts(t, ns({
			...r.computedPosition,
			...r.dimensions
		}));
	}
	return rs(t);
}
function os(e, t, n = {
	x: 0,
	y: 0,
	zoom: 1
}, r = !1, i = !1) {
	let a = {
		...es(t, n),
		width: t.width / n.zoom,
		height: t.height / n.zoom
	}, o = [];
	for (let t of e) {
		let { dimensions: e, selectable: n = !0, hidden: s = !1 } = t, c = e.width ?? t.width ?? null, l = e.height ?? t.height ?? null;
		if (i && !n || s) continue;
		let u = Io(a, Fo(t)), d = c === null || l === null, f = r && u > 0, p = (c ?? 0) * (l ?? 0);
		(d || f || u >= p || t.dragging) && o.push(t);
	}
	return o;
}
function ss(e, t) {
	let n = /* @__PURE__ */ new Set();
	if (typeof e == "string") n.add(e);
	else if (e.length >= 1) for (let t of e) n.add(t.id);
	return t.filter((e) => n.has(e.source) || n.has(e.target));
}
function cs(e, t) {
	if (typeof e == "number") return Math.floor((t - t / (1 + e)) * .5);
	if (typeof e == "string" && e.endsWith("px")) {
		let t = Number.parseFloat(e);
		if (!Number.isNaN(t)) return Math.floor(t);
	}
	if (typeof e == "string" && e.endsWith("%")) {
		let n = Number.parseFloat(e);
		if (!Number.isNaN(n)) return Math.floor(t * n * .01);
	}
	return rc(`The padding value "${e}" is invalid. Please provide a number or a string with a valid unit (px or %).`), 0;
}
function ls(e, t, n) {
	if (typeof e == "string" || typeof e == "number") {
		let r = cs(e, n), i = cs(e, t);
		return {
			top: r,
			right: i,
			bottom: r,
			left: i,
			x: i * 2,
			y: r * 2
		};
	}
	if (typeof e == "object") {
		let r = cs(e.top ?? e.y ?? 0, n), i = cs(e.bottom ?? e.y ?? 0, n), a = cs(e.left ?? e.x ?? 0, t), o = cs(e.right ?? e.x ?? 0, t);
		return {
			top: r,
			right: o,
			bottom: i,
			left: a,
			x: a + o,
			y: r + i
		};
	}
	return {
		top: 0,
		right: 0,
		bottom: 0,
		left: 0,
		x: 0,
		y: 0
	};
}
function us(e, t, n, r, i, a) {
	let { x: o, y: s } = $o(e, {
		x: t,
		y: n,
		zoom: r
	}), { x: c, y: l } = $o({
		x: e.x + e.width,
		y: e.y + e.height
	}, {
		x: t,
		y: n,
		zoom: r
	}), u = i - c, d = a - l;
	return {
		left: Math.floor(o),
		top: Math.floor(s),
		right: Math.floor(u),
		bottom: Math.floor(d)
	};
}
function ds(e, t, n, r, i, a = .1) {
	let o = ls(a, t, n), s = (t - o.x) / e.width, c = (n - o.y) / e.height, l = Ro(Math.min(s, c), r, i), u = e.x + e.width / 2, d = e.y + e.height / 2, f = t / 2 - u * l, p = n / 2 - d * l, m = us(e, f, p, l, t, n), h = {
		left: Math.min(m.left - o.left, 0),
		top: Math.min(m.top - o.top, 0),
		right: Math.min(m.right - o.right, 0),
		bottom: Math.min(m.bottom - o.bottom, 0)
	};
	return {
		x: f - h.left + h.right,
		y: p - h.top + h.bottom,
		zoom: l
	};
}
function fs(e, t) {
	return {
		x: t.x + e.x,
		y: t.y + e.y,
		z: (e.z > t.z ? e.z : t.z) + 1
	};
}
function ps(e, t) {
	if (!e.parentNode) return !1;
	let n = t.get(e.parentNode);
	return n ? n.selected ? !0 : ps(n, t) : !1;
}
function ms(e, t) {
	return e === void 0 ? "" : typeof e == "string" ? e : `${t ? `${t}__` : ""}${Object.keys(e).sort().map((t) => `${t}=${e[t]}`).join("&")}`;
}
function hs(e) {
	let t = e.ctrlKey && Hs() ? 10 : 1;
	return -e.deltaY * (e.deltaMode === 1 ? .05 : e.deltaMode ? 1 : .002) * t;
}
function gs(e, t, n) {
	return e < t ? Ro(Math.abs(e - t), 1, t) / t : e > n ? -Ro(Math.abs(e - n), 1, t) / t : 0;
}
function _s(e, t, n = 15, r = 40) {
	return [gs(e.x, r, t.width - r) * n, gs(e.y, r, t.height - r) * n];
}
function vs(e, t) {
	if (t) {
		let n = e.position.x + e.dimensions.width - t.dimensions.width, r = e.position.y + e.dimensions.height - t.dimensions.height;
		if (n > 0 || r > 0 || e.position.x < 0 || e.position.y < 0) {
			let i = {};
			if (typeof t.style == "function" ? i = { ...t.style(t) } : t.style && (i = { ...t.style }), i.width = i.width ?? `${t.dimensions.width}px`, i.height = i.height ?? `${t.dimensions.height}px`, n > 0) {
				if (typeof i.width == "string") {
					let e = Number(i.width.replace("px", ""));
					i.width = `${e + n}px`;
				} else i.width += n;
			}
			if (r > 0) {
				if (typeof i.height == "string") {
					let e = Number(i.height.replace("px", ""));
					i.height = `${e + r}px`;
				} else i.height += r;
			}
			if (e.position.x < 0) {
				let n = Math.abs(e.position.x);
				if (t.position.x = t.position.x - n, typeof i.width == "string") {
					let e = Number(i.width.replace("px", ""));
					i.width = `${e + n}px`;
				} else i.width += n;
				e.position.x = 0;
			}
			if (e.position.y < 0) {
				let n = Math.abs(e.position.y);
				if (t.position.y = t.position.y - n, typeof i.height == "string") {
					let e = Number(i.height.replace("px", ""));
					i.height = `${e + n}px`;
				} else i.height += n;
				e.position.y = 0;
			}
			t.dimensions.width = Number(i.width.toString().replace("px", "")), t.dimensions.height = Number(i.height.toString().replace("px", "")), t.style = typeof t.style == "function" ? (e) => {
				let n = t.style;
				return {
					...n(e),
					...i
				};
			} : {
				...t.style,
				...i
			};
		}
	}
}
function ys(e, t) {
	let n = e.filter((e) => e.type === "add" || e.type === "remove");
	for (let e of n) if (e.type === "add") t.findIndex((t) => t.id === e.item.id) === -1 && t.push(e.item);
	else if (e.type === "remove") {
		let n = t.findIndex((t) => t.id === e.id);
		n !== -1 && t.splice(n, 1);
	}
	let r = t.map((e) => e.id);
	for (let n of t) for (let i of e) if (i.id === n.id) switch (i.type) {
		case "select":
			n.selected = i.selected;
			break;
		case "position":
			if (Uo(n) && (i.position !== void 0 && (n.position = i.position), i.dragging !== void 0 && (n.dragging = i.dragging), n.expandParent && n.parentNode)) {
				let e = t[r.indexOf(n.parentNode)];
				e && Uo(e) && vs(n, e);
			}
			break;
		case "dimensions": if (Uo(n) && (i.dimensions !== void 0 && (n.dimensions = i.dimensions), i.updateStyle !== void 0 && i.updateStyle && (n.style = {
			...n.style || {},
			width: `${i.dimensions?.width}px`,
			height: `${i.dimensions?.height}px`
		}), i.resizing !== void 0 && (n.resizing = i.resizing), n.expandParent && n.parentNode)) {
			let e = t[r.indexOf(n.parentNode)];
			e && Uo(e) && (e.dimensions.width && e.dimensions.height ? vs(n, e) : x(() => {
				vs(n, e);
			}));
		}
	}
	return t;
}
function bs(e, t) {
	return {
		id: e,
		type: "select",
		selected: t
	};
}
function xs(e) {
	return {
		item: e,
		type: "add"
	};
}
function Ss(e) {
	return {
		id: e,
		type: "remove"
	};
}
function Cs(e, t, n, r, i) {
	return {
		id: e,
		source: t,
		target: n,
		sourceHandle: r || null,
		targetHandle: i || null,
		type: "remove"
	};
}
function ws(e, t = /* @__PURE__ */ new Set(), n = !1) {
	let r = [];
	for (let [i, a] of e) {
		let e = t.has(i);
		(a.selected !== void 0 || e) && a.selected !== e && (n && (a.selected = e), r.push(bs(a.id, e)));
	}
	return r;
}
var Ts = () => {};
function Y(e) {
	let t = /* @__PURE__ */ new Set(), n = Ts, r = () => !1, i = () => t.size > 0 || r(), a = (e) => {
		n = e;
	}, o = () => {
		n = Ts;
	}, s = (e) => {
		r = e;
	}, c = () => {
		r = () => !1;
	}, l = (e) => {
		t.delete(e);
	};
	return {
		on: (e) => {
			t.add(e);
			let n = () => l(e);
			return ne(n), { off: n };
		},
		off: l,
		trigger: (r) => {
			let a = [n];
			return i() ? a.push(...t) : e && a.push(e), Promise.allSettled(a.map((e) => e(r)));
		},
		hasListeners: i,
		listeners: t,
		setEmitter: a,
		removeEmitter: o,
		setHasEmitListeners: s,
		removeHasEmitListeners: c
	};
}
function Es(e, t, n) {
	let r = e;
	do {
		if (r && r.matches(t)) return !0;
		if (r === n) return !1;
		r = r.parentElement;
	} while (r);
	return !1;
}
function Ds(e, t, n, r) {
	let i = /* @__PURE__ */ new Map();
	for (let [a, o] of e) (o.selected || o.id === r) && (!o.parentNode || !ps(o, e)) && (o.draggable || t && o.draggable === void 0) && e.get(a) && i.set(a, {
		id: o.id,
		position: o.position || {
			x: 0,
			y: 0
		},
		distance: {
			x: n.x - o.computedPosition?.x || 0,
			y: n.y - o.computedPosition?.y || 0
		},
		from: {
			x: o.computedPosition.x,
			y: o.computedPosition.y
		},
		extent: o.extent,
		parentNode: o.parentNode,
		dimensions: { ...o.dimensions },
		expandParent: o.expandParent
	});
	return Array.from(i.values());
}
function Os({ id: e, dragItems: t, findNode: n }) {
	let r = [];
	for (let e of t) {
		let t = n(e.id);
		t && r.push(t);
	}
	return [e ? r.find((t) => t.id === e) : r[0], r];
}
function ks(e) {
	if (Array.isArray(e)) switch (e.length) {
		case 1: return [
			e[0],
			e[0],
			e[0],
			e[0]
		];
		case 2: return [
			e[0],
			e[1],
			e[0],
			e[1]
		];
		case 3: return [
			e[0],
			e[1],
			e[2],
			e[1]
		];
		case 4: return e;
		default: return [
			0,
			0,
			0,
			0
		];
	}
	return [
		e,
		e,
		e,
		e
	];
}
function As(e, t, n) {
	let [r, i, a, o] = typeof e == "string" ? [
		0,
		0,
		0,
		0
	] : ks(e.padding);
	return n && n.computedPosition.x !== void 0 && n.computedPosition.y !== void 0 && n.dimensions.width !== void 0 && n.dimensions.height !== void 0 ? [[n.computedPosition.x + o, n.computedPosition.y + r], [n.computedPosition.x + n.dimensions.width - i, n.computedPosition.y + n.dimensions.height - a]] : !1;
}
function js(e, t, n, r) {
	let i = e.extent || n;
	if ((i === "parent" || !Array.isArray(i) && i?.range === "parent") && !e.expandParent) {
		if (e.parentNode && r && e.dimensions.width && e.dimensions.height) {
			let t = As(i, e, r);
			t && (i = t);
		} else t(new Z(X.NODE_EXTENT_INVALID, e.id)), i = n;
	} else if (Array.isArray(i)) {
		let e = r?.computedPosition.x || 0, t = r?.computedPosition.y || 0;
		i = [[i[0][0] + e, i[0][1] + t], [i[1][0] + e, i[1][1] + t]];
	} else if (i !== "parent" && i?.range && Array.isArray(i.range)) {
		let [e, t, n, a] = ks(i.padding), o = r?.computedPosition.x || 0, s = r?.computedPosition.y || 0;
		i = [[i.range[0][0] + o + a, i.range[0][1] + s + e], [i.range[1][0] + o - t, i.range[1][1] + s - n]];
	}
	return i === "parent" ? [[-Infinity, -Infinity], [Infinity, Infinity]] : i;
}
function Ms({ width: e, height: t }, n) {
	return [n[0], [n[1][0] - (e || 0), n[1][1] - (t || 0)]];
}
function Ns(e, t, n, r, i) {
	let a = zo(t, Ms(e.dimensions, js(e, n, r, i)));
	return {
		position: {
			x: a.x - (i?.computedPosition.x || 0),
			y: a.y - (i?.computedPosition.y || 0)
		},
		computedPosition: a
	};
}
function Ps(e, t, n = J.Left, r = !1) {
	let i = (t?.x ?? 0) + e.computedPosition.x, a = (t?.y ?? 0) + e.computedPosition.y, { width: o, height: s } = t ?? Us(e);
	if (r) return {
		x: i + o / 2,
		y: a + s / 2
	};
	switch (t?.position ?? n) {
		case J.Top: return {
			x: i + o / 2,
			y: a
		};
		case J.Right: return {
			x: i + o,
			y: a + s / 2
		};
		case J.Bottom: return {
			x: i + o / 2,
			y: a + s
		};
		case J.Left: return {
			x: i,
			y: a + s / 2
		};
	}
}
function Fs(e, t) {
	return e && (t ? e.find((e) => e.id === t) : e[0]) || null;
}
function Is({ sourcePos: e, targetPos: t, sourceWidth: n, sourceHeight: r, targetWidth: i, targetHeight: a, width: o, height: s, viewport: c }) {
	let l = {
		x: Math.min(e.x, t.x),
		y: Math.min(e.y, t.y),
		x2: Math.max(e.x + n, t.x + i),
		y2: Math.max(e.y + r, t.y + a)
	};
	l.x === l.x2 && (l.x2 += 1), l.y === l.y2 && (l.y2 += 1);
	let u = ns({
		x: (0 - c.x) / c.zoom,
		y: (0 - c.y) / c.zoom,
		width: o / c.zoom,
		height: s / c.zoom
	}), d = Math.max(0, Math.min(u.x2, l.x2) - Math.max(u.x, l.x)), f = Math.max(0, Math.min(u.y2, l.y2) - Math.max(u.y, l.y));
	return Math.ceil(d * f) > 0;
}
function Ls(e, t, n = !1) {
	let r = typeof e.zIndex == "number", i = r ? e.zIndex : 0, a = t(e.source), o = t(e.target);
	return !a || !o ? 0 : (n && (i = r ? e.zIndex : Math.max(a.computedPosition.z || 0, o.computedPosition.z || 0)), i);
}
var X = /* @__PURE__ */ ((e) => (e.MISSING_STYLES = "MISSING_STYLES", e.MISSING_VIEWPORT_DIMENSIONS = "MISSING_VIEWPORT_DIMENSIONS", e.NODE_INVALID = "NODE_INVALID", e.NODE_NOT_FOUND = "NODE_NOT_FOUND", e.NODE_MISSING_PARENT = "NODE_MISSING_PARENT", e.NODE_TYPE_MISSING = "NODE_TYPE_MISSING", e.NODE_EXTENT_INVALID = "NODE_EXTENT_INVALID", e.EDGE_INVALID = "EDGE_INVALID", e.EDGE_NOT_FOUND = "EDGE_NOT_FOUND", e.EDGE_SOURCE_MISSING = "EDGE_SOURCE_MISSING", e.EDGE_TARGET_MISSING = "EDGE_TARGET_MISSING", e.EDGE_TYPE_MISSING = "EDGE_TYPE_MISSING", e.EDGE_SOURCE_TARGET_SAME = "EDGE_SOURCE_TARGET_SAME", e.EDGE_SOURCE_TARGET_MISSING = "EDGE_SOURCE_TARGET_MISSING", e.EDGE_ORPHANED = "EDGE_ORPHANED", e.USEVUEFLOW_OPTIONS = "USEVUEFLOW_OPTIONS", e))(X || {}), Rs = {
	MISSING_STYLES: () => "It seems that you haven't loaded the necessary styles. Please import '@vue-flow/core/dist/style.css' to ensure that the graph is rendered correctly",
	MISSING_VIEWPORT_DIMENSIONS: () => "The Vue Flow parent container needs a width and a height to render the graph",
	NODE_INVALID: (e) => `Node is invalid
Node: ${e}`,
	NODE_NOT_FOUND: (e) => `Node not found
Node: ${e}`,
	NODE_MISSING_PARENT: (e, t) => `Node is missing a parent
Node: ${e}
Parent: ${t}`,
	NODE_TYPE_MISSING: (e) => `Node type is missing
Type: ${e}`,
	NODE_EXTENT_INVALID: (e) => `Only child nodes can use a parent extent
Node: ${e}`,
	EDGE_INVALID: (e) => `An edge needs a source and a target
Edge: ${e}`,
	EDGE_SOURCE_MISSING: (e, t) => `Edge source is missing
Edge: ${e} 
Source: ${t}`,
	EDGE_TARGET_MISSING: (e, t) => `Edge target is missing
Edge: ${e} 
Target: ${t}`,
	EDGE_TYPE_MISSING: (e) => `Edge type is missing
Type: ${e}`,
	EDGE_SOURCE_TARGET_SAME: (e, t, n) => `Edge source and target are the same
Edge: ${e} 
Source: ${t} 
Target: ${n}`,
	EDGE_SOURCE_TARGET_MISSING: (e, t, n) => `Edge source or target is missing
Edge: ${e} 
Source: ${t} 
Target: ${n}`,
	EDGE_ORPHANED: (e) => `Edge was orphaned (suddenly missing source or target) and has been removed
Edge: ${e}`,
	EDGE_NOT_FOUND: (e) => `Edge not found
Edge: ${e}`,
	USEVUEFLOW_OPTIONS: () => "The options parameter is deprecated and will be removed in the next major version. Please use the id parameter instead"
}, Z = class extends Error {
	constructor(e, ...t) {
		super(Rs[e]?.call(Rs, ...t)), this.name = "VueFlowError", this.code = e, this.args = t;
	}
};
function zs(e) {
	return "clientX" in e;
}
function Bs(e) {
	return "sourceEvent" in e;
}
function Vs(e, t) {
	let n = zs(e), r, i;
	return n ? (r = e.clientX, i = e.clientY) : "touches" in e && e.touches.length > 0 ? (r = e.touches[0].clientX, i = e.touches[0].clientY) : "changedTouches" in e && e.changedTouches.length > 0 ? (r = e.changedTouches[0].clientX, i = e.changedTouches[0].clientY) : (r = 0, i = 0), {
		x: r - (t?.left ?? 0),
		y: i - (t?.top ?? 0)
	};
}
var Hs = () => typeof navigator < "u" && (navigator == null ? void 0 : navigator.userAgent)?.indexOf("Mac") >= 0;
function Us(e) {
	return {
		width: e.dimensions?.width ?? e.width ?? 0,
		height: e.dimensions?.height ?? e.height ?? 0
	};
}
function Ws(e, t = [1, 1]) {
	return {
		x: t[0] * Math.round(e.x / t[0]),
		y: t[1] * Math.round(e.y / t[1])
	};
}
var Gs = () => !0;
function Ks(e) {
	e?.classList.remove("valid", "connecting", "vue-flow__handle-valid", "vue-flow__handle-connecting");
}
function qs(e, t, n) {
	let r = [], i = {
		x: e.x - n,
		y: e.y - n,
		width: n * 2,
		height: n * 2
	};
	for (let e of t.values()) Io(i, Fo(e)) > 0 && r.push(e);
	return r;
}
var Js = 250;
function Ys(e, t, n, r) {
	let i = [], a = Infinity, o = qs(e, n, t + Js);
	for (let n of o) {
		let o = [...n.handleBounds?.source ?? [], ...n.handleBounds?.target ?? []];
		for (let s of o) {
			if (r.nodeId === s.nodeId && r.type === s.type && r.id === s.id) continue;
			let { x: o, y: c } = Ps(n, s, s.position, !0), l = Math.sqrt((o - e.x) ** 2 + (c - e.y) ** 2);
			l > t || (l < a ? (i = [{
				...s,
				x: o,
				y: c
			}], a = l) : l === a && i.push({
				...s,
				x: o,
				y: c
			}));
		}
	}
	if (!i.length) return null;
	if (i.length > 1) {
		let e = r.type === "source" ? "target" : "source";
		return i.find((t) => t.type === e) ?? i[0];
	}
	return i[0];
}
function Xs(e, { handle: t, connectionMode: n, fromNodeId: r, fromHandleId: i, fromType: a, doc: o, lib: s, flowId: c, isValidConnection: l = Gs }, u, d, f, p) {
	let m = a === "target", h = t ? o.querySelector(`.${s}-flow__handle[data-id="${c}-${t?.nodeId}-${t?.id}-${t?.type}"]`) : null, { x: g, y: _ } = Vs(e), v = o.elementFromPoint(g, _), y = v?.classList.contains(`${s}-flow__handle`) ? v : h, b = {
		handleDomNode: y,
		isValid: !1,
		connection: null,
		toHandle: null
	};
	if (y) {
		let e = Zs(void 0, y), t = y.getAttribute("data-nodeid"), a = y.getAttribute("data-handleid"), o = y.classList.contains("connectable"), s = y.classList.contains("connectableend");
		if (!t || !e) return b;
		let c = {
			source: m ? t : r,
			sourceHandle: m ? a : i,
			target: m ? r : t,
			targetHandle: m ? i : a
		};
		b.connection = c, b.isValid = o && s && (n === vo.Strict ? m && e === "source" || !m && e === "target" : t !== r || a !== i) && l(c, {
			nodes: d,
			edges: u,
			sourceNode: f(c.source),
			targetNode: f(c.target)
		}), b.toHandle = ec(t, e, a, p, n, !0);
	}
	return b;
}
function Zs(e, t) {
	return e || (t?.classList.contains("target") ? "target" : t?.classList.contains("source") ? "source" : null);
}
function Qs(e, t) {
	let n = null;
	return t ? n = "valid" : e && !t && (n = "invalid"), n;
}
function $s(e, t) {
	let n = null;
	return t ? n = !0 : e && !t && (n = !1), n;
}
function ec(e, t, n, r, i, a = !1) {
	let o = r.get(e);
	if (!o) return null;
	let s = i === vo.Strict ? o.handleBounds?.[t] : [...o.handleBounds?.source ?? [], ...o.handleBounds?.target ?? []], c = (n ? s?.find((e) => e.id === n) : s?.[0]) ?? null;
	return c && a ? {
		...c,
		...Ps(o, c, c.position, !0)
	} : c;
}
var tc = {
	[J.Left]: J.Right,
	[J.Right]: J.Left,
	[J.Top]: J.Bottom,
	[J.Bottom]: J.Top
}, nc = ["production", "prod"];
function rc(e, ...t) {
	ic() && console.warn(`[Vue Flow]: ${e}`, ...t);
}
function ic() {
	return !nc.includes("production");
}
function ac(e, t, n, r, i) {
	let a = t.querySelectorAll(`.vue-flow__handle.${e}`);
	return a?.length ? Array.from(a).map((t) => {
		let a = t.getBoundingClientRect();
		return {
			id: t.getAttribute("data-handleid"),
			type: e,
			nodeId: i,
			position: t.getAttribute("data-handlepos"),
			x: (a.left - n.left) / r,
			y: (a.top - n.top) / r,
			...Lo(t)
		};
	}) : null;
}
function oc(e, t, n, r, i, a = !1, o) {
	i.value = !1, e.selected ? (a || e.selected && t) && (r([e]), x(() => {
		o.blur();
	})) : n([e]);
}
function Q(e) {
	return U(e) !== void 0;
}
function sc(e, t, n, r) {
	if (!e || !e.source || !e.target) return n(new Z(X.EDGE_INVALID, e?.id ?? "[ID UNKNOWN]")), !1;
	let i;
	return i = Vo(e) ? e : {
		...e,
		id: Zo(e)
	}, i = qo(i, void 0, r), !Qo(i, t) && i;
}
function cc(e, t, n, r, i) {
	if (!t.source || !t.target) return i(new Z(X.EDGE_INVALID, e.id)), !1;
	if (!n) return i(new Z(X.EDGE_NOT_FOUND, e.id)), !1;
	let { id: a, ...o } = e;
	return {
		...o,
		id: r ? Zo(t) : a,
		source: t.source,
		target: t.target,
		sourceHandle: t.sourceHandle,
		targetHandle: t.targetHandle
	};
}
function lc(e, t, n) {
	let r = {}, i = [];
	for (let a = 0; a < e.length; ++a) {
		let o = e[a];
		if (!Ho(o)) {
			n(new Z(X.NODE_INVALID, o?.id));
			continue;
		}
		let s = Ko(o, t(o.id), o.parentNode);
		o.parentNode && (r[o.parentNode] = !0), i[a] = s;
	}
	for (let e of i) {
		let a = t(e.parentNode) || i.find((t) => t.id === e.parentNode);
		e.parentNode && !a && n(new Z(X.NODE_MISSING_PARENT, e.id, e.parentNode)), (e.parentNode || r[e.id]) && (r[e.id] && (e.isParent = !0), a && (a.isParent = !0));
	}
	return i;
}
function uc(e, t, n, r, i, a) {
	let o = i, s = r.get(o) || /* @__PURE__ */ new Map();
	r.set(o, s.set(n, t)), o = `${i}-${e}`;
	let c = r.get(o) || /* @__PURE__ */ new Map();
	if (r.set(o, c.set(n, t)), a) {
		o = `${i}-${e}-${a}`;
		let s = r.get(o) || /* @__PURE__ */ new Map();
		r.set(o, s.set(n, t));
	}
}
function dc(e, t, n) {
	e.clear();
	for (let t of n) {
		let { source: n, target: r, sourceHandle: i = null, targetHandle: a = null } = t, o = {
			edgeId: t.id,
			source: n,
			target: r,
			sourceHandle: i,
			targetHandle: a
		}, s = `${n}-${i}--${r}-${a}`;
		uc("source", o, `${r}-${a}--${n}-${i}`, e, n, i), uc("target", o, s, e, r, a);
	}
}
function fc(e, t) {
	if (e.size !== t.size) return !1;
	for (let n of e) if (!t.has(n)) return !1;
	return !0;
}
function pc(e, t, n, r, i, a, o, s) {
	let c = [];
	for (let l of e) {
		let e = Vo(l) ? l : sc(l, s, i, a);
		if (!e) continue;
		let u = n(e.source), d = n(e.target);
		if (!u || !d) {
			i(new Z(X.EDGE_SOURCE_TARGET_MISSING, e.id, e.source, e.target));
			continue;
		}
		if (!u) {
			i(new Z(X.EDGE_SOURCE_MISSING, e.id, e.source));
			continue;
		}
		if (!d) {
			i(new Z(X.EDGE_TARGET_MISSING, e.id, e.target));
			continue;
		}
		if (t && !t(e, {
			edges: s,
			nodes: o,
			sourceNode: u,
			targetNode: d
		})) {
			i(new Z(X.EDGE_INVALID, e.id));
			continue;
		}
		let f = r(e.id);
		c.push({
			...qo(e, f, a),
			sourceNode: u,
			targetNode: d
		});
	}
	return c;
}
var mc = Symbol("vueFlow"), hc = Symbol("nodeId"), gc = Symbol("nodeRef"), _c = Symbol("edgeId"), vc = Symbol("edgeRef"), yc = Symbol("slots");
function bc(e) {
	let { vueFlowRef: t, snapToGrid: n, snapGrid: r, noDragClassName: i, nodeLookup: a, nodeExtent: o, nodeDragThreshold: s, viewport: c, autoPanOnNodeDrag: l, autoPanSpeed: u, nodesDraggable: d, panBy: f, findNode: p, multiSelectionActive: m, nodesSelectionActive: h, selectNodesOnDrag: g, removeSelectedElements: _, addSelectedNodes: v, updateNodePositions: y, emits: b } = $(), { onStart: x, onDrag: S, onStop: C, onClick: w, el: T, disabled: E, id: D, selectable: O, dragHandle: k } = e, A = z(!1), j = [], M, N = null, P = {
		x: void 0,
		y: void 0
	}, F = {
		x: 0,
		y: 0
	}, I = null, L = !1, R = !1, ee = 0, B = !1, V = Cc(), U = ({ x: e, y: t }) => {
		P = {
			x: e,
			y: t
		};
		let i = !1;
		if (j = j.map((a) => {
			let s = {
				x: e - a.distance.x,
				y: t - a.distance.y
			}, { computedPosition: c } = Ns(a, n.value ? Ws(s, r.value) : s, b.error, o.value, a.parentNode ? p(a.parentNode) : void 0);
			return i = i || a.position.x !== c.x || a.position.y !== c.y, a.position = c, a;
		}), R = R || i, i && (y(j, !0, !0), A.value = !0, I)) {
			let [e, t] = Os({
				id: D,
				dragItems: j,
				findNode: p
			});
			S({
				event: I,
				node: e,
				nodes: t
			});
		}
	}, W = () => {
		if (!N) return;
		let [e, t] = _s(F, N, u.value);
		if (e !== 0 || t !== 0) {
			let n = {
				x: (P.x ?? 0) - e / c.value.zoom,
				y: (P.y ?? 0) - t / c.value.zoom
			};
			f({
				x: e,
				y: t
			}) && U(n);
		}
		ee = requestAnimationFrame(W);
	}, G = (e, t) => {
		L = !0;
		let n = p(D);
		g.value || m.value || !n || n.selected || _(), n && H(O) && g.value && oc(n, m.value, v, _, h, !1, t);
		let r = V(e.sourceEvent);
		if (P = r, j = Ds(a.value, d.value, r, D), j.length) {
			let [t, n] = Os({
				id: D,
				dragItems: j,
				findNode: p
			});
			x({
				event: e.sourceEvent,
				node: t,
				nodes: n
			});
		}
	}, te = (e, n) => {
		e.sourceEvent.type === "touchmove" && e.sourceEvent.touches.length > 1 || (R = !1, s.value === 0 && G(e, n), P = V(e.sourceEvent), N = t.value?.getBoundingClientRect() || null, F = Vs(e.sourceEvent, N));
	}, q = (e, t) => {
		let n = V(e.sourceEvent);
		if (!B && L && l.value && (B = !0, W()), !L) {
			let r = n.xSnapped - (P.x ?? 0), i = n.ySnapped - (P.y ?? 0);
			Math.sqrt(r * r + i * i) > s.value && G(e, t);
		}
		(P.x !== n.xSnapped || P.y !== n.ySnapped) && j.length && L && (I = e.sourceEvent, F = Vs(e.sourceEvent, N), U(n));
	}, ne = (e) => {
		let t = !1;
		if (!L && !A.value && !m.value) {
			let n = e.sourceEvent, r = V(n), i = r.xSnapped - (P.x ?? 0), a = r.ySnapped - (P.y ?? 0), o = Math.sqrt(i * i + a * a);
			o !== 0 && o <= s.value && (w?.(n), t = !0);
		}
		if (j.length && !t) {
			R && (y(j, !1, !1), R = !1);
			let [t, n] = Os({
				id: D,
				dragItems: j,
				findNode: p
			});
			C({
				event: e.sourceEvent,
				node: t,
				nodes: n
			});
		}
		j = [], A.value = !1, B = !1, L = !1, P = {
			x: void 0,
			y: void 0
		}, cancelAnimationFrame(ee);
	};
	return K([() => H(E), T], ([e, t], n, r) => {
		if (t) {
			let n = jn(t);
			e || (M = Kn().on("start", (e) => te(e, t)).on("drag", (e) => q(e, t)).on("end", (e) => ne(e)).filter((e) => {
				let n = e.target, r = H(k);
				return !e.button && (!i.value || !Es(n, `.${i.value}`, t) && (!r || Es(n, r, t)));
			}), n.call(M)), r(() => {
				n.on(".drag", null), M && (M.on("start", null), M.on("drag", null), M.on("end", null));
			});
		}
	}), A;
}
function xc() {
	return {
		doubleClick: Y(),
		click: Y(),
		mouseEnter: Y(),
		mouseMove: Y(),
		mouseLeave: Y(),
		contextMenu: Y(),
		updateStart: Y(),
		update: Y(),
		updateEnd: Y()
	};
}
function Sc(e, t) {
	let n = xc();
	return n.doubleClick.on((n) => {
		var r, i;
		t.edgeDoubleClick(n), (i = (r = e.events)?.doubleClick) == null || i.call(r, n);
	}), n.click.on((n) => {
		var r, i;
		t.edgeClick(n), (i = (r = e.events)?.click) == null || i.call(r, n);
	}), n.mouseEnter.on((n) => {
		var r, i;
		t.edgeMouseEnter(n), (i = (r = e.events)?.mouseEnter) == null || i.call(r, n);
	}), n.mouseMove.on((n) => {
		var r, i;
		t.edgeMouseMove(n), (i = (r = e.events)?.mouseMove) == null || i.call(r, n);
	}), n.mouseLeave.on((n) => {
		var r, i;
		t.edgeMouseLeave(n), (i = (r = e.events)?.mouseLeave) == null || i.call(r, n);
	}), n.contextMenu.on((n) => {
		var r, i;
		t.edgeContextMenu(n), (i = (r = e.events)?.contextMenu) == null || i.call(r, n);
	}), n.updateStart.on((n) => {
		var r, i;
		t.edgeUpdateStart(n), (i = (r = e.events)?.updateStart) == null || i.call(r, n);
	}), n.update.on((n) => {
		var r, i;
		t.edgeUpdate(n), (i = (r = e.events)?.update) == null || i.call(r, n);
	}), n.updateEnd.on((n) => {
		var r, i;
		t.edgeUpdateEnd(n), (i = (r = e.events)?.updateEnd) == null || i.call(r, n);
	}), Object.entries(n).reduce((e, [t, n]) => (e.emit[t] = n.trigger, e.on[t] = n.on, e), {
		emit: {},
		on: {}
	});
}
function Cc() {
	let { viewport: e, snapGrid: t, snapToGrid: n, vueFlowRef: r } = $();
	return (i) => {
		let a = r.value?.getBoundingClientRect() ?? {
			left: 0,
			top: 0
		}, { x: o, y: s } = Vs(Bs(i) ? i.sourceEvent : i, a), c = es({
			x: o,
			y: s
		}, e.value), { x: l, y: u } = n.value ? Ws(c, t.value) : c;
		return {
			xSnapped: l,
			ySnapped: u,
			...c
		};
	};
}
function wc() {
	return !0;
}
function Tc({ handleId: e, nodeId: t, type: n, isValidConnection: r, edgeUpdaterType: i, onEdgeUpdate: a, onEdgeUpdateEnd: o }) {
	let { id: s, vueFlowRef: c, connectionMode: l, connectionRadius: u, connectOnClick: d, connectionClickStartHandle: f, nodesConnectable: p, autoPanOnConnect: m, autoPanSpeed: h, findNode: g, panBy: _, startConnection: v, updateConnection: y, endConnection: b, emits: x, viewport: S, edges: C, nodes: w, isValidConnection: T, nodeLookup: E } = $(), D = null, O = !1, k = null;
	function A(d) {
		let f = H(n) === "target", p = zs(d), A = Bo(d.target), j = d.currentTarget;
		if (j && (p && d.button === 0 || !p)) {
			let n = function(n) {
				V = Vs(n, z), P = Ys(es(V, S.value, !1, [1, 1]), u.value, E.value, G), U || (W(), U = !0);
				let r = Xs(n, {
					handle: P,
					connectionMode: l.value,
					fromNodeId: H(t),
					fromHandleId: H(e),
					fromType: f ? "target" : "source",
					isValidConnection: N,
					doc: A,
					lib: "vue",
					flowId: s,
					nodeLookup: E.value
				}, C.value, w.value, g, E.value);
				k = r.handleDomNode, D = r.connection, O = $s(!!P, r.isValid);
				let i = {
					...q,
					isValid: O,
					to: r.toHandle && O ? $o({
						x: r.toHandle.x,
						y: r.toHandle.y
					}, S.value) : V,
					toHandle: r.toHandle,
					toPosition: O && r.toHandle ? r.toHandle.position : tc[G.position],
					toNode: r.toHandle ? E.value.get(r.toHandle.nodeId) : null
				};
				if (O && P && q?.toHandle && i.toHandle && q.toHandle.type === i.toHandle.type && q.toHandle.nodeId === i.toHandle.nodeId && q.toHandle.id === i.toHandle.id && q.to.x === i.to.x && q.to.y === i.to.y) return;
				let a = P ?? r.toHandle;
				if (y(a && O ? $o({
					x: a.x,
					y: a.y
				}, S.value) : V, a, Qs(!!a, O)), q = i, !P && !O && !k) return Ks(B);
				D && D.source !== D.target && k && (Ks(B), B = k, k.classList.add("connecting", "vue-flow__handle-connecting"), k.classList.toggle("valid", !!O), k.classList.toggle("vue-flow__handle-valid", !!O));
			}, p = function(e) {
				"touches" in e && e.touches.length > 0 || ((P || k) && D && O && (a ? a(e, D) : x.connect(D)), x.connectEnd(e), i && o?.(e), Ks(B), cancelAnimationFrame(F), b(e), U = !1, O = !1, D = null, k = null, A.removeEventListener("mousemove", n), A.removeEventListener("mouseup", p), A.removeEventListener("touchmove", n), A.removeEventListener("touchend", p));
			}, M = g(H(t)), N = H(r) || T.value || wc;
			!N && M && (N = (f ? M.isValidSourcePos : M.isValidTargetPos) || wc);
			let P, F = 0, { x: I, y: L } = Vs(d), R = Zs(H(i), j), z = c.value?.getBoundingClientRect();
			if (!z || !R) return;
			let ee = ec(H(t), R, H(e), E.value, l.value);
			if (!ee) return;
			let B, V = Vs(d, z), U = !1, W = () => {
				if (!m.value) return;
				let [e, t] = _s(V, z, h.value);
				_({
					x: e,
					y: t
				}), F = requestAnimationFrame(W);
			}, G = {
				...ee,
				nodeId: H(t),
				type: R,
				position: ee.position
			}, K = E.value.get(H(t)), te = {
				inProgress: !0,
				isValid: null,
				from: Ps(K, G, J.Left, !0),
				fromHandle: G,
				fromPosition: G.position,
				fromNode: K,
				to: V,
				toHandle: null,
				toPosition: tc[G.position],
				toNode: null
			};
			v({
				nodeId: H(t),
				id: H(e),
				type: R,
				position: j?.getAttribute("data-handlepos") || J.Top,
				...V
			}, {
				x: I - z.left,
				y: L - z.top
			}), x.connectStart({
				event: d,
				nodeId: H(t),
				handleId: H(e),
				handleType: R
			});
			let q = te;
			A.addEventListener("mousemove", n), A.addEventListener("mouseup", p), A.addEventListener("touchmove", n), A.addEventListener("touchend", p);
		}
	}
	function j(i) {
		if (!d.value) return;
		let a = H(n) === "target";
		if (!f.value) {
			x.clickConnectStart({
				event: i,
				nodeId: H(t),
				handleId: H(e)
			}), v({
				nodeId: H(t),
				type: H(n),
				id: H(e),
				position: J.Top,
				...Vs(i)
			}, void 0, !0);
			return;
		}
		let o = H(r) || T.value || wc, c = g(H(t));
		if (!o && c && (o = (a ? c.isValidSourcePos : c.isValidTargetPos) || wc), c && (c.connectable === void 0 ? p.value : c.connectable) === !1) return;
		let u = Bo(i.target), m = Xs(i, {
			handle: {
				nodeId: H(t),
				id: H(e),
				type: H(n),
				position: J.Top,
				...Vs(i)
			},
			connectionMode: l.value,
			fromNodeId: f.value.nodeId,
			fromHandleId: f.value.id ?? null,
			fromType: f.value.type,
			isValidConnection: o,
			doc: u,
			lib: "vue",
			flowId: s,
			nodeLookup: E.value
		}, C.value, w.value, g, E.value), h = m.connection?.source === m.connection?.target;
		m.isValid && m.connection && !h && x.connect(m.connection), x.clickConnectEnd(i), b(i, !0);
	}
	return {
		handlePointerDown: A,
		handleClick: j
	};
}
function Ec() {
	return g(hc, "");
}
function Dc(e) {
	let t = e ?? Ec() ?? "", r = g(gc, P(null)), { findNode: i, edges: a, emits: o } = $(), s = i(t);
	return s || o.error(new Z(X.NODE_NOT_FOUND, t)), {
		id: t,
		nodeEl: r,
		node: s,
		parentNode: n(() => i(s.parentNode)),
		connectedEdges: n(() => ss([s], a.value))
	};
}
function Oc() {
	return {
		doubleClick: Y(),
		click: Y(),
		mouseEnter: Y(),
		mouseMove: Y(),
		mouseLeave: Y(),
		contextMenu: Y(),
		dragStart: Y(),
		drag: Y(),
		dragStop: Y()
	};
}
function kc(e, t) {
	let n = Oc();
	return n.doubleClick.on((n) => {
		var r, i;
		t.nodeDoubleClick(n), (i = (r = e.events)?.doubleClick) == null || i.call(r, n);
	}), n.click.on((n) => {
		var r, i;
		t.nodeClick(n), (i = (r = e.events)?.click) == null || i.call(r, n);
	}), n.mouseEnter.on((n) => {
		var r, i;
		t.nodeMouseEnter(n), (i = (r = e.events)?.mouseEnter) == null || i.call(r, n);
	}), n.mouseMove.on((n) => {
		var r, i;
		t.nodeMouseMove(n), (i = (r = e.events)?.mouseMove) == null || i.call(r, n);
	}), n.mouseLeave.on((n) => {
		var r, i;
		t.nodeMouseLeave(n), (i = (r = e.events)?.mouseLeave) == null || i.call(r, n);
	}), n.contextMenu.on((n) => {
		var r, i;
		t.nodeContextMenu(n), (i = (r = e.events)?.contextMenu) == null || i.call(r, n);
	}), n.dragStart.on((n) => {
		var r, i;
		t.nodeDragStart(n), (i = (r = e.events)?.dragStart) == null || i.call(r, n);
	}), n.drag.on((n) => {
		var r, i;
		t.nodeDrag(n), (i = (r = e.events)?.drag) == null || i.call(r, n);
	}), n.dragStop.on((n) => {
		var r, i;
		t.nodeDragStop(n), (i = (r = e.events)?.dragStop) == null || i.call(r, n);
	}), Object.entries(n).reduce((e, [t, n]) => (e.emit[t] = n.trigger, e.on[t] = n.on, e), {
		emit: {},
		on: {}
	});
}
function Ac() {
	let { getSelectedNodes: e, nodeExtent: t, updateNodePositions: n, findNode: r, snapGrid: i, snapToGrid: a, nodesDraggable: o, emits: s } = $();
	return (c, l = !1) => {
		let u = a.value ? i.value[0] : 5, d = a.value ? i.value[1] : 5, f = l ? 4 : 1, p = c.x * u * f, m = c.y * d * f, h = [];
		for (let n of e.value) if (n.draggable || o && n.draggable === void 0) {
			let { position: e } = Ns(n, {
				x: n.computedPosition.x + p,
				y: n.computedPosition.y + m
			}, s.error, t.value, n.parentNode ? r(n.parentNode) : void 0);
			h.push({
				id: n.id,
				position: e,
				from: n.position,
				distance: {
					x: c.x,
					y: c.y
				},
				dimensions: n.dimensions
			});
		}
		n(h, !0, !1);
	};
}
var jc = .1, Mc = (e) => ((e *= 2) <= 1 ? e * e * e : (e -= 2) * e * e + 2) / 2;
function Nc() {
	return rc("Viewport not initialized yet."), Promise.resolve(!1);
}
var Pc = {
	zoomIn: Nc,
	zoomOut: Nc,
	zoomTo: Nc,
	fitView: Nc,
	setCenter: Nc,
	fitBounds: Nc,
	project: (e) => e,
	screenToFlowCoordinate: (e) => e,
	flowToScreenCoordinate: (e) => e,
	setViewport: Nc,
	setTransform: Nc,
	getViewport: () => ({
		x: 0,
		y: 0,
		zoom: 1
	}),
	getTransform: () => ({
		x: 0,
		y: 0,
		zoom: 1
	}),
	viewportInitialized: !1
};
function Fc(e) {
	function t(t, n) {
		return new Promise((r) => {
			e.d3Selection && e.d3Zoom ? e.d3Zoom.interpolate(n?.interpolate === "linear" ? Yr : li).scaleBy(Ic(e.d3Selection, n?.duration, n?.ease, () => {
				r(!0);
			}), t) : r(!1);
		});
	}
	function r(t, n, r, i) {
		return new Promise((a) => {
			var o;
			let { x: s, y: c } = zo({
				x: -t,
				y: -n
			}, e.translateExtent), l = ao.translate(-s, -c).scale(r);
			e.d3Selection && e.d3Zoom ? (o = e.d3Zoom) == null || o.interpolate(i?.interpolate === "linear" ? Yr : li).transform(Ic(e.d3Selection, i?.duration, i?.ease, () => {
				a(!0);
			}), l) : a(!1);
		});
	}
	return n(() => e.d3Zoom && e.d3Selection && e.dimensions.width && e.dimensions.height ? {
		viewportInitialized: !0,
		zoomIn: (e) => t(1.2, e),
		zoomOut: (e) => t(1 / 1.2, e),
		zoomTo: (t, n) => new Promise((r) => {
			e.d3Selection && e.d3Zoom ? e.d3Zoom.interpolate(n?.interpolate === "linear" ? Yr : li).scaleTo(Ic(e.d3Selection, n?.duration, n?.ease, () => {
				r(!0);
			}), t) : r(!1);
		}),
		setViewport: (e, t) => r(e.x, e.y, e.zoom, t),
		setTransform: (e, t) => r(e.x, e.y, e.zoom, t),
		getViewport: () => ({
			x: e.viewport.x,
			y: e.viewport.y,
			zoom: e.viewport.zoom
		}),
		getTransform: () => ({
			x: e.viewport.x,
			y: e.viewport.y,
			zoom: e.viewport.zoom
		}),
		fitView: (t = {
			padding: jc,
			includeHiddenNodes: !1,
			duration: 0
		}) => {
			let n = [];
			for (let r of e.nodes) r.dimensions.width && r.dimensions.height && (t?.includeHiddenNodes || !r.hidden) && (!t.nodes?.length || t.nodes?.length && t.nodes.includes(r.id)) && n.push(r);
			if (!n.length) return Promise.resolve(!1);
			let { x: i, y: a, zoom: o } = ds(as(n), e.dimensions.width, e.dimensions.height, t.minZoom ?? e.minZoom, t.maxZoom ?? e.maxZoom, t.padding ?? jc);
			return r(i, a, o, t);
		},
		setCenter: (t, n, i) => {
			let a = i?.zoom === void 0 ? e.maxZoom : i.zoom;
			return r(e.dimensions.width / 2 - t * a, e.dimensions.height / 2 - n * a, a, i);
		},
		fitBounds: (t, n = { padding: jc }) => {
			let { x: i, y: a, zoom: o } = ds(t, e.dimensions.width, e.dimensions.height, e.minZoom, e.maxZoom, n.padding ?? jc);
			return r(i, a, o, n);
		},
		project: (t) => es(t, e.viewport, e.snapToGrid, e.snapGrid),
		screenToFlowCoordinate: (t) => {
			if (e.vueFlowRef) {
				let { x: n, y: r } = e.vueFlowRef.getBoundingClientRect();
				return es({
					x: t.x - n,
					y: t.y - r
				}, e.viewport, e.snapToGrid, e.snapGrid);
			}
			return {
				x: 0,
				y: 0
			};
		},
		flowToScreenCoordinate: (t) => {
			if (e.vueFlowRef) {
				let { x: n, y: r } = e.vueFlowRef.getBoundingClientRect();
				return $o({
					x: t.x + n,
					y: t.y + r
				}, e.viewport);
			}
			return {
				x: 0,
				y: 0
			};
		}
	} : Pc);
}
function Ic(e, t = 0, n = Mc, r = () => {}) {
	let i = typeof t == "number" && t > 0;
	return i || r(), i ? e.transition().duration(t).ease(n).on("end", r) : e;
}
function Lc(e, t, n) {
	let r = f(!0);
	return r.run(() => {
		r.run(() => {
			let t, r, i = !!(n.nodes.value.length || n.edges.value.length);
			t = me([e.modelValue, () => e.modelValue?.value?.length], ([e]) => {
				e && Array.isArray(e) && (r?.pause(), n.setElements(e), !r && !i && e.length ? i = !0 : r?.resume());
			}), r = me([
				n.nodes,
				n.edges,
				() => n.edges.value.length,
				() => n.nodes.value.length
			], ([n, r]) => {
				e.modelValue?.value && Array.isArray(e.modelValue.value) && (t?.pause(), e.modelValue.value = [...n, ...r], x(() => {
					t?.resume();
				}));
			}, { immediate: i }), O(() => {
				t?.stop(), r?.stop();
			});
		}), r.run(() => {
			let t, r, i = !!n.nodes.value.length;
			t = me([e.nodes, () => e.nodes?.value?.length], ([e]) => {
				e && Array.isArray(e) && (r?.pause(), n.setNodes(e), !r && !i && e.length ? i = !0 : r?.resume());
			}), r = me([n.nodes, () => n.nodes.value.length], ([n]) => {
				e.nodes?.value && Array.isArray(e.nodes.value) && (t?.pause(), e.nodes.value = [...n], x(() => {
					t?.resume();
				}));
			}, { immediate: i }), O(() => {
				t?.stop(), r?.stop();
			});
		}), r.run(() => {
			let t, r, i = !!n.edges.value.length;
			t = me([e.edges, () => e.edges?.value?.length], ([e]) => {
				e && Array.isArray(e) && (r?.pause(), n.setEdges(e), !r && !i && e.length ? i = !0 : r?.resume());
			}), r = me([n.edges, () => n.edges.value.length], ([n]) => {
				e.edges?.value && Array.isArray(e.edges.value) && (t?.pause(), e.edges.value = [...n], x(() => {
					t?.resume();
				}));
			}, { immediate: i }), O(() => {
				t?.stop(), r?.stop();
			});
		}), r.run(() => {
			K(() => t.minZoom, () => {
				t.minZoom && Q(t.minZoom) && n.setMinZoom(t.minZoom);
			}, { immediate: !0 });
		}), r.run(() => {
			K(() => t.maxZoom, () => {
				t.maxZoom && Q(t.maxZoom) && n.setMaxZoom(t.maxZoom);
			}, { immediate: !0 });
		}), r.run(() => {
			K(() => t.translateExtent, () => {
				t.translateExtent && Q(t.translateExtent) && n.setTranslateExtent(t.translateExtent);
			}, { immediate: !0 });
		}), r.run(() => {
			K(() => t.nodeExtent, () => {
				t.nodeExtent && Q(t.nodeExtent) && n.setNodeExtent(t.nodeExtent);
			}, { immediate: !0 });
		}), r.run(() => {
			K(() => t.applyDefault, () => {
				Q(t.applyDefault) && (n.applyDefault.value = t.applyDefault);
			}, { immediate: !0 });
		}), r.run(() => {
			let e = async (e) => {
				let r = e;
				typeof t.autoConnect == "function" && (r = await t.autoConnect(e)), r !== !1 && n.addEdges([r]);
			};
			K(() => t.autoConnect, () => {
				Q(t.autoConnect) && (n.autoConnect.value = t.autoConnect);
			}, { immediate: !0 }), K(n.autoConnect, (t, r, i) => {
				t ? n.onConnect(e) : n.hooks.value.connect.off(e), i(() => {
					n.hooks.value.connect.off(e);
				});
			}, { immediate: !0 });
		}), (() => {
			let e = [
				"id",
				"modelValue",
				"translateExtent",
				"nodeExtent",
				"edges",
				"nodes",
				"maxZoom",
				"minZoom",
				"applyDefault",
				"autoConnect"
			];
			for (let i of Object.keys(t)) {
				let a = i;
				if (!e.includes(a)) {
					let e = B(() => t[a]), i = n[a];
					v(i) && r.run(() => {
						K(e, (e) => {
							Q(e) && (i.value = e);
						}, { immediate: !0 });
					});
				}
			}
		})();
	}), () => r.stop();
}
function Rc() {
	return {
		edgesChange: Y(),
		nodesChange: Y(),
		nodeDoubleClick: Y(),
		nodeClick: Y(),
		nodeMouseEnter: Y(),
		nodeMouseMove: Y(),
		nodeMouseLeave: Y(),
		nodeContextMenu: Y(),
		nodeDragStart: Y(),
		nodeDrag: Y(),
		nodeDragStop: Y(),
		nodesInitialized: Y(),
		miniMapNodeClick: Y(),
		miniMapNodeDoubleClick: Y(),
		miniMapNodeMouseEnter: Y(),
		miniMapNodeMouseMove: Y(),
		miniMapNodeMouseLeave: Y(),
		connect: Y(),
		connectStart: Y(),
		connectEnd: Y(),
		clickConnectStart: Y(),
		clickConnectEnd: Y(),
		paneReady: Y(),
		init: Y(),
		move: Y(),
		moveStart: Y(),
		moveEnd: Y(),
		selectionDragStart: Y(),
		selectionDrag: Y(),
		selectionDragStop: Y(),
		selectionContextMenu: Y(),
		selectionStart: Y(),
		selectionEnd: Y(),
		viewportChangeStart: Y(),
		viewportChange: Y(),
		viewportChangeEnd: Y(),
		paneScroll: Y(),
		paneClick: Y(),
		paneContextMenu: Y(),
		paneMouseEnter: Y(),
		paneMouseMove: Y(),
		paneMouseLeave: Y(),
		edgeContextMenu: Y(),
		edgeMouseEnter: Y(),
		edgeMouseMove: Y(),
		edgeMouseLeave: Y(),
		edgeDoubleClick: Y(),
		edgeClick: Y(),
		edgeUpdateStart: Y(),
		edgeUpdate: Y(),
		edgeUpdateEnd: Y(),
		updateNodeInternals: Y(),
		error: Y((e) => rc(e.message))
	};
}
function zc(e, t) {
	let n = p();
	T(() => {
		for (let [n, i] of Object.entries(t.value)) i.setEmitter((t) => {
			e(n, t);
		}), ne(i.removeEmitter), i.setHasEmitListeners(() => r(n)), ne(i.removeHasEmitListeners);
	});
	function r(e) {
		let t = Bc(e);
		return !!n?.vnode.props?.[t];
	}
}
function Bc(e) {
	let [t, ...n] = e.split(":");
	return `on${t.replace(/(?:^|-)(\w)/g, (e, t) => t.toUpperCase())}${n.length ? `:${n.join(":")}` : ""}`;
}
function Vc() {
	return {
		vueFlowRef: null,
		viewportRef: null,
		nodes: [],
		edges: [],
		connectionLookup: /* @__PURE__ */ new Map(),
		nodeTypes: {},
		edgeTypes: {},
		initialized: !1,
		dimensions: {
			width: 0,
			height: 0
		},
		viewport: {
			x: 0,
			y: 0,
			zoom: 1
		},
		d3Zoom: null,
		d3Selection: null,
		d3ZoomHandler: null,
		minZoom: .5,
		maxZoom: 2,
		translateExtent: [[-Infinity, -Infinity], [Infinity, Infinity]],
		nodeExtent: [[-Infinity, -Infinity], [Infinity, Infinity]],
		selectionMode: go.Full,
		paneDragging: !1,
		preventScrolling: !0,
		zoomOnScroll: !0,
		zoomOnPinch: !0,
		zoomOnDoubleClick: !0,
		panOnScroll: !1,
		panOnScrollSpeed: .5,
		panOnScrollMode: bo.Free,
		paneClickDistance: 0,
		panOnDrag: !0,
		edgeUpdaterRadius: 10,
		onlyRenderVisibleElements: !1,
		defaultViewport: {
			x: 0,
			y: 0,
			zoom: 1
		},
		nodesSelectionActive: !1,
		userSelectionActive: !1,
		userSelectionRect: null,
		defaultMarkerColor: "#b1b1b7",
		connectionLineStyle: {},
		connectionLineType: null,
		connectionLineOptions: {
			type: _o.Bezier,
			style: {}
		},
		connectionMode: vo.Loose,
		connectionStartHandle: null,
		connectionEndHandle: null,
		connectionClickStartHandle: null,
		connectionPosition: {
			x: NaN,
			y: NaN
		},
		connectionRadius: 20,
		connectOnClick: !0,
		connectionStatus: null,
		isValidConnection: null,
		snapGrid: [15, 15],
		snapToGrid: !1,
		edgesUpdatable: !1,
		edgesFocusable: !0,
		nodesFocusable: !0,
		nodesConnectable: !0,
		nodesDraggable: !0,
		nodeDragThreshold: 1,
		elementsSelectable: !0,
		selectNodesOnDrag: !0,
		multiSelectionActive: !1,
		selectionKeyCode: "Shift",
		multiSelectionKeyCode: Hs() ? "Meta" : "Control",
		zoomActivationKeyCode: Hs() ? "Meta" : "Control",
		deleteKeyCode: "Backspace",
		panActivationKeyCode: "Space",
		hooks: Rc(),
		applyDefault: !0,
		autoConnect: !1,
		fitViewOnInit: !1,
		fitViewOnInitDone: !1,
		noDragClassName: "nodrag",
		noWheelClassName: "nowheel",
		noPanClassName: "nopan",
		defaultEdgeOptions: void 0,
		elevateEdgesOnSelect: !1,
		elevateNodesOnSelect: !0,
		autoPanOnNodeDrag: !0,
		autoPanOnConnect: !0,
		autoPanSpeed: 15,
		disableKeyboardA11y: !1,
		ariaLiveMessage: ""
	};
}
var Hc = [
	"id",
	"vueFlowRef",
	"viewportRef",
	"initialized",
	"modelValue",
	"nodes",
	"edges",
	"maxZoom",
	"minZoom",
	"translateExtent",
	"hooks",
	"defaultEdgeOptions"
];
function Uc(e, t, n) {
	let r = Fc(e), i = (t) => {
		let n = t ?? [];
		e.hooks.updateNodeInternals.trigger(n);
	}, a = (t) => Xo(t, e.nodes, e.edges), o = (t) => Yo(t, e.nodes, e.edges), s = (t) => ss(t, e.edges), c = ({ id: t, type: n, nodeId: r }) => {
		let i = t ? `-${n}-${t}` : `-${n}`;
		return Array.from(e.connectionLookup.get(`${r}${i}`)?.values() ?? []);
	}, l = (e) => {
		if (e) return t.value.get(e);
	}, u = (e) => {
		if (e) return n.value.get(e);
	}, d = (t, n, r) => {
		let i = [];
		for (let e of t) {
			let t = {
				id: e.id,
				type: "position",
				dragging: r,
				from: e.from
			};
			if (n && (t.position = e.position, e.parentNode)) {
				let n = l(e.parentNode);
				t.position = {
					x: t.position.x - (n?.computedPosition?.x ?? 0),
					y: t.position.y - (n?.computedPosition?.y ?? 0)
				};
			}
			i.push(t);
		}
		i?.length && e.hooks.nodesChange.trigger(i);
	}, f = (t) => {
		if (!e.vueFlowRef) return;
		let n = e.vueFlowRef.querySelector(".vue-flow__transformationpane");
		if (!n) return;
		let i = window.getComputedStyle(n), { m22: a } = new window.DOMMatrixReadOnly(i.transform), o = [];
		for (let e of t) {
			let t = e, n = l(t.id);
			if (n) {
				let e = Lo(t.nodeElement);
				if (e.width && e.height && (n.dimensions.width !== e.width || n.dimensions.height !== e.height || t.forceUpdate)) {
					let r = t.nodeElement.getBoundingClientRect();
					n.dimensions = e, n.handleBounds.source = ac("source", t.nodeElement, r, a, n.id), n.handleBounds.target = ac("target", t.nodeElement, r, a, n.id), o.push({
						id: n.id,
						type: "dimensions",
						dimensions: e
					});
				}
			}
		}
		!e.fitViewOnInitDone && e.fitViewOnInit && r.value.fitView().then(() => {
			e.fitViewOnInitDone = !0;
		}), o.length && e.hooks.nodesChange.trigger(o);
	}, p = (r, i) => {
		let a = /* @__PURE__ */ new Set(), o = /* @__PURE__ */ new Set();
		for (let e of r) Ho(e) ? a.add(e.id) : Vo(e) && o.add(e.id);
		let s = ws(t.value, a, !0), c = ws(n.value, o);
		if (e.multiSelectionActive) {
			for (let e of a) s.push(bs(e, i));
			for (let e of o) c.push(bs(e, i));
		}
		s.length && e.hooks.nodesChange.trigger(s), c.length && e.hooks.edgesChange.trigger(c);
	}, m = (r) => {
		if (e.multiSelectionActive) {
			let t = r.map((e) => bs(e.id, !0));
			e.hooks.nodesChange.trigger(t);
		} else e.hooks.nodesChange.trigger(ws(t.value, new Set(r.map((e) => e.id)), !0)), e.hooks.edgesChange.trigger(ws(n.value));
	}, h = (r) => {
		if (e.multiSelectionActive) {
			let t = r.map((e) => bs(e.id, !0));
			e.hooks.edgesChange.trigger(t);
		} else e.hooks.edgesChange.trigger(ws(n.value, new Set(r.map((e) => e.id)))), e.hooks.nodesChange.trigger(ws(t.value, /* @__PURE__ */ new Set(), !0));
	}, g = (e) => {
		p(e, !0);
	}, _ = (t) => {
		let n = (t || e.nodes).map((e) => (e.selected = !1, bs(e.id, !1)));
		e.hooks.nodesChange.trigger(n);
	}, v = (t) => {
		let n = (t || e.edges).map((e) => (e.selected = !1, bs(e.id, !1)));
		e.hooks.edgesChange.trigger(n);
	}, y = (t) => {
		if (!t || !t.length) return p([], !1);
		let n = t.reduce((e, t) => {
			let n = bs(t.id, !1);
			return Ho(t) ? e.nodes.push(n) : e.edges.push(n), e;
		}, {
			nodes: [],
			edges: []
		});
		n.nodes.length && e.hooks.nodesChange.trigger(n.nodes), n.edges.length && e.hooks.edgesChange.trigger(n.edges);
	}, b = (t) => {
		var n;
		(n = e.d3Zoom) == null || n.scaleExtent([t, e.maxZoom]), e.minZoom = t;
	}, x = (t) => {
		var n;
		(n = e.d3Zoom) == null || n.scaleExtent([e.minZoom, t]), e.maxZoom = t;
	}, S = (t) => {
		var n;
		(n = e.d3Zoom) == null || n.translateExtent(t), e.translateExtent = t;
	}, C = (t) => {
		e.nodeExtent = t, i();
	}, w = (t) => {
		var n;
		(n = e.d3Zoom) == null || n.clickDistance(t);
	}, T = (t) => {
		e.nodesDraggable = t, e.nodesConnectable = t, e.elementsSelectable = t;
	}, E = (t) => {
		let n = t instanceof Function ? t(e.nodes) : t;
		(e.initialized || n.length) && (e.nodes = lc(n, l, e.hooks.error.trigger));
	}, D = (t) => {
		let r = t instanceof Function ? t(e.edges) : t;
		if (!e.initialized && !r.length) return;
		let i = pc(r, e.isValidConnection, l, u, e.hooks.error.trigger, e.defaultEdgeOptions, e.nodes, e.edges);
		dc(e.connectionLookup, n.value, i), e.edges = i;
	}, O = (t) => {
		let n = t instanceof Function ? t([...e.nodes, ...e.edges]) : t;
		(e.initialized || n.length) && (E(n.filter(Ho)), D(n.filter(Vo)));
	}, k = (t) => {
		let n = t instanceof Function ? t(e.nodes) : t;
		n = Array.isArray(n) ? n : [n];
		let r = lc(n, l, e.hooks.error.trigger), i = [];
		for (let e of r) i.push(xs(e));
		i.length && e.hooks.nodesChange.trigger(i);
	}, A = (t) => {
		let n = t instanceof Function ? t(e.edges) : t;
		n = Array.isArray(n) ? n : [n];
		let r = pc(n, e.isValidConnection, l, u, e.hooks.error.trigger, e.defaultEdgeOptions, e.nodes, e.edges), i = [];
		for (let e of r) i.push(xs(e));
		i.length && e.hooks.edgesChange.trigger(i);
	}, j = (t, n = !0, r = !1) => {
		let i = t instanceof Function ? t(e.nodes) : t, a = Array.isArray(i) ? i : [i], o = [], c = [];
		function u(e) {
			let t = s(e);
			for (let e of t) (!Q(e.deletable) || e.deletable) && c.push(Cs(e.id, e.source, e.target, e.sourceHandle, e.targetHandle));
		}
		function d(t) {
			let r = [];
			for (let n of e.nodes) n.parentNode === t && r.push(n);
			if (r.length) {
				for (let e of r) o.push(Ss(e.id));
				n && u(r);
				for (let e of r) d(e.id);
			}
		}
		for (let e of a) {
			let t = typeof e == "string" ? l(e) : e;
			t && (!Q(t.deletable) || t.deletable) && (o.push(Ss(t.id)), n && u([t]), r && d(t.id));
		}
		c.length && e.hooks.edgesChange.trigger(c), o.length && e.hooks.nodesChange.trigger(o);
	}, M = (t) => {
		let n = t instanceof Function ? t(e.edges) : t, r = Array.isArray(n) ? n : [n], i = [];
		for (let e of r) {
			let t = typeof e == "string" ? u(e) : e;
			t && (!Q(t.deletable) || t.deletable) && i.push(Cs(typeof e == "string" ? e : e.id, t.source, t.target, t.sourceHandle, t.targetHandle));
		}
		e.hooks.edgesChange.trigger(i);
	}, N = (t, r, i = !0) => {
		let a = u(t.id);
		if (!a) return !1;
		let o = e.edges.indexOf(a), s = cc(t, r, a, i, e.hooks.error.trigger);
		if (s) {
			let [t] = pc([s], e.isValidConnection, l, u, e.hooks.error.trigger, e.defaultEdgeOptions, e.nodes, e.edges);
			return e.edges = e.edges.map((e, n) => n === o ? t : e), dc(e.connectionLookup, n.value, [t]), t;
		}
		return !1;
	}, P = (e, t, n = { replace: !1 }) => {
		let r = u(e);
		if (!r) return;
		let i = typeof t == "function" ? t(r) : t;
		r.data = n.replace ? i : {
			...r.data,
			...i
		};
	}, F = (t) => ys(t, e.nodes), I = (t) => {
		let r = ys(t, e.edges);
		return dc(e.connectionLookup, n.value, r), r;
	}, L = (t, n, r = { replace: !1 }) => {
		let i = l(t);
		if (!i) return;
		let a = typeof n == "function" ? n(i) : n;
		r.replace ? e.nodes.splice(e.nodes.indexOf(i), 1, a) : Object.assign(i, a);
	}, R = (e, t, n = { replace: !1 }) => {
		let r = l(e);
		if (!r) return;
		let i = typeof t == "function" ? t(r) : t;
		r.data = n.replace ? i : {
			...r.data,
			...i
		};
	}, z = (t, n, r = !1) => {
		r ? e.connectionClickStartHandle = t : e.connectionStartHandle = t, e.connectionEndHandle = null, e.connectionStatus = null, n && (e.connectionPosition = n);
	}, ee = (t, n = null, r = null) => {
		e.connectionStartHandle && (e.connectionPosition = t, e.connectionEndHandle = n, e.connectionStatus = r);
	}, B = (t, n) => {
		e.connectionPosition = {
			x: NaN,
			y: NaN
		}, e.connectionEndHandle = null, e.connectionStatus = null, n ? e.connectionClickStartHandle = null : e.connectionStartHandle = null;
	}, V = (e) => {
		let t = Go(e), n = t ? null : Uo(e) ? e : l(e.id);
		return !t && !n ? [
			null,
			null,
			t
		] : [
			t ? e : Fo(n),
			n,
			t
		];
	}, H = (t, n = !0, r = e.nodes) => {
		let [i, a, o] = V(t);
		if (!i) return [];
		let s = [];
		for (let t of r || e.nodes) {
			if (!o && (t.id === a.id || !t.computedPosition)) continue;
			let e = Fo(t), r = Io(e, i);
			(n && r > 0 || r >= e.width * e.height || r >= Number(i.width) * Number(i.height)) && s.push(t);
		}
		return s;
	}, U = (e, t, n = !0) => {
		let [r] = V(e);
		if (!r) return !1;
		let i = Io(r, t);
		return n && i > 0 || i >= Number(r.width) * Number(r.height);
	}, W = (t) => {
		let { viewport: n, dimensions: r, d3Zoom: i, d3Selection: a, translateExtent: o } = e;
		if (!i || !a || !t.x && !t.y) return !1;
		let s = ao.translate(n.x + t.x, n.y + t.y).scale(n.zoom), c = [[0, 0], [r.width, r.height]], l = i.constrain()(s, c, o), u = e.viewport.x !== l.x || e.viewport.y !== l.y || e.viewport.zoom !== l.k;
		return i.transform(a, l), u;
	}, G = (t) => {
		let n = t instanceof Function ? t(e) : t, r = [
			"d3Zoom",
			"d3Selection",
			"d3ZoomHandler",
			"viewportRef",
			"vueFlowRef",
			"dimensions",
			"hooks"
		];
		Q(n.defaultEdgeOptions) && (e.defaultEdgeOptions = n.defaultEdgeOptions);
		let i = n.modelValue || n.nodes || n.edges ? [] : void 0;
		i && (n.modelValue && i.push(...n.modelValue), n.nodes && i.push(...n.nodes), n.edges && i.push(...n.edges), O(i));
		let a = () => {
			Q(n.maxZoom) && x(n.maxZoom), Q(n.minZoom) && b(n.minZoom), Q(n.translateExtent) && S(n.translateExtent);
		};
		for (let t of Object.keys(n)) {
			let i = t, a = n[i];
			![...Hc, ...r].includes(i) && Q(a) && (e[i] = a);
		}
		_e(() => e.d3Zoom).not.toBeNull().then(a), e.initialized || (e.initialized = !0);
	};
	return {
		updateNodePositions: d,
		updateNodeDimensions: f,
		setElements: O,
		setNodes: E,
		setEdges: D,
		addNodes: k,
		addEdges: A,
		removeNodes: j,
		removeEdges: M,
		findNode: l,
		findEdge: u,
		updateEdge: N,
		updateEdgeData: P,
		updateNode: L,
		updateNodeData: R,
		applyEdgeChanges: I,
		applyNodeChanges: F,
		addSelectedElements: g,
		addSelectedNodes: m,
		addSelectedEdges: h,
		setMinZoom: b,
		setMaxZoom: x,
		setTranslateExtent: S,
		setNodeExtent: C,
		setPaneClickDistance: w,
		removeSelectedElements: y,
		removeSelectedNodes: _,
		removeSelectedEdges: v,
		startConnection: z,
		updateConnection: ee,
		endConnection: B,
		setInteractive: T,
		setState: G,
		getIntersectingNodes: H,
		getIncomers: a,
		getOutgoers: o,
		getConnectedEdges: s,
		getHandleConnections: c,
		isNodeIntersecting: U,
		panBy: W,
		fitView: (e) => r.value.fitView(e),
		zoomIn: (e) => r.value.zoomIn(e),
		zoomOut: (e) => r.value.zoomOut(e),
		zoomTo: (e, t) => r.value.zoomTo(e, t),
		setViewport: (e, t) => r.value.setViewport(e, t),
		setTransform: (e, t) => r.value.setTransform(e, t),
		getViewport: () => r.value.getViewport(),
		getTransform: () => r.value.getTransform(),
		setCenter: (e, t, n) => r.value.setCenter(e, t, n),
		fitBounds: (e, t) => r.value.fitBounds(e, t),
		project: (e) => r.value.project(e),
		screenToFlowCoordinate: (e) => r.value.screenToFlowCoordinate(e),
		flowToScreenCoordinate: (e) => r.value.flowToScreenCoordinate(e),
		toObject: () => {
			let t = [], n = [];
			for (let n of e.nodes) {
				let { computedPosition: e, handleBounds: r, selected: i, dimensions: a, isParent: o, resizing: s, dragging: c, events: l, ...u } = n;
				t.push(u);
			}
			for (let t of e.edges) {
				let { selected: e, sourceNode: r, targetNode: i, events: a, ...o } = t;
				n.push(o);
			}
			return JSON.parse(JSON.stringify({
				nodes: t,
				edges: n,
				position: [e.viewport.x, e.viewport.y],
				zoom: e.viewport.zoom,
				viewport: e.viewport
			}));
		},
		fromObject: (t) => new Promise((n) => {
			let { nodes: i, edges: a, position: o, zoom: s, viewport: c } = t;
			i && E(i), a && D(a);
			let [l, u] = c?.x && c?.y ? [c.x, c.y] : o ?? [null, null];
			if (l && u) {
				let t = c?.zoom || s || e.viewport.zoom;
				return _e(() => r.value.viewportInitialized).toBe(!0).then(() => {
					r.value.setViewport({
						x: l,
						y: u,
						zoom: t
					}).then(() => {
						n(!0);
					});
				});
			}
			n(!0);
		}),
		updateNodeInternals: i,
		viewportHelper: r,
		$reset: () => {
			let t = Vc();
			if (e.edges = [], e.nodes = [], e.d3Zoom && e.d3Selection) {
				let n = ao.translate(t.defaultViewport.x ?? 0, t.defaultViewport.y ?? 0).scale(Ro(t.defaultViewport.zoom ?? 1, t.minZoom, t.maxZoom)), r = e.viewportRef.getBoundingClientRect(), i = [[0, 0], [r.width, r.height]], a = e.d3Zoom.constrain()(n, i, t.translateExtent);
				e.d3Zoom.transform(e.d3Selection, a);
			}
			G(t);
		},
		$destroy: () => {}
	};
}
var Wc = [
	"data-id",
	"data-handleid",
	"data-nodeid",
	"data-handlepos"
], Gc = /* @__PURE__ */ d({
	name: "Handle",
	compatConfig: { MODE: 3 },
	props: {
		id: { default: null },
		type: {},
		position: { default: () => J.Top },
		isValidConnection: { type: Function },
		connectable: {
			type: [
				Boolean,
				Number,
				String,
				Function
			],
			default: void 0
		},
		connectableStart: {
			type: Boolean,
			default: !0
		},
		connectableEnd: {
			type: Boolean,
			default: !0
		}
	},
	setup(e, { expose: t }) {
		let r = s(e, [
			"position",
			"connectable",
			"connectableStart",
			"connectableEnd",
			"id"
		]), i = B(() => r.type ?? "source"), o = B(() => r.isValidConnection ?? null), { id: c, connectionStartHandle: l, connectionClickStartHandle: u, connectionEndHandle: d, vueFlowRef: f, nodesConnectable: p, noDragClassName: m, noPanClassName: h } = $(), { id: g, node: _, nodeEl: v, connectedEdges: y } = Dc(), b = P(), x = B(() => e.connectableStart === void 0 || e.connectableStart), C = B(() => e.connectableEnd === void 0 || e.connectableEnd), w = B(() => l.value?.nodeId === g && l.value?.id === e.id && l.value?.type === i.value || d.value?.nodeId === g && d.value?.id === e.id && d.value?.type === i.value), T = B(() => u.value?.nodeId === g && u.value?.id === e.id && u.value?.type === i.value), { handlePointerDown: E, handleClick: O } = Tc({
			nodeId: g,
			handleId: e.id,
			isValidConnection: o,
			type: i
		}), k = n(() => typeof e.connectable == "string" && e.connectable === "single" ? !y.value.some((t) => {
			let n = t[`${i.value}Handle`];
			return t[i.value] === g ? !n || n === e.id : !1;
		}) : typeof e.connectable == "number" ? y.value.filter((t) => {
			let n = t[`${i.value}Handle`];
			return t[i.value] === g ? !n || n === e.id : !1;
		}).length < e.connectable : typeof e.connectable == "function" ? e.connectable(_, y.value) : Q(e.connectable) ? e.connectable : p.value);
		D(() => {
			if (!_.dimensions.width || !_.dimensions.height) return;
			let t = _.handleBounds[i.value]?.find((t) => t.id === e.id);
			if (!f.value || t) return;
			let n = f.value.querySelector(".vue-flow__transformationpane");
			if (!v.value || !b.value || !n || !e.id) return;
			let r = v.value.getBoundingClientRect(), a = b.value.getBoundingClientRect(), o = window.getComputedStyle(n), { m22: s } = new window.DOMMatrixReadOnly(o.transform), c = {
				id: e.id,
				position: e.position,
				x: (a.left - r.left) / s,
				y: (a.top - r.top) / s,
				type: i.value,
				nodeId: g,
				...Lo(b.value)
			};
			_.handleBounds[i.value] = [..._.handleBounds[i.value] ?? [], c];
		});
		function j(e) {
			let t = zs(e);
			k.value && x.value && (t && e.button === 0 || !t) && E(e);
		}
		function M(e) {
			g && (u.value || x.value) && k.value && O(e);
		}
		return t({
			handleClick: O,
			handlePointerDown: E,
			onClick: M,
			onPointerDown: j
		}), (t, n) => (A(), a("div", {
			ref_key: "handle",
			ref: b,
			"data-id": `${U(c)}-${U(g)}-${e.id}-${i.value}`,
			"data-handleid": e.id,
			"data-nodeid": U(g),
			"data-handlepos": t.position,
			class: S(["vue-flow__handle", [
				`vue-flow__handle-${t.position}`,
				`vue-flow__handle-${e.id}`,
				U(m),
				U(h),
				i.value,
				{
					connectable: k.value,
					connecting: T.value,
					connectablestart: x.value,
					connectableend: C.value,
					connectionindicator: k.value && (x.value && !w.value || C.value && w.value)
				}
			]]),
			onMousedown: j,
			onTouchstartPassive: j,
			onClick: M
		}, [I(t.$slots, "default", { id: t.id })], 42, Wc));
	}
}), Kc = function({ sourcePosition: e = J.Bottom, targetPosition: n = J.Top, label: r, connectable: i = !0, isValidTargetPos: a, isValidSourcePos: o, data: s }) {
	let c = s.label ?? r;
	return [
		h(Gc, {
			type: "target",
			position: n,
			connectable: i,
			isValidConnection: a
		}),
		typeof c != "string" && c ? h(c) : h(t, [c]),
		h(Gc, {
			type: "source",
			position: e,
			connectable: i,
			isValidConnection: o
		})
	];
};
Kc.props = [
	"sourcePosition",
	"targetPosition",
	"label",
	"isValidTargetPos",
	"isValidSourcePos",
	"connectable",
	"data"
], Kc.inheritAttrs = !1, Kc.compatConfig = { MODE: 3 };
var qc = Kc, Jc = function({ targetPosition: e = J.Top, label: n, connectable: r = !0, isValidTargetPos: i, data: a }) {
	let o = a.label ?? n;
	return [h(Gc, {
		type: "target",
		position: e,
		connectable: r,
		isValidConnection: i
	}), typeof o != "string" && o ? h(o) : h(t, [o])];
};
Jc.props = [
	"targetPosition",
	"label",
	"isValidTargetPos",
	"connectable",
	"data"
], Jc.inheritAttrs = !1, Jc.compatConfig = { MODE: 3 };
var Yc = Jc, Xc = function({ sourcePosition: e = J.Bottom, label: n, connectable: r = !0, isValidSourcePos: i, data: a }) {
	let o = a.label ?? n;
	return [typeof o != "string" && o ? h(o) : h(t, [o]), h(Gc, {
		type: "source",
		position: e,
		connectable: r,
		isValidConnection: i
	})];
};
Xc.props = [
	"sourcePosition",
	"label",
	"isValidSourcePos",
	"connectable",
	"data"
], Xc.inheritAttrs = !1, Xc.compatConfig = { MODE: 3 };
var Zc = Xc, Qc = ["transform"], $c = [
	"width",
	"height",
	"x",
	"y",
	"rx",
	"ry"
], el = ["y"], tl = /* @__PURE__ */ d({
	name: "EdgeText",
	compatConfig: { MODE: 3 },
	props: {
		x: {},
		y: {},
		label: {},
		labelStyle: { default: () => ({}) },
		labelShowBg: {
			type: Boolean,
			default: !0
		},
		labelBgStyle: { default: () => ({}) },
		labelBgPadding: { default: () => [2, 4] },
		labelBgBorderRadius: { default: 2 }
	},
	setup(e) {
		let s = P({
			x: 0,
			y: 0,
			width: 0,
			height: 0
		}), l = P(null), u = n(() => `translate(${e.x - s.value.width / 2} ${e.y - s.value.height / 2})`);
		D(d), K([
			() => e.x,
			() => e.y,
			l,
			() => e.label
		], d);
		function d() {
			if (!l.value) return;
			let e = l.value.getBBox();
			(e.width !== s.value.width || e.height !== s.value.height) && (s.value = e);
		}
		return (e, n) => (A(), a("g", {
			transform: u.value,
			class: "vue-flow__edge-textwrapper"
		}, [e.labelShowBg ? (A(), a("rect", {
			key: 0,
			class: "vue-flow__edge-textbg",
			width: `${s.value.width + 2 * e.labelBgPadding[0]}px`,
			height: `${s.value.height + 2 * e.labelBgPadding[1]}px`,
			x: -e.labelBgPadding[0],
			y: -e.labelBgPadding[1],
			style: w(e.labelBgStyle),
			rx: e.labelBgBorderRadius,
			ry: e.labelBgBorderRadius
		}, null, 12, $c)) : i("", !0), o("text", b(e.$attrs, {
			ref_key: "el",
			ref: l,
			class: "vue-flow__edge-text",
			y: s.value.height / 2,
			dy: "0.3em",
			style: e.labelStyle
		}), [I(e.$slots, "default", {}, () => [typeof e.label == "string" ? (A(), a(t, { key: 1 }, [c(ee(e.label), 1)], 64)) : (A(), r(R(e.label), { key: 0 }))])], 16, el)], 8, Qc));
	}
}), nl = [
	"id",
	"d",
	"marker-end",
	"marker-start"
], rl = ["d", "stroke-width"], il = /* @__PURE__ */ d({
	name: "BaseEdge",
	inheritAttrs: !1,
	compatConfig: { MODE: 3 },
	props: {
		id: {},
		labelX: {},
		labelY: {},
		path: {},
		label: {},
		markerStart: {},
		markerEnd: {},
		interactionWidth: { default: 20 },
		labelStyle: {},
		labelShowBg: { type: Boolean },
		labelBgStyle: {},
		labelBgPadding: {},
		labelBgBorderRadius: {}
	},
	setup(e, { expose: n }) {
		let s = P(null), c = P(null), l = P(null), u = W();
		return n({
			pathEl: s,
			interactionEl: c,
			labelEl: l
		}), (e, n) => (A(), a(t, null, [
			o("path", b(U(u), {
				id: e.id,
				ref_key: "pathEl",
				ref: s,
				d: e.path,
				class: "vue-flow__edge-path",
				"marker-end": e.markerEnd,
				"marker-start": e.markerStart
			}), null, 16, nl),
			e.interactionWidth ? (A(), a("path", {
				key: 0,
				ref_key: "interactionEl",
				ref: c,
				fill: "none",
				d: e.path,
				"stroke-width": e.interactionWidth,
				"stroke-opacity": 0,
				class: "vue-flow__edge-interaction"
			}, null, 8, rl)) : i("", !0),
			e.label && e.labelX && e.labelY ? (A(), r(tl, {
				key: 1,
				ref_key: "labelEl",
				ref: l,
				x: e.labelX,
				y: e.labelY,
				label: e.label,
				"label-show-bg": e.labelShowBg,
				"label-bg-style": e.labelBgStyle,
				"label-bg-padding": e.labelBgPadding,
				"label-bg-border-radius": e.labelBgBorderRadius,
				"label-style": e.labelStyle
			}, null, 8, [
				"x",
				"y",
				"label",
				"label-show-bg",
				"label-bg-style",
				"label-bg-padding",
				"label-bg-border-radius",
				"label-style"
			])) : i("", !0)
		], 64));
	}
});
function al({ sourceX: e, sourceY: t, targetX: n, targetY: r }) {
	let i = Math.abs(n - e) / 2, a = n < e ? n + i : n - i, o = Math.abs(r - t) / 2;
	return [
		a,
		r < t ? r + o : r - o,
		i,
		o
	];
}
function ol({ sourceX: e, sourceY: t, targetX: n, targetY: r, sourceControlX: i, sourceControlY: a, targetControlX: o, targetControlY: s }) {
	let c = e * .125 + i * .375 + o * .375 + n * .125, l = t * .125 + a * .375 + s * .375 + r * .125;
	return [
		c,
		l,
		Math.abs(c - e),
		Math.abs(l - t)
	];
}
function sl(e, t) {
	return e >= 0 ? .5 * e : t * 25 * Math.sqrt(-e);
}
function cl({ pos: e, x1: t, y1: n, x2: r, y2: i, c: a }) {
	let o, s;
	switch (e) {
		case J.Left:
			o = t - sl(t - r, a), s = n;
			break;
		case J.Right:
			o = t + sl(r - t, a), s = n;
			break;
		case J.Top:
			o = t, s = n - sl(n - i, a);
			break;
		case J.Bottom: o = t, s = n + sl(i - n, a);
	}
	return [o, s];
}
function ll(e) {
	let { sourceX: t, sourceY: n, sourcePosition: r = J.Bottom, targetX: i, targetY: a, targetPosition: o = J.Top, curvature: s = .25 } = e, [c, l] = cl({
		pos: r,
		x1: t,
		y1: n,
		x2: i,
		y2: a,
		c: s
	}), [u, d] = cl({
		pos: o,
		x1: i,
		y1: a,
		x2: t,
		y2: n,
		c: s
	}), [f, p, m, h] = ol({
		sourceX: t,
		sourceY: n,
		targetX: i,
		targetY: a,
		sourceControlX: c,
		sourceControlY: l,
		targetControlX: u,
		targetControlY: d
	});
	return [
		`M${t},${n} C${c},${l} ${u},${d} ${i},${a}`,
		f,
		p,
		m,
		h
	];
}
function ul({ pos: e, x1: t, y1: n, x2: r, y2: i }) {
	let a, o;
	switch (e) {
		case J.Left:
		case J.Right:
			a = .5 * (t + r), o = n;
			break;
		case J.Top:
		case J.Bottom: a = t, o = .5 * (n + i);
	}
	return [a, o];
}
function dl(e) {
	let { sourceX: t, sourceY: n, sourcePosition: r = J.Bottom, targetX: i, targetY: a, targetPosition: o = J.Top } = e, [s, c] = ul({
		pos: r,
		x1: t,
		y1: n,
		x2: i,
		y2: a
	}), [l, u] = ul({
		pos: o,
		x1: i,
		y1: a,
		x2: t,
		y2: n
	}), [d, f, p, m] = ol({
		sourceX: t,
		sourceY: n,
		targetX: i,
		targetY: a,
		sourceControlX: s,
		sourceControlY: c,
		targetControlX: l,
		targetControlY: u
	});
	return [
		`M${t},${n} C${s},${c} ${l},${u} ${i},${a}`,
		d,
		f,
		p,
		m
	];
}
var fl = {
	[J.Left]: {
		x: -1,
		y: 0
	},
	[J.Right]: {
		x: 1,
		y: 0
	},
	[J.Top]: {
		x: 0,
		y: -1
	},
	[J.Bottom]: {
		x: 0,
		y: 1
	}
};
function pl({ source: e, sourcePosition: t = J.Bottom, target: n }) {
	return t === J.Left || t === J.Right ? e.x < n.x ? {
		x: 1,
		y: 0
	} : {
		x: -1,
		y: 0
	} : e.y < n.y ? {
		x: 0,
		y: 1
	} : {
		x: 0,
		y: -1
	};
}
function ml(e, t) {
	return Math.sqrt((t.x - e.x) ** 2 + (t.y - e.y) ** 2);
}
function hl({ source: e, sourcePosition: t = J.Bottom, target: n, targetPosition: r = J.Top, center: i, offset: a }) {
	let o = fl[t], s = fl[r], c = {
		x: e.x + o.x * a,
		y: e.y + o.y * a
	}, l = {
		x: n.x + s.x * a,
		y: n.y + s.y * a
	}, u = pl({
		source: c,
		sourcePosition: t,
		target: l
	}), d = u.x === 0 ? "y" : "x", f = u[d], p, m, h, g = {
		x: 0,
		y: 0
	}, _ = {
		x: 0,
		y: 0
	}, [v, y, b, x] = al({
		sourceX: e.x,
		sourceY: e.y,
		targetX: n.x,
		targetY: n.y
	});
	if (o[d] * s[d] === -1) {
		m = i.x ?? v, h = i.y ?? y;
		let e = [{
			x: m,
			y: c.y
		}, {
			x: m,
			y: l.y
		}], t = [{
			x: c.x,
			y: h
		}, {
			x: l.x,
			y: h
		}];
		p = o[d] === f ? d === "x" ? e : t : d === "x" ? t : e;
	} else {
		let i = [{
			x: c.x,
			y: l.y
		}], u = [{
			x: l.x,
			y: c.y
		}];
		if (p = d === "x" ? o.x === f ? u : i : o.y === f ? i : u, t === r) {
			let t = Math.abs(e[d] - n[d]);
			if (t <= a) {
				let r = Math.min(a - 1, a - t);
				o[d] === f ? g[d] = (c[d] > e[d] ? -1 : 1) * r : _[d] = (l[d] > n[d] ? -1 : 1) * r;
			}
		}
		if (t !== r) {
			let e = d === "x" ? "y" : "x", t = o[d] === s[e], n = c[e] > l[e], r = c[e] < l[e];
			(o[d] === 1 && (!t && n || t && r) || o[d] !== 1 && (!t && r || t && n)) && (p = d === "x" ? i : u);
		}
		let v = {
			x: c.x + g.x,
			y: c.y + g.y
		}, y = {
			x: l.x + _.x,
			y: l.y + _.y
		};
		Math.max(Math.abs(v.x - p[0].x), Math.abs(y.x - p[0].x)) >= Math.max(Math.abs(v.y - p[0].y), Math.abs(y.y - p[0].y)) ? (m = (v.x + y.x) / 2, h = p[0].y) : (m = p[0].x, h = (v.y + y.y) / 2);
	}
	return [
		[
			e,
			{
				x: c.x + g.x,
				y: c.y + g.y
			},
			...p,
			{
				x: l.x + _.x,
				y: l.y + _.y
			},
			n
		],
		m,
		h,
		b,
		x
	];
}
function gl(e, t, n, r) {
	let i = Math.min(ml(e, t) / 2, ml(t, n) / 2, r), { x: a, y: o } = t;
	if (e.x === a && a === n.x || e.y === o && o === n.y) return `L${a} ${o}`;
	if (e.y === o) {
		let t = e.x < n.x ? -1 : 1, r = e.y < n.y ? 1 : -1;
		return `L ${a + i * t},${o}Q ${a},${o} ${a},${o + i * r}`;
	}
	let s = e.x < n.x ? 1 : -1;
	return `L ${a},${o + i * (e.y < n.y ? -1 : 1)}Q ${a},${o} ${a + i * s},${o}`;
}
function _l(e) {
	let { sourceX: t, sourceY: n, sourcePosition: r = J.Bottom, targetX: i, targetY: a, targetPosition: o = J.Top, borderRadius: s = 5, centerX: c, centerY: l, offset: u = 20 } = e, [d, f, p, m, h] = hl({
		source: {
			x: t,
			y: n
		},
		sourcePosition: r,
		target: {
			x: i,
			y: a
		},
		targetPosition: o,
		center: {
			x: c,
			y: l
		},
		offset: u
	});
	return [
		d.reduce((e, t, n) => {
			let r;
			return r = n > 0 && n < d.length - 1 ? gl(d[n - 1], t, d[n + 1], s) : `${n === 0 ? "M" : "L"}${t.x} ${t.y}`, e += r, e;
		}, ""),
		f,
		p,
		m,
		h
	];
}
function vl(e) {
	let { sourceX: t, sourceY: n, targetX: r, targetY: i } = e, [a, o, s, c] = al({
		sourceX: t,
		sourceY: n,
		targetX: r,
		targetY: i
	});
	return [
		`M ${t},${n}L ${r},${i}`,
		a,
		o,
		s,
		c
	];
}
var yl = d({
	name: "StraightEdge",
	props: [
		"label",
		"labelStyle",
		"labelShowBg",
		"labelBgStyle",
		"labelBgPadding",
		"labelBgBorderRadius",
		"sourceY",
		"sourceX",
		"targetX",
		"targetY",
		"markerEnd",
		"markerStart",
		"interactionWidth"
	],
	compatConfig: { MODE: 3 },
	setup(e, { attrs: t }) {
		return () => {
			let [n, r, i] = vl(e);
			return h(il, {
				path: n,
				labelX: r,
				labelY: i,
				...t,
				...e
			});
		};
	}
}), bl = d({
	name: "SmoothStepEdge",
	props: [
		"sourcePosition",
		"targetPosition",
		"label",
		"labelStyle",
		"labelShowBg",
		"labelBgStyle",
		"labelBgPadding",
		"labelBgBorderRadius",
		"sourceY",
		"sourceX",
		"targetX",
		"targetY",
		"borderRadius",
		"markerEnd",
		"markerStart",
		"interactionWidth",
		"offset"
	],
	compatConfig: { MODE: 3 },
	setup(e, { attrs: t }) {
		return () => {
			let [n, r, i] = _l({
				...e,
				sourcePosition: e.sourcePosition ?? J.Bottom,
				targetPosition: e.targetPosition ?? J.Top
			});
			return h(il, {
				path: n,
				labelX: r,
				labelY: i,
				...t,
				...e
			});
		};
	}
}), xl = d({
	name: "StepEdge",
	props: [
		"sourcePosition",
		"targetPosition",
		"label",
		"labelStyle",
		"labelShowBg",
		"labelBgStyle",
		"labelBgPadding",
		"labelBgBorderRadius",
		"sourceY",
		"sourceX",
		"targetX",
		"targetY",
		"markerEnd",
		"markerStart",
		"interactionWidth"
	],
	setup(e, { attrs: t }) {
		return () => h(bl, {
			...e,
			...t,
			borderRadius: 0
		});
	}
}), Sl = d({
	name: "BezierEdge",
	props: [
		"sourcePosition",
		"targetPosition",
		"label",
		"labelStyle",
		"labelShowBg",
		"labelBgStyle",
		"labelBgPadding",
		"labelBgBorderRadius",
		"sourceY",
		"sourceX",
		"targetX",
		"targetY",
		"curvature",
		"markerEnd",
		"markerStart",
		"interactionWidth"
	],
	compatConfig: { MODE: 3 },
	setup(e, { attrs: t }) {
		return () => {
			let [n, r, i] = ll({
				...e,
				sourcePosition: e.sourcePosition ?? J.Bottom,
				targetPosition: e.targetPosition ?? J.Top
			});
			return h(il, {
				path: n,
				labelX: r,
				labelY: i,
				...t,
				...e
			});
		};
	}
}), Cl = d({
	name: "SimpleBezierEdge",
	props: [
		"sourcePosition",
		"targetPosition",
		"label",
		"labelStyle",
		"labelShowBg",
		"labelBgStyle",
		"labelBgPadding",
		"labelBgBorderRadius",
		"sourceY",
		"sourceX",
		"targetX",
		"targetY",
		"markerEnd",
		"markerStart",
		"interactionWidth"
	],
	compatConfig: { MODE: 3 },
	setup(e, { attrs: t }) {
		return () => {
			let [n, r, i] = dl({
				...e,
				sourcePosition: e.sourcePosition ?? J.Bottom,
				targetPosition: e.targetPosition ?? J.Top
			});
			return h(il, {
				path: n,
				labelX: r,
				labelY: i,
				...t,
				...e
			});
		};
	}
}), wl = {
	input: Zc,
	default: qc,
	output: Yc
}, Tl = {
	default: Sl,
	straight: yl,
	step: xl,
	smoothstep: bl,
	simplebezier: Cl
};
function El(e, t, r) {
	let i = n(() => (e) => t.value.get(e)), a = n(() => (e) => r.value.get(e)), o = n(() => {
		let t = {
			...Tl,
			...e.edgeTypes
		}, n = Object.keys(t);
		for (let r of e.edges) r.type && !n.includes(r.type) && (t[r.type] = r.type);
		return t;
	}), s = n(() => {
		let t = {
			...wl,
			...e.nodeTypes
		}, n = Object.keys(t);
		for (let r of e.nodes) r.type && !n.includes(r.type) && (t[r.type] = r.type);
		return t;
	}), c = n(() => e.onlyRenderVisibleElements ? os(e.nodes, {
		x: 0,
		y: 0,
		width: e.dimensions.width,
		height: e.dimensions.height
	}, e.viewport, !0) : e.nodes), l = n(() => {
		if (e.onlyRenderVisibleElements) {
			let n = [];
			for (let r of e.edges) {
				let i = t.value.get(r.source), a = t.value.get(r.target);
				Is({
					sourcePos: i.computedPosition || {
						x: 0,
						y: 0
					},
					targetPos: a.computedPosition || {
						x: 0,
						y: 0
					},
					sourceWidth: i.dimensions.width,
					sourceHeight: i.dimensions.height,
					targetWidth: a.dimensions.width,
					targetHeight: a.dimensions.height,
					width: e.dimensions.width,
					height: e.dimensions.height,
					viewport: e.viewport
				}) && n.push(r);
			}
			return n;
		}
		return e.edges;
	}), u = n(() => [...c.value, ...l.value]), d = n(() => {
		let t = [];
		for (let n of e.nodes) n.selected && t.push(n);
		return t;
	}), f = n(() => {
		let t = [];
		for (let n of e.edges) n.selected && t.push(n);
		return t;
	}), p = n(() => [...d.value, ...f.value]), m = n(() => {
		let t = [];
		for (let n of e.nodes) n.dimensions.width && n.dimensions.height && n.handleBounds !== void 0 && t.push(n);
		return t;
	});
	return {
		getNode: i,
		getEdge: a,
		getElements: u,
		getEdgeTypes: o,
		getNodeTypes: s,
		getEdges: l,
		getNodes: c,
		getSelectedElements: p,
		getSelectedNodes: d,
		getSelectedEdges: f,
		getNodesInitialized: m,
		areNodesInitialized: n(() => c.value.length > 0 && m.value.length === c.value.length)
	};
}
var Dl = class e {
	constructor() {
		this.currentId = 0, this.flows = /* @__PURE__ */ new Map();
	}
	static getInstance() {
		let t = p()?.appContext.app, n = t?.config.globalProperties.$vueFlowStorage ?? e.instance;
		return e.instance = n ?? new e(), t && (t.config.globalProperties.$vueFlowStorage = e.instance), e.instance;
	}
	set(e, t) {
		return this.flows.set(e, t);
	}
	get(e) {
		return this.flows.get(e);
	}
	remove(e) {
		return this.flows.delete(e);
	}
	create(e, t) {
		let r = Vc(), i = M(r), a = {};
		for (let [e, t] of Object.entries(i.hooks)) {
			let n = `on${e.charAt(0).toUpperCase() + e.slice(1)}`;
			a[n] = t.on;
		}
		let o = {};
		for (let [e, t] of Object.entries(i.hooks)) o[e] = t.trigger;
		let s = n(() => {
			let e = /* @__PURE__ */ new Map();
			for (let t of i.nodes) e.set(t.id, t);
			return e;
		}), c = n(() => {
			let e = /* @__PURE__ */ new Map();
			for (let t of i.edges) e.set(t.id, t);
			return e;
		}), l = El(i, s, c), u = Uc(i, s, c);
		u.setState({
			...i,
			...t
		});
		let d = {
			...a,
			...l,
			...u,
			...he(i),
			nodeLookup: s,
			edgeLookup: c,
			emits: o,
			id: e,
			vueFlowVersion: "1.48.2",
			$destroy: () => {
				this.remove(e);
			}
		};
		return this.set(e, d), d;
	}
	getId() {
		return `vue-flow-${this.currentId++}`;
	}
};
function $(e) {
	let t = Dl.getInstance(), n = m(), r = typeof e == "object", i = r ? e : { id: e }, a = i.id, o = a ?? n?.vueFlowId, s;
	if (n) {
		let e = g(mc, null);
		e == null || o && e.id !== o || (s = e);
	}
	if (s || o && (s = t.get(o)), !s || o && s.id !== o) {
		let e = a ?? t.getId(), r = t.create(e, i);
		s = r, (n ?? f(!0)).run(() => {
			K(r.applyDefault, (e, t, n) => {
				let i = (e) => {
					r.applyNodeChanges(e);
				}, a = (e) => {
					r.applyEdgeChanges(e);
				};
				e ? (r.onNodesChange(i), r.onEdgesChange(a)) : (r.hooks.value.nodesChange.off(i), r.hooks.value.edgesChange.off(a)), n(() => {
					r.hooks.value.nodesChange.off(i), r.hooks.value.edgesChange.off(a);
				});
			}, { immediate: !0 }), ne(() => {
				if (s) {
					let e = t.get(s.id);
					e ? e.$destroy() : rc(`No store instance found for id ${s.id} in storage.`);
				}
			});
		});
	} else r && s.setState(i);
	return n && (j(mc, s), n.vueFlowId = s.id), r && p()?.type.name !== "VueFlow" && s.emits.error(new Z(X.USEVUEFLOW_OPTIONS)), s;
}
function Ol(e) {
	let { emits: t, dimensions: n } = $(), r;
	D(() => {
		let i = () => {
			var r;
			if (!e.value || !((r = e.value).checkVisibility?.call(r) ?? !0)) return;
			let i = Lo(e.value);
			(i.width === 0 || i.height === 0) && t.error(new Z(X.MISSING_VIEWPORT_DIMENSIONS)), n.value = {
				width: i.width || 500,
				height: i.height || 500
			};
		};
		i(), window.addEventListener("resize", i), e.value && (r = new ResizeObserver(() => i()), r.observe(e.value)), E(() => {
			window.removeEventListener("resize", i), r && e.value && r.unobserve(e.value);
		});
	});
}
var kl = /* @__PURE__ */ d({
	name: "UserSelection",
	compatConfig: { MODE: 3 },
	props: { userSelectionRect: {} },
	setup(e) {
		return (e, t) => (A(), a("div", {
			class: "vue-flow__selection vue-flow__container",
			style: w({
				width: `${e.userSelectionRect.width}px`,
				height: `${e.userSelectionRect.height}px`,
				transform: `translate(${e.userSelectionRect.x}px, ${e.userSelectionRect.y}px)`
			})
		}, null, 4));
	}
}), Al = ["tabIndex"], jl = /* @__PURE__ */ d({
	name: "NodesSelection",
	compatConfig: { MODE: 3 },
	setup(e) {
		let { emits: t, viewport: r, getSelectedNodes: s, noPanClassName: c, disableKeyboardA11y: l, userSelectionActive: u } = $(), d = Ac(), f = P(null), p = bc({
			el: f,
			onStart(e) {
				t.selectionDragStart(e), t.nodeDragStart(e);
			},
			onDrag(e) {
				t.selectionDrag(e), t.nodeDrag(e);
			},
			onStop(e) {
				t.selectionDragStop(e), t.nodeDragStop(e);
			}
		});
		D(() => {
			var e;
			l.value || (e = f.value) == null || e.focus({ preventScroll: !0 });
		});
		let m = n(() => as(s.value)), h = n(() => ({
			width: `${m.value.width}px`,
			height: `${m.value.height}px`,
			top: `${m.value.y}px`,
			left: `${m.value.x}px`
		}));
		function g(e) {
			t.selectionContextMenu({
				event: e,
				nodes: s.value
			});
		}
		function _(e) {
			l.value || Po[e.key] && (e.preventDefault(), d({
				x: Po[e.key].x,
				y: Po[e.key].y
			}, e.shiftKey));
		}
		return (e, t) => !U(u) && m.value.width && m.value.height ? (A(), a("div", {
			key: 0,
			class: S(["vue-flow__nodesselection vue-flow__container", U(c)]),
			style: w({ transform: `translate(${U(r).x}px,${U(r).y}px) scale(${U(r).zoom})` })
		}, [o("div", {
			ref_key: "el",
			ref: f,
			class: S([{ dragging: U(p) }, "vue-flow__nodesselection-rect"]),
			style: w(h.value),
			tabIndex: U(l) ? void 0 : -1,
			onContextmenu: g,
			onKeydown: _
		}, null, 46, Al)], 6)) : i("", !0);
	}
});
function Ml(e, t) {
	return {
		x: e.clientX - t.left,
		y: e.clientY - t.top
	};
}
var Nl = /* @__PURE__ */ d({
	name: "Pane",
	compatConfig: { MODE: 3 },
	props: {
		isSelecting: { type: Boolean },
		selectionKeyPressed: { type: Boolean }
	},
	setup(e) {
		let { vueFlowRef: t, nodes: n, viewport: o, emits: s, userSelectionActive: c, removeSelectedElements: l, userSelectionRect: u, elementsSelectable: d, nodesSelectionActive: f, getSelectedEdges: p, getSelectedNodes: m, removeNodes: h, removeEdges: g, selectionMode: _, deleteKeyCode: v, multiSelectionKeyCode: y, multiSelectionActive: b, edgeLookup: x, nodeLookup: C, connectionLookup: w, defaultEdgeOptions: T, connectionStartHandle: E, panOnDrag: D } = $(), O = z(null), k = z(/* @__PURE__ */ new Set()), j = z(/* @__PURE__ */ new Set()), M = z(null), N = B(() => d.value && (e.isSelecting || c.value)), P = B(() => E.value !== null), F = !1, L = !1, R = ko(v, { actInsideInputWithModifier: !1 }), ee = ko(y);
		K(R, (e) => {
			e && (h(m.value), g(p.value), f.value = !1);
		}), K(ee, (e) => {
			b.value = e;
		});
		function V(e, t) {
			return (n) => {
				n.target === t && e?.(n);
			};
		}
		function H(e) {
			F || P.value ? F = !1 : (s.paneClick(e), l(), f.value = !1);
		}
		function W(e) {
			Array.isArray(D.value) && D.value?.includes(2) ? e.preventDefault() : s.paneContextMenu(e);
		}
		function G(e) {
			s.paneScroll(e);
		}
		function te(n) {
			var r, i;
			if (M.value = t.value?.getBoundingClientRect() ?? null, !d.value || !e.isSelecting || n.button !== 0 || n.target !== O.value || !M.value) return;
			(i = (r = n.target)?.setPointerCapture) == null || i.call(r, n.pointerId);
			let { x: a, y: o } = Ml(n, M.value);
			L = !0, F = !1, l(), u.value = {
				width: 0,
				height: 0,
				startX: a,
				startY: o,
				x: a,
				y: o
			}, s.selectionStart(n);
		}
		function q(e) {
			if (!M.value || !u.value) return;
			F = !0;
			let { x: t, y: r } = Vs(e, M.value), { startX: i = 0, startY: a = 0 } = u.value, l = {
				startX: i,
				startY: a,
				x: t < i ? t : i,
				y: r < a ? r : a,
				width: Math.abs(t - i),
				height: Math.abs(r - a)
			}, d = k.value, p = j.value;
			k.value = new Set(os(n.value, l, o.value, _.value === go.Partial, !0).map((e) => e.id)), j.value = /* @__PURE__ */ new Set();
			let m = T.value?.selectable ?? !0;
			for (let e of k.value) {
				let t = w.value.get(e);
				if (t) for (let { edgeId: e } of t.values()) {
					let t = x.value.get(e);
					t && (t.selectable ?? m) && j.value.add(e);
				}
			}
			if (!fc(d, k.value)) {
				let e = ws(C.value, k.value, !0);
				s.nodesChange(e);
			}
			if (!fc(p, j.value)) {
				let e = ws(x.value, j.value);
				s.edgesChange(e);
			}
			u.value = l, c.value = !0, f.value = !1;
		}
		function ne(t) {
			var n;
			t.button === 0 && L && ((n = t.target) == null || n.releasePointerCapture(t.pointerId), !c.value && u.value && t.target === O.value && H(t), c.value = !1, u.value = null, f.value = k.value.size > 0, s.selectionEnd(t), e.selectionKeyPressed && (F = !1), L = !1);
		}
		return (e, t) => (A(), a("div", {
			ref_key: "container",
			ref: O,
			class: S(["vue-flow__pane vue-flow__container", { selection: e.isSelecting }]),
			onClick: t[0] || (t[0] = (e) => N.value ? void 0 : V(H, O.value)(e)),
			onContextmenu: t[1] || (t[1] = (e) => V(W, O.value)(e)),
			onWheelPassive: t[2] || (t[2] = (e) => V(G, O.value)(e)),
			onPointerenter: t[3] || (t[3] = (e) => N.value ? void 0 : U(s).paneMouseEnter(e)),
			onPointerdown: t[4] || (t[4] = (e) => N.value ? te(e) : U(s).paneMouseMove(e)),
			onPointermove: t[5] || (t[5] = (e) => N.value ? q(e) : U(s).paneMouseMove(e)),
			onPointerup: t[6] || (t[6] = (e) => N.value ? ne(e) : void 0),
			onPointerleave: t[7] || (t[7] = (e) => U(s).paneMouseLeave(e))
		}, [
			I(e.$slots, "default"),
			U(c) && U(u) ? (A(), r(kl, {
				key: 0,
				"user-selection-rect": U(u)
			}, null, 8, ["user-selection-rect"])) : i("", !0),
			U(f) && U(m).length ? (A(), r(jl, { key: 1 })) : i("", !0)
		], 34));
	}
}), Pl = /* @__PURE__ */ d({
	name: "Transform",
	compatConfig: { MODE: 3 },
	setup(e) {
		let { viewport: t, fitViewOnInit: r, fitViewOnInitDone: i } = $(), o = n(() => r.value ? !i.value : !1), s = n(() => `translate(${t.value.x}px,${t.value.y}px) scale(${t.value.zoom})`);
		return (e, t) => (A(), a("div", {
			class: "vue-flow__transformationpane vue-flow__container",
			style: w({
				transform: s.value,
				opacity: o.value ? 0 : void 0
			})
		}, [I(e.$slots, "default")], 4));
	}
}), Fl = /* @__PURE__ */ d({
	name: "Viewport",
	compatConfig: { MODE: 3 },
	setup(e) {
		let { minZoom: t, maxZoom: n, defaultViewport: r, translateExtent: i, zoomActivationKeyCode: o, selectionKeyCode: s, panActivationKeyCode: c, panOnScroll: u, panOnScrollMode: d, panOnScrollSpeed: f, panOnDrag: p, zoomOnDoubleClick: m, zoomOnPinch: h, zoomOnScroll: g, preventScrolling: _, noWheelClassName: v, noPanClassName: y, emits: b, connectionStartHandle: x, userSelectionActive: C, paneDragging: w, d3Zoom: T, d3Selection: E, d3ZoomHandler: O, viewport: k, viewportRef: j, paneClickDistance: M } = $();
		Ol(j);
		let N = z(!1), P = z(!1), F = null, L = !1, R = 0, ee = {
			x: 0,
			y: 0,
			zoom: 0
		}, V = ko(c), H = ko(s), W = ko(o), G = B(() => (!H.value || H.value && s.value === !0) && (V.value || p.value)), te = B(() => V.value || u.value), ne = B(() => s.value === !0 && G.value !== !0), re = B(() => H.value && s.value !== !0 || C.value || ne.value), ie = B(() => x.value !== null);
		D(() => {
			if (!j.value) {
				rc("Viewport element is missing");
				return;
			}
			let e = j.value, a = e.getBoundingClientRect(), o = ho().clickDistance(M.value).scaleExtent([t.value, n.value]).translateExtent(i.value), c = jn(e).call(o), l = c.on("wheel.zoom"), x = ao.translate(r.value.x ?? 0, r.value.y ?? 0).scale(Ro(r.value.zoom ?? 1, t.value, n.value)), S = [[0, 0], [a.width, a.height]], D = o.constrain()(x, S, i.value);
			o.transform(c, D), o.wheelDelta(hs), T.value = o, E.value = c, O.value = l, k.value = {
				x: D.x,
				y: D.y,
				zoom: D.k
			}, o.on("start", (e) => {
				if (!e.sourceEvent) return null;
				R = e.sourceEvent.button, N.value = !0;
				let t = se(e.transform);
				e.sourceEvent?.type === "mousedown" && (w.value = !0), ee = t, b.viewportChangeStart(t), b.moveStart({
					event: e,
					flowTransform: t
				});
			}), o.on("end", (e) => {
				if (!e.sourceEvent) return null;
				if (N.value = !1, w.value = !1, ae(G.value, R ?? 0) && !L && b.paneContextMenu(e.sourceEvent), L = !1, oe(ee, e.transform)) {
					let t = se(e.transform);
					ee = t, b.viewportChangeEnd(t), b.moveEnd({
						event: e,
						flowTransform: t
					});
				}
			}), o.filter((e) => {
				let t = W.value || g.value, n = h.value && e.ctrlKey, r = e.button, i = e.type === "wheel";
				if (r === 1 && e.type === "mousedown" && (ce(e, "vue-flow__node") || ce(e, "vue-flow__edge"))) return !0;
				if (!G.value && !t && !te.value && !m.value && !h.value || C.value || ie.value && !i || !m.value && e.type === "dblclick" || ce(e, v.value) && i || ce(e, y.value) && (!i || te.value && i && !W.value) || !h.value && e.ctrlKey && i || !t && !te.value && !n && i) return !1;
				if (!h && e.type === "touchstart" && e.touches?.length > 1) return e.preventDefault(), !1;
				if (!G.value && (e.type === "mousedown" || e.type === "touchstart") || ne.value && Array.isArray(p.value) && p.value.includes(0) && r === 0 || Array.isArray(p.value) && !p.value.includes(r) && (e.type === "mousedown" || e.type === "touchstart")) return !1;
				let a = Array.isArray(p.value) && p.value.includes(r) || s.value === !0 && Array.isArray(p.value) && !p.value.includes(0) || !r || r <= 1;
				return (!e.ctrlKey || V.value || i) && a;
			}), K([C, G], () => {
				C.value && !N.value ? o.on("zoom", null) : C.value || o.on("zoom", (e) => {
					k.value = {
						x: e.transform.x,
						y: e.transform.y,
						zoom: e.transform.k
					};
					let t = se(e.transform);
					L = ae(G.value, R ?? 0), b.viewportChange(t), b.move({
						event: e,
						flowTransform: t
					});
				});
			}, { immediate: !0 }), K([
				C,
				te,
				d,
				W,
				h,
				_,
				v
			], () => {
				te.value && !W.value && !C.value ? c.on("wheel.zoom", (e) => {
					if (ce(e, v.value)) return !1;
					let t = W.value || g.value, n = h.value && e.ctrlKey;
					if (!(!_.value || te.value || t || n)) return !1;
					e.preventDefault(), e.stopImmediatePropagation();
					let r = c.property("__zoom").k || 1, i = Hs();
					if (!V.value && e.ctrlKey && h.value && i) {
						let t = Nn(e), n = r * 2 ** hs(e);
						o.scaleTo(c, n, t, e);
						return;
					}
					let a = e.deltaMode === 1 ? 20 : 1, s = d.value === bo.Vertical ? 0 : e.deltaX * a, l = d.value === bo.Horizontal ? 0 : e.deltaY * a;
					!i && e.shiftKey && d.value !== bo.Vertical && !s && l && (s = l, l = 0), o.translateBy(c, -(s / r) * f.value, -(l / r) * f.value);
					let u = se(c.property("__zoom"));
					F && clearTimeout(F), P.value ? (b.move({
						event: e,
						flowTransform: u
					}), b.viewportChange(u), F = setTimeout(() => {
						b.moveEnd({
							event: e,
							flowTransform: u
						}), b.viewportChangeEnd(u), P.value = !1;
					}, 150)) : (P.value = !0, b.moveStart({
						event: e,
						flowTransform: u
					}), b.viewportChangeStart(u));
				}, { passive: !1 }) : l !== void 0 && c.on("wheel.zoom", function(e, t) {
					let n = !_.value && e.type === "wheel" && !e.ctrlKey, r = W.value || g.value, i = h.value && e.ctrlKey;
					if (!r && !u.value && !i && e.type === "wheel" || n || ce(e, v.value)) return null;
					e.preventDefault(), l.call(this, e, t);
				}, { passive: !1 });
			}, { immediate: !0 });
		});
		function ae(e, t) {
			return t === 2 && Array.isArray(e) && e.includes(2);
		}
		function oe(e, t) {
			return e.x !== t.x && !Number.isNaN(t.x) || e.y !== t.y && !Number.isNaN(t.y) || e.zoom !== t.k && !Number.isNaN(t.k);
		}
		function se(e) {
			return {
				x: e.x,
				y: e.y,
				zoom: e.k
			};
		}
		function ce(e, t) {
			return e.target.closest(`.${t}`);
		}
		return (e, t) => (A(), a("div", {
			ref_key: "viewportRef",
			ref: j,
			class: "vue-flow__viewport vue-flow__container"
		}, [l(Nl, {
			"is-selecting": re.value,
			"selection-key-pressed": U(H),
			class: S({
				connecting: ie.value,
				dragging: U(w),
				draggable: U(p) === !0 || Array.isArray(U(p)) && U(p).includes(0)
			})
		}, {
			default: q(() => [l(Pl, null, {
				default: q(() => [I(e.$slots, "default")]),
				_: 3
			})]),
			_: 3
		}, 8, [
			"is-selecting",
			"selection-key-pressed",
			"class"
		])], 512));
	}
}), Il = ["id"], Ll = ["id"], Rl = ["id"], zl = /* @__PURE__ */ d({
	name: "A11yDescriptions",
	compatConfig: { MODE: 3 },
	setup(e) {
		let { id: n, disableKeyboardA11y: r, ariaLiveMessage: s } = $();
		return (e, c) => (A(), a(t, null, [
			o("div", {
				id: `${U(Ao)}-${U(n)}`,
				style: { display: "none" }
			}, " Press enter or space to select a node. " + ee(U(r) ? "" : "You can then use the arrow keys to move the node around.") + " You can then use the arrow keys to move the node around, press delete to remove it and press escape to cancel. ", 9, Il),
			o("div", {
				id: `${U(jo)}-${U(n)}`,
				style: { display: "none" }
			}, " Press enter or space to select an edge. You can then press delete to remove it or press escape to cancel. ", 8, Ll),
			U(r) ? i("", !0) : (A(), a("div", {
				key: 0,
				id: `${U(Mo)}-${U(n)}`,
				"aria-live": "assertive",
				"aria-atomic": "true",
				style: {
					position: "absolute",
					width: "1px",
					height: "1px",
					margin: "-1px",
					border: "0",
					padding: "0",
					overflow: "hidden",
					clip: "rect(0px, 0px, 0px, 0px)",
					"clip-path": "inset(100%)"
				}
			}, ee(U(s)), 9, Rl))
		], 64));
	}
});
function Bl() {
	let e = $();
	K(() => e.viewportHelper.value.viewportInitialized, (t) => {
		t && setTimeout(() => {
			e.emits.init(e), e.emits.paneReady(e);
		}, 1);
	});
}
function Vl(e, t, n) {
	return n === J.Left ? e - t : n === J.Right ? e + t : e;
}
function Hl(e, t, n) {
	return n === J.Top ? e - t : n === J.Bottom ? e + t : e;
}
var Ul = function({ radius: e = 10, centerX: t = 0, centerY: n = 0, position: r = J.Top, type: i }) {
	return h("circle", {
		class: `vue-flow__edgeupdater vue-flow__edgeupdater-${i}`,
		cx: Vl(t, e, r),
		cy: Hl(n, e, r),
		r: e,
		stroke: "transparent",
		fill: "transparent"
	});
};
Ul.props = [
	"radius",
	"centerX",
	"centerY",
	"position",
	"type"
], Ul.compatConfig = { MODE: 3 };
var Wl = Ul, Gl = d({
	name: "Edge",
	compatConfig: { MODE: 3 },
	props: ["id"],
	setup(e) {
		let { id: t, addSelectedEdges: r, connectionMode: i, edgeUpdaterRadius: a, emits: o, nodesSelectionActive: s, noPanClassName: c, getEdgeTypes: l, removeSelectedEdges: u, findEdge: d, findNode: f, isValidConnection: m, multiSelectionActive: _, disableKeyboardA11y: v, elementsSelectable: y, edgesUpdatable: b, edgesFocusable: x, hooks: S } = $(), C = n(() => d(e.id)), { emit: w, on: T } = Sc(C.value, o), E = g(yc), D = p(), O = P(!1), k = P(!1), A = P(""), M = P(null), N = P("source"), F = P(null), I = B(() => C.value.selectable === void 0 ? y.value : C.value.selectable), R = B(() => C.value.updatable === void 0 ? b.value : C.value.updatable), z = B(() => C.value.focusable === void 0 ? x.value : C.value.focusable);
		j(_c, e.id), j(vc, F);
		let ee = n(() => C.value.class instanceof Function ? C.value.class(C.value) : C.value.class), V = n(() => C.value.style instanceof Function ? C.value.style(C.value) : C.value.style), H = n(() => {
			let e = C.value.type || "default", t = E?.[`edge-${e}`];
			if (t) return t;
			let n = C.value.template ?? l.value[e];
			if (typeof n == "string" && D) {
				let t = Object.keys(D.appContext.components);
				t && t.includes(e) && (n = L(e, !1));
			}
			return n && typeof n != "string" ? n : (o.error(new Z(X.EDGE_TYPE_MISSING, n)), !1);
		}), { handlePointerDown: U } = Tc({
			nodeId: A,
			handleId: M,
			type: N,
			isValidConnection: m,
			edgeUpdaterType: N,
			onEdgeUpdate: K,
			onEdgeUpdateEnd: te
		});
		return () => {
			let n = f(C.value.source), r = f(C.value.target), s = "pathOptions" in C.value ? C.value.pathOptions : {};
			if (!n && !r) return o.error(new Z(X.EDGE_SOURCE_TARGET_MISSING, C.value.id, C.value.source, C.value.target)), null;
			if (!n) return o.error(new Z(X.EDGE_SOURCE_MISSING, C.value.id, C.value.source)), null;
			if (!r) return o.error(new Z(X.EDGE_TARGET_MISSING, C.value.id, C.value.target)), null;
			if (!C.value || C.value.hidden || n.hidden || r.hidden) return null;
			let u;
			u = i.value === vo.Strict ? n.handleBounds.source : [...n.handleBounds.source || [], ...n.handleBounds.target || []];
			let d = Fs(u, C.value.sourceHandle), p;
			p = i.value === vo.Strict ? r.handleBounds.target : [...r.handleBounds.target || [], ...r.handleBounds.source || []];
			let m = Fs(p, C.value.targetHandle), g = d?.position || J.Bottom, _ = m?.position || J.Top, { x: v, y } = Ps(n, d, g), { x: b, y: x } = Ps(r, m, _);
			return C.value.sourceX = v, C.value.sourceY = y, C.value.targetX = b, C.value.targetY = x, h("g", {
				ref: F,
				key: e.id,
				"data-id": e.id,
				class: [
					"vue-flow__edge",
					`vue-flow__edge-${H.value === !1 ? "default" : C.value.type || "default"}`,
					c.value,
					ee.value,
					{
						updating: O.value,
						selected: C.value.selected,
						animated: C.value.animated,
						inactive: !I.value && !S.value.edgeClick.hasListeners()
					}
				],
				tabIndex: z.value ? 0 : void 0,
				"aria-label": C.value.ariaLabel === null ? void 0 : C.value.ariaLabel ?? `Edge from ${C.value.source} to ${C.value.target}`,
				"aria-describedby": z.value ? `${jo}-${t}` : void 0,
				"aria-roledescription": "edge",
				role: z.value ? "group" : "img",
				...C.value.domAttributes,
				onClick: ne,
				onContextmenu: re,
				onDblclick: ie,
				onMouseenter: ae,
				onMousemove: oe,
				onMouseleave: se,
				onKeyDown: z.value ? ue : void 0
			}, [k.value ? null : h(H.value === !1 ? l.value.default : H.value, {
				id: e.id,
				sourceNode: n,
				targetNode: r,
				source: C.value.source,
				target: C.value.target,
				type: C.value.type,
				updatable: R.value,
				selected: C.value.selected,
				animated: C.value.animated,
				label: C.value.label,
				labelStyle: C.value.labelStyle,
				labelShowBg: C.value.labelShowBg,
				labelBgStyle: C.value.labelBgStyle,
				labelBgPadding: C.value.labelBgPadding,
				labelBgBorderRadius: C.value.labelBgBorderRadius,
				data: C.value.data,
				events: {
					...C.value.events,
					...T
				},
				style: V.value,
				markerStart: `url('#${ms(C.value.markerStart, t)}')`,
				markerEnd: `url('#${ms(C.value.markerEnd, t)}')`,
				sourcePosition: g,
				targetPosition: _,
				sourceX: v,
				sourceY: y,
				targetX: b,
				targetY: x,
				sourceHandleId: C.value.sourceHandle,
				targetHandleId: C.value.targetHandle,
				interactionWidth: C.value.interactionWidth,
				...s
			}), [R.value === "source" || R.value === !0 ? [h("g", {
				onMousedown: ce,
				onMouseenter: W,
				onMouseout: G
			}, h(Wl, {
				position: g,
				centerX: v,
				centerY: y,
				radius: a.value,
				type: "source",
				"data-type": "source"
			}))] : null, R.value === "target" || R.value === !0 ? [h("g", {
				onMousedown: le,
				onMouseenter: W,
				onMouseout: G
			}, h(Wl, {
				position: _,
				centerX: b,
				centerY: x,
				radius: a.value,
				type: "target",
				"data-type": "target"
			}))] : null]]);
		};
		function W() {
			O.value = !0;
		}
		function G() {
			O.value = !1;
		}
		function K(e, t) {
			w.update({
				event: e,
				edge: C.value,
				connection: t
			});
		}
		function te(e) {
			w.updateEnd({
				event: e,
				edge: C.value
			}), k.value = !1;
		}
		function q(e, t) {
			e.button === 0 && (k.value = !0, A.value = t ? C.value.target : C.value.source, M.value = (t ? C.value.targetHandle : C.value.sourceHandle) ?? null, N.value = t ? "target" : "source", w.updateStart({
				event: e,
				edge: C.value
			}), U(e));
		}
		function ne(e) {
			var t;
			let n = {
				event: e,
				edge: C.value
			};
			I.value && (s.value = !1, C.value.selected && _.value ? (u([C.value]), (t = F.value) == null || t.blur()) : r([C.value])), w.click(n);
		}
		function re(e) {
			w.contextMenu({
				event: e,
				edge: C.value
			});
		}
		function ie(e) {
			w.doubleClick({
				event: e,
				edge: C.value
			});
		}
		function ae(e) {
			w.mouseEnter({
				event: e,
				edge: C.value
			});
		}
		function oe(e) {
			w.mouseMove({
				event: e,
				edge: C.value
			});
		}
		function se(e) {
			w.mouseLeave({
				event: e,
				edge: C.value
			});
		}
		function ce(e) {
			q(e, !0);
		}
		function le(e) {
			q(e, !1);
		}
		function ue(t) {
			var n;
			!v.value && No.includes(t.key) && I.value && (t.key === "Escape" ? ((n = F.value) == null || n.blur(), u([d(e.id)])) : r([d(e.id)]));
		}
	}
}), Kl = d({
	name: "ConnectionLine",
	compatConfig: { MODE: 3 },
	setup() {
		let { id: e, connectionMode: t, connectionStartHandle: r, connectionEndHandle: i, connectionPosition: a, connectionLineType: o, connectionLineStyle: s, connectionLineOptions: c, connectionStatus: l, viewport: u, findNode: d } = $(), f = g(yc)?.["connection-line"], p = n(() => d(r.value?.nodeId)), m = n(() => d(i.value?.nodeId) ?? null), _ = n(() => ({
			x: (a.value.x - u.value.x) / u.value.zoom,
			y: (a.value.y - u.value.y) / u.value.zoom
		})), v = n(() => c.value.markerStart ? `url(#${ms(c.value.markerStart, e)})` : ""), y = n(() => c.value.markerEnd ? `url(#${ms(c.value.markerEnd, e)})` : "");
		return () => {
			if (!p.value || !r.value) return null;
			let e = r.value.id, n = r.value.type, a = p.value.handleBounds, u = a?.[n] ?? [];
			if (t.value === vo.Loose) {
				let e = a?.[n === "source" ? "target" : "source"] ?? [];
				u = [...u, ...e];
			}
			if (!u) return null;
			let d = (e ? u.find((t) => t.id === e) : u[0]) ?? null, g = d?.position ?? J.Top, { x: b, y: x } = Ps(p.value, d, g), S = null;
			m.value && (S = t.value === vo.Strict ? m.value.handleBounds[n === "source" ? "target" : "source"]?.find((e) => e.id === i.value?.id) || null : [...m.value.handleBounds.source ?? [], ...m.value.handleBounds.target ?? []].find((e) => e.id === i.value?.id) || null);
			let C = i.value?.position ?? (g ? tc[g] : null);
			if (!g || !C) return null;
			let w = o.value ?? c.value.type ?? _o.Bezier, T = "", E = {
				sourceX: b,
				sourceY: x,
				sourcePosition: g,
				targetX: _.value.x,
				targetY: _.value.y,
				targetPosition: C
			};
			return w === _o.Bezier ? [T] = ll(E) : w === _o.Step ? [T] = _l({
				...E,
				borderRadius: 0
			}) : w === _o.SmoothStep ? [T] = _l(E) : w === _o.SimpleBezier ? [T] = dl(E) : T = `M${b},${x} ${_.value.x},${_.value.y}`, h("svg", { class: "vue-flow__edges vue-flow__connectionline vue-flow__container" }, h("g", { class: "vue-flow__connection" }, f ? h(f, {
				sourceX: b,
				sourceY: x,
				sourcePosition: g,
				targetX: _.value.x,
				targetY: _.value.y,
				targetPosition: C,
				sourceNode: p.value,
				sourceHandle: d,
				targetNode: m.value,
				targetHandle: S,
				markerEnd: y.value,
				markerStart: v.value,
				connectionStatus: l.value
			}) : h("path", {
				d: T,
				class: [
					c.value.class,
					l.value,
					"vue-flow__connection-path"
				],
				style: {
					...s.value,
					...c.value.style
				},
				"marker-end": y.value,
				"marker-start": v.value
			})));
		};
	}
}), ql = [
	"id",
	"markerWidth",
	"markerHeight",
	"markerUnits",
	"orient"
], Jl = /* @__PURE__ */ d({
	name: "MarkerType",
	compatConfig: { MODE: 3 },
	props: {
		id: {},
		type: {},
		color: { default: "none" },
		width: { default: 12.5 },
		height: { default: 12.5 },
		markerUnits: { default: "strokeWidth" },
		orient: { default: "auto-start-reverse" },
		strokeWidth: { default: 1 }
	},
	setup(e) {
		return (e, t) => (A(), a("marker", {
			id: e.id,
			class: "vue-flow__arrowhead",
			viewBox: "-10 -10 20 20",
			refX: "0",
			refY: "0",
			markerWidth: `${e.width}`,
			markerHeight: `${e.height}`,
			markerUnits: e.markerUnits,
			orient: e.orient
		}, [e.type === U(yo).ArrowClosed ? (A(), a("polyline", {
			key: 0,
			style: w({
				stroke: e.color,
				fill: e.color,
				strokeWidth: e.strokeWidth
			}),
			"stroke-linecap": "round",
			"stroke-linejoin": "round",
			points: "-5,-4 0,0 -5,4 -5,-4"
		}, null, 4)) : i("", !0), e.type === U(yo).Arrow ? (A(), a("polyline", {
			key: 1,
			style: w({
				stroke: e.color,
				strokeWidth: e.strokeWidth
			}),
			"stroke-linecap": "round",
			"stroke-linejoin": "round",
			fill: "none",
			points: "-5,-4 0,0 -5,4"
		}, null, 4)) : i("", !0)], 8, ql));
	}
}), Yl = {
	class: "vue-flow__marker vue-flow__container",
	"aria-hidden": "true"
}, Xl = /* @__PURE__ */ d({
	name: "MarkerDefinitions",
	compatConfig: { MODE: 3 },
	setup(e) {
		let { id: i, edges: s, connectionLineOptions: c, defaultMarkerColor: l } = $(), u = n(() => {
			let e = /* @__PURE__ */ new Set(), t = [], n = (n) => {
				if (n) {
					let r = ms(n, i);
					e.has(r) || (typeof n == "object" ? t.push({
						...n,
						id: r,
						color: n.color || l.value
					}) : t.push({
						id: r,
						color: l.value,
						type: n
					}), e.add(r));
				}
			};
			for (let e of [c.value.markerEnd, c.value.markerStart]) n(e);
			for (let e of s.value) for (let t of [e.markerStart, e.markerEnd]) n(t);
			return t.sort((e, t) => e.id.localeCompare(t.id));
		});
		return (e, n) => (A(), a("svg", Yl, [o("defs", null, [(A(!0), a(t, null, F(u.value, (e) => (A(), r(Jl, {
			id: e.id,
			key: e.id,
			type: e.type,
			color: e.color,
			width: e.width,
			height: e.height,
			markerUnits: e.markerUnits,
			"stroke-width": e.strokeWidth,
			orient: e.orient
		}, null, 8, [
			"id",
			"type",
			"color",
			"width",
			"height",
			"markerUnits",
			"stroke-width",
			"orient"
		]))), 128))])]));
	}
}), Zl = /* @__PURE__ */ d({
	name: "Edges",
	compatConfig: { MODE: 3 },
	setup(e) {
		let { findNode: n, getEdges: r, elevateEdgesOnSelect: i } = $();
		return (e, o) => (A(), a(t, null, [
			l(Xl),
			(A(!0), a(t, null, F(U(r), (e) => (A(), a("svg", {
				key: e.id,
				class: "vue-flow__edges vue-flow__container",
				style: w({ zIndex: U(Ls)(e, U(n), U(i)) })
			}, [l(U(Gl), { id: e.id }, null, 8, ["id"])], 4))), 128)),
			l(U(Kl))
		], 64));
	}
}), Ql = d({
	name: "Node",
	compatConfig: { MODE: 3 },
	props: ["id", "resizeObserver"],
	setup(e) {
		let { id: t, noPanClassName: r, selectNodesOnDrag: i, nodesSelectionActive: a, multiSelectionActive: o, emits: s, removeSelectedNodes: c, addSelectedNodes: l, updateNodeDimensions: u, onUpdateNodeInternals: d, getNodeTypes: f, nodeExtent: m, elevateNodesOnSelect: _, disableKeyboardA11y: v, ariaLiveMessage: y, snapToGrid: b, snapGrid: S, nodeDragThreshold: C, nodesDraggable: w, elementsSelectable: T, nodesConnectable: E, nodesFocusable: O, hooks: k } = $(), A = P(null);
		j(gc, A), j(hc, e.id);
		let M = g(yc), N = p(), F = Ac(), { node: I, parentNode: R } = Dc(e.id), { emit: z, on: ee } = kc(I, s), V = B(() => I.draggable === void 0 ? w.value : I.draggable), H = B(() => I.selectable === void 0 ? T.value : I.selectable), U = B(() => I.connectable === void 0 ? E.value : I.connectable), W = B(() => I.focusable === void 0 ? O.value : I.focusable), G = n(() => H.value || V.value || k.value.nodeClick.hasListeners() || k.value.nodeDoubleClick.hasListeners() || k.value.nodeMouseEnter.hasListeners() || k.value.nodeMouseMove.hasListeners() || k.value.nodeMouseLeave.hasListeners()), te = B(() => !!I.dimensions.width && !!I.dimensions.height), q = n(() => {
			let e = I.type || "default", t = M?.[`node-${e}`];
			if (t) return t;
			let n = I.template || f.value[e];
			if (typeof n == "string" && N) {
				let t = Object.keys(N.appContext.components);
				t && t.includes(e) && (n = L(e, !1));
			}
			return n && typeof n != "string" ? n : (s.error(new Z(X.NODE_TYPE_MISSING, n)), !1);
		}), ne = bc({
			id: e.id,
			el: A,
			disabled: () => !V.value,
			selectable: H,
			dragHandle: () => I.dragHandle,
			onStart(e) {
				z.dragStart(e);
			},
			onDrag(e) {
				z.drag(e);
			},
			onStop(e) {
				z.dragStop(e);
			},
			onClick(e) {
				pe(e);
			}
		}), re = n(() => I.class instanceof Function ? I.class(I) : I.class), ie = n(() => {
			let e = (I.style instanceof Function ? I.style(I) : I.style) || {}, t = I.width instanceof Function ? I.width(I) : I.width, n = I.height instanceof Function ? I.height(I) : I.height;
			return !e.width && t && (e.width = typeof t == "string" ? t : `${t}px`), !e.height && n && (e.height = typeof n == "string" ? n : `${n}px`), e;
		}), ae = B(() => Number(I.zIndex ?? ie.value.zIndex ?? 0));
		return d((t) => {
			(t.includes(e.id) || !t.length) && se();
		}), D(() => {
			K(() => I.hidden, (t = !1, n, r) => {
				!t && A.value && (e.resizeObserver.observe(A.value), r(() => {
					A.value && e.resizeObserver.unobserve(A.value);
				}));
			}, {
				immediate: !0,
				flush: "post"
			});
		}), K([
			() => I.type,
			() => I.sourcePosition,
			() => I.targetPosition
		], () => {
			x(() => {
				u([{
					id: e.id,
					nodeElement: A.value,
					forceUpdate: !0
				}]);
			});
		}), K([
			() => I.position.x,
			() => I.position.y,
			() => R.value?.computedPosition.x,
			() => R.value?.computedPosition.y,
			() => R.value?.computedPosition.z,
			ae,
			() => I.selected,
			() => I.dimensions.height,
			() => I.dimensions.width,
			() => R.value?.dimensions.height,
			() => R.value?.dimensions.width
		], ([e, t, n, r, i, a]) => {
			let o = {
				x: e,
				y: t,
				z: a + (_.value && I.selected ? 1e3 : 0)
			};
			n !== void 0 && r !== void 0 ? I.computedPosition = fs({
				x: n,
				y: r,
				z: i
			}, o) : I.computedPosition = o;
		}, {
			flush: "post",
			immediate: !0
		}), K([() => I.extent, m], ([e, t], [n, r]) => {
			(e !== n || t !== r) && oe();
		}), I.extent === "parent" || typeof I.extent == "object" && "range" in I.extent && I.extent.range === "parent" ? _e(() => te).toBe(!0).then(oe) : oe(), () => I.hidden ? null : h("div", {
			ref: A,
			"data-id": I.id,
			class: [
				"vue-flow__node",
				`vue-flow__node-${q.value === !1 ? "default" : I.type || "default"}`,
				{
					[r.value]: V.value,
					dragging: ne?.value,
					draggable: V.value,
					selected: I.selected,
					selectable: H.value,
					parent: I.isParent
				},
				re.value
			],
			style: {
				visibility: te.value ? "visible" : "hidden",
				zIndex: I.computedPosition.z ?? ae.value,
				transform: `translate(${I.computedPosition.x}px,${I.computedPosition.y}px)`,
				pointerEvents: G.value ? "all" : "none",
				...ie.value
			},
			tabIndex: W.value ? 0 : void 0,
			role: W.value ? "group" : void 0,
			"aria-describedby": v.value ? void 0 : `${Ao}-${t}`,
			"aria-label": I.ariaLabel,
			"aria-roledescription": "node",
			...I.domAttributes,
			onMouseenter: ce,
			onMousemove: le,
			onMouseleave: ue,
			onContextmenu: de,
			onClick: pe,
			onDblclick: fe,
			onKeydown: me
		}, [h(q.value === !1 ? f.value.default : q.value, {
			id: I.id,
			type: I.type,
			data: I.data,
			events: {
				...I.events,
				...ee
			},
			selected: I.selected,
			resizing: I.resizing,
			dragging: ne.value,
			connectable: U.value,
			position: I.computedPosition,
			dimensions: I.dimensions,
			isValidTargetPos: I.isValidTargetPos,
			isValidSourcePos: I.isValidSourcePos,
			parent: I.parentNode,
			parentNodeId: I.parentNode,
			zIndex: I.computedPosition.z ?? ae.value,
			targetPosition: I.targetPosition,
			sourcePosition: I.sourcePosition,
			label: I.label,
			dragHandle: I.dragHandle,
			onUpdateNodeInternals: se
		})]);
		function oe() {
			let e = I.computedPosition, { computedPosition: t, position: n } = Ns(I, b.value ? Ws(e, S.value) : e, s.error, m.value, R.value);
			(I.computedPosition.x !== t.x || I.computedPosition.y !== t.y) && (I.computedPosition = {
				...I.computedPosition,
				...t
			}), (I.position.x !== n.x || I.position.y !== n.y) && (I.position = n);
		}
		function se() {
			A.value && u([{
				id: e.id,
				nodeElement: A.value,
				forceUpdate: !0
			}]);
		}
		function ce(e) {
			ne?.value || z.mouseEnter({
				event: e,
				node: I
			});
		}
		function le(e) {
			ne?.value || z.mouseMove({
				event: e,
				node: I
			});
		}
		function ue(e) {
			ne?.value || z.mouseLeave({
				event: e,
				node: I
			});
		}
		function de(e) {
			return z.contextMenu({
				event: e,
				node: I
			});
		}
		function fe(e) {
			return z.doubleClick({
				event: e,
				node: I
			});
		}
		function pe(e) {
			H.value && (!i.value || !V.value || C.value > 0) && oc(I, o.value, l, c, a, !1, A.value), z.click({
				event: e,
				node: I
			});
		}
		function me(e) {
			if (!(wo(e) || v.value)) {
				if (No.includes(e.key) && H.value) {
					let t = e.key === "Escape";
					oc(I, o.value, l, c, a, t, A.value);
				} else V.value && I.selected && Po[e.key] && (e.preventDefault(), y.value = `Moved selected node ${e.key.replace("Arrow", "").toLowerCase()}. New position, x: ${~~I.position.x}, y: ${~~I.position.y}`, F({
					x: Po[e.key].x,
					y: Po[e.key].y
				}, e.shiftKey));
			}
		}
	}
});
function $l(e = { includeHiddenNodes: !1 }) {
	let { nodes: t } = $();
	return n(() => {
		if (t.value.length === 0) return !1;
		for (let n of t.value) if ((e.includeHiddenNodes || !n.hidden) && (n?.handleBounds === void 0 || n.dimensions.width === 0 || n.dimensions.height === 0)) return !1;
		return !0;
	});
}
var eu = { class: "vue-flow__nodes vue-flow__container" }, tu = /* @__PURE__ */ d({
	name: "Nodes",
	compatConfig: { MODE: 3 },
	setup(e) {
		let { getNodes: n, updateNodeDimensions: o, emits: s } = $(), c = $l(), l = P();
		return K(c, (e) => {
			e && x(() => {
				s.nodesInitialized(n.value);
			});
		}, { immediate: !0 }), D(() => {
			l.value = new ResizeObserver((e) => {
				let t = e.map((e) => ({
					id: e.target.getAttribute("data-id"),
					nodeElement: e.target,
					forceUpdate: !0
				}));
				x(() => o(t));
			});
		}), E(() => l.value?.disconnect()), (e, o) => (A(), a("div", eu, [l.value ? (A(!0), a(t, { key: 0 }, F(U(n), (e, t, n, i) => {
			let a = [e.id];
			if (i && i.key === e.id && _(i, a)) return i;
			let o = (A(), r(U(Ql), {
				id: e.id,
				key: e.id,
				"resize-observer": l.value
			}, null, 8, ["id", "resize-observer"]));
			return o.memo = a, o;
		}, o, 0), 128)) : i("", !0)]));
	}
});
function nu() {
	let { emits: e } = $();
	D(() => {
		if (ic()) {
			let t = document.querySelector(".vue-flow__pane");
			t && window.getComputedStyle(t).zIndex !== "1" && e.error(new Z(X.MISSING_STYLES));
		}
	});
}
var ru = /* @__PURE__ */ o("div", { class: "vue-flow__edge-labels" }, null, -1), iu = /* @__PURE__ */ d({
	name: "VueFlow",
	compatConfig: { MODE: 3 },
	props: {
		id: {},
		modelValue: {},
		nodes: {},
		edges: {},
		edgeTypes: {},
		nodeTypes: {},
		connectionMode: {},
		connectionLineType: {},
		connectionLineStyle: { default: void 0 },
		connectionLineOptions: { default: void 0 },
		connectionRadius: {},
		isValidConnection: {
			type: [Function, null],
			default: void 0
		},
		deleteKeyCode: { default: void 0 },
		selectionKeyCode: {
			type: [Boolean, null],
			default: void 0
		},
		multiSelectionKeyCode: { default: void 0 },
		zoomActivationKeyCode: { default: void 0 },
		panActivationKeyCode: { default: void 0 },
		snapToGrid: {
			type: Boolean,
			default: void 0
		},
		snapGrid: {},
		onlyRenderVisibleElements: {
			type: Boolean,
			default: void 0
		},
		edgesUpdatable: {
			type: [Boolean, String],
			default: void 0
		},
		nodesDraggable: {
			type: Boolean,
			default: void 0
		},
		nodesConnectable: {
			type: Boolean,
			default: void 0
		},
		nodeDragThreshold: {},
		elementsSelectable: {
			type: Boolean,
			default: void 0
		},
		selectNodesOnDrag: {
			type: Boolean,
			default: void 0
		},
		panOnDrag: {
			type: [Boolean, Array],
			default: void 0
		},
		minZoom: {},
		maxZoom: {},
		defaultViewport: {},
		translateExtent: {},
		nodeExtent: {},
		defaultMarkerColor: {},
		zoomOnScroll: {
			type: Boolean,
			default: void 0
		},
		zoomOnPinch: {
			type: Boolean,
			default: void 0
		},
		panOnScroll: {
			type: Boolean,
			default: void 0
		},
		panOnScrollSpeed: {},
		panOnScrollMode: {},
		paneClickDistance: {},
		zoomOnDoubleClick: {
			type: Boolean,
			default: void 0
		},
		preventScrolling: {
			type: Boolean,
			default: void 0
		},
		selectionMode: {},
		edgeUpdaterRadius: {},
		fitViewOnInit: {
			type: Boolean,
			default: void 0
		},
		connectOnClick: {
			type: Boolean,
			default: void 0
		},
		applyDefault: {
			type: Boolean,
			default: void 0
		},
		autoConnect: {
			type: [Boolean, Function],
			default: void 0
		},
		noDragClassName: {},
		noWheelClassName: {},
		noPanClassName: {},
		defaultEdgeOptions: {},
		elevateEdgesOnSelect: {
			type: Boolean,
			default: void 0
		},
		elevateNodesOnSelect: {
			type: Boolean,
			default: void 0
		},
		disableKeyboardA11y: {
			type: Boolean,
			default: void 0
		},
		edgesFocusable: {
			type: Boolean,
			default: void 0
		},
		nodesFocusable: {
			type: Boolean,
			default: void 0
		},
		autoPanOnConnect: {
			type: Boolean,
			default: void 0
		},
		autoPanOnNodeDrag: {
			type: Boolean,
			default: void 0
		},
		autoPanSpeed: {}
	},
	emits: /* @__PURE__ */ "nodesChange.edgesChange.nodesInitialized.paneReady.init.updateNodeInternals.error.connect.connectStart.connectEnd.clickConnectStart.clickConnectEnd.moveStart.move.moveEnd.selectionDragStart.selectionDrag.selectionDragStop.selectionContextMenu.selectionStart.selectionEnd.viewportChangeStart.viewportChange.viewportChangeEnd.paneScroll.paneClick.paneContextMenu.paneMouseEnter.paneMouseMove.paneMouseLeave.edgeUpdate.edgeContextMenu.edgeMouseEnter.edgeMouseMove.edgeMouseLeave.edgeDoubleClick.edgeClick.edgeUpdateStart.edgeUpdateEnd.nodeContextMenu.nodeMouseEnter.nodeMouseMove.nodeMouseLeave.nodeDoubleClick.nodeClick.nodeDragStart.nodeDrag.nodeDragStop.miniMapNodeClick.miniMapNodeDoubleClick.miniMapNodeMouseEnter.miniMapNodeMouseMove.miniMapNodeMouseLeave.update:modelValue.update:nodes.update:edges".split("."),
	setup(e, { expose: t, emit: n }) {
		let r = e, i = G(), o = we(r, "modelValue", n), s = we(r, "nodes", n), c = we(r, "edges", n), u = $(r), d = Lc({
			modelValue: o,
			nodes: s,
			edges: c
		}, r, u);
		return zc(n, u.hooks), Bl(), nu(), j(yc, i), k(d), t(u), (e, t) => (A(), a("div", {
			ref: U(u).vueFlowRef,
			class: "vue-flow"
		}, [
			l(Fl, null, {
				default: q(() => [
					l(Zl),
					ru,
					l(tu),
					I(e.$slots, "zoom-pane")
				]),
				_: 3
			}),
			I(e.$slots, "default"),
			l(zl)
		], 512));
	}
}), au = /* @__PURE__ */ d({
	name: "Panel",
	compatConfig: { MODE: 3 },
	props: { position: {} },
	setup(e) {
		let t = e, { userSelectionActive: r } = $(), i = n(() => `${t.position}`.split("-"));
		return (e, t) => (A(), a("div", {
			class: S(["vue-flow__panel", i.value]),
			style: w({ pointerEvents: U(r) ? "none" : "all" })
		}, [I(e.$slots, "default")], 6));
	}
}), ou = { value: () => {} };
function su() {
	for (var e = 0, t = arguments.length, n = {}, r; e < t; ++e) {
		if (!(r = arguments[e] + "") || r in n || /[\s.]/.test(r)) throw Error("illegal type: " + r);
		n[r] = [];
	}
	return new cu(n);
}
function cu(e) {
	this._ = e;
}
function lu(e, t) {
	return e.trim().split(/^|\s+/).map(function(e) {
		var n = "", r = e.indexOf(".");
		if (r >= 0 && (n = e.slice(r + 1), e = e.slice(0, r)), e && !t.hasOwnProperty(e)) throw Error("unknown type: " + e);
		return {
			type: e,
			name: n
		};
	});
}
cu.prototype = su.prototype = {
	constructor: cu,
	on: function(e, t) {
		var n = this._, r = lu(e + "", n), i, a = -1, o = r.length;
		if (arguments.length < 2) {
			for (; ++a < o;) if ((i = (e = r[a]).type) && (i = uu(n[i], e.name))) return i;
		} else {
			if (t != null && typeof t != "function") throw Error("invalid callback: " + t);
			for (; ++a < o;) if (i = (e = r[a]).type) n[i] = du(n[i], e.name, t);
			else if (t == null) for (i in n) n[i] = du(n[i], e.name, null);
			return this;
		}
	},
	copy: function() {
		var e = {}, t = this._;
		for (var n in t) e[n] = t[n].slice();
		return new cu(e);
	},
	call: function(e, t) {
		if ((i = arguments.length - 2) > 0) for (var n = Array(i), r = 0, i, a; r < i; ++r) n[r] = arguments[r + 2];
		if (!this._.hasOwnProperty(e)) throw Error("unknown type: " + e);
		for (a = this._[e], r = 0, i = a.length; r < i; ++r) a[r].value.apply(t, n);
	},
	apply: function(e, t, n) {
		if (!this._.hasOwnProperty(e)) throw Error("unknown type: " + e);
		for (var r = this._[e], i = 0, a = r.length; i < a; ++i) r[i].value.apply(t, n);
	}
};
function uu(e, t) {
	for (var n = 0, r = e.length, i; n < r; ++n) if ((i = e[n]).name === t) return i.value;
}
function du(e, t, n) {
	for (var r = 0, i = e.length; r < i; ++r) if (e[r].name === t) {
		e[r] = ou, e = e.slice(0, r).concat(e.slice(r + 1));
		break;
	}
	return n != null && e.push({
		name: t,
		value: n
	}), e;
}
var fu = "http://www.w3.org/1999/xhtml", pu = {
	svg: "http://www.w3.org/2000/svg",
	xhtml: fu,
	xlink: "http://www.w3.org/1999/xlink",
	xml: "http://www.w3.org/XML/1998/namespace",
	xmlns: "http://www.w3.org/2000/xmlns/"
};
function mu(e) {
	var t = e += "", n = t.indexOf(":");
	return n >= 0 && (t = e.slice(0, n)) !== "xmlns" && (e = e.slice(n + 1)), pu.hasOwnProperty(t) ? {
		space: pu[t],
		local: e
	} : e;
}
function hu(e) {
	return function() {
		var t = this.ownerDocument, n = this.namespaceURI;
		return n === fu && t.documentElement.namespaceURI === fu ? t.createElement(e) : t.createElementNS(n, e);
	};
}
function gu(e) {
	return function() {
		return this.ownerDocument.createElementNS(e.space, e.local);
	};
}
function _u(e) {
	var t = mu(e);
	return (t.local ? gu : hu)(t);
}
function vu() {}
function yu(e) {
	return e == null ? vu : function() {
		return this.querySelector(e);
	};
}
function bu(e) {
	typeof e != "function" && (e = yu(e));
	for (var t = this._groups, n = t.length, r = Array(n), i = 0; i < n; ++i) for (var a = t[i], o = a.length, s = r[i] = Array(o), c, l, u = 0; u < o; ++u) (c = a[u]) && (l = e.call(c, c.__data__, u, a)) && ("__data__" in c && (l.__data__ = c.__data__), s[u] = l);
	return new lf(r, this._parents);
}
function xu(e) {
	return e == null ? [] : Array.isArray(e) ? e : Array.from(e);
}
function Su() {
	return [];
}
function Cu(e) {
	return e == null ? Su : function() {
		return this.querySelectorAll(e);
	};
}
function wu(e) {
	return function() {
		return xu(e.apply(this, arguments));
	};
}
function Tu(e) {
	e = typeof e == "function" ? wu(e) : Cu(e);
	for (var t = this._groups, n = t.length, r = [], i = [], a = 0; a < n; ++a) for (var o = t[a], s = o.length, c, l = 0; l < s; ++l) (c = o[l]) && (r.push(e.call(c, c.__data__, l, o)), i.push(c));
	return new lf(r, i);
}
function Eu(e) {
	return function() {
		return this.matches(e);
	};
}
function Du(e) {
	return function(t) {
		return t.matches(e);
	};
}
var Ou = Array.prototype.find;
function ku(e) {
	return function() {
		return Ou.call(this.children, e);
	};
}
function Au() {
	return this.firstElementChild;
}
function ju(e) {
	return this.select(e == null ? Au : ku(typeof e == "function" ? e : Du(e)));
}
var Mu = Array.prototype.filter;
function Nu() {
	return Array.from(this.children);
}
function Pu(e) {
	return function() {
		return Mu.call(this.children, e);
	};
}
function Fu(e) {
	return this.selectAll(e == null ? Nu : Pu(typeof e == "function" ? e : Du(e)));
}
function Iu(e) {
	typeof e != "function" && (e = Eu(e));
	for (var t = this._groups, n = t.length, r = Array(n), i = 0; i < n; ++i) for (var a = t[i], o = a.length, s = r[i] = [], c, l = 0; l < o; ++l) (c = a[l]) && e.call(c, c.__data__, l, a) && s.push(c);
	return new lf(r, this._parents);
}
function Lu(e) {
	return Array(e.length);
}
function Ru() {
	return new lf(this._enter || this._groups.map(Lu), this._parents);
}
function zu(e, t) {
	this.ownerDocument = e.ownerDocument, this.namespaceURI = e.namespaceURI, this._next = null, this._parent = e, this.__data__ = t;
}
zu.prototype = {
	constructor: zu,
	appendChild: function(e) {
		return this._parent.insertBefore(e, this._next);
	},
	insertBefore: function(e, t) {
		return this._parent.insertBefore(e, t);
	},
	querySelector: function(e) {
		return this._parent.querySelector(e);
	},
	querySelectorAll: function(e) {
		return this._parent.querySelectorAll(e);
	}
};
function Bu(e) {
	return function() {
		return e;
	};
}
function Vu(e, t, n, r, i, a) {
	for (var o = 0, s, c = t.length, l = a.length; o < l; ++o) (s = t[o]) ? (s.__data__ = a[o], r[o] = s) : n[o] = new zu(e, a[o]);
	for (; o < c; ++o) (s = t[o]) && (i[o] = s);
}
function Hu(e, t, n, r, i, a, o) {
	var s, c, l = /* @__PURE__ */ new Map(), u = t.length, d = a.length, f = Array(u), p;
	for (s = 0; s < u; ++s) (c = t[s]) && (f[s] = p = o.call(c, c.__data__, s, t) + "", l.has(p) ? i[s] = c : l.set(p, c));
	for (s = 0; s < d; ++s) p = o.call(e, a[s], s, a) + "", (c = l.get(p)) ? (r[s] = c, c.__data__ = a[s], l.delete(p)) : n[s] = new zu(e, a[s]);
	for (s = 0; s < u; ++s) (c = t[s]) && l.get(f[s]) === c && (i[s] = c);
}
function Uu(e) {
	return e.__data__;
}
function Wu(e, t) {
	if (!arguments.length) return Array.from(this, Uu);
	var n = t ? Hu : Vu, r = this._parents, i = this._groups;
	typeof e != "function" && (e = Bu(e));
	for (var a = i.length, o = Array(a), s = Array(a), c = Array(a), l = 0; l < a; ++l) {
		var u = r[l], d = i[l], f = d.length, p = Gu(e.call(u, u && u.__data__, l, r)), m = p.length, h = s[l] = Array(m), g = o[l] = Array(m);
		n(u, d, h, g, c[l] = Array(f), p, t);
		for (var _ = 0, v = 0, y, b; _ < m; ++_) if (y = h[_]) {
			for (_ >= v && (v = _ + 1); !(b = g[v]) && ++v < m;);
			y._next = b || null;
		}
	}
	return o = new lf(o, r), o._enter = s, o._exit = c, o;
}
function Gu(e) {
	return typeof e == "object" && "length" in e ? e : Array.from(e);
}
function Ku() {
	return new lf(this._exit || this._groups.map(Lu), this._parents);
}
function qu(e, t, n) {
	var r = this.enter(), i = this, a = this.exit();
	return typeof e == "function" ? (r = e(r), r && (r = r.selection())) : r = r.append(e + ""), t != null && (i = t(i), i && (i = i.selection())), n == null ? a.remove() : n(a), r && i ? r.merge(i).order() : i;
}
function Ju(e) {
	for (var t = e.selection ? e.selection() : e, n = this._groups, r = t._groups, i = n.length, a = r.length, o = Math.min(i, a), s = Array(i), c = 0; c < o; ++c) for (var l = n[c], u = r[c], d = l.length, f = s[c] = Array(d), p, m = 0; m < d; ++m) (p = l[m] || u[m]) && (f[m] = p);
	for (; c < i; ++c) s[c] = n[c];
	return new lf(s, this._parents);
}
function Yu() {
	for (var e = this._groups, t = -1, n = e.length; ++t < n;) for (var r = e[t], i = r.length - 1, a = r[i], o; --i >= 0;) (o = r[i]) && (a && o.compareDocumentPosition(a) ^ 4 && a.parentNode.insertBefore(o, a), a = o);
	return this;
}
function Xu(e) {
	e || (e = Zu);
	function t(t, n) {
		return t && n ? e(t.__data__, n.__data__) : !t - !n;
	}
	for (var n = this._groups, r = n.length, i = Array(r), a = 0; a < r; ++a) {
		for (var o = n[a], s = o.length, c = i[a] = Array(s), l, u = 0; u < s; ++u) (l = o[u]) && (c[u] = l);
		c.sort(t);
	}
	return new lf(i, this._parents).order();
}
function Zu(e, t) {
	return e < t ? -1 : e > t ? 1 : e >= t ? 0 : NaN;
}
function Qu() {
	var e = arguments[0];
	return arguments[0] = this, e.apply(null, arguments), this;
}
function $u() {
	return Array.from(this);
}
function ed() {
	for (var e = this._groups, t = 0, n = e.length; t < n; ++t) for (var r = e[t], i = 0, a = r.length; i < a; ++i) {
		var o = r[i];
		if (o) return o;
	}
	return null;
}
function td() {
	let e = 0;
	for (let t of this) ++e;
	return e;
}
function nd() {
	return !this.node();
}
function rd(e) {
	for (var t = this._groups, n = 0, r = t.length; n < r; ++n) for (var i = t[n], a = 0, o = i.length, s; a < o; ++a) (s = i[a]) && e.call(s, s.__data__, a, i);
	return this;
}
function id(e) {
	return function() {
		this.removeAttribute(e);
	};
}
function ad(e) {
	return function() {
		this.removeAttributeNS(e.space, e.local);
	};
}
function od(e, t) {
	return function() {
		this.setAttribute(e, t);
	};
}
function sd(e, t) {
	return function() {
		this.setAttributeNS(e.space, e.local, t);
	};
}
function cd(e, t) {
	return function() {
		var n = t.apply(this, arguments);
		n == null ? this.removeAttribute(e) : this.setAttribute(e, n);
	};
}
function ld(e, t) {
	return function() {
		var n = t.apply(this, arguments);
		n == null ? this.removeAttributeNS(e.space, e.local) : this.setAttributeNS(e.space, e.local, n);
	};
}
function ud(e, t) {
	var n = mu(e);
	if (arguments.length < 2) {
		var r = this.node();
		return n.local ? r.getAttributeNS(n.space, n.local) : r.getAttribute(n);
	}
	return this.each((t == null ? n.local ? ad : id : typeof t == "function" ? n.local ? ld : cd : n.local ? sd : od)(n, t));
}
function dd(e) {
	return e.ownerDocument && e.ownerDocument.defaultView || e.document && e || e.defaultView;
}
function fd(e) {
	return function() {
		this.style.removeProperty(e);
	};
}
function pd(e, t, n) {
	return function() {
		this.style.setProperty(e, t, n);
	};
}
function md(e, t, n) {
	return function() {
		var r = t.apply(this, arguments);
		r == null ? this.style.removeProperty(e) : this.style.setProperty(e, r, n);
	};
}
function hd(e, t, n) {
	return arguments.length > 1 ? this.each((t == null ? fd : typeof t == "function" ? md : pd)(e, t, n ?? "")) : gd(this.node(), e);
}
function gd(e, t) {
	return e.style.getPropertyValue(t) || dd(e).getComputedStyle(e, null).getPropertyValue(t);
}
function _d(e) {
	return function() {
		delete this[e];
	};
}
function vd(e, t) {
	return function() {
		this[e] = t;
	};
}
function yd(e, t) {
	return function() {
		var n = t.apply(this, arguments);
		n == null ? delete this[e] : this[e] = n;
	};
}
function bd(e, t) {
	return arguments.length > 1 ? this.each((t == null ? _d : typeof t == "function" ? yd : vd)(e, t)) : this.node()[e];
}
function xd(e) {
	return e.trim().split(/^|\s+/);
}
function Sd(e) {
	return e.classList || new Cd(e);
}
function Cd(e) {
	this._node = e, this._names = xd(e.getAttribute("class") || "");
}
Cd.prototype = {
	add: function(e) {
		this._names.indexOf(e) < 0 && (this._names.push(e), this._node.setAttribute("class", this._names.join(" ")));
	},
	remove: function(e) {
		var t = this._names.indexOf(e);
		t >= 0 && (this._names.splice(t, 1), this._node.setAttribute("class", this._names.join(" ")));
	},
	contains: function(e) {
		return this._names.indexOf(e) >= 0;
	}
};
function wd(e, t) {
	for (var n = Sd(e), r = -1, i = t.length; ++r < i;) n.add(t[r]);
}
function Td(e, t) {
	for (var n = Sd(e), r = -1, i = t.length; ++r < i;) n.remove(t[r]);
}
function Ed(e) {
	return function() {
		wd(this, e);
	};
}
function Dd(e) {
	return function() {
		Td(this, e);
	};
}
function Od(e, t) {
	return function() {
		(t.apply(this, arguments) ? wd : Td)(this, e);
	};
}
function kd(e, t) {
	var n = xd(e + "");
	if (arguments.length < 2) {
		for (var r = Sd(this.node()), i = -1, a = n.length; ++i < a;) if (!r.contains(n[i])) return !1;
		return !0;
	}
	return this.each((typeof t == "function" ? Od : t ? Ed : Dd)(n, t));
}
function Ad() {
	this.textContent = "";
}
function jd(e) {
	return function() {
		this.textContent = e;
	};
}
function Md(e) {
	return function() {
		var t = e.apply(this, arguments);
		this.textContent = t ?? "";
	};
}
function Nd(e) {
	return arguments.length ? this.each(e == null ? Ad : (typeof e == "function" ? Md : jd)(e)) : this.node().textContent;
}
function Pd() {
	this.innerHTML = "";
}
function Fd(e) {
	return function() {
		this.innerHTML = e;
	};
}
function Id(e) {
	return function() {
		var t = e.apply(this, arguments);
		this.innerHTML = t ?? "";
	};
}
function Ld(e) {
	return arguments.length ? this.each(e == null ? Pd : (typeof e == "function" ? Id : Fd)(e)) : this.node().innerHTML;
}
function Rd() {
	this.nextSibling && this.parentNode.appendChild(this);
}
function zd() {
	return this.each(Rd);
}
function Bd() {
	this.previousSibling && this.parentNode.insertBefore(this, this.parentNode.firstChild);
}
function Vd() {
	return this.each(Bd);
}
function Hd(e) {
	var t = typeof e == "function" ? e : _u(e);
	return this.select(function() {
		return this.appendChild(t.apply(this, arguments));
	});
}
function Ud() {
	return null;
}
function Wd(e, t) {
	var n = typeof e == "function" ? e : _u(e), r = t == null ? Ud : typeof t == "function" ? t : yu(t);
	return this.select(function() {
		return this.insertBefore(n.apply(this, arguments), r.apply(this, arguments) || null);
	});
}
function Gd() {
	var e = this.parentNode;
	e && e.removeChild(this);
}
function Kd() {
	return this.each(Gd);
}
function qd() {
	var e = this.cloneNode(!1), t = this.parentNode;
	return t ? t.insertBefore(e, this.nextSibling) : e;
}
function Jd() {
	var e = this.cloneNode(!0), t = this.parentNode;
	return t ? t.insertBefore(e, this.nextSibling) : e;
}
function Yd(e) {
	return this.select(e ? Jd : qd);
}
function Xd(e) {
	return arguments.length ? this.property("__data__", e) : this.node().__data__;
}
function Zd(e) {
	return function(t) {
		e.call(this, t, this.__data__);
	};
}
function Qd(e) {
	return e.trim().split(/^|\s+/).map(function(e) {
		var t = "", n = e.indexOf(".");
		return n >= 0 && (t = e.slice(n + 1), e = e.slice(0, n)), {
			type: e,
			name: t
		};
	});
}
function $d(e) {
	return function() {
		var t = this.__on;
		if (t) {
			for (var n = 0, r = -1, i = t.length, a; n < i; ++n) a = t[n], (!e.type || a.type === e.type) && a.name === e.name ? this.removeEventListener(a.type, a.listener, a.options) : t[++r] = a;
			++r ? t.length = r : delete this.__on;
		}
	};
}
function ef(e, t, n) {
	return function() {
		var r = this.__on, i, a = Zd(t);
		if (r) {
			for (var o = 0, s = r.length; o < s; ++o) if ((i = r[o]).type === e.type && i.name === e.name) {
				this.removeEventListener(i.type, i.listener, i.options), this.addEventListener(i.type, i.listener = a, i.options = n), i.value = t;
				return;
			}
		}
		this.addEventListener(e.type, a, n), i = {
			type: e.type,
			name: e.name,
			value: t,
			listener: a,
			options: n
		}, r ? r.push(i) : this.__on = [i];
	};
}
function tf(e, t, n) {
	var r = Qd(e + ""), i, a = r.length, o;
	if (arguments.length < 2) {
		var s = this.node().__on;
		if (s) {
			for (var c = 0, l = s.length, u; c < l; ++c) for (i = 0, u = s[c]; i < a; ++i) if ((o = r[i]).type === u.type && o.name === u.name) return u.value;
		}
	} else {
		for (s = t ? ef : $d, i = 0; i < a; ++i) this.each(s(r[i], t, n));
		return this;
	}
}
function nf(e, t, n) {
	var r = dd(e), i = r.CustomEvent;
	typeof i == "function" ? i = new i(t, n) : (i = r.document.createEvent("Event"), n ? (i.initEvent(t, n.bubbles, n.cancelable), i.detail = n.detail) : i.initEvent(t, !1, !1)), e.dispatchEvent(i);
}
function rf(e, t) {
	return function() {
		return nf(this, e, t);
	};
}
function af(e, t) {
	return function() {
		return nf(this, e, t.apply(this, arguments));
	};
}
function of(e, t) {
	return this.each((typeof t == "function" ? af : rf)(e, t));
}
function* sf() {
	for (var e = this._groups, t = 0, n = e.length; t < n; ++t) for (var r = e[t], i = 0, a = r.length, o; i < a; ++i) (o = r[i]) && (yield o);
}
var cf = [null];
function lf(e, t) {
	this._groups = e, this._parents = t;
}
function uf() {
	return new lf([[document.documentElement]], cf);
}
function df() {
	return this;
}
lf.prototype = uf.prototype = {
	constructor: lf,
	select: bu,
	selectAll: Tu,
	selectChild: ju,
	selectChildren: Fu,
	filter: Iu,
	data: Wu,
	enter: Ru,
	exit: Ku,
	join: qu,
	merge: Ju,
	selection: df,
	order: Yu,
	sort: Xu,
	call: Qu,
	nodes: $u,
	node: ed,
	size: td,
	empty: nd,
	each: rd,
	attr: ud,
	style: hd,
	property: bd,
	classed: kd,
	text: Nd,
	html: Ld,
	raise: zd,
	lower: Vd,
	append: Hd,
	insert: Wd,
	remove: Kd,
	clone: Yd,
	datum: Xd,
	on: tf,
	dispatch: of,
	[Symbol.iterator]: sf
};
function ff(e) {
	return typeof e == "string" ? new lf([[document.querySelector(e)]], [document.documentElement]) : new lf([[e]], cf);
}
function pf(e) {
	let t;
	for (; t = e.sourceEvent;) e = t;
	return e;
}
function mf(e, t) {
	if (e = pf(e), t === void 0 && (t = e.currentTarget), t) {
		var n = t.ownerSVGElement || t;
		if (n.createSVGPoint) {
			var r = n.createSVGPoint();
			return r.x = e.clientX, r.y = e.clientY, r = r.matrixTransform(t.getScreenCTM().inverse()), [r.x, r.y];
		}
		if (t.getBoundingClientRect) {
			var i = t.getBoundingClientRect();
			return [e.clientX - i.left - t.clientLeft, e.clientY - i.top - t.clientTop];
		}
	}
	return [e.pageX, e.pageY];
}
var hf = {
	capture: !0,
	passive: !1
};
function gf(e) {
	e.preventDefault(), e.stopImmediatePropagation();
}
function _f(e) {
	var t = e.document.documentElement, n = ff(e).on("dragstart.drag", gf, hf);
	"onselectstart" in t ? n.on("selectstart.drag", gf, hf) : (t.__noselect = t.style.MozUserSelect, t.style.MozUserSelect = "none");
}
function vf(e, t) {
	var n = e.document.documentElement, r = ff(e).on("dragstart.drag", null);
	t && (r.on("click.drag", gf, hf), setTimeout(function() {
		r.on("click.drag", null);
	}, 0)), "onselectstart" in n ? r.on("selectstart.drag", null) : (n.style.MozUserSelect = n.__noselect, delete n.__noselect);
}
function yf(e, t, n) {
	e.prototype = t.prototype = n, n.constructor = e;
}
function bf(e, t) {
	var n = Object.create(e.prototype);
	for (var r in t) n[r] = t[r];
	return n;
}
function xf() {}
var Sf = .7, Cf = 1 / Sf, wf = "\\s*([+-]?\\d+)\\s*", Tf = "\\s*([+-]?(?:\\d*\\.)?\\d+(?:[eE][+-]?\\d+)?)\\s*", Ef = "\\s*([+-]?(?:\\d*\\.)?\\d+(?:[eE][+-]?\\d+)?)%\\s*", Df = /^#([0-9a-f]{3,8})$/, Of = RegExp(`^rgb\\(${wf},${wf},${wf}\\)$`), kf = RegExp(`^rgb\\(${Ef},${Ef},${Ef}\\)$`), Af = RegExp(`^rgba\\(${wf},${wf},${wf},${Tf}\\)$`), jf = RegExp(`^rgba\\(${Ef},${Ef},${Ef},${Tf}\\)$`), Mf = RegExp(`^hsl\\(${Tf},${Ef},${Ef}\\)$`), Nf = RegExp(`^hsla\\(${Tf},${Ef},${Ef},${Tf}\\)$`), Pf = {
	aliceblue: 15792383,
	antiquewhite: 16444375,
	aqua: 65535,
	aquamarine: 8388564,
	azure: 15794175,
	beige: 16119260,
	bisque: 16770244,
	black: 0,
	blanchedalmond: 16772045,
	blue: 255,
	blueviolet: 9055202,
	brown: 10824234,
	burlywood: 14596231,
	cadetblue: 6266528,
	chartreuse: 8388352,
	chocolate: 13789470,
	coral: 16744272,
	cornflowerblue: 6591981,
	cornsilk: 16775388,
	crimson: 14423100,
	cyan: 65535,
	darkblue: 139,
	darkcyan: 35723,
	darkgoldenrod: 12092939,
	darkgray: 11119017,
	darkgreen: 25600,
	darkgrey: 11119017,
	darkkhaki: 12433259,
	darkmagenta: 9109643,
	darkolivegreen: 5597999,
	darkorange: 16747520,
	darkorchid: 10040012,
	darkred: 9109504,
	darksalmon: 15308410,
	darkseagreen: 9419919,
	darkslateblue: 4734347,
	darkslategray: 3100495,
	darkslategrey: 3100495,
	darkturquoise: 52945,
	darkviolet: 9699539,
	deeppink: 16716947,
	deepskyblue: 49151,
	dimgray: 6908265,
	dimgrey: 6908265,
	dodgerblue: 2003199,
	firebrick: 11674146,
	floralwhite: 16775920,
	forestgreen: 2263842,
	fuchsia: 16711935,
	gainsboro: 14474460,
	ghostwhite: 16316671,
	gold: 16766720,
	goldenrod: 14329120,
	gray: 8421504,
	green: 32768,
	greenyellow: 11403055,
	grey: 8421504,
	honeydew: 15794160,
	hotpink: 16738740,
	indianred: 13458524,
	indigo: 4915330,
	ivory: 16777200,
	khaki: 15787660,
	lavender: 15132410,
	lavenderblush: 16773365,
	lawngreen: 8190976,
	lemonchiffon: 16775885,
	lightblue: 11393254,
	lightcoral: 15761536,
	lightcyan: 14745599,
	lightgoldenrodyellow: 16448210,
	lightgray: 13882323,
	lightgreen: 9498256,
	lightgrey: 13882323,
	lightpink: 16758465,
	lightsalmon: 16752762,
	lightseagreen: 2142890,
	lightskyblue: 8900346,
	lightslategray: 7833753,
	lightslategrey: 7833753,
	lightsteelblue: 11584734,
	lightyellow: 16777184,
	lime: 65280,
	limegreen: 3329330,
	linen: 16445670,
	magenta: 16711935,
	maroon: 8388608,
	mediumaquamarine: 6737322,
	mediumblue: 205,
	mediumorchid: 12211667,
	mediumpurple: 9662683,
	mediumseagreen: 3978097,
	mediumslateblue: 8087790,
	mediumspringgreen: 64154,
	mediumturquoise: 4772300,
	mediumvioletred: 13047173,
	midnightblue: 1644912,
	mintcream: 16121850,
	mistyrose: 16770273,
	moccasin: 16770229,
	navajowhite: 16768685,
	navy: 128,
	oldlace: 16643558,
	olive: 8421376,
	olivedrab: 7048739,
	orange: 16753920,
	orangered: 16729344,
	orchid: 14315734,
	palegoldenrod: 15657130,
	palegreen: 10025880,
	paleturquoise: 11529966,
	palevioletred: 14381203,
	papayawhip: 16773077,
	peachpuff: 16767673,
	peru: 13468991,
	pink: 16761035,
	plum: 14524637,
	powderblue: 11591910,
	purple: 8388736,
	rebeccapurple: 6697881,
	red: 16711680,
	rosybrown: 12357519,
	royalblue: 4286945,
	saddlebrown: 9127187,
	salmon: 16416882,
	sandybrown: 16032864,
	seagreen: 3050327,
	seashell: 16774638,
	sienna: 10506797,
	silver: 12632256,
	skyblue: 8900331,
	slateblue: 6970061,
	slategray: 7372944,
	slategrey: 7372944,
	snow: 16775930,
	springgreen: 65407,
	steelblue: 4620980,
	tan: 13808780,
	teal: 32896,
	thistle: 14204888,
	tomato: 16737095,
	turquoise: 4251856,
	violet: 15631086,
	wheat: 16113331,
	white: 16777215,
	whitesmoke: 16119285,
	yellow: 16776960,
	yellowgreen: 10145074
};
yf(xf, zf, {
	copy(e) {
		return Object.assign(new this.constructor(), this, e);
	},
	displayable() {
		return this.rgb().displayable();
	},
	hex: Ff,
	formatHex: Ff,
	formatHex8: If,
	formatHsl: Lf,
	formatRgb: Rf,
	toString: Rf
});
function Ff() {
	return this.rgb().formatHex();
}
function If() {
	return this.rgb().formatHex8();
}
function Lf() {
	return Qf(this).formatHsl();
}
function Rf() {
	return this.rgb().formatRgb();
}
function zf(e) {
	var t, n;
	return e = (e + "").trim().toLowerCase(), (t = Df.exec(e)) ? (n = t[1].length, t = parseInt(t[1], 16), n === 6 ? Bf(t) : n === 3 ? new Wf(t >> 8 & 15 | t >> 4 & 240, t >> 4 & 15 | t & 240, (t & 15) << 4 | t & 15, 1) : n === 8 ? Vf(t >> 24 & 255, t >> 16 & 255, t >> 8 & 255, (t & 255) / 255) : n === 4 ? Vf(t >> 12 & 15 | t >> 8 & 240, t >> 8 & 15 | t >> 4 & 240, t >> 4 & 15 | t & 240, ((t & 15) << 4 | t & 15) / 255) : null) : (t = Of.exec(e)) ? new Wf(t[1], t[2], t[3], 1) : (t = kf.exec(e)) ? new Wf(t[1] * 255 / 100, t[2] * 255 / 100, t[3] * 255 / 100, 1) : (t = Af.exec(e)) ? Vf(t[1], t[2], t[3], t[4]) : (t = jf.exec(e)) ? Vf(t[1] * 255 / 100, t[2] * 255 / 100, t[3] * 255 / 100, t[4]) : (t = Mf.exec(e)) ? Zf(t[1], t[2] / 100, t[3] / 100, 1) : (t = Nf.exec(e)) ? Zf(t[1], t[2] / 100, t[3] / 100, t[4]) : Pf.hasOwnProperty(e) ? Bf(Pf[e]) : e === "transparent" ? new Wf(NaN, NaN, NaN, 0) : null;
}
function Bf(e) {
	return new Wf(e >> 16 & 255, e >> 8 & 255, e & 255, 1);
}
function Vf(e, t, n, r) {
	return r <= 0 && (e = t = n = NaN), new Wf(e, t, n, r);
}
function Hf(e) {
	return e instanceof xf || (e = zf(e)), e ? (e = e.rgb(), new Wf(e.r, e.g, e.b, e.opacity)) : new Wf();
}
function Uf(e, t, n, r) {
	return arguments.length === 1 ? Hf(e) : new Wf(e, t, n, r ?? 1);
}
function Wf(e, t, n, r) {
	this.r = +e, this.g = +t, this.b = +n, this.opacity = +r;
}
yf(Wf, Uf, bf(xf, {
	brighter(e) {
		return e = e == null ? Cf : Cf ** +e, new Wf(this.r * e, this.g * e, this.b * e, this.opacity);
	},
	darker(e) {
		return e = e == null ? Sf : Sf ** +e, new Wf(this.r * e, this.g * e, this.b * e, this.opacity);
	},
	rgb() {
		return this;
	},
	clamp() {
		return new Wf(Yf(this.r), Yf(this.g), Yf(this.b), Jf(this.opacity));
	},
	displayable() {
		return -.5 <= this.r && this.r < 255.5 && -.5 <= this.g && this.g < 255.5 && -.5 <= this.b && this.b < 255.5 && 0 <= this.opacity && this.opacity <= 1;
	},
	hex: Gf,
	formatHex: Gf,
	formatHex8: Kf,
	formatRgb: qf,
	toString: qf
}));
function Gf() {
	return `#${Xf(this.r)}${Xf(this.g)}${Xf(this.b)}`;
}
function Kf() {
	return `#${Xf(this.r)}${Xf(this.g)}${Xf(this.b)}${Xf((isNaN(this.opacity) ? 1 : this.opacity) * 255)}`;
}
function qf() {
	let e = Jf(this.opacity);
	return `${e === 1 ? "rgb(" : "rgba("}${Yf(this.r)}, ${Yf(this.g)}, ${Yf(this.b)}${e === 1 ? ")" : `, ${e})`}`;
}
function Jf(e) {
	return isNaN(e) ? 1 : Math.max(0, Math.min(1, e));
}
function Yf(e) {
	return Math.max(0, Math.min(255, Math.round(e) || 0));
}
function Xf(e) {
	return e = Yf(e), (e < 16 ? "0" : "") + e.toString(16);
}
function Zf(e, t, n, r) {
	return r <= 0 ? e = t = n = NaN : n <= 0 || n >= 1 ? e = t = NaN : t <= 0 && (e = NaN), new ep(e, t, n, r);
}
function Qf(e) {
	if (e instanceof ep) return new ep(e.h, e.s, e.l, e.opacity);
	if (e instanceof xf || (e = zf(e)), !e) return new ep();
	if (e instanceof ep) return e;
	e = e.rgb();
	var t = e.r / 255, n = e.g / 255, r = e.b / 255, i = Math.min(t, n, r), a = Math.max(t, n, r), o = NaN, s = a - i, c = (a + i) / 2;
	return s ? (o = t === a ? (n - r) / s + (n < r) * 6 : n === a ? (r - t) / s + 2 : (t - n) / s + 4, s /= c < .5 ? a + i : 2 - a - i, o *= 60) : s = c > 0 && c < 1 ? 0 : o, new ep(o, s, c, e.opacity);
}
function $f(e, t, n, r) {
	return arguments.length === 1 ? Qf(e) : new ep(e, t, n, r ?? 1);
}
function ep(e, t, n, r) {
	this.h = +e, this.s = +t, this.l = +n, this.opacity = +r;
}
yf(ep, $f, bf(xf, {
	brighter(e) {
		return e = e == null ? Cf : Cf ** +e, new ep(this.h, this.s, this.l * e, this.opacity);
	},
	darker(e) {
		return e = e == null ? Sf : Sf ** +e, new ep(this.h, this.s, this.l * e, this.opacity);
	},
	rgb() {
		var e = this.h % 360 + (this.h < 0) * 360, t = isNaN(e) || isNaN(this.s) ? 0 : this.s, n = this.l, r = n + (n < .5 ? n : 1 - n) * t, i = 2 * n - r;
		return new Wf(rp(e >= 240 ? e - 240 : e + 120, i, r), rp(e, i, r), rp(e < 120 ? e + 240 : e - 120, i, r), this.opacity);
	},
	clamp() {
		return new ep(tp(this.h), np(this.s), np(this.l), Jf(this.opacity));
	},
	displayable() {
		return (0 <= this.s && this.s <= 1 || isNaN(this.s)) && 0 <= this.l && this.l <= 1 && 0 <= this.opacity && this.opacity <= 1;
	},
	formatHsl() {
		let e = Jf(this.opacity);
		return `${e === 1 ? "hsl(" : "hsla("}${tp(this.h)}, ${np(this.s) * 100}%, ${np(this.l) * 100}%${e === 1 ? ")" : `, ${e})`}`;
	}
}));
function tp(e) {
	return e = (e || 0) % 360, e < 0 ? e + 360 : e;
}
function np(e) {
	return Math.max(0, Math.min(1, e || 0));
}
function rp(e, t, n) {
	return (e < 60 ? t + (n - t) * e / 60 : e < 180 ? n : e < 240 ? t + (n - t) * (240 - e) / 60 : t) * 255;
}
var ip = (e) => () => e;
function ap(e, t) {
	return function(n) {
		return e + n * t;
	};
}
function op(e, t, n) {
	return e **= +n, t = t ** +n - e, n = 1 / n, function(r) {
		return (e + r * t) ** +n;
	};
}
function sp(e) {
	return (e = +e) == 1 ? cp : function(t, n) {
		return n - t ? op(t, n, e) : ip(isNaN(t) ? n : t);
	};
}
function cp(e, t) {
	var n = t - e;
	return n ? ap(e, n) : ip(isNaN(e) ? t : e);
}
var lp = function e(t) {
	var n = sp(t);
	function r(e, t) {
		var r = n((e = Uf(e)).r, (t = Uf(t)).r), i = n(e.g, t.g), a = n(e.b, t.b), o = cp(e.opacity, t.opacity);
		return function(t) {
			return e.r = r(t), e.g = i(t), e.b = a(t), e.opacity = o(t), e + "";
		};
	}
	return r.gamma = e, r;
}(1);
function up(e, t) {
	return e = +e, t = +t, function(n) {
		return e * (1 - n) + t * n;
	};
}
var dp = /[-+]?(?:\d+\.?\d*|\.?\d+)(?:[eE][-+]?\d+)?/g, fp = new RegExp(dp.source, "g");
function pp(e) {
	return function() {
		return e;
	};
}
function mp(e) {
	return function(t) {
		return e(t) + "";
	};
}
function hp(e, t) {
	var n = dp.lastIndex = fp.lastIndex = 0, r, i, a, o = -1, s = [], c = [];
	for (e += "", t += ""; (r = dp.exec(e)) && (i = fp.exec(t));) (a = i.index) > n && (a = t.slice(n, a), s[o] ? s[o] += a : s[++o] = a), (r = r[0]) === (i = i[0]) ? s[o] ? s[o] += i : s[++o] = i : (s[++o] = null, c.push({
		i: o,
		x: up(r, i)
	})), n = fp.lastIndex;
	return n < t.length && (a = t.slice(n), s[o] ? s[o] += a : s[++o] = a), s.length < 2 ? c[0] ? mp(c[0].x) : pp(t) : (t = c.length, function(e) {
		for (var n = 0, r; n < t; ++n) s[(r = c[n]).i] = r.x(e);
		return s.join("");
	});
}
var gp = 180 / Math.PI, _p = {
	translateX: 0,
	translateY: 0,
	rotate: 0,
	skewX: 0,
	scaleX: 1,
	scaleY: 1
};
function vp(e, t, n, r, i, a) {
	var o, s, c;
	return (o = Math.sqrt(e * e + t * t)) && (e /= o, t /= o), (c = e * n + t * r) && (n -= e * c, r -= t * c), (s = Math.sqrt(n * n + r * r)) && (n /= s, r /= s, c /= s), e * r < t * n && (e = -e, t = -t, c = -c, o = -o), {
		translateX: i,
		translateY: a,
		rotate: Math.atan2(t, e) * gp,
		skewX: Math.atan(c) * gp,
		scaleX: o,
		scaleY: s
	};
}
var yp;
function bp(e) {
	let t = new (typeof DOMMatrix == "function" ? DOMMatrix : WebKitCSSMatrix)(e + "");
	return t.isIdentity ? _p : vp(t.a, t.b, t.c, t.d, t.e, t.f);
}
function xp(e) {
	return e == null || (yp || (yp = document.createElementNS("http://www.w3.org/2000/svg", "g")), yp.setAttribute("transform", e), !(e = yp.transform.baseVal.consolidate())) ? _p : (e = e.matrix, vp(e.a, e.b, e.c, e.d, e.e, e.f));
}
function Sp(e, t, n, r) {
	function i(e) {
		return e.length ? e.pop() + " " : "";
	}
	function a(e, r, i, a, o, s) {
		if (e !== i || r !== a) {
			var c = o.push("translate(", null, t, null, n);
			s.push({
				i: c - 4,
				x: up(e, i)
			}, {
				i: c - 2,
				x: up(r, a)
			});
		} else (i || a) && o.push("translate(" + i + t + a + n);
	}
	function o(e, t, n, a) {
		e === t ? t && n.push(i(n) + "rotate(" + t + r) : (e - t > 180 ? t += 360 : t - e > 180 && (e += 360), a.push({
			i: n.push(i(n) + "rotate(", null, r) - 2,
			x: up(e, t)
		}));
	}
	function s(e, t, n, a) {
		e === t ? t && n.push(i(n) + "skewX(" + t + r) : a.push({
			i: n.push(i(n) + "skewX(", null, r) - 2,
			x: up(e, t)
		});
	}
	function c(e, t, n, r, a, o) {
		if (e !== n || t !== r) {
			var s = a.push(i(a) + "scale(", null, ",", null, ")");
			o.push({
				i: s - 4,
				x: up(e, n)
			}, {
				i: s - 2,
				x: up(t, r)
			});
		} else (n !== 1 || r !== 1) && a.push(i(a) + "scale(" + n + "," + r + ")");
	}
	return function(t, n) {
		var r = [], i = [];
		return t = e(t), n = e(n), a(t.translateX, t.translateY, n.translateX, n.translateY, r, i), o(t.rotate, n.rotate, r, i), s(t.skewX, n.skewX, r, i), c(t.scaleX, t.scaleY, n.scaleX, n.scaleY, r, i), t = n = null, function(e) {
			for (var t = -1, n = i.length, a; ++t < n;) r[(a = i[t]).i] = a.x(e);
			return r.join("");
		};
	};
}
var Cp = Sp(bp, "px, ", "px)", "deg)"), wp = Sp(xp, ", ", ")", ")"), Tp = 1e-12;
function Ep(e) {
	return ((e = Math.exp(e)) + 1 / e) / 2;
}
function Dp(e) {
	return ((e = Math.exp(e)) - 1 / e) / 2;
}
function Op(e) {
	return ((e = Math.exp(2 * e)) - 1) / (e + 1);
}
var kp = function e(t, n, r) {
	function i(e, i) {
		var a = e[0], o = e[1], s = e[2], c = i[0], l = i[1], u = i[2], d = c - a, f = l - o, p = d * d + f * f, m, h;
		if (p < Tp) h = Math.log(u / s) / t, m = function(e) {
			return [
				a + e * d,
				o + e * f,
				s * Math.exp(t * e * h)
			];
		};
		else {
			var g = Math.sqrt(p), _ = (u * u - s * s + r * p) / (2 * s * n * g), v = (u * u - s * s - r * p) / (2 * u * n * g), y = Math.log(Math.sqrt(_ * _ + 1) - _);
			h = (Math.log(Math.sqrt(v * v + 1) - v) - y) / t, m = function(e) {
				var r = e * h, i = Ep(y), c = s / (n * g) * (i * Op(t * r + y) - Dp(y));
				return [
					a + c * d,
					o + c * f,
					s * i / Ep(t * r + y)
				];
			};
		}
		return m.duration = h * 1e3 * t / Math.SQRT2, m;
	}
	return i.rho = function(t) {
		var n = Math.max(.001, +t), r = n * n;
		return e(n, r, r * r);
	}, i;
}(Math.SQRT2, 2, 4), Ap = 0, jp = 0, Mp = 0, Np = 1e3, Pp, Fp, Ip = 0, Lp = 0, Rp = 0, zp = typeof performance == "object" && performance.now ? performance : Date, Bp = typeof window == "object" && window.requestAnimationFrame ? window.requestAnimationFrame.bind(window) : function(e) {
	setTimeout(e, 17);
};
function Vp() {
	return Lp || (Bp(Hp), Lp = zp.now() + Rp);
}
function Hp() {
	Lp = 0;
}
function Up() {
	this._call = this._time = this._next = null;
}
Up.prototype = Wp.prototype = {
	constructor: Up,
	restart: function(e, t, n) {
		if (typeof e != "function") throw TypeError("callback is not a function");
		n = (n == null ? Vp() : +n) + (t == null ? 0 : +t), !this._next && Fp !== this && (Fp ? Fp._next = this : Pp = this, Fp = this), this._call = e, this._time = n, Yp();
	},
	stop: function() {
		this._call && (this._call = null, this._time = Infinity, Yp());
	}
};
function Wp(e, t, n) {
	var r = new Up();
	return r.restart(e, t, n), r;
}
function Gp() {
	Vp(), ++Ap;
	for (var e = Pp, t; e;) (t = Lp - e._time) >= 0 && e._call.call(void 0, t), e = e._next;
	--Ap;
}
function Kp() {
	Lp = (Ip = zp.now()) + Rp, Ap = jp = 0;
	try {
		Gp();
	} finally {
		Ap = 0, Jp(), Lp = 0;
	}
}
function qp() {
	var e = zp.now(), t = e - Ip;
	t > Np && (Rp -= t, Ip = e);
}
function Jp() {
	for (var e, t = Pp, n, r = Infinity; t;) t._call ? (r > t._time && (r = t._time), e = t, t = t._next) : (n = t._next, t._next = null, t = e ? e._next = n : Pp = n);
	Fp = e, Yp(r);
}
function Yp(e) {
	Ap || (jp && (jp = clearTimeout(jp)), e - Lp > 24 ? (e < Infinity && (jp = setTimeout(Kp, e - zp.now() - Rp)), Mp && (Mp = clearInterval(Mp))) : (Mp || (Ip = zp.now(), Mp = setInterval(qp, Np)), Ap = 1, Bp(Kp)));
}
function Xp(e, t, n) {
	var r = new Up();
	return t = t == null ? 0 : +t, r.restart((n) => {
		r.stop(), e(n + t);
	}, t, n), r;
}
var Zp = su("start", "end", "cancel", "interrupt"), Qp = [], $p = 0, em = 1, tm = 2, nm = 3, rm = 4, im = 5, am = 6;
function om(e, t, n, r, i, a) {
	var o = e.__transition;
	if (!o) e.__transition = {};
	else if (n in o) return;
	um(e, n, {
		name: t,
		index: r,
		group: i,
		on: Zp,
		tween: Qp,
		time: a.time,
		delay: a.delay,
		duration: a.duration,
		ease: a.ease,
		timer: null,
		state: $p
	});
}
function sm(e, t) {
	var n = lm(e, t);
	if (n.state > $p) throw Error("too late; already scheduled");
	return n;
}
function cm(e, t) {
	var n = lm(e, t);
	if (n.state > nm) throw Error("too late; already running");
	return n;
}
function lm(e, t) {
	var n = e.__transition;
	if (!n || !(n = n[t])) throw Error("transition not found");
	return n;
}
function um(e, t, n) {
	var r = e.__transition, i;
	r[t] = n, n.timer = Wp(a, 0, n.time);
	function a(e) {
		n.state = em, n.timer.restart(o, n.delay, n.time), n.delay <= e && o(e - n.delay);
	}
	function o(a) {
		var l, u, d, f;
		if (n.state !== em) return c();
		for (l in r) if (f = r[l], f.name === n.name) {
			if (f.state === nm) return Xp(o);
			f.state === rm ? (f.state = am, f.timer.stop(), f.on.call("interrupt", e, e.__data__, f.index, f.group), delete r[l]) : +l < t && (f.state = am, f.timer.stop(), f.on.call("cancel", e, e.__data__, f.index, f.group), delete r[l]);
		}
		if (Xp(function() {
			n.state === nm && (n.state = rm, n.timer.restart(s, n.delay, n.time), s(a));
		}), n.state = tm, n.on.call("start", e, e.__data__, n.index, n.group), n.state === tm) {
			for (n.state = nm, i = Array(d = n.tween.length), l = 0, u = -1; l < d; ++l) (f = n.tween[l].value.call(e, e.__data__, n.index, n.group)) && (i[++u] = f);
			i.length = u + 1;
		}
	}
	function s(t) {
		for (var r = t < n.duration ? n.ease.call(null, t / n.duration) : (n.timer.restart(c), n.state = im, 1), a = -1, o = i.length; ++a < o;) i[a].call(e, r);
		n.state === im && (n.on.call("end", e, e.__data__, n.index, n.group), c());
	}
	function c() {
		for (var i in n.state = am, n.timer.stop(), delete r[t], r) return;
		delete e.__transition;
	}
}
function dm(e, t) {
	var n = e.__transition, r, i, a = !0, o;
	if (n) {
		for (o in t = t == null ? null : t + "", n) (r = n[o]).name === t ? (i = r.state > tm && r.state < im, r.state = am, r.timer.stop(), r.on.call(i ? "interrupt" : "cancel", e, e.__data__, r.index, r.group), delete n[o]) : a = !1;
		a && delete e.__transition;
	}
}
function fm(e) {
	return this.each(function() {
		dm(this, e);
	});
}
function pm(e, t) {
	var n, r;
	return function() {
		var i = cm(this, e), a = i.tween;
		if (a !== n) {
			r = n = a;
			for (var o = 0, s = r.length; o < s; ++o) if (r[o].name === t) {
				r = r.slice(), r.splice(o, 1);
				break;
			}
		}
		i.tween = r;
	};
}
function mm(e, t, n) {
	var r, i;
	if (typeof n != "function") throw Error();
	return function() {
		var a = cm(this, e), o = a.tween;
		if (o !== r) {
			i = (r = o).slice();
			for (var s = {
				name: t,
				value: n
			}, c = 0, l = i.length; c < l; ++c) if (i[c].name === t) {
				i[c] = s;
				break;
			}
			c === l && i.push(s);
		}
		a.tween = i;
	};
}
function hm(e, t) {
	var n = this._id;
	if (e += "", arguments.length < 2) {
		for (var r = lm(this.node(), n).tween, i = 0, a = r.length, o; i < a; ++i) if ((o = r[i]).name === e) return o.value;
		return null;
	}
	return this.each((t == null ? pm : mm)(n, e, t));
}
function gm(e, t, n) {
	var r = e._id;
	return e.each(function() {
		var e = cm(this, r);
		(e.value || (e.value = {}))[t] = n.apply(this, arguments);
	}), function(e) {
		return lm(e, r).value[t];
	};
}
function _m(e, t) {
	var n;
	return (typeof t == "number" ? up : t instanceof zf ? lp : (n = zf(t)) ? (t = n, lp) : hp)(e, t);
}
function vm(e) {
	return function() {
		this.removeAttribute(e);
	};
}
function ym(e) {
	return function() {
		this.removeAttributeNS(e.space, e.local);
	};
}
function bm(e, t, n) {
	var r, i = n + "", a;
	return function() {
		var o = this.getAttribute(e);
		return o === i ? null : o === r ? a : a = t(r = o, n);
	};
}
function xm(e, t, n) {
	var r, i = n + "", a;
	return function() {
		var o = this.getAttributeNS(e.space, e.local);
		return o === i ? null : o === r ? a : a = t(r = o, n);
	};
}
function Sm(e, t, n) {
	var r, i, a;
	return function() {
		var o, s = n(this), c;
		return s == null ? void this.removeAttribute(e) : (o = this.getAttribute(e), c = s + "", o === c ? null : o === r && c === i ? a : (i = c, a = t(r = o, s)));
	};
}
function Cm(e, t, n) {
	var r, i, a;
	return function() {
		var o, s = n(this), c;
		return s == null ? void this.removeAttributeNS(e.space, e.local) : (o = this.getAttributeNS(e.space, e.local), c = s + "", o === c ? null : o === r && c === i ? a : (i = c, a = t(r = o, s)));
	};
}
function wm(e, t) {
	var n = mu(e), r = n === "transform" ? wp : _m;
	return this.attrTween(e, typeof t == "function" ? (n.local ? Cm : Sm)(n, r, gm(this, "attr." + e, t)) : t == null ? (n.local ? ym : vm)(n) : (n.local ? xm : bm)(n, r, t));
}
function Tm(e, t) {
	return function(n) {
		this.setAttribute(e, t.call(this, n));
	};
}
function Em(e, t) {
	return function(n) {
		this.setAttributeNS(e.space, e.local, t.call(this, n));
	};
}
function Dm(e, t) {
	var n, r;
	function i() {
		var i = t.apply(this, arguments);
		return i !== r && (n = (r = i) && Em(e, i)), n;
	}
	return i._value = t, i;
}
function Om(e, t) {
	var n, r;
	function i() {
		var i = t.apply(this, arguments);
		return i !== r && (n = (r = i) && Tm(e, i)), n;
	}
	return i._value = t, i;
}
function km(e, t) {
	var n = "attr." + e;
	if (arguments.length < 2) return (n = this.tween(n)) && n._value;
	if (t == null) return this.tween(n, null);
	if (typeof t != "function") throw Error();
	var r = mu(e);
	return this.tween(n, (r.local ? Dm : Om)(r, t));
}
function Am(e, t) {
	return function() {
		sm(this, e).delay = +t.apply(this, arguments);
	};
}
function jm(e, t) {
	return t = +t, function() {
		sm(this, e).delay = t;
	};
}
function Mm(e) {
	var t = this._id;
	return arguments.length ? this.each((typeof e == "function" ? Am : jm)(t, e)) : lm(this.node(), t).delay;
}
function Nm(e, t) {
	return function() {
		cm(this, e).duration = +t.apply(this, arguments);
	};
}
function Pm(e, t) {
	return t = +t, function() {
		cm(this, e).duration = t;
	};
}
function Fm(e) {
	var t = this._id;
	return arguments.length ? this.each((typeof e == "function" ? Nm : Pm)(t, e)) : lm(this.node(), t).duration;
}
function Im(e, t) {
	if (typeof t != "function") throw Error();
	return function() {
		cm(this, e).ease = t;
	};
}
function Lm(e) {
	var t = this._id;
	return arguments.length ? this.each(Im(t, e)) : lm(this.node(), t).ease;
}
function Rm(e, t) {
	return function() {
		var n = t.apply(this, arguments);
		if (typeof n != "function") throw Error();
		cm(this, e).ease = n;
	};
}
function zm(e) {
	if (typeof e != "function") throw Error();
	return this.each(Rm(this._id, e));
}
function Bm(e) {
	typeof e != "function" && (e = Eu(e));
	for (var t = this._groups, n = t.length, r = Array(n), i = 0; i < n; ++i) for (var a = t[i], o = a.length, s = r[i] = [], c, l = 0; l < o; ++l) (c = a[l]) && e.call(c, c.__data__, l, a) && s.push(c);
	return new hh(r, this._parents, this._name, this._id);
}
function Vm(e) {
	if (e._id !== this._id) throw Error();
	for (var t = this._groups, n = e._groups, r = t.length, i = n.length, a = Math.min(r, i), o = Array(r), s = 0; s < a; ++s) for (var c = t[s], l = n[s], u = c.length, d = o[s] = Array(u), f, p = 0; p < u; ++p) (f = c[p] || l[p]) && (d[p] = f);
	for (; s < r; ++s) o[s] = t[s];
	return new hh(o, this._parents, this._name, this._id);
}
function Hm(e) {
	return (e + "").trim().split(/^|\s+/).every(function(e) {
		var t = e.indexOf(".");
		return t >= 0 && (e = e.slice(0, t)), !e || e === "start";
	});
}
function Um(e, t, n) {
	var r, i, a = Hm(t) ? sm : cm;
	return function() {
		var o = a(this, e), s = o.on;
		s !== r && (i = (r = s).copy()).on(t, n), o.on = i;
	};
}
function Wm(e, t) {
	var n = this._id;
	return arguments.length < 2 ? lm(this.node(), n).on.on(e) : this.each(Um(n, e, t));
}
function Gm(e) {
	return function() {
		var t = this.parentNode;
		for (var n in this.__transition) if (+n !== e) return;
		t && t.removeChild(this);
	};
}
function Km() {
	return this.on("end.remove", Gm(this._id));
}
function qm(e) {
	var t = this._name, n = this._id;
	typeof e != "function" && (e = yu(e));
	for (var r = this._groups, i = r.length, a = Array(i), o = 0; o < i; ++o) for (var s = r[o], c = s.length, l = a[o] = Array(c), u, d, f = 0; f < c; ++f) (u = s[f]) && (d = e.call(u, u.__data__, f, s)) && ("__data__" in u && (d.__data__ = u.__data__), l[f] = d, om(l[f], t, n, f, l, lm(u, n)));
	return new hh(a, this._parents, t, n);
}
function Jm(e) {
	var t = this._name, n = this._id;
	typeof e != "function" && (e = Cu(e));
	for (var r = this._groups, i = r.length, a = [], o = [], s = 0; s < i; ++s) for (var c = r[s], l = c.length, u, d = 0; d < l; ++d) if (u = c[d]) {
		for (var f = e.call(u, u.__data__, d, c), p, m = lm(u, n), h = 0, g = f.length; h < g; ++h) (p = f[h]) && om(p, t, n, h, f, m);
		a.push(f), o.push(u);
	}
	return new hh(a, o, t, n);
}
var Ym = uf.prototype.constructor;
function Xm() {
	return new Ym(this._groups, this._parents);
}
function Zm(e, t) {
	var n, r, i;
	return function() {
		var a = gd(this, e), o = (this.style.removeProperty(e), gd(this, e));
		return a === o ? null : a === n && o === r ? i : i = t(n = a, r = o);
	};
}
function Qm(e) {
	return function() {
		this.style.removeProperty(e);
	};
}
function $m(e, t, n) {
	var r, i = n + "", a;
	return function() {
		var o = gd(this, e);
		return o === i ? null : o === r ? a : a = t(r = o, n);
	};
}
function eh(e, t, n) {
	var r, i, a;
	return function() {
		var o = gd(this, e), s = n(this), c = s + "";
		return s ?? (c = s = (this.style.removeProperty(e), gd(this, e))), o === c ? null : o === r && c === i ? a : (i = c, a = t(r = o, s));
	};
}
function th(e, t) {
	var n, r, i, a = "style." + t, o = "end." + a, s;
	return function() {
		var c = cm(this, e), l = c.on, u = c.value[a] == null ? s || (s = Qm(t)) : void 0;
		(l !== n || i !== u) && (r = (n = l).copy()).on(o, i = u), c.on = r;
	};
}
function nh(e, t, n) {
	var r = (e += "") == "transform" ? Cp : _m;
	return t == null ? this.styleTween(e, Zm(e, r)).on("end.style." + e, Qm(e)) : typeof t == "function" ? this.styleTween(e, eh(e, r, gm(this, "style." + e, t))).each(th(this._id, e)) : this.styleTween(e, $m(e, r, t), n).on("end.style." + e, null);
}
function rh(e, t, n) {
	return function(r) {
		this.style.setProperty(e, t.call(this, r), n);
	};
}
function ih(e, t, n) {
	var r, i;
	function a() {
		var a = t.apply(this, arguments);
		return a !== i && (r = (i = a) && rh(e, a, n)), r;
	}
	return a._value = t, a;
}
function ah(e, t, n) {
	var r = "style." + (e += "");
	if (arguments.length < 2) return (r = this.tween(r)) && r._value;
	if (t == null) return this.tween(r, null);
	if (typeof t != "function") throw Error();
	return this.tween(r, ih(e, t, n ?? ""));
}
function oh(e) {
	return function() {
		this.textContent = e;
	};
}
function sh(e) {
	return function() {
		var t = e(this);
		this.textContent = t ?? "";
	};
}
function ch(e) {
	return this.tween("text", typeof e == "function" ? sh(gm(this, "text", e)) : oh(e == null ? "" : e + ""));
}
function lh(e) {
	return function(t) {
		this.textContent = e.call(this, t);
	};
}
function uh(e) {
	var t, n;
	function r() {
		var r = e.apply(this, arguments);
		return r !== n && (t = (n = r) && lh(r)), t;
	}
	return r._value = e, r;
}
function dh(e) {
	var t = "text";
	if (arguments.length < 1) return (t = this.tween(t)) && t._value;
	if (e == null) return this.tween(t, null);
	if (typeof e != "function") throw Error();
	return this.tween(t, uh(e));
}
function fh() {
	for (var e = this._name, t = this._id, n = gh(), r = this._groups, i = r.length, a = 0; a < i; ++a) for (var o = r[a], s = o.length, c, l = 0; l < s; ++l) if (c = o[l]) {
		var u = lm(c, t);
		om(c, e, n, l, o, {
			time: u.time + u.delay + u.duration,
			delay: 0,
			duration: u.duration,
			ease: u.ease
		});
	}
	return new hh(r, this._parents, e, n);
}
function ph() {
	var e, t, n = this, r = n._id, i = n.size();
	return new Promise(function(a, o) {
		var s = { value: o }, c = { value: function() {
			--i === 0 && a();
		} };
		n.each(function() {
			var n = cm(this, r), i = n.on;
			i !== e && (t = (e = i).copy(), t._.cancel.push(s), t._.interrupt.push(s), t._.end.push(c)), n.on = t;
		}), i === 0 && a();
	});
}
var mh = 0;
function hh(e, t, n, r) {
	this._groups = e, this._parents = t, this._name = n, this._id = r;
}
function gh() {
	return ++mh;
}
var _h = uf.prototype;
hh.prototype = {
	constructor: hh,
	select: qm,
	selectAll: Jm,
	selectChild: _h.selectChild,
	selectChildren: _h.selectChildren,
	filter: Bm,
	merge: Vm,
	selection: Xm,
	transition: fh,
	call: _h.call,
	nodes: _h.nodes,
	node: _h.node,
	size: _h.size,
	empty: _h.empty,
	each: _h.each,
	on: Wm,
	attr: wm,
	attrTween: km,
	style: nh,
	styleTween: ah,
	text: ch,
	textTween: dh,
	remove: Km,
	tween: hm,
	delay: Mm,
	duration: Fm,
	ease: Lm,
	easeVarying: zm,
	end: ph,
	[Symbol.iterator]: _h[Symbol.iterator]
};
function vh(e) {
	return ((e *= 2) <= 1 ? e * e * e : (e -= 2) * e * e + 2) / 2;
}
var yh = {
	time: null,
	delay: 0,
	duration: 250,
	ease: vh
};
function bh(e, t) {
	for (var n; !(n = e.__transition) || !(n = n[t]);) if (!(e = e.parentNode)) throw Error(`transition ${t} not found`);
	return n;
}
function xh(e) {
	var t, n;
	e instanceof hh ? (t = e._id, e = e._name) : (t = gh(), (n = yh).time = Vp(), e = e == null ? null : e + "");
	for (var r = this._groups, i = r.length, a = 0; a < i; ++a) for (var o = r[a], s = o.length, c, l = 0; l < s; ++l) (c = o[l]) && om(c, e, t, l, o, n || bh(c, t));
	return new hh(r, this._parents, e, t);
}
uf.prototype.interrupt = fm, uf.prototype.transition = xh;
var Sh = (e) => () => e;
function Ch(e, { sourceEvent: t, target: n, transform: r, dispatch: i }) {
	Object.defineProperties(this, {
		type: {
			value: e,
			enumerable: !0,
			configurable: !0
		},
		sourceEvent: {
			value: t,
			enumerable: !0,
			configurable: !0
		},
		target: {
			value: n,
			enumerable: !0,
			configurable: !0
		},
		transform: {
			value: r,
			enumerable: !0,
			configurable: !0
		},
		_: { value: i }
	});
}
function wh(e, t, n) {
	this.k = e, this.x = t, this.y = n;
}
wh.prototype = {
	constructor: wh,
	scale: function(e) {
		return e === 1 ? this : new wh(this.k * e, this.x, this.y);
	},
	translate: function(e, t) {
		return e === 0 & t === 0 ? this : new wh(this.k, this.x + this.k * e, this.y + this.k * t);
	},
	apply: function(e) {
		return [e[0] * this.k + this.x, e[1] * this.k + this.y];
	},
	applyX: function(e) {
		return e * this.k + this.x;
	},
	applyY: function(e) {
		return e * this.k + this.y;
	},
	invert: function(e) {
		return [(e[0] - this.x) / this.k, (e[1] - this.y) / this.k];
	},
	invertX: function(e) {
		return (e - this.x) / this.k;
	},
	invertY: function(e) {
		return (e - this.y) / this.k;
	},
	rescaleX: function(e) {
		return e.copy().domain(e.range().map(this.invertX, this).map(e.invert, e));
	},
	rescaleY: function(e) {
		return e.copy().domain(e.range().map(this.invertY, this).map(e.invert, e));
	},
	toString: function() {
		return "translate(" + this.x + "," + this.y + ") scale(" + this.k + ")";
	}
};
var Th = new wh(1, 0, 0);
wh.prototype;
function Eh(e) {
	e.stopImmediatePropagation();
}
function Dh(e) {
	e.preventDefault(), e.stopImmediatePropagation();
}
function Oh(e) {
	return (!e.ctrlKey || e.type === "wheel") && !e.button;
}
function kh() {
	var e = this;
	return e instanceof SVGElement ? (e = e.ownerSVGElement || e, e.hasAttribute("viewBox") ? (e = e.viewBox.baseVal, [[e.x, e.y], [e.x + e.width, e.y + e.height]]) : [[0, 0], [e.width.baseVal.value, e.height.baseVal.value]]) : [[0, 0], [e.clientWidth, e.clientHeight]];
}
function Ah() {
	return this.__zoom || Th;
}
function jh(e) {
	return -e.deltaY * (e.deltaMode === 1 ? .05 : e.deltaMode ? 1 : .002) * (e.ctrlKey ? 10 : 1);
}
function Mh() {
	return navigator.maxTouchPoints || "ontouchstart" in this;
}
function Nh(e, t, n) {
	var r = e.invertX(t[0][0]) - n[0][0], i = e.invertX(t[1][0]) - n[1][0], a = e.invertY(t[0][1]) - n[0][1], o = e.invertY(t[1][1]) - n[1][1];
	return e.translate(i > r ? (r + i) / 2 : Math.min(0, r) || Math.max(0, i), o > a ? (a + o) / 2 : Math.min(0, a) || Math.max(0, o));
}
function Ph() {
	var e = Oh, t = kh, n = Nh, r = jh, i = Mh, a = [0, Infinity], o = [[-Infinity, -Infinity], [Infinity, Infinity]], s = 250, c = kp, l = su("start", "zoom", "end"), u, d, f, p = 500, m = 150, h = 0, g = 10;
	function _(e) {
		e.property("__zoom", Ah).on("wheel.zoom", w, { passive: !1 }).on("mousedown.zoom", T).on("dblclick.zoom", E).filter(i).on("touchstart.zoom", D).on("touchmove.zoom", O).on("touchend.zoom touchcancel.zoom", k).style("-webkit-tap-highlight-color", "rgba(0,0,0,0)");
	}
	_.transform = function(e, t, n, r) {
		var i = e.selection ? e.selection() : e;
		i.property("__zoom", Ah), e === i ? i.interrupt().each(function() {
			S(this, arguments).event(r).start().zoom(null, typeof t == "function" ? t.apply(this, arguments) : t).end();
		}) : x(e, t, n, r);
	}, _.scaleBy = function(e, t, n, r) {
		_.scaleTo(e, function() {
			return this.__zoom.k * (typeof t == "function" ? t.apply(this, arguments) : t);
		}, n, r);
	}, _.scaleTo = function(e, r, i, a) {
		_.transform(e, function() {
			var e = t.apply(this, arguments), a = this.__zoom, s = i == null ? b(e) : typeof i == "function" ? i.apply(this, arguments) : i, c = a.invert(s), l = typeof r == "function" ? r.apply(this, arguments) : r;
			return n(y(v(a, l), s, c), e, o);
		}, i, a);
	}, _.translateBy = function(e, r, i, a) {
		_.transform(e, function() {
			return n(this.__zoom.translate(typeof r == "function" ? r.apply(this, arguments) : r, typeof i == "function" ? i.apply(this, arguments) : i), t.apply(this, arguments), o);
		}, null, a);
	}, _.translateTo = function(e, r, i, a, s) {
		_.transform(e, function() {
			var e = t.apply(this, arguments), s = this.__zoom, c = a == null ? b(e) : typeof a == "function" ? a.apply(this, arguments) : a;
			return n(Th.translate(c[0], c[1]).scale(s.k).translate(typeof r == "function" ? -r.apply(this, arguments) : -r, typeof i == "function" ? -i.apply(this, arguments) : -i), e, o);
		}, a, s);
	};
	function v(e, t) {
		return t = Math.max(a[0], Math.min(a[1], t)), t === e.k ? e : new wh(t, e.x, e.y);
	}
	function y(e, t, n) {
		var r = t[0] - n[0] * e.k, i = t[1] - n[1] * e.k;
		return r === e.x && i === e.y ? e : new wh(e.k, r, i);
	}
	function b(e) {
		return [(+e[0][0] + +e[1][0]) / 2, (+e[0][1] + +e[1][1]) / 2];
	}
	function x(e, n, r, i) {
		e.on("start.zoom", function() {
			S(this, arguments).event(i).start();
		}).on("interrupt.zoom end.zoom", function() {
			S(this, arguments).event(i).end();
		}).tween("zoom", function() {
			var e = this, a = arguments, o = S(e, a).event(i), s = t.apply(e, a), l = r == null ? b(s) : typeof r == "function" ? r.apply(e, a) : r, u = Math.max(s[1][0] - s[0][0], s[1][1] - s[0][1]), d = e.__zoom, f = typeof n == "function" ? n.apply(e, a) : n, p = c(d.invert(l).concat(u / d.k), f.invert(l).concat(u / f.k));
			return function(e) {
				if (e === 1) e = f;
				else {
					var t = p(e), n = u / t[2];
					e = new wh(n, l[0] - t[0] * n, l[1] - t[1] * n);
				}
				o.zoom(null, e);
			};
		});
	}
	function S(e, t, n) {
		return !n && e.__zooming || new C(e, t);
	}
	function C(e, n) {
		this.that = e, this.args = n, this.active = 0, this.sourceEvent = null, this.extent = t.apply(e, n), this.taps = 0;
	}
	C.prototype = {
		event: function(e) {
			return e && (this.sourceEvent = e), this;
		},
		start: function() {
			return ++this.active === 1 && (this.that.__zooming = this, this.emit("start")), this;
		},
		zoom: function(e, t) {
			return this.mouse && e !== "mouse" && (this.mouse[1] = t.invert(this.mouse[0])), this.touch0 && e !== "touch" && (this.touch0[1] = t.invert(this.touch0[0])), this.touch1 && e !== "touch" && (this.touch1[1] = t.invert(this.touch1[0])), this.that.__zoom = t, this.emit("zoom"), this;
		},
		end: function() {
			return --this.active === 0 && (delete this.that.__zooming, this.emit("end")), this;
		},
		emit: function(e) {
			var t = ff(this.that).datum();
			l.call(e, this.that, new Ch(e, {
				sourceEvent: this.sourceEvent,
				target: _,
				type: e,
				transform: this.that.__zoom,
				dispatch: l
			}), t);
		}
	};
	function w(t, ...i) {
		if (!e.apply(this, arguments)) return;
		var s = S(this, i).event(t), c = this.__zoom, l = Math.max(a[0], Math.min(a[1], c.k * 2 ** r.apply(this, arguments))), u = mf(t);
		if (s.wheel) (s.mouse[0][0] !== u[0] || s.mouse[0][1] !== u[1]) && (s.mouse[1] = c.invert(s.mouse[0] = u)), clearTimeout(s.wheel);
		else if (c.k === l) return;
		else s.mouse = [u, c.invert(u)], dm(this), s.start();
		Dh(t), s.wheel = setTimeout(d, m), s.zoom("mouse", n(y(v(c, l), s.mouse[0], s.mouse[1]), s.extent, o));
		function d() {
			s.wheel = null, s.end();
		}
	}
	function T(t, ...r) {
		if (f || !e.apply(this, arguments)) return;
		var i = t.currentTarget, a = S(this, r, !0).event(t), s = ff(t.view).on("mousemove.zoom", d, !0).on("mouseup.zoom", p, !0), c = mf(t, i), l = t.clientX, u = t.clientY;
		_f(t.view), Eh(t), a.mouse = [c, this.__zoom.invert(c)], dm(this), a.start();
		function d(e) {
			if (Dh(e), !a.moved) {
				var t = e.clientX - l, r = e.clientY - u;
				a.moved = t * t + r * r > h;
			}
			a.event(e).zoom("mouse", n(y(a.that.__zoom, a.mouse[0] = mf(e, i), a.mouse[1]), a.extent, o));
		}
		function p(e) {
			s.on("mousemove.zoom mouseup.zoom", null), vf(e.view, a.moved), Dh(e), a.event(e).end();
		}
	}
	function E(r, ...i) {
		if (e.apply(this, arguments)) {
			var a = this.__zoom, c = mf(r.changedTouches ? r.changedTouches[0] : r, this), l = a.invert(c), u = a.k * (r.shiftKey ? .5 : 2), d = n(y(v(a, u), c, l), t.apply(this, i), o);
			Dh(r), s > 0 ? ff(this).transition().duration(s).call(x, d, c, r) : ff(this).call(_.transform, d, c, r);
		}
	}
	function D(t, ...n) {
		if (e.apply(this, arguments)) {
			var r = t.touches, i = r.length, a = S(this, n, t.changedTouches.length === i).event(t), o, s, c, l;
			for (Eh(t), s = 0; s < i; ++s) c = r[s], l = mf(c, this), l = [
				l,
				this.__zoom.invert(l),
				c.identifier
			], a.touch0 ? !a.touch1 && a.touch0[2] !== l[2] && (a.touch1 = l, a.taps = 0) : (a.touch0 = l, o = !0, a.taps = 1 + !!u);
			u && (u = clearTimeout(u)), o && (a.taps < 2 && (d = l[0], u = setTimeout(function() {
				u = null;
			}, p)), dm(this), a.start());
		}
	}
	function O(e, ...t) {
		if (this.__zooming) {
			var r = S(this, t).event(e), i = e.changedTouches, a = i.length, s, c, l, u;
			for (Dh(e), s = 0; s < a; ++s) c = i[s], l = mf(c, this), r.touch0 && r.touch0[2] === c.identifier ? r.touch0[0] = l : r.touch1 && r.touch1[2] === c.identifier && (r.touch1[0] = l);
			if (c = r.that.__zoom, r.touch1) {
				var d = r.touch0[0], f = r.touch0[1], p = r.touch1[0], m = r.touch1[1], h = (h = p[0] - d[0]) * h + (h = p[1] - d[1]) * h, g = (g = m[0] - f[0]) * g + (g = m[1] - f[1]) * g;
				c = v(c, Math.sqrt(h / g)), l = [(d[0] + p[0]) / 2, (d[1] + p[1]) / 2], u = [(f[0] + m[0]) / 2, (f[1] + m[1]) / 2];
			} else if (r.touch0) l = r.touch0[0], u = r.touch0[1];
			else return;
			r.zoom("touch", n(y(c, l, u), r.extent, o));
		}
	}
	function k(e, ...t) {
		if (this.__zooming) {
			var n = S(this, t).event(e), r = e.changedTouches, i = r.length, a, o;
			for (Eh(e), f && clearTimeout(f), f = setTimeout(function() {
				f = null;
			}, p), a = 0; a < i; ++a) o = r[a], n.touch0 && n.touch0[2] === o.identifier ? delete n.touch0 : n.touch1 && n.touch1[2] === o.identifier && delete n.touch1;
			if (n.touch1 && !n.touch0 && (n.touch0 = n.touch1, delete n.touch1), n.touch0) n.touch0[1] = this.__zoom.invert(n.touch0[0]);
			else if (n.end(), n.taps === 2 && (o = mf(o, this), Math.hypot(d[0] - o[0], d[1] - o[1]) < g)) {
				var s = ff(this).on("dblclick.zoom");
				s && s.apply(this, arguments);
			}
		}
	}
	return _.wheelDelta = function(e) {
		return arguments.length ? (r = typeof e == "function" ? e : Sh(+e), _) : r;
	}, _.filter = function(t) {
		return arguments.length ? (e = typeof t == "function" ? t : Sh(!!t), _) : e;
	}, _.touchable = function(e) {
		return arguments.length ? (i = typeof e == "function" ? e : Sh(!!e), _) : i;
	}, _.extent = function(e) {
		return arguments.length ? (t = typeof e == "function" ? e : Sh([[+e[0][0], +e[0][1]], [+e[1][0], +e[1][1]]]), _) : t;
	}, _.scaleExtent = function(e) {
		return arguments.length ? (a[0] = +e[0], a[1] = +e[1], _) : [a[0], a[1]];
	}, _.translateExtent = function(e) {
		return arguments.length ? (o[0][0] = +e[0][0], o[1][0] = +e[1][0], o[0][1] = +e[0][1], o[1][1] = +e[1][1], _) : [[o[0][0], o[0][1]], [o[1][0], o[1][1]]];
	}, _.constrain = function(e) {
		return arguments.length ? (n = e, _) : n;
	}, _.duration = function(e) {
		return arguments.length ? (s = +e, _) : s;
	}, _.interpolate = function(e) {
		return arguments.length ? (c = e, _) : c;
	}, _.on = function() {
		var e = l.on.apply(l, arguments);
		return e === l ? _ : e;
	}, _.clickDistance = function(e) {
		return arguments.length ? (h = (e = +e) * e, _) : Math.sqrt(h);
	}, _.tapDistance = function(e) {
		return arguments.length ? (g = +e, _) : g;
	}, _;
}
var Fh = Symbol("MiniMapSlots"), Ih = [
	"id",
	"x",
	"y",
	"rx",
	"ry",
	"width",
	"height",
	"fill",
	"stroke",
	"stroke-width",
	"shape-rendering"
], Lh = /* @__PURE__ */ d({
	name: "MiniMapNode",
	compatConfig: { MODE: 3 },
	inheritAttrs: !1,
	props: {
		id: {},
		type: {},
		selected: { type: Boolean },
		dragging: { type: Boolean },
		position: {},
		dimensions: {},
		borderRadius: {},
		color: {},
		shapeRendering: {},
		strokeColor: {},
		strokeWidth: {},
		hidden: { type: Boolean }
	},
	emits: [
		"click",
		"dblclick",
		"mouseenter",
		"mousemove",
		"mouseleave"
	],
	setup(e, { emit: n }) {
		let o = e, s = g(Fh), c = W(), l = B(() => c.style ?? {});
		function u(e) {
			n("click", e);
		}
		function d(e) {
			n("dblclick", e);
		}
		function f(e) {
			n("mouseenter", e);
		}
		function p(e) {
			n("mousemove", e);
		}
		function m(e) {
			n("mouseleave", e);
		}
		return (e, n) => !e.hidden && e.dimensions.width !== 0 && e.dimensions.height !== 0 ? (A(), a(t, { key: 0 }, [U(s)[`node-${o.type}`] ? (A(), r(R(U(s)[`node-${o.type}`]), C(b({ key: 0 }, {
			...o,
			...e.$attrs
		})), null, 16)) : (A(), a("rect", b({
			key: 1,
			id: e.id
		}, e.$attrs, {
			class: ["vue-flow__minimap-node", {
				selected: e.selected,
				dragging: e.dragging
			}],
			x: e.position.x,
			y: e.position.y,
			rx: e.borderRadius,
			ry: e.borderRadius,
			width: e.dimensions.width,
			height: e.dimensions.height,
			fill: e.color || l.value.background || l.value.backgroundColor,
			stroke: e.strokeColor,
			"stroke-width": e.strokeWidth,
			"shape-rendering": e.shapeRendering,
			onClick: u,
			onDblclick: d,
			onMouseenter: f,
			onMousemove: p,
			onMouseleave: m
		}), null, 16, Ih))], 64)) : i("", !0);
	}
}), Rh = [
	"width",
	"height",
	"viewBox",
	"aria-labelledby"
], zh = ["id"], Bh = [
	"d",
	"fill",
	"stroke",
	"stroke-width"
], Vh = /* @__PURE__ */ d({
	name: "MiniMap",
	compatConfig: { MODE: 3 },
	props: {
		nodeColor: {
			type: [String, Function],
			default: "#e2e2e2"
		},
		nodeStrokeColor: {
			type: [String, Function],
			default: "transparent"
		},
		nodeClassName: { type: [String, Function] },
		nodeBorderRadius: { default: 5 },
		nodeStrokeWidth: { default: 2 },
		maskColor: { default: "rgb(240, 240, 240, 0.6)" },
		maskStrokeColor: { default: "none" },
		maskStrokeWidth: { default: 1 },
		position: { default: "bottom-right" },
		pannable: {
			type: Boolean,
			default: !1
		},
		zoomable: {
			type: Boolean,
			default: !1
		},
		width: {},
		height: {},
		ariaLabel: { default: "Vue Flow mini map" },
		inversePan: {
			type: Boolean,
			default: !1
		},
		zoomStep: { default: 1 },
		offsetScale: { default: 5 },
		maskBorderRadius: { default: 0 }
	},
	emits: [
		"click",
		"nodeClick",
		"nodeDblclick",
		"nodeMouseenter",
		"nodeMousemove",
		"nodeMouseleave"
	],
	setup(e, { emit: s }) {
		let c = G(), l = W(), { id: u, edges: d, viewport: f, translateExtent: p, dimensions: m, emits: h, d3Selection: g, d3Zoom: _, getNodesInitialized: v } = $(), y = P();
		j(Fh, c);
		let b = B(() => e.width ?? l.style?.width ?? 200), x = B(() => e.height ?? l.style?.height ?? 150), C = typeof window > "u" || window.chrome ? "crispEdges" : "geometricPrecision", T = n(() => typeof e.nodeColor == "string" ? () => e.nodeColor : e.nodeColor), E = n(() => typeof e.nodeStrokeColor == "string" ? () => e.nodeStrokeColor : e.nodeStrokeColor), D = n(() => typeof e.nodeClassName == "string" ? () => e.nodeClassName : typeof e.nodeClassName == "function" ? e.nodeClassName : () => ""), O = n(() => as(v.value.filter((e) => !e.hidden))), k = n(() => ({
			x: -f.value.x / f.value.zoom,
			y: -f.value.y / f.value.zoom,
			width: m.value.width / f.value.zoom,
			height: m.value.height / f.value.zoom
		})), M = n(() => v.value && v.value.length ? is(O.value, k.value) : k.value), N = n(() => {
			let e = M.value.width / b.value, t = M.value.height / x.value;
			return Math.max(e, t);
		}), I = n(() => {
			let t = N.value * b.value, n = N.value * x.value, r = e.offsetScale * N.value;
			return {
				offset: r,
				x: M.value.x - (t - M.value.width) / 2 - r,
				y: M.value.y - (n - M.value.height) / 2 - r,
				width: t + r * 2,
				height: n + r * 2
			};
		}), L = n(() => !I.value.x || !I.value.y ? "" : `
    M${I.value.x - I.value.offset},${I.value.y - I.value.offset}
    h${I.value.width + I.value.offset * 2}
    v${I.value.height + I.value.offset * 2}
    h${-I.value.width - I.value.offset * 2}z
    M${k.value.x + e.maskBorderRadius},${k.value.y}
    h${k.value.width - 2 * e.maskBorderRadius}
    a${e.maskBorderRadius},${e.maskBorderRadius} 0 0 1 ${e.maskBorderRadius},${e.maskBorderRadius}
    v${k.value.height - 2 * e.maskBorderRadius}
    a${e.maskBorderRadius},${e.maskBorderRadius} 0 0 1 -${e.maskBorderRadius},${e.maskBorderRadius}
    h${-(k.value.width - 2 * e.maskBorderRadius)}
    a${e.maskBorderRadius},${e.maskBorderRadius} 0 0 1 -${e.maskBorderRadius},-${e.maskBorderRadius}
    v${-(k.value.height - 2 * e.maskBorderRadius)}
    a${e.maskBorderRadius},${e.maskBorderRadius} 0 0 1 ${e.maskBorderRadius},-${e.maskBorderRadius}z`);
		te((t) => {
			if (y.value) {
				let n = ff(y.value), r = Ph().wheelDelta((t) => hs(t) * (e.zoomStep / 10)).on("zoom", e.pannable ? (t) => {
					if (t.sourceEvent.type !== "mousemove" || !g.value || !_.value) return;
					let n = N.value * Math.max(1, f.value.zoom) * (e.inversePan ? -1 : 1), r = {
						x: f.value.x - t.sourceEvent.movementX * n,
						y: f.value.y - t.sourceEvent.movementY * n
					}, i = [[0, 0], [m.value.width, m.value.height]], a = Th.translate(r.x, r.y).scale(f.value.zoom), o = _.value.constrain()(a, i, p.value);
					_.value.transform(g.value, o);
				} : () => {}).on("zoom.wheel", e.zoomable ? (t) => {
					if (t.sourceEvent.type !== "wheel" || !g.value || !_.value) return;
					let n = t.sourceEvent.ctrlKey && Hs() ? 10 : 1, r = -t.sourceEvent.deltaY * (t.sourceEvent.deltaMode === 1 ? .05 : t.sourceEvent.deltaMode ? 1 : .002) * e.zoomStep, i = f.value.zoom * 2 ** (r * n);
					_.value.scaleTo(g.value, i);
				} : () => {});
				n.call(r), t(() => {
					n.on("zoom", null);
				});
			}
		}, { flush: "post" });
		function R(e) {
			let [t, n] = mf(e);
			s("click", {
				event: e,
				position: {
					x: t,
					y: n
				}
			});
		}
		function z(e, t) {
			let n = {
				event: e,
				node: t,
				connectedEdges: ss([t], d.value)
			};
			h.miniMapNodeClick(n), s("nodeClick", n);
		}
		function V(e, t) {
			let n = {
				event: e,
				node: t,
				connectedEdges: ss([t], d.value)
			};
			h.miniMapNodeDoubleClick(n), s("nodeDblclick", n);
		}
		function H(e, t) {
			let n = {
				event: e,
				node: t,
				connectedEdges: ss([t], d.value)
			};
			h.miniMapNodeMouseEnter(n), s("nodeMouseenter", n);
		}
		function K(e, t) {
			let n = {
				event: e,
				node: t,
				connectedEdges: ss([t], d.value)
			};
			h.miniMapNodeMouseMove(n), s("nodeMousemove", n);
		}
		function ne(e, t) {
			let n = {
				event: e,
				node: t,
				connectedEdges: ss([t], d.value)
			};
			h.miniMapNodeMouseLeave(n), s("nodeMouseleave", n);
		}
		return (e, n) => (A(), r(U(au), {
			position: e.position,
			class: S(["vue-flow__minimap", {
				pannable: e.pannable,
				zoomable: e.zoomable
			}])
		}, {
			default: q(() => [(A(), a("svg", {
				ref_key: "el",
				ref: y,
				width: b.value,
				height: x.value,
				viewBox: [
					I.value.x,
					I.value.y,
					I.value.width,
					I.value.height
				].join(" "),
				role: "img",
				"aria-labelledby": `vue-flow__minimap-${U(u)}`,
				onClick: R
			}, [
				e.ariaLabel ? (A(), a("title", {
					key: 0,
					id: `vue-flow__minimap-${U(u)}`
				}, ee(e.ariaLabel), 9, zh)) : i("", !0),
				(A(!0), a(t, null, F(U(v), (t) => (A(), r(Lh, {
					id: t.id,
					key: t.id,
					f: "",
					position: t.computedPosition,
					dimensions: t.dimensions,
					selected: t.selected,
					dragging: t.dragging,
					style: w(t.style),
					class: S(D.value(t)),
					color: T.value(t),
					"border-radius": e.nodeBorderRadius,
					"stroke-color": E.value(t),
					"stroke-width": e.nodeStrokeWidth,
					"shape-rendering": U(C),
					type: t.type,
					hidden: t.hidden,
					onClick: (e) => z(e, t),
					onDblclick: (e) => V(e, t),
					onMouseenter: (e) => H(e, t),
					onMousemove: (e) => K(e, t),
					onMouseleave: (e) => ne(e, t)
				}, null, 8, [
					"id",
					"position",
					"dimensions",
					"selected",
					"dragging",
					"style",
					"class",
					"color",
					"border-radius",
					"stroke-color",
					"stroke-width",
					"shape-rendering",
					"type",
					"hidden",
					"onClick",
					"onDblclick",
					"onMouseenter",
					"onMousemove",
					"onMouseleave"
				]))), 128)),
				o("path", {
					class: "vue-flow__minimap-mask",
					d: L.value,
					fill: e.maskColor,
					stroke: e.maskStrokeColor,
					"stroke-width": e.maskStrokeWidth,
					"fill-rule": "evenodd"
				}, null, 8, Bh)
			], 8, Rh))]),
			_: 1
		}, 8, ["position", "class"]));
	}
}), Hh = {
	name: "ControlButton",
	compatConfig: { MODE: 3 }
}, Uh = (e, t) => {
	let n = e.__vccOpts || e;
	for (let [e, r] of t) n[e] = r;
	return n;
}, Wh = {
	type: "button",
	class: "vue-flow__controls-button"
};
function Gh(e, t, n, r, i, o) {
	return A(), a("button", Wh, [I(e.$slots, "default")]);
}
var Kh = /* @__PURE__ */ Uh(Hh, [["render", Gh]]), qh = {
	xmlns: "http://www.w3.org/2000/svg",
	viewBox: "0 0 32 32"
}, Jh = [/* @__PURE__ */ o("path", { d: "M32 18.133H18.133V32h-4.266V18.133H0v-4.266h13.867V0h4.266v13.867H32z" }, null, -1)];
function Yh(e, t) {
	return A(), a("svg", qh, Jh);
}
var Xh = { render: Yh }, Zh = {
	xmlns: "http://www.w3.org/2000/svg",
	viewBox: "0 0 32 5"
}, Qh = [/* @__PURE__ */ o("path", { d: "M0 0h32v4.2H0z" }, null, -1)];
function $h(e, t) {
	return A(), a("svg", Zh, Qh);
}
var eg = { render: $h }, tg = {
	xmlns: "http://www.w3.org/2000/svg",
	viewBox: "0 0 32 30"
}, ng = [/* @__PURE__ */ o("path", { d: "M3.692 4.63c0-.53.4-.938.939-.938h5.215V0H4.708C2.13 0 0 2.054 0 4.63v5.216h3.692V4.631zM27.354 0h-5.2v3.692h5.17c.53 0 .984.4.984.939v5.215H32V4.631A4.624 4.624 0 0 0 27.354 0zm.954 24.83c0 .532-.4.94-.939.94h-5.215v3.768h5.215c2.577 0 4.631-2.13 4.631-4.707v-5.139h-3.692v5.139zm-23.677.94a.919.919 0 0 1-.939-.94v-5.138H0v5.139c0 2.577 2.13 4.707 4.708 4.707h5.138V25.77H4.631z" }, null, -1)];
function rg(e, t) {
	return A(), a("svg", tg, ng);
}
var ig = { render: rg }, ag = {
	xmlns: "http://www.w3.org/2000/svg",
	viewBox: "0 0 25 32"
}, og = [/* @__PURE__ */ o("path", { d: "M21.333 10.667H19.81V7.619C19.81 3.429 16.38 0 12.19 0 8 0 4.571 3.429 4.571 7.619v3.048H3.048A3.056 3.056 0 0 0 0 13.714v15.238A3.056 3.056 0 0 0 3.048 32h18.285a3.056 3.056 0 0 0 3.048-3.048V13.714a3.056 3.056 0 0 0-3.048-3.047zM12.19 24.533a3.056 3.056 0 0 1-3.047-3.047 3.056 3.056 0 0 1 3.047-3.048 3.056 3.056 0 0 1 3.048 3.048 3.056 3.056 0 0 1-3.048 3.047zm4.724-13.866H7.467V7.619c0-2.59 2.133-4.724 4.723-4.724 2.591 0 4.724 2.133 4.724 4.724v3.048z" }, null, -1)];
function sg(e, t) {
	return A(), a("svg", ag, og);
}
var cg = { render: sg }, lg = {
	xmlns: "http://www.w3.org/2000/svg",
	viewBox: "0 0 25 32"
}, ug = [/* @__PURE__ */ o("path", { d: "M21.333 10.667H19.81V7.619C19.81 3.429 16.38 0 12.19 0c-4.114 1.828-1.37 2.133.305 2.438 1.676.305 4.42 2.59 4.42 5.181v3.048H3.047A3.056 3.056 0 0 0 0 13.714v15.238A3.056 3.056 0 0 0 3.048 32h18.285a3.056 3.056 0 0 0 3.048-3.048V13.714a3.056 3.056 0 0 0-3.048-3.047zM12.19 24.533a3.056 3.056 0 0 1-3.047-3.047 3.056 3.056 0 0 1 3.047-3.048 3.056 3.056 0 0 1 3.048 3.048 3.056 3.056 0 0 1-3.048 3.047z" }, null, -1)];
function dg(e, t) {
	return A(), a("svg", lg, ug);
}
var fg = { render: dg }, pg = /* @__PURE__ */ d({
	name: "Controls",
	compatConfig: { MODE: 3 },
	props: {
		showZoom: {
			type: Boolean,
			default: !0
		},
		showFitView: {
			type: Boolean,
			default: !0
		},
		showInteractive: {
			type: Boolean,
			default: !0
		},
		fitViewParams: {},
		position: { default: () => xo.BottomLeft }
	},
	emits: [
		"zoomIn",
		"zoomOut",
		"fitView",
		"interactionChange"
	],
	setup(e, { emit: n }) {
		let { nodesDraggable: o, nodesConnectable: s, elementsSelectable: c, setInteractive: u, zoomIn: d, zoomOut: f, fitView: p, viewport: m, minZoom: h, maxZoom: g } = $(), _ = B(() => o.value || s.value || c.value), v = B(() => m.value.zoom <= h.value), y = B(() => m.value.zoom >= g.value);
		function b() {
			d(), n("zoomIn");
		}
		function x() {
			f(), n("zoomOut");
		}
		function S() {
			p(e.fitViewParams), n("fitView");
		}
		function C() {
			u(!_.value), n("interactionChange", !_.value);
		}
		return (e, n) => (A(), r(U(au), {
			class: "vue-flow__controls",
			position: e.position
		}, {
			default: q(() => [
				I(e.$slots, "top"),
				e.showZoom ? (A(), a(t, { key: 0 }, [I(e.$slots, "control-zoom-in", {}, () => [l(Kh, {
					class: "vue-flow__controls-zoomin",
					disabled: y.value,
					onClick: b
				}, {
					default: q(() => [I(e.$slots, "icon-zoom-in", {}, () => [(A(), r(R(U(Xh))))])]),
					_: 3
				}, 8, ["disabled"])]), I(e.$slots, "control-zoom-out", {}, () => [l(Kh, {
					class: "vue-flow__controls-zoomout",
					disabled: v.value,
					onClick: x
				}, {
					default: q(() => [I(e.$slots, "icon-zoom-out", {}, () => [(A(), r(R(U(eg))))])]),
					_: 3
				}, 8, ["disabled"])])], 64)) : i("", !0),
				e.showFitView ? I(e.$slots, "control-fit-view", { key: 1 }, () => [l(Kh, {
					class: "vue-flow__controls-fitview",
					onClick: S
				}, {
					default: q(() => [I(e.$slots, "icon-fit-view", {}, () => [(A(), r(R(U(ig))))])]),
					_: 3
				})]) : i("", !0),
				e.showInteractive ? I(e.$slots, "control-interactive", { key: 2 }, () => [e.showInteractive ? (A(), r(Kh, {
					key: 0,
					class: "vue-flow__controls-interactive",
					onClick: C
				}, {
					default: q(() => [_.value ? I(e.$slots, "icon-unlock", { key: 0 }, () => [(A(), r(R(U(fg))))]) : i("", !0), _.value ? i("", !0) : I(e.$slots, "icon-lock", { key: 1 }, () => [(A(), r(R(U(cg))))])]),
					_: 3
				})) : i("", !0)]) : i("", !0),
				I(e.$slots, "default")
			]),
			_: 3
		}, 8, ["position"]));
	}
}), mg = /* @__PURE__ */ ((e) => (e.Lines = "lines", e.Dots = "dots", e))(mg || {}), hg = function({ dimensions: e, size: t, color: n }) {
	return h("path", {
		stroke: n,
		"stroke-width": t,
		d: `M${e[0] / 2} 0 V${e[1]} M0 ${e[1] / 2} H${e[0]}`
	});
}, gg = function({ radius: e, color: t }) {
	return h("circle", {
		cx: e,
		cy: e,
		r: e,
		fill: t
	});
};
mg.Lines, mg.Dots;
var _g = {
	[mg.Dots]: "#81818a",
	[mg.Lines]: "#eee"
}, vg = [
	"id",
	"x",
	"y",
	"width",
	"height",
	"patternTransform"
], yg = {
	key: 2,
	height: "100",
	width: "100"
}, bg = ["fill"], xg = [
	"x",
	"y",
	"fill"
], Sg = /* @__PURE__ */ d({
	name: "Background",
	compatConfig: { MODE: 3 },
	props: {
		id: {},
		variant: { default: () => mg.Dots },
		gap: { default: 20 },
		size: { default: 1 },
		lineWidth: { default: 1 },
		patternColor: {},
		color: {},
		bgColor: {},
		height: { default: 100 },
		width: { default: 100 },
		x: { default: 0 },
		y: { default: 0 },
		offset: { default: 0 }
	},
	setup(e) {
		let { id: t, viewport: s } = $(), c = n(() => {
			let t = s.value.zoom, [n, r] = Array.isArray(e.gap) ? e.gap : [e.gap, e.gap], i = [n * t || 1, r * t || 1], a = e.size * t, [o, c] = Array.isArray(e.offset) ? e.offset : [e.offset, e.offset];
			return {
				scaledGap: i,
				offset: [o * t || 1 + i[0] / 2, c * t || 1 + i[1] / 2],
				size: a
			};
		}), l = B(() => `pattern-${t}${e.id ? `-${e.id}` : ""}`), u = B(() => e.color || e.patternColor || _g[e.variant || mg.Dots]);
		return (e, t) => (A(), a("svg", {
			class: "vue-flow__background vue-flow__container",
			style: w({
				height: `${e.height > 100 ? 100 : e.height}%`,
				width: `${e.width > 100 ? 100 : e.width}%`
			})
		}, [
			I(e.$slots, "pattern-container", { id: l.value }, () => [o("pattern", {
				id: l.value,
				x: U(s).x % c.value.scaledGap[0],
				y: U(s).y % c.value.scaledGap[1],
				width: c.value.scaledGap[0],
				height: c.value.scaledGap[1],
				patternTransform: `translate(-${c.value.offset[0]},-${c.value.offset[1]})`,
				patternUnits: "userSpaceOnUse"
			}, [I(e.$slots, "pattern", {}, () => [e.variant === U(mg).Lines ? (A(), r(U(hg), {
				key: 0,
				size: e.lineWidth,
				color: u.value,
				dimensions: c.value.scaledGap
			}, null, 8, [
				"size",
				"color",
				"dimensions"
			])) : e.variant === U(mg).Dots ? (A(), r(U(gg), {
				key: 1,
				color: u.value,
				radius: c.value.size / 2
			}, null, 8, ["color", "radius"])) : i("", !0), e.bgColor ? (A(), a("svg", yg, [o("rect", {
				width: "100%",
				height: "100%",
				fill: e.bgColor
			}, null, 8, bg)])) : i("", !0)])], 8, vg)]),
			o("rect", {
				x: e.x,
				y: e.y,
				width: "100%",
				height: "100%",
				fill: `url(#${l.value})`
			}, null, 8, xg),
			I(e.$slots, "default", { id: l.value })
		], 4));
	}
}), Cg = ".vue-flow{z-index:0;direction:ltr;width:100%;height:100%;position:relative;overflow:hidden}.vue-flow__container{width:100%;height:100%;position:absolute;top:0;left:0}.vue-flow__pane{z-index:1}.vue-flow__pane.draggable{cursor:grab}.vue-flow__pane.selection{cursor:pointer}.vue-flow__pane.dragging{cursor:grabbing}.vue-flow__transformationpane{transform-origin:0 0;z-index:2;pointer-events:none}.vue-flow__viewport{z-index:4;overflow:clip}.vue-flow__selection{z-index:6}.vue-flow__edge-labels{pointer-events:none;-webkit-user-select:none;user-select:none;width:100%;height:100%;position:absolute}.vue-flow__nodesselection-rect:focus{outline:none}.vue-flow__nodesselection-rect:focus-visible{outline:none}.vue-flow .vue-flow__edges{pointer-events:none;overflow:visible}.vue-flow__edge-path,.vue-flow__connection-path{stroke:#b1b1b7;stroke-width:1px;fill:none}.vue-flow__edge{pointer-events:visibleStroke;cursor:pointer}.vue-flow__edge.animated path{stroke-dasharray:5;animation:.5s linear infinite dashdraw}.vue-flow__edge.animated path.vue-flow__edge-interaction{stroke-dasharray:none;animation:none}.vue-flow__edge.inactive{pointer-events:none}.vue-flow__edge.selected,.vue-flow__edge:focus{outline:none}.vue-flow__edge:focus-visible{outline:none}.vue-flow__edge.selected .vue-flow__edge-path,.vue-flow__edge:focus .vue-flow__edge-path{stroke:#555}.vue-flow__edge:focus-visible .vue-flow__edge-path{stroke:#555}.vue-flow__edge-textwrapper{pointer-events:all}.vue-flow__edge-textbg{fill:#fff}.vue-flow__edge-text{pointer-events:none;-webkit-user-select:none;user-select:none}.vue-flow__connection{pointer-events:none}.vue-flow__connection .animated{stroke-dasharray:5;animation:.5s linear infinite dashdraw}.vue-flow__connectionline{z-index:1001}.vue-flow__nodes{pointer-events:none;transform-origin:0 0}.vue-flow__node{-webkit-user-select:none;user-select:none;pointer-events:all;transform-origin:0 0;box-sizing:border-box;cursor:default;position:absolute}.vue-flow__node.draggable{cursor:grab;pointer-events:all}.vue-flow__node.draggable.dragging{cursor:grabbing}.vue-flow__nodesselection{z-index:3;transform-origin:0 0;pointer-events:none}.vue-flow__nodesselection-rect{pointer-events:all;cursor:grab;position:absolute}.vue-flow__nodesselection-rect.dragging{cursor:grabbing}.vue-flow__handle{pointer-events:none;min-width:5px;min-height:5px;position:absolute}.vue-flow__handle.connectable{pointer-events:all;cursor:crosshair}.vue-flow__handle-bottom{bottom:0;left:50%;transform:translate(-50%,50%)}.vue-flow__handle-top{top:0;left:50%;transform:translate(-50%,-50%)}.vue-flow__handle-left{top:50%;left:0;transform:translate(-50%,-50%)}.vue-flow__handle-right{top:50%;right:0;transform:translate(50%,-50%)}.vue-flow__edgeupdater{cursor:move;pointer-events:all}.vue-flow__panel{z-index:5;margin:15px;position:absolute}.vue-flow__panel.top{top:0}.vue-flow__panel.bottom{bottom:0}.vue-flow__panel.left{left:0}.vue-flow__panel.right{right:0}.vue-flow__panel.center{left:50%;transform:translate(-50%)}@keyframes dashdraw{0%{stroke-dashoffset:10px}}", wg = ":root{--vf-node-bg:#fff;--vf-node-text:#222;--vf-connection-path:#b1b1b7;--vf-handle:#555}.vue-flow__edge.updating .vue-flow__edge-path{stroke:#777}.vue-flow__edge-text{font-size:10px}.vue-flow__edge-textbg{fill:#fff}.vue-flow__connection-path{stroke:var(--vf-connection-path)}.vue-flow__node{cursor:grab}.vue-flow__node.selectable:focus{outline:none}.vue-flow__node.selectable:focus-visible{outline:none}.vue-flow__node-default,.vue-flow__node-input,.vue-flow__node-output{text-align:center;width:150px;color:var(--vf-node-text);background-color:var(--vf-node-bg);border-style:solid;border-width:1px;border-color:var(--vf-node-color);border-radius:3px;padding:10px;font-size:12px}.vue-flow__node-default.selected,.vue-flow__node-default.selected:hover,.vue-flow__node-input.selected,.vue-flow__node-input.selected:hover,.vue-flow__node-output.selected,.vue-flow__node-output.selected:hover{box-shadow:0 0 0 .5px var(--vf-box-shadow)}.vue-flow__node-default.selected,.vue-flow__node-default:focus,.vue-flow__node-input.selected,.vue-flow__node-input:focus,.vue-flow__node-output.selected,.vue-flow__node-output:focus{border:1px solid #555;outline:none}.vue-flow__node-default:focus-visible{border:1px solid #555;outline:none}.vue-flow__node-input:focus-visible{border:1px solid #555;outline:none}.vue-flow__node-output:focus-visible{border:1px solid #555;outline:none}.vue-flow__node-default .vue-flow__handle,.vue-flow__node-input .vue-flow__handle,.vue-flow__node-output .vue-flow__handle{background:var(--vf-handle)}.vue-flow__node-default.selectable:hover,.vue-flow__node-input.selectable:hover,.vue-flow__node-output.selectable:hover{box-shadow:0 1px 4px 1px #00000014}.vue-flow__node-input{--vf-node-color:var(--vf-node-color,#0041d0);--vf-handle:var(--vf-node-color,#0041d0);--vf-box-shadow:var(--vf-node-color,#0041d0);background:var(--vf-node-bg);border-color:var(--vf-node-color,#0041d0)}.vue-flow__node-input.selected,.vue-flow__node-input:focus{border:1px solid var(--vf-node-color,#0041d0);outline:none}.vue-flow__node-input:focus-visible{border:1px solid var(--vf-node-color,#0041d0);outline:none}.vue-flow__node-default{--vf-handle:var(--vf-node-color,#1a192b);--vf-box-shadow:var(--vf-node-color,#1a192b);background:var(--vf-node-bg);border-color:var(--vf-node-color,#1a192b)}.vue-flow__node-default.selected,.vue-flow__node-default:focus{border:1px solid var(--vf-node-color,#1a192b);outline:none}.vue-flow__node-default:focus-visible{border:1px solid var(--vf-node-color,#1a192b);outline:none}.vue-flow__node-output{--vf-handle:var(--vf-node-color,#ff0072);--vf-box-shadow:var(--vf-node-color,#ff0072);background:var(--vf-node-bg);border-color:var(--vf-node-color,#ff0072)}.vue-flow__node-output.selected,.vue-flow__node-output:focus{border:1px solid var(--vf-node-color,#ff0072);outline:none}.vue-flow__node-output:focus-visible{border:1px solid var(--vf-node-color,#ff0072);outline:none}.vue-flow__nodesselection-rect,.vue-flow__selection{background:#0059dc14;border:1px dotted #0059dccc}.vue-flow__nodesselection-rect:focus,.vue-flow__selection:focus{outline:none}.vue-flow__nodesselection-rect:focus-visible{outline:none}.vue-flow__selection:focus-visible{outline:none}.vue-flow__handle{background:var(--vf-handle);border:1px solid #fff;border-radius:100%;width:6px;height:6px}", Tg = ".vue-flow__minimap{background-color:#fff}.vue-flow__minimap.pannable{cursor:grab}.vue-flow__minimap.dragging{cursor:grabbing}.vue-flow__minimap-mask.pannable{cursor:grab}", Eg = ".vue-flow__controls{box-shadow:0 0 2px 1px #00000014}.vue-flow__controls-button{box-sizing:content-box;cursor:pointer;-webkit-user-select:none;user-select:none;background:#fefefe;border:none;border-bottom:1px solid #eee;justify-content:center;align-items:center;width:16px;height:16px;padding:5px;display:flex}.vue-flow__controls-button svg{width:100%;max-width:12px;max-height:12px}.vue-flow__controls-button:hover{background:#f4f4f4}.vue-flow__controls-button:disabled{pointer-events:none}.vue-flow__controls-button:disabled svg{fill-opacity:.4}", Dg = () => import("./elk.bundled.js").then((t) => /* @__PURE__ */ e(t.default, 1)).then((e) => e.default), Og = [
	Cg,
	wg,
	Tg,
	Eg
].join("\n"), kg = "groundinsight-vueflow-styles";
function Ag() {
	if (document.getElementById(kg)) return;
	let e = document.createElement("style");
	e.id = kg, e.textContent = Og, document.head.append(e);
}
export { Sg as Background, mg as BackgroundVariant, Kh as ControlButton, pg as Controls, Gc as Handle, yo as MarkerType, Vh as MiniMap, J as Position, iu as VueFlow, Ag as injectStyles, Dg as loadELK, Og as styles, $ as useVueFlow };
