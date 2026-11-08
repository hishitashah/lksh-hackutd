# TONIGHT: cut scope (overrides plan.md / tasks.md / backend-next.md until the demo)

**Coding freeze: 2:30 AM.** Demo: 12:00 PM. Anything not listed under "In" is cut. Don't start it, even if you finish early;
fix bugs and rehearse instead.

## What judges must see (requirements → what proves it)

| Track | Requirement | What we show |
|---|---|---|
| **EOG** | Detect pump failure, leak, worn pump from sensors only | Alert list on any dataset |
| | Time until shutdown | Countdown on each pump failure |
| | Cheapest plan (trucks / shut in wells / do nothing), shared trucks | Plan panel: cheapest vs do nothing, "Show your work", joint plan when several pumps fail in one system |
| | Alerts in the right order | Pump failures by soonest shutdown, then leaks/wear |
| | No hardcoding; works on the new `test/` network | Dataset switcher → `test/` live |
| **NVIDIA** | Uses Nemotron | Sentinel runs on Nemotron (hosted API) |
| | Beyond a chatbot | Sentinel **starts on its own** when a dataset with problems loads. Nobody types a prompt |
| | Multi-step workflow | 4-step graph: triage → investigate → plan → act, calling our tools at each step |
| | Tool integration (external APIs) | On approve: **GitHub Issue** work order + the order on the field crew's phone (`/field`) |
| | Real-world | Field operations, $ impact, a human approves every action |
| **CBRE** | Connect fragmented data | One picture from the equipment list + business rules/prices + sensor stream (pitch slide) |
| | Predict operational failures | Countdowns; worn-pump alerts before failure |
| | Quantify business impact | "Do nothing $X vs plan $Y" per alert + **$ at risk** total in the header |
| | Execute before disruptions spread | Approve → dispatch on phones + work order. Joint planning so one failure doesn't starve the next of trucks |

## In / Out

| **In (build tonight)** | **Out (cut; say "next steps" in the pitch if asked)** |
|---|---|
| Loader, detection of all 3 problems (simple rules), countdown | Time machine, trend drawer, skyline, truck animation, map (stretch only) |
| Simulator + optimizer: single event **and** joint (shared trucks) at "now" | `at_onset_sequential` mode, long-run planner, savings replay page |
| `/state`, `/plan`, `/network`, `/dispatches`, `/agent/run`, `/agent/approve` | `/series`, `/simulate`, `/replay`, `events.json`, upload, SSE streaming |
| Sentinel: 4 LangGraph nodes, one Nemotron model, hosted API | Specialist agents + referee as separate agents, grounding check, self-correction, Brev, borderline review, scenarios |
| On approve: GitHub Issue + dispatch card on `/field` | Discord, reminders, role-specific cards |
| Frontend: alert list, plan panel + what-if chart, Sentinel panel with Approve, `/field` (polls every 2 s), QR code, header $ at risk, facility grid | Replay page, handoff page, PWA polish |
| Minimal scorer printout (proof slide) + a few key tests | CI scorer step, deploy to the cloud (use a tunnel) |

**Whiteboard idea, kept cheaply:** the Plan step's prompt asks Nemotron for a 1-line **feasibility / production / safety**
note on the chosen plan. Same call, no extra agents.

## Contract changes (do first, 11:00–11:15, Person 1; then `make types` + push)
1. `POST /api/{ds}/agent/run` returns **`AgentRunResult` as plain JSON** (no streaming). The frontend reveals the
   steps one by one. The mock already has this shape (`fixtures/agent_run.json`).
2. **`GET /api/{ds}/dispatches` → `list[DraftAction]`** (approved actions). `/field` polls it every 2 s.
   The mock returns the fixture drafts marked approved.
3. Add **`work_order_url: str | None`** to `DraftAction` (the GitHub issue link).
4. Add **`cost_at_risk_usd`** (sum of do-nothing costs of ongoing pump failures) and **`cost_with_plans_usd`**
   (sum of the cheapest plans) to `StateResponse`. Add the same values to `fixtures/state.json`.

## Who does what

| Person | Owns | Files |
|---|---|---|
| **Person 1** | **NVIDIA: Sentinel** + the contract changes + approve → dispatch + GitHub work order | `models.py` changes, `agent/llm.py`, `agent/tools.py`, `agent/graph.py`, `/agent/run`, `/agent/approve`, `/dispatches` |
| **Person 2** | Data, detection, alerts, live state | `data.py`, `detect.py`, `alerts.py`, `state.py`, `scripts/score.py`, `/network`, `/state`, `test_api.py` updates |
| **Person 3** | Frontend | everything in `frontend/src/` |
| **Person 4** | Planning math: simulator + optimizer. Morning: slides, Devpost, script | `sim.py`, `optimize.py`, `/plan` |

If Person 4 can't code tonight, Person 1 takes `sim.py` + `optimize.py` right after Sentinel works on mocks
(~12:30), and Person 4 starts the slides tonight instead.

