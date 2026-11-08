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

## Contract changes (do first, 11:00–11:15, Lane B; then `make types` + push)
1. `POST /api/{ds}/agent/run` returns **`AgentRunResult` as plain JSON** (no streaming). The frontend reveals the
   steps one by one. The mock already has this shape (`fixtures/agent_run.json`).
2. **`GET /api/{ds}/dispatches` → `list[DraftAction]`** (approved actions). `/field` polls it every 2 s.
   The mock returns the fixture drafts marked approved.
3. Add **`work_order_url: str | None`** to `DraftAction` (the GitHub issue link).
4. Add **`cost_at_risk_usd`** (sum of do-nothing costs of ongoing pump failures) and **`cost_with_plans_usd`**
   (sum of the cheapest plans) to `StateResponse`. Add the same values to `fixtures/state.json`.

## Lanes and timeline

### Lane A: data, detection, state (backend 1)
| Until | File | Done when |
|---|---|---|
| 11:45 | `data.py`: load + pivot + `time_range` + `value_at`/`window`; move `scan_datasets` here | 5 folders load; `/network` tests still pass |
| 12:20 | `detect.py`: pump failures (`status == 2` runs) + countdown (baseline water of producing wells, level from a fit of the last readings) | Snapshot 10-08 12:00: 3 S2 failures, ~1.42 / 2.33 / 2.58 h |
| 1:10 | `detect.py`: **leaks** = while pumping, pipeline pressure below its own median **and** pump flow − receive flow above its median, over the last 6 running samples. **Worn pump** = discharge pressure below its median with no flow gap. Thresholds = k × the asset's measured noise. Onset = first abnormal sample of the run | Quick `scripts/score.py` printout on full_week shows found/missed for all 23 |
| 1:45 | `alerts.py` + `state.py` (facilities, events, alerts with labels from `optimize`, the two $ totals) + real `/state` | Real alerts in the UI with `NIGHTWATCH_MOCK=0` |
| 2:15 | `/dispatches` (in-memory list) + approve handler adds to it | `/field` shows an approved order |

### Lane B: planning + Sentinel (backend 2)
| Until | File | Done when |
|---|---|---|
| 11:15 | Contract changes above + `make types` + push | Frontend can build on them |
| 12:00 | `sim.py`: exact tank simulator (event-by-event, exact trip time, cost per well = revenue × (repair end − min(shut-in start, trip))) | EVENT_9: trip 04:49, ~$5,016 / $1,958 / $905 |
| 1:00 | `optimize.py`: per event, try trucks × trips × well subsets × latest safe start. **Joint:** try every split of the free fleet across a system's ongoing failures and sum the best | Snapshot refs: 10-08 → $905 / $1,298 / $905; 10-10 → $905 / $1,215 / $975 |
| 1:15 | Real `/plan/{event_id}` (pump failures only; 400 for leaks) | Plan panel shows real numbers |
| 2:00 | `agent/`: `llm.py` (ChatNVIDIA, one model), `tools.py` (`get_alerts`, `get_readings`, `get_plan`, `draft_action`), `graph.py` (triage → investigate → plan → act), real `/agent/run` | A real run returns 4 steps + drafts on the snapshot |
| 2:20 | `/agent/approve`: mark approved → add to dispatches → create a **GitHub Issue** (httpx, `GITHUB_TOKEN`/`GITHUB_REPO`), save `work_order_url` | Approving opens a real issue |

### Frontend
| Until | What | Data |
|---|---|---|
| 12:15 | Plan panel: "Do this now" banner, cheapest vs do-nothing cost, "Show your work" list | `usePlan` (mock now, real ~1:15) |
| 12:45 | What-if tank chart (ECharts lines + warning/shutdown lines from `traces`) | `usePlan().traces` |
| 1:30 | Sentinel panel: auto-run on dataset load when alerts exist (once per dataset), reveal steps, Approve buttons on drafts | `POST /agent/run`, `POST /agent/approve` |
| 2:00 | `/field`: big cards from `/dispatches`, polled every 2 s; QR code on the console that opens `/field` | `GET /dispatches` |
| 2:30 | Header "$ at risk → $ with plan"; facility grid (tank level bars per facility, grouped by system) | `useNetworkState` |

### 4th teammate (no code)
Slides (problem → demo → proof → why us; one CBRE slide: "fragmented data → one picture → act before it spreads"),
Devpost write-up for all 3 tracks, 3-minute script, the GitHub work-order repo + fine-grained token, and the
`cloudflared` tunnel test so phones can open `/field`.

## Setup now (11:00, one person)
- `git pull`
- `cp .env.example .env`, NVIDIA key from build.nvidia.com, `make check-nvidia`, set `SUPER_MODEL` to a listed Nemotron
- Create a repo for work orders and a token with Issues read/write on that repo only
- Tunnel for phones: `cloudflared tunnel --url http://localhost:5173`. Vite needs `server.allowedHosts: true` (or the
  tunnel host) in `vite.config.ts`.

## 2:30 AM freeze checklist
- `NIGHTWATCH_MOCK=0`; the MOCK badge is gone; `make test lint branding` pass (update `test_api.py` for real routes)
- Every history folder + `mock_test_2026-10-10T1530` loads with no errors
- Full demo once: snapshot 10-08 12:00 → alerts → plan → Sentinel → approve → phone + GitHub issue
- Commit + push. **Sleep.**

## Morning (before 12 PM)
- `git pull upstream main` when `test/` is released → switch to `test` → sanity check
- Fix only demo-breaking bugs. Rehearse 3×. Record a backup video.
