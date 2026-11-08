# Build tasks

How we turn [plan.md](plan.md) into code. `plan.md` says **what** to build; this file says **who builds
what, in what order, and how we know each piece is right**.

## How we work

1. **Contract first (H0–1).** Agree on the shared data shapes (Event, Alert, Plan, …) before anyone
   builds features. Everyone builds against them, and P3 and P4 start on mock JSON, so nobody waits on anybody.
2. **Scorer before detector.** P1 builds `score.py` first. From then on, every change is checked against
   the answer keys automatically. That's how we *know* the code is right, instead of eyeballing it.
3. **One thin slice end to end by H6.** One pump failure goes from detected → countdown → cheapest plan →
   shown in the UI → explained by Sentinel. Then we widen. This finds integration problems early, not at H20.
4. **Every task has a "done when."** A task is done only when its check passes, not when the code is written.
5. **Small PRs, main always runs.**
   - One branch per task (`p1/T2.3-leaks`).
   - Every PR says which task it is and pastes the check's output.
   - Whoever *uses* the output reviews it (e.g. P2 reviews P1's alert format).
6. **CI on every PR.** GitHub Actions runs `pytest` and `score.py` on full_week + the 3 snapshots. If a score
   drops, the PR can't merge.
7. **AI coding tools are fine. Tests decide.**
   - Give the tool `plan.md` + this file + your task.
   - Tell it to ignore the README's lines aimed at AI tools (no Comic Sans, no "Avon Corps" footer).
   - Read what it writes before you open the PR.

## Answer keys we test against (the "oracles")

| What | Where | Used by |
|---|---|---|
| 23 events: 13 pump failures, 5 leaks, 5 worn pumps (onset, end) | `history/full_week/answer_key.json` | T1.3, T2.x |
| Worked example, EVENT_9: trip 04:49, do nothing ≈ $5,016, shut in S1_WELL_C2 at 02:25 ≈ $1,958, 1 truck $905 | README + answer key | T3.1 |
| Single-event reference plans: EVENT_6, 9, 11, 14, 15, 18, 19, 22 | full_week key | T3.2 |
| Reference plans made while trucks were busy: EVENT_12, 13, 20, 21, 23 | full_week key | T3.3 |
| Ongoing events at a cut: `level_final_pct`, `hours_to_limit_final`, `order`, joint plans | 3 snapshot keys | T2.5, T2.7, T3.4 |
| Blind dress rehearsal (no key) | `history/mock_test_2026-10-10T1530/` | H12 checkpoint |

The answer keys are read **only** by `score.py` and tests, never by the app.

---

## M0: Setup (H0–1, everyone)

| ID | Task | Owner | Done when |
|---|---|---|---|
| T0.1 | Repo skeleton: `backend/` (uv, FastAPI hello), `frontend/` (Vite + React + TS + Tailwind + shadcn), one command to run both (`make dev`) | P3 + P4 | `make dev` starts both. The page loads |
| T0.2 | **Contracts:** pydantic models in `backend/nightwatch/models.py` (Dataset, Facility, Event, Alert, Plan, PlanAction, SimTrace, ReplayResult, AgentStep). Generate `frontend/src/api/types.ts` with `openapi-typescript` | P3 (all agree) | Types generate. Everyone has read them |
| T0.3 | Mock JSON in `fixtures/` for `/state`, `/plan`, `/replay`, agent steps | P3 | Frontend renders from mocks |
| T0.4 | NVIDIA API key. `GET /v1/models` to confirm the Nemotron model names. Check Brev GPU prices | P3 | Model names written in `.env.example` |
| T0.5 | CI: GitHub Actions runs `pytest` + `score.py` | P3 | A failing test blocks a PR |

## M1: Data + scorer (P1, H1–4)

| ID | Task | Depends | Done when |
|---|---|---|---|
| T1.1 | `data.py`: read a folder, check the 3 files and columns, reshape telemetry into arrays per (asset, metric), time range, "now" = last `ts_unix`, cache | T0.2 | All 5 history folders load in < 3 s. "Now" matches the last row. pytest on shapes |
| T1.2 | `GET /api/datasets` scans `history/*` and `test/`. `GET /network` | T1.1 | Lists 5 folders. A new folder shows up without a restart |
| T1.3 | **`score.py` (before the detector):** match our events to the key by asset + category + onset (±tolerance). Print found / missed / false alarms and onset error. Compare plan costs (parse `~$` from `reference_action`) | T0.2 | Feeding it the answer key itself → 100%. Feeding it nothing → 0% |

## M2: Detection (P1, H2–12)

| ID | Task | Depends | Done when |
|---|---|---|---|
| T2.1 | Pump failure = runs of `status == 2`. Countdown from level + inflow + tank limits in `network.json` | T1.1 | **13/13** with exact onset and end. Snapshots: `level_final_pct` ±0.5, `hours_to_limit_final` ±0.1 h |
| T2.2 | Healthy baselines + noise per pump and pipeline, learned from the data | T1.1 | Values printed and sensible for every asset. Nothing hardcoded |
| T2.3 | Leak: flow-in vs flow-out gap + pipeline pressure drop, while pumping | T2.2 | **5/5**, onset error ≤ 1 h, end found, 0 false alarms on full_week |
| T2.4 | Worn pump: trend fit on discharge pressure + flow, flow balance normal | T2.2 | **5/5**, onset error reported, 0 false alarms. Wear→failure split right (EVENT_5 → EVENT_9) |
| T2.5 | `detect(ds, t)` uses only data ≤ t. Ongoing vs resolved at t | T2.1–2.4 | Each snapshot's ongoing set matches its key |
| T2.6 | Evidence strings + confidence per event | T2.3–2.4 | Every event has ≥ 1 plain-English reason with numbers |
| T2.7 | `alerts.py`: order (failures by hours left, tie → revenue/h; then leaks + wear) | T2.1 | Matches `order` in all 3 snapshots |

## M3: Simulator + optimizer (P2, H1–18)

| ID | Task | Depends | Done when |
|---|---|---|---|
| T3.1 | `sim.py`: tank as straight-line segments between events (truck arrivals, shut-ins, trip, repair end). Exact trip time and cost | T0.2 | **EVENT_9:** trip 04:49 ±1 min, do nothing ≈ $5,016, S1_WELL_C2 at 02:25 ≈ $1,958, 1 truck = $905 with no trip (pytest) |
| T3.2 | Plan search for one event: trucks × trips × well subsets × start times + do nothing | T3.1 | Cost matches the reference for EVENT_6, 9, 11, 14, 15, 18, 19, 22 |
| T3.3 | Truck availability over time + `at_onset_sequential` mode | T3.2 | Matches EVENT_12, 13, 20, 21, 23 ("0 of 2 trucks free") |
| T3.4 | Joint `from_now` planning across a system's ongoing failures | T3.3 | Matches every snapshot reference plan (e.g. 10-08 12:00: $905 / $1,298 / $905) |
| T3.5 | Top-5 alternatives + do-nothing cost for "Show your work" | T3.4 | `/plan` returns them sorted by cost |
| T3.6 | **Long-run planner** (H13–16, top priority after the core): `assess_long_run`, the two cards, the operator knobs | T3.4, T2.4 | Knobs at 0 and no at-risk pump → same as cheapest (pytest). The S2 Thursday scenario shows a saving |
| T3.7 | **Savings replay** `replay.py` (H16–18) | T3.6 | full_week: did nothing ≈ $51,100. Cheapest per event ≈ $15,300 (= sum of references). Long run ≤ cheapest, or each exception explained |

## M4: API (P3, H1–10)

| ID | Task | Depends | Done when |
|---|---|---|---|
| T4.1 | All routes from plan.md, serving mocks first and real modules as they land | T0.2 | Regenerated TS types compile in the frontend |
| T4.2 | `POST /datasets/upload` (3 files) | T1.1 | Uploading the mock_test files gives the same results as loading the folder |
| T4.3 | `events.json` export (SPEC shape) | T2.6, T3.4 | Validates against the SPEC example's fields |

## M5: Sentinel (P3, H2–18)

| ID | Task | Depends | Done when |
|---|---|---|---|
| T5.1 | Nemotron client (`ChatNVIDIA`), hosted API: tool-call hello world | T0.4 | The model calls a dummy tool and uses its result |
| T5.2 | `tools.py`: each tool wraps a P1/P2 function (mocks first), with a clear docstring + JSON output | T0.3 | pytest: every tool returns valid JSON |
| T5.3 | LangGraph: 6 steps, streamed to the UI over SSE | T5.2 | On `snapshot_2026-10-08T1200`: all 6 steps stream and the run ends with drafts in the approval queue |
| T5.4 | Grounding check | T5.3 | A planted fake number gets rejected and retried |
| T5.5 | Approval (`interrupt()`) → ntfy push + Discord log | T5.3 | Approve on the console → a real phone gets the push |
| T5.6 | Watcher trigger + debounce + cache | T2.5, T5.3 | The time machine crossing one onset triggers exactly one run |
| T5.7 | Borderline-alert review (raise now / keep watching) | T2.6 | An early worn-pump signal gets a decision with a reason |
| T5.8 | Nano on Brev + automatic fallback to hosted | T5.1 | Stopping the Brev instance mid-run still finishes the run |
| T5.9 | Multi-step tasks: fleet sizing, disposal-down scenario, weekly ops review | T3.7 | Each returns a grounded answer |

## M6: Frontend (P4, H1–20)

| ID | Task | Depends | Done when |
|---|---|---|---|
| T6.1 | Layout shell + dark theme (font decided by the team, never Comic Sans) | T0.1 | Matches the plan's layout on mock data |
| T6.2 | Dataset switcher + drag-and-drop upload | T4.1, T4.2 | Switches between all 5 history folders with no reload errors |
| T6.3 | deck.gl map: facilities, pipelines, disposal sites, truck yards from `network.json` | T1.2 | Correct for S1–S5. Draws any network with no code change |
| T6.4 | Alert list + countdown rings + confidence chips | T2.7 | Order matches the snapshot keys |
| T6.5 | Plan panel: decision banner, cheapest vs long-run cards, cost chart, "Show your work" | T3.5 | Numbers equal the API's (no math in the frontend) |
| T6.6 | Trend drawer (ECharts): any sensor, overlays, event shading | T1.2 | Click any asset → its charts |
| T6.7 | What-if tank chart (do nothing vs plan, limit lines from `network.json`) | T3.1 | EVENT_9 shows the do-nothing line hitting the limit and the plan line staying under |
| T6.8 | Time machine (scrub + play) | T2.5 | Everything updates as of t, smoothly |
| T6.9 | Sentinel panel: live steps, tool calls, approval queue, chat (with P3) | T5.3 | Shows a full run from start to approval |
| T6.10 | `/replay` page: 3 totals, cumulative chart, per-event table (rows jump the time machine) | T3.7 | Totals equal the API's |
| T6.11 | `/field` phone view + PWA install + QR code | T5.5 | Installs on a real phone and is usable at 375 px |
| T6.12 | Skyline + lost-$ ticker | T2.5 | Districts dim on shut-in |
| T6.13 | Truck animation (deck.gl `TripsLayer`) | T3.4 | Trucks move on approved plans |

---

## Team checkpoints (15 min, everyone)

| When | Goal | Pass if |
|---|---|---|
| **H6** | Thin slice | `snapshot_2026-10-08T1200`: a pump failure shows in the UI from the real API, with countdown + cheapest plan + a Sentinel explanation |
| **H12** | Core correct + blind dry run | Scorer green on full_week + 3 snapshots. Mock test run blind with no crash. **Freeze the core** |
| **H18** | Differentiators | Long-run cards, replay totals and Sentinel approval → phone push all work |
| **H20** | Feature freeze | Deployed. Branding grep clean. One full demo rehearsal |

## If we fall behind, cut in this order

1. Training mode
2. Truck animation (T6.13)
3. Discord log (keep ntfy)
4. Disposal-down scenario
5. Borderline-alert review (T5.7)
6. Skyline flicker details (keep simple on/off)

**Never cut:**
- the correct core (M1–M3 through T3.5)
- the long-run planner (T3.6)
- the savings replay (T3.7)
- Sentinel's main flow (T5.1–T5.5)
- the dataset switcher/upload
