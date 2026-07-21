# Frontend — the desktop app

The Electron desktop interface for the Legal Knowledge Manager: a left sidebar
(Dashboard, Sources, Downloads, Documents, Search, Review Queue, Settings), a
main working area with tables, a resizable document preview pane, and light
and dark themes. On launch it starts the Python backend from `../backend/`
automatically, waits for it to answer, and talks to it over 127.0.0.1 only.

## Run it

You need [Node.js](https://nodejs.org) (the LTS version is fine — during
setup just keep clicking Next) in addition to the Python setup from the
backend. Then, from this `frontend` folder:

```
npm install
npm start
```

`npm install` is needed once (and again only if `package.json` changes). It
downloads Electron, React and the IBM Plex fonts to `node_modules/`; after
that the app runs fully offline. `npm start` opens the window and starts the
backend for you — you never need to run Python by hand.

If the backend cannot start, the app shows a checklist dialog (is Python
installed, were the backend packages installed) with the exact commands to run.

## Handy things to know

- **Theme** — Settings → Preferences → Appearance: follow Windows, light
  ("green legal paper") or dark ("ink well").
- **Developer mode** — `npm run dev` opens the same app with DevTools
  attached. If you already have a backend running (e.g. started by hand),
  the app detects and reuses it instead of starting a second one.
- **Tests** — `npm test` runs the unit tests plus an integration test that
  spawns a stub Python server through the real process-management code.

## How this folder is built (for the curious)

There is deliberately **no build step** — no bundler, no JSX, no CSS
framework. What is on disk is exactly what runs:

- `electron/main.cjs` — the desktop shell: starts/stops the backend, creates
  the window, opens document files on request.
- `electron/backend.cjs` — backend process management (find Python, spawn,
  wait for health, stop), kept free of Electron imports so it is testable
  with plain Node.
- `electron/preload.cjs` — the only bridge between the page and the system:
  four narrow functions, nothing else.
- `renderer/index.html` — loads React's prebuilt bundles and the app modules.
- `renderer/js/` — plain ES modules. `h.js` is a 40-line helper that gives
  JSX-like ergonomics (`h("button.btn.primary", …)`) without a compiler.
- `renderer/styles/` — a hand-written design system. `tokens.css` holds the
  two themes; `app.css` holds every component style.
- `tests/` — `node --test` suites for the helpers, the HTTP client and the
  backend process manager.

Why: fewer moving parts to install, nothing to compile, and every line can be
read and verified as-is. React is pinned to 18.3.1, the last version that
ships ready-to-use browser bundles.