**Handoffs (push at these times so others can switch from mocks to real):**
- **11:15** Person 1: contract changes + `make types`
- **11:30** Person 4: the shared formulas at the top of `sim.py` (Person 2's countdown imports them)
- **11:45** Person 2: `data.py`
- **1:15** Person 4: `optimize.py` + real `/plan`, including `plan_system(ds, at)` for Person 2's labels and $ totals
- **1:45** Person 2: real `/state`
- **2:15** Person 1: real Sentinel + approve

### Person 1: NVIDIA / Sentinel
| Until | What | Done when |
|---|---|---|
| 11:15 | Contract changes above + `make types` + push | Others can build on them |
| 11:45 | `.env` with the NVIDIA key, `make check-nvidia`, set `SUPER_MODEL`. `agent/llm.py`: `ChatNVIDIA`, one model. Tool-call hello world | Nemotron calls a dummy tool and uses its result |
| 12:30 | `agent/tools.py`: `get_alerts` (→ `build_state`; use `mocks.mock_state` until 1:45), `get_readings` (→ `ds.window` after 11:45), `get_plan` (→ `optimize`; use `mocks.mock_plan` until 1:15), `draft_action`. Each returns compact JSON | pytest: every tool returns valid JSON |
| 1:30 | `agent/graph.py`: LangGraph triage → investigate → plan → act. Each node runs a short tool loop and emits one `AgentStep`. The plan prompt adds a 1-line feasibility / production / safety note. Real `/agent/run` returns `AgentRunResult` JSON (falls back to the mock without a key) | A real run returns 4 steps + drafts on snapshot 10-08 12:00 |
| 2:15 | `/agent/approve`: mark approved → in-memory dispatch list (`GET /dispatches`) → **GitHub Issue** via httpx (`GITHUB_TOKEN`/`GITHUB_REPO`) → save `work_order_url`. Create the work-order repo + fine-grained token (Issues read/write on that repo only) | Approve → a real issue opens, and `/field` shows the order |
| 2:30 | Swap the mock fallbacks for real `/state` + `/plan`; tune the prompts | Every number in the run matches `/state` and `/plan` |

### Person 2: data, detection, state
| Until | What | Done when |
|---|---|---|
| 11:45 | `data.py`: load + pivot + `time_range` + `value_at`/`window`; move `scan_datasets` here; `/network` uses it | 5 folders load; `/network` tests still pass |
| 12:20 | `detect.py`: pump failures (`status == 2` runs) + countdown (baseline water of producing wells, level from a fit of the last readings, formulas from `sim.py`) | Snapshot 10-08 12:00: 3 S2 failures, ~1.42 / 2.33 / 2.58 h |
| 1:10 | `detect.py`: **leaks** = while pumping, pipeline pressure below its own median **and** pump flow − receive flow above its median, over the last 6 running samples. **Worn pump** = discharge pressure below its median with no flow gap. Thresholds = k × the asset's measured noise. Onset = first abnormal sample of the run. Quick `scripts/score.py` | Scorer on full_week shows found/missed for all 23 |
| 1:45 | `alerts.py` + `state.py` (facilities, events, alerts; labels + the two $ totals from `optimize.plan_system`, placeholder "Planning…" until 1:15) + real `/state` | Real alerts in the UI with `NIGHTWATCH_MOCK=0` |
| 2:30 | Update `test_api.py` for real routes; load every history folder + mock_test with no errors | `make test` passes |

### Person 3: frontend
| Until | What | Data |
|---|---|---|
| 12:15 | Plan panel: "Do this now" banner, cheapest vs do-nothing cost, "Show your work" list | `usePlan` (mock now, real ~1:15) |
| 12:45 | What-if tank chart (ECharts lines + warning/shutdown lines from `traces`) | `usePlan().traces` |
| 1:30 | Sentinel panel: auto-run on dataset load when alerts exist (once per dataset), reveal the steps one by one, Approve buttons on drafts | `POST /agent/run`, `POST /agent/approve` |
| 2:00 | `/field`: big cards from `/dispatches`, polled every 2 s; QR code on the console that opens `/field` | `GET /dispatches` |
| 2:30 | Header "$ at risk → $ with plan"; facility grid (tank level bars per facility, grouped by system); tunnel test (`cloudflared tunnel --url http://localhost:5173`, set `server.allowedHosts` in `vite.config.ts`) and open `/field` on a phone | `useNetworkState` |

### Person 4: planning math (tonight), pitch (morning)
| Until | What | Done when |
|---|---|---|
| 11:30 | Shared formulas at the top of `sim.py`: `well_revenue_per_hour`, `facility_revenue_per_hour`, `fill_rate_pct_per_hour`, `hours_to_limit`. **Push** | Person 2 can import them |
| 12:15 | `sim.py`: exact tank simulator (event-by-event, exact trip time, cost per well = revenue × (repair end − min(shut-in start, trip))) | EVENT_9: trip 04:49, ~$5,016 / $1,958 / $905 (pytest) |
| 1:15 | `optimize.py`: per event, try trucks × trips × well subsets × latest safe start. **Joint:** every split of the free fleet across a system's ongoing failures, summing the best. `plan_system(ds, at)` returns each event's cheapest + do-nothing. Real `/plan/{event_id}` (pump failures only; 400 for leaks). **Push** | Snapshot refs: 10-08 → $905 / $1,298 / $905; 10-10 → $905 / $1,215 / $975 |
| 2:30 | Tests for the snapshot references; fix mismatches | pytest green |
| Morning | Slides (problem → demo → proof → why us; one CBRE slide: "fragmented data → one picture → act before it spreads"), Devpost for all 3 tracks, 3-minute script | Reviewed by the team by 11 AM |

## Setup now (11:00)
- Everyone: `git pull`, `make setup`.
- Person 1: `.env` + NVIDIA key + `make check-nvidia`; the work-order repo + token.

## 2:30 AM freeze checklist
- `NIGHTWATCH_MOCK=0`; the MOCK badge is gone; `make test lint branding` pass (update `test_api.py` for real routes)
- Every history folder + `mock_test_2026-10-10T1530` loads with no errors
- Full demo once: snapshot 10-08 12:00 → alerts → plan → Sentinel → approve → phone + GitHub issue
- Commit + push. **Sleep.**

## Morning (before 12 PM)
- `git pull upstream main` when `test/` is released → switch to `test` → sanity check
- Fix only demo-breaking bugs. Rehearse 3×. Record a backup video.
