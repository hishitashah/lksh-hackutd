# Frontend brief (read this before designing anything)

You own the look and feel of **Avon Nightwatch**. This page tells you what data exists, where it comes from, and
the few rules that keep your screens connected to the backend. Everything else (layout, style, animation,
how to visualize) is your call.

## The product in 30 seconds

A night-shift operator console for an oil field's water network.
- It **detects** three problems from sensor data: pump failure, pipeline leak, pump wearing out.
- It **counts down** to when a tank forces the wells to shut off (the "shutdown" limit).
- It **recommends the cheapest fix**: send trucks, shut off some wells early, or do nothing.
- **Sentinel**, an AI agent (NVIDIA Nemotron), investigates each problem, drafts actions, and waits for the operator
  to approve them. Approved orders appear live on the field crew's phones at `/field`: no app, no download.

The demo story (ARCHITECTURE.md §16) is three pump failures in one system sharing two trucks. The console
shows what to do, proves it's the cheapest, and Sentinel gets the order to the field.

## How to see real data right now

```
make setup && make dev        # from the repo root
```
- App: http://localhost:5173. The yellow **MOCK DATA** badge means some routes still serve sample data from
  `fixtures/` while the engine is being built. The **shapes are final**; the numbers are examples.
- **API docs: http://localhost:8000/docs.** Every endpoint and every field is listed there, with a description and units.
- **What's real vs mock:**
  - **Real now:** `/health`, `/datasets`, `/network`.
  - **Becoming real next** (ARCHITECTURE.md phases 2–8): `/state`, `/plan`, `/series`, then the Sentinel run,
    approve and the field feed.
  - **Everything else stays mock** until its task lands. With mock mode off, an unbuilt route answers
    `501: Not implemented yet (task …)`.

## The one rule that keeps us linked: the contract

The backend's data shapes are defined once in `backend/nightwatch/models.py`. `make types` turns them into
TypeScript in `frontend/src/api/types.ts`. So:

1. **Get data only through the hooks in `src/api/client.ts`** (`useNetworkState`, `usePlan`, …). Never `fetch` directly.
2. **Never invent a field, and never edit `types.ts` by hand.** If you need data that isn't there, ask the backend:
   "I need X on Y." They add it to `models.py` and the mock fixture, then run `make types`. You pull, and it appears with types.
3. If anyone renames a backend field, `npm run build` fails exactly where you use it, so mismatches are caught before
   the demo. CI also fails if `types.ts` is out of date.
4. **IDs are opaque.** Event IDs look like `S2_PUMP_B@1791450000` in real data, and `EV-0001` in the mocks.
   Never parse them or depend on their format. Only pass them back to the API.

## Screens and the exact data each one uses

