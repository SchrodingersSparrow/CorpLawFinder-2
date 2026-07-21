/* Tests for renderer/js/h.js — the tiny hyperscript layer.
 *
 * React itself is stubbed: h.js only needs React.createElement, so the stub
 * records what it was called with and the assertions inspect that record.
 * The stub must exist BEFORE h.js is imported (it captures globalThis.React
 * at module load), hence the dynamic import below.
 */

import test from "node:test";
import assert from "node:assert/strict";

globalThis.React = {
  Fragment: Symbol("Fragment"),
  createElement: (type, props, ...children) => ({ type, props, children }),
  useState: () => {}, useEffect: () => {}, useMemo: () => {},
  useRef: () => {}, useCallback: () => {}, useSyncExternalStore: () => {},
};

const { h, Fragment } = await import("../renderer/js/h.js");

test("plain tag with props and children", () => {
  const el = h("span", { id: "x" }, "hello");
  assert.equal(el.type, "span");
  assert.equal(el.props.id, "x");
  assert.deepEqual(el.children, ["hello"]);
});

test("class shorthand becomes className", () => {
  const el = h("button.btn.primary", null, "Save");
  assert.equal(el.type, "button");
  assert.equal(el.props.className, "btn primary");
});

test("class shorthand merges with an explicit className", () => {
  const el = h("div.card", { className: "selected" });
  assert.equal(el.props.className, "card selected");
});

test("bare .class defaults the tag to div", () => {
  const el = h(".toolbar");
  assert.equal(el.type, "div");
  assert.equal(el.props.className, "toolbar");
});

test("dotted type with no props object still works", () => {
  const el = h("td.mono.dim");
  assert.equal(el.props.className, "mono dim");
});

test("null/undefined/false/true children are skipped, 0 and '' kept", () => {
  const el = h("div", null, null, undefined, false, true, 0, "", "x");
  assert.deepEqual(el.children, [0, "", "x"]);
});

test("nested arrays of children are flattened", () => {
  const el = h("ul", null, [h("li", null, "a"), [h("li", null, "b"), false]], "tail");
  assert.equal(el.children.length, 3);
  assert.equal(el.children[0].type, "li");
  assert.equal(el.children[1].type, "li");
  assert.equal(el.children[2], "tail");
});

test("component types pass through untouched (no dot parsing)", () => {
  function Widget() {}
  const el = h(Widget, { value: 3 });
  assert.equal(el.type, Widget);
  assert.equal(el.props.value, 3);
});

test("Fragment re-export comes from the React global", () => {
  assert.equal(Fragment, globalThis.React.Fragment);
});

test("props object passed in is not required (null props forwarded)", () => {
  const el = h("hr");
  assert.equal(el.props, null);
});
