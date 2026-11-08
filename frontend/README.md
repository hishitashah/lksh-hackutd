# frontend/: Avon Nightwatch UI

React + TypeScript + Vite, styled with Tailwind v4 and shadcn/ui. See `plan.md` "UI" for the design and
`tasks.md` M6 for the tasks. Run everything from the repo root with `make dev` (backend + frontend together).

## npm scripts (package.json can't hold comments, so they're described here)

| Script | What it does |
|---|---|
| `npm run dev` | Vite dev server on :5173. `/api` is proxied to the backend on :8000 |
| `npm run build` | Type-check (`tsc -b`) then production build into `dist/` (includes the PWA service worker) |
| `npm run lint` | ESLint (TypeScript, React hooks, hot-reload rules) |
| `npm run gen:types` | `src/api/openapi.json` to `src/api/types.ts`. Use `make types` from the root, which exports the spec first |

## Where things go

| Path | What |
|---|---|
| `src/api/client.ts` | Typed API client + TanStack Query hooks. Components use these, never `fetch` |
| `src/api/types.ts`, `openapi.json` | **Generated** from the backend models by `make types`. Don't edit by hand |
| `src/state/store.ts` | UI state (dataset, time-machine position, selection) in zustand |
| `src/pages/` | One file per route: Command (`/`), Field (`/field`), Replay (`/replay`), Handoff (`/handoff`) |
| `src/components/` | Panels. Stubs render a `Placeholder` naming the task that builds them |
| `src/components/ui/` | shadcn/ui building blocks. Add more with `npx shadcn@latest add <name>` |
| `src/index.css` | Theme tokens (colors, fonts) and Tailwind setup |
| `public/icon.svg` | Placeholder app icon (final PNG icons come with the PWA work in T6.11) |

## Rules (full list in the root `CLAUDE.md`)

- No math on costs or plans in the frontend: show what the API returns.
- No hardcoded dataset IDs, facility IDs, limits or prices.
- The team picks the font later. `make branding` fails if the banned font name appears in `src/`.