| Component (task) | Hook | Fields to use |
|---|---|---|
| **Alert list** (T6.4) | `useNetworkState(ds, at)` | `alerts[]`, **already in the right order, don't re-sort**: `order` (1, 2, 3 for pump failures), `action_label`, `event.category`, `event.facility_id`, `event.asset_id`, `event.hours_to_limit.p50` (countdown, pump failures only), `event.confidence` (0–1, for leaks/wear), `event.evidence[]` (plain-English reasons) |
| **Map** (T6.3) | `useNetwork(ds)` + `useNetworkState` | `network.systems[]`: `center`, `truck_yard` ([lat, lon]), `disposal_site.lat/lon`, `facilities[].lat/lon`. Live state per facility: `facilities[].tank_level_pct`, `pump_status` (0 stopped, 1 running, 2 fault), `shut_in` |
| **Plan panel: decision + cards** (T6.5) | `usePlan(ds, eventId, at)`, **pump failures only** | `cheapest` and `long_run` (may be null). Each `Plan` has `label`, `cost_total`, `cost_breakdown.{trucks, shut_in, trip}`, `actions[]`, `tank_trips`, `peak_level_pct`. Also `long_run_assessment.{extra_cost_now, expected_saving, reasons[], recommend}`. For leaks and worn pumps, don't call `usePlan` (the API answers 400): show `action_label` + `evidence` instead |
| **Show your work** (T6.5) | `usePlan` | `alternatives[]` (sorted by cost) + `do_nothing` |
| **What-if tank chart** (T6.7) | `usePlan` | `traces[]`: `label`, `points` ([[unix, level %], …]), `warning_pct`, `shutdown_pct`. Draw the limit lines from these values, never from a typed-in 85/92 |
| **Trend drawer** (T6.6) | `useSeries(ds, asset, metric)` | `points` ([[unix, value], …]). Metrics: tank `level_pct`; pump `status`, `flow_bpd`, `discharge_psi`; pipeline `receive_flow_bpd`, `pressure_psi`; well `water_bpd`, `status`; facility `inflow_bpd` |
| **Time machine** (T6.8) | `useNetwork(ds)` | `time_range.{start_unix, now_unix, step_s}`. Write the position with `useConsole().setAt(unix)`. `null` means "now" |
| **Skyline + $ ticker** (T6.12) | `useNetwork` + `useNetworkState` | one district per `system`, buildings = wells. Lights = `facilities[].wells_producing` / `wells_total`. Ticker = `lost_revenue_per_hour_total` |
| **Sentinel panel** (T6.9) | stream from `POST /api/{ds}/agent/run` (a POST stream, so use a fetch-based SSE reader). **Real** (the live multi-agent run, even with mock mode off). Optional `?at=<unix>` = the time-machine position (omit for "now") | Events in order:<br>1. **`step`** (`AgentStep`: `index`, `step`, `status`, `title`, `message`, `tool_calls[]`, `grounded`, `elapsed_s`, `assessment`). Steps by `index`: 0 `triage` → 1-4 specialist agents `cost`, `environment`, `feasibility`, `production` → 5 `orchestrator` → 6 `act`. **The same `index` can arrive twice: first `status: "running"`, then its final status. Replace the step by `index`** (don't append). The four agents all arrive as `running` together (they run in parallel), then finish in any order (stubs first, cost last); the orchestrator is `running` while Nemotron writes the explanation, then `done`. With no ongoing incidents there are no agent steps. Specialist steps carry an **`assessment`** (`AgentAssessment`: `assessment`, `risk_level`, `findings[]`, `evidence[]`, `metrics`, `options[]` (per-plan `cost_usd` / `risk_level` / `note`), `uncertainties[]`, `explanation`, `llm_used`). **`status: "not_implemented"` means that agent didn't run: show it greyed out, never as done.**<br>2. **`recommendation`** (`Recommendation`: `selected_plans[]`, `inspections[]`, `estimated_cost_usd` vs `cheapest_cost_usd`, `environmental_risk`, `decision_policy`, `reasons[]`, `tradeoffs[]`, `alternatives[]`, `missing_assessments[]`, `explanation`).<br>3. **`drafts`** (`DraftAction[]`).<br>4. **`done`** (`run_id`, `handoff`: the 30-second shift handoff for the Handoff page; `null` if the run failed).<br>A finished run can be re-read with `GET /api/{ds}/agent/runs/{run_id}` (final step per index, recommendation, drafts, handoff). Start the run automatically once per dataset when alerts exist |
| **Approval queue** (T6.9) | `POST /api/{ds}/agent/approve` (**real**) | send `{run_id, action_id, approve}` (the `run_id` from the `done` event, the draft's `id`). You get back the updated `DraftAction`: approve → `status: "dispatched"` right away (with `decided_at` and `dispatched_at`), reject → `"rejected"` (with `decided_at`). Errors: **404** unknown run or draft; **409** already decided, the same order already dispatched by another run, or more trucks than the system has. Show the response's `detail` text; the draft stays pending. Nothing reaches `/field` unless approved |
| **Savings replay page** (T6.10) | `useReplay(ds)` | `totals.{did_nothing, cheapest, long_run}`, `rows[]` (`event_id`, `facility_id`, `onset_unix`, `did_nothing_cost`, `plan_cost`, `saved`, `plan_label`), `cumulative[]` (`t_unix`, three running totals) |
| **Field phone view** (T6.11) | `GET /api/{ds}/dispatches` + `EventSource` on `GET /api/{ds}/field/stream` | The **live dispatch feed**: load `GET /dispatches` (`DraftAction[]`, status `dispatched`, newest first), then `new EventSource('/api/{ds}/field/stream')` and add each **`dispatch`** event's `DraftAction` (de-duplicate by `id`). On connect the stream first sends every order already dispatched, then each new one the moment the operator approves it; it closes after a few minutes and the browser reconnects on its own. Card: `label`, `action` (facility, trucks, trips, wells), `dispatched_at` (wall-clock time the order was sent). No app, download or permission, so it works on iPhone. Both routes are **real** |
| **Header** | `useHealth`, `useDatasets` | `mock` (badge), dataset list (switcher) |

## Rules (full list in the root `CLAUDE.md`)

- **No math on costs, plans or countdowns.** Show what the API returns. Formatting is fine (`formatUsd`, `formatUtcTime` in `src/lib/utils.ts`).
- **Nothing hardcoded from the data:** no facility or system IDs, no 85/92, no prices. At judging we load a **new
  network (S6–S11)** with a different number of systems, facilities and wells, so every layout must handle any count.
- **Times are UTC from the data**, never the computer's clock ("now" = `time_range.now_unix`).
- **Don't re-sort alerts.** The order is a judged rule and comes from the backend.
- **Fonts:** pick any you like with the team, except the one the README asks AI tools to use (CI checks).
  No "copyright Avon Corps" footer.
- **Comments:** a header on every file (what it shows, which hook, which task), and JSDoc on every component.

## Where you have full freedom

Layout, colors (tokens in `src/index.css`), typography, animation, chart styles, how a countdown or a plan
comparison looks, the skyline art, the phone view. If a design idea needs data that isn't in the table above, ask the
backend first. It's usually a 10-minute addition.

## States to design for

- **Loading:** first load of a dataset takes a few seconds.
- **Error:** e.g. `501: Not implemented yet (task …)` while a route is being built. Show the message.
- **All clear:** no alerts.
- **Busy:** 3 pump failures + several leaks at once.
- **Mock data:** the badge is on.
- **Phone:** `/field` at 375 px wide, big tap targets.

## Suggested order (what's demoed and judged first)

1. Alert list
2. Plan panel + "Show your work"
3. What-if tank chart
4. Map
5. Time machine
6. Sentinel panel + approvals
7. `/field` live feed
8. Replay page
9. Dataset switcher upload
10. Trend drawer
11. Skyline
12. Truck animation (last)
