/* Tiny hyperscript layer over React (loaded globally from react.production.min.js).
 *
 * There is deliberately no bundler and no JSX in this project — every file is
 * plain JavaScript the browser runs as-is, which keeps the app installable
 * with nothing but `npm install` and verifiable without a build toolchain.
 *
 * Usage:
 *   h("div", { className: "row" }, "Hello ", h("b", null, name))
 *   h(MyComponent, { value: 3 })
 *
 * Class shorthand: h("button.btn.primary", ...) → className "btn primary".
 * Falsy children (null/undefined/false) are simply skipped, so
 * `cond && h(...)` works exactly like it does in JSX.
 */

const R = globalThis.React;

export const Fragment = R ? R.Fragment : "fragment";

export function h(type, props, ...children) {
  if (typeof type === "string" && type.includes(".")) {
    const [tag, ...classes] = type.split(".");
    const cls = classes.join(" ");
    props = props || {};
    props.className = props.className ? `${cls} ${props.className}` : cls;
    type = tag || "div";
  }
  const kids = [];
  flatten(children, kids);
  return R.createElement(type, props || null, ...kids);
}

function flatten(list, out) {
  for (const child of list) {
    if (child === null || child === undefined || child === false || child === true) continue;
    if (Array.isArray(child)) flatten(child, out);
    else out.push(child);
  }
}

/* Re-exported React hooks so app modules import everything from one place. */
export const useState = (...a) => R.useState(...a);
export const useEffect = (...a) => R.useEffect(...a);
export const useMemo = (...a) => R.useMemo(...a);
export const useRef = (...a) => R.useRef(...a);
export const useCallback = (...a) => R.useCallback(...a);
export const useSyncExternalStore = (...a) => R.useSyncExternalStore(...a);
