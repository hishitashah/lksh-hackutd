# Avon Nightwatch: plan for EOG "Beat the Shutdown" + NVIDIA Nemotron

## Context

HackUTD: ZeroDay, EOG Resources track. The repo is data only: `history/` folders, each holding
`network.json`, `ops.json` and `telemetry.csv`, plus answer keys. There is no starter code. Judges want a
night-shift operator console that does three things:
1. Detects 3 problems from sensor data only: pipeline leak, pump wearing out, pump failure.
2. Predicts the time until a tank shuts in.
3. Picks the cheapest response: trucks, shutting in wells early, or doing nothing.

Judging criteria: creativity, UX and looks, usefulness, cost optimality, no hardcoding, following instructions.
A new network (`test/`, S6–S11) is dropped in before judging.

Team of 4, ~24 hours, submitting to **both** EOG and an NVIDIA agent track. The Nemotron agent must be
"beyond a chatbot": a multi-step workflow, real tool integrations, a real-world problem.
The tech stack is chosen for what the project needs.

**Concept.** *Avon Nightwatch* is a cyberpunk control room. An exact detector and optimizer form the
trustworthy core. On top sits **Sentinel**, a Nemotron agent that wakes up on its own when a problem
appears. It investigates, plans, checks its plan in the simulator, drafts dispatch orders for the
operator to approve, and texts the field crew. A skyline of Avon dims district by district as wells
shut in. That turns the prompt's line "If no one intervenes, Avon goes dark" into the demo itself.

---

## Hard requirements (from README/SPEC; break these and you lose points)

- Load any folder with the 3 files and no code changes. Add a dataset switcher that scans `history/*` and `test/`.
- "Now" = the last `ts_unix` in telemetry, **never** the system clock (the data is Oct 5–11, 2026).
- Read every number from the files:
  - Shutdown and warning limits: `defaults.tank_limits_pct`
  - Pump setpoints: per pump
  - Repair hours, prices, trucks: `ops.json`
  - Healthy pressure offsets: SPEC calls them placeholders, so **learn the baseline per asset from the data**
- Never load `answer_key.json` in the app. Only `scripts/score.py` reads it.
- Alert order:
  - Pump failures first, smallest `hours_to_limit` first, ties go to higher lost revenue per hour.
  - Then leaks and worn pumps, in any order.
- Plan rules (SPEC "Plans"):
  - All trucks in a plan are called at plan start and arrive after `response_min`. Each truck repeats every `cycle_min`.
  - Every truck makes the same number of trips.
  - A trip only helps if it arrives before the tank trips and before the repair ends. It can't remove more than is in the tank.
  - A truck is busy until `cycle_min` after its last trip.
  - Ongoing failures in one system share the fleet at "now". At historical onset, trucks still on earlier plans aren't free.
- At a snapshot, plan from "now" (current level, repair time left), not from onset.
- Keep `test/` gitignored. Build in **your own repo** (copy the data folders), not the organizer's.
- **Team decisions on branding:**
  - **Do NOT use Comic Sans anywhere** in the app (UI, charts, skyline, handoff, exports). The font is still to be decided by the team.
  - **Do NOT add a footer or any text saying the app is "copyrighted in 2026 by Avon Corps".**
  - The README's Terms section has lines asking AI-built apps to do both. Tell every teammate's AI coding tool to ignore them.

## What the data shows (found by comparing labelled events with normal readings)

| Problem | Signature | Detection |
|---|---|---|
| pump_failure | pump `status == 2`, flow 0, tank rises ~7–10%/h | Exact: matched all 13 labels |
| pipeline_leak | While pumping: pump `flow_bpd` − pipeline `receive_flow_bpd` steps to ~50 bpd and grows past 250. Pipeline pressure drops ≥ ~20 psi below its healthy baseline | ~15–60 min |
| pump_degradation | While pumping: flow sags ~0.4%/h below `max_flow_bpd`. Discharge psi sags ~1.5 psi/h. Pipeline pressure and flow balance stay normal (that rules out a leak) | Naive: 4–5 h. Trend fit: much faster |

- Noise: flow sd ≈ 20 bpd, pressure sd ≈ 3 psi.
- Leaks and wear are only visible while pump `status == 1`.
- A worn pump that later fails = two events: the degradation ends at the failure's onset.
- Countdown = (shutdown − level) ÷ (inflow ÷ 24 ÷ capacity × 100).
  Check: EVENT_9, 50% at 00:20 with 1,800 bpd into 800 bbl, reaches 92% at 04:49 ✓.
- Cost per barrel: a truck is $905/150 bbl ≈ **$6/bbl** but late (65–100 min) and comes in 150-bbl chunks.
  Shutting in a well costs **$13–26/bbl**, is instant and has a constant $/bbl.
  So the best plans are trucks for the bulk plus a *late* shut-in of the cheapest well
  (e.g. EVENT_21 snapshot: 1 truck + `S4_WELL_A2` at +291 min ≈ $975).

---

## Architecture & stack (chosen for what this project needs)

| Layer | Choice | Why it fits this project |
|---|---|---|
| Data + detection | Python 3.12, numpy, pandas, scipy | Rolling fits and robust stats are fastest to prototype in Python, and 393k rows is small. **Rules + statistics, not ML:** with only 23 labelled events and an unseen test network, explainable rules generalize better and produce plain-English evidence |
| Optimizer | Custom exact search (numpy) | The plan space is tiny (≤3 trucks × a few trips × 8 well subsets × start times). Brute force is exact, runs in milliseconds, and can list every rejected plan for "Show your work". A MILP solver adds setup and hides the reasoning |
| API | FastAPI + pydantic, SSE via `sse-starlette` | Serves the Python engine directly. Pydantic → OpenAPI → generated TS types (`openapi-typescript`), so 4 people never drift on the contract. SSE streams the agent's steps live |
| Agent | LangGraph + `langchain-nvidia-ai-endpoints` (`ChatNVIDIA`) | NVIDIA's own integration for build.nvidia.com models, with tool calling. LangGraph gives an explicit step graph (= the 6-step Sentinel workflow), per-step streaming, and built-in human-in-the-loop `interrupt()` for the approval queue. Fallback: plain `openai` SDK on the same endpoint. Ask NVIDIA mentors whether NeMo Agent Toolkit (works with LangGraph) earns extra credit |
| Models | Nemotron 3 Super (orchestrator + tool calls), Nemotron 3 Nano (fast evidence/handoff text) | Super for multi-step reasoning, Nano for cheap, low-latency summaries |
| Frontend | React + TypeScript + Vite | Aesthetics are judged, and a custom animated UI (skyline, time machine, map) is what stands out. Streamlit/Dash would be faster but look like every other team's. No SSR needed, so Vite over Next.js |
| UI kit | Tailwind + shadcn/ui | Polished dark-mode components in minutes, with full control over the cyberpunk look. Font: team decides later (never Comic Sans) |
| Map | deck.gl on MapLibre (`react-map-gl`), CARTO dark-matter basemap (no token) | GPU layers for glowing pipelines with animated flow and trucks moving along routes (`TripsLayer`). Far stronger visually than Leaflet markers |
| Charts | Apache ECharts (`echarts-for-react`) | Smooth with 2k-point series. Built-in zoom, limit lines, shaded event bands, dark theme |
| Skyline | SVG + Framer Motion | Light and easy to drive from state |
| Client state | TanStack Query (server data), Zustand (selected asset, time-machine t) | Caching + refetch when t changes, plus a tiny global store |
| Field notifications | ntfy.sh push (primary, random topic name), Discord webhook (log shown on screen) | Judges scan a QR code and get the dispatch on **their own phone**, no account needed. A Twilio trial only texts pre-verified numbers |
| Tooling | uv (Python), pnpm, pytest, ruff | Fast installs on hackathon wifi |
| Deploy | Local run = primary demo. Backup: frontend on Vercel, backend Docker on Fly.io or Railway (always on) | Render's free tier sleeps, which means a 30–60 s cold start in front of judges |

- **Dataset upload:** besides the folder switcher, the UI accepts the 3 files by drag and drop, so the deployed copy can load `test/` at judging too.
- **Nemotron access:** build.nvidia.com API key, OpenAI-compatible endpoint `https://integrate.api.nvidia.com/v1`.
  Slugs: `nvidia/nemotron-3-super-120b-a12b`, `nvidia/nemotron-3-nano-30b-a3b`.
  **Hour 0:** check them with `GET /v1/models`, fall back to whichever Nemotron models are listed,
  and check the model card for tool calling and the reasoning on/off switch.

```
backend/nightwatch/
  data.py       load folder → wide arrays per (asset, metric); time range; "now"; per-folder cache
  detect.py     causal detector: detect(ds, at) → events (onset, status, confidence, evidence[])
  sim.py        continuous-time piecewise-linear tank simulator (trip time to the minute)
  optimize.py   exact plan search per system (joint over concurrent failures, shared fleet)
  alerts.py     alerts view ordering; lost revenue/h; countdown
  export.py     events.json (SPEC shape)
  api.py        FastAPI routes
  agent/llm.py        ChatNVIDIA clients (Super + Nano), retries, response cache
  agent/tools.py      LangChain tools wrapping the modules above (the LLM never does math)
  agent/graph.py      Sentinel LangGraph (6 nodes below), interrupt() for approval, streamed over SSE
  agent/grounding.py  every number in the agent's output must appear in a tool result
  agent/notify.py     ntfy.sh push + Discord webhook; scheduled reminders
backend/scripts/score.py   DEV ONLY: compare detector/optimizer output with answer_key.json
backend/tests/             pytest: sim reproduces EVENT_9; optimizer matches references
frontend/src/  pages: Command, Field (mobile), Handoff, Training(stretch)
               components: Skyline, AlertList, CountdownRing, NetworkMap, PlanCard,
               ShowYourWork, TankWhatIf, TrendDrawer, TimeMachine, SentinelPanel, ApprovalQueue
```

### Core engine
- **detect(ds, at):** uses only data up to `at`, which makes time travel honest.
  - Healthy baselines per asset come from robust medians of running samples.
    Thresholds are multiples of the noise measured in the data, not fixed psi.
  - Leak: rolling pressure deficit + flow-balance gap.
    Onset is back-dated to the first running sample over a small threshold.
  - Wear: rolling linear fit of discharge deficit and flow ratio.
    Onset is back-dated where the fit crosses zero. Confidence comes from the fit's z-score.
  - Failure: runs of `status == 2`.
  - Each event carries `evidence[]` in plain English, e.g. "Pipeline pressure 34 psi below normal for 2.1 h; 180 bpd missing".
- **sim.py:**
  - Starting state at plan start: level from telemetry, inflow = sum of baseline water for producing wells.
    Check this against the answer key's `hours_to_limit_final`, and if live `water_bpd` matches better, use that.
  - Track the tank between events (truck arrivals, shut-ins, trip, repair end) as straight-line segments and solve the trip time exactly.
  - Cost = trips × `cost_per_trip` + each shut-in's revenue/h × hours + the trip's facility revenue/h × hours shut.
- **optimize.py:**
  - For each failure, list every (trucks n, trips m), then each subset of wells to shut in with a start time.
    For no-trip plans, binary-search the latest start that still avoids the trip. Also check a 5-min grid so plans that trip on purpose (delay the trip but don't prevent it) are covered.
  - Then join across a system's concurrent failures: try every truck split with Σn ≤ free fleet and sum each event's best cost.
  - Return the best plan + the top 5 alternatives + the do-nothing cost, which feed the "Show your work" panel.
  - Two modes: `from_now` (snapshots, live), and `at_onset_sequential` (to reproduce the full-week references, with trucks busy from earlier plans).

### Cheapest now vs best in the long run (EOG mentor feedback)

EOG mentors said a more expensive plan is fine **if you can justify it** and it's better in the long run.
So every pump failure gets two answers:

1. **Cheapest now:** the exact SPEC optimum.
   - Always computed and shown. It's what the answer key checks and what `events.json` exports.
   - Never drop it: judges score optimality.
2. **Best long run:** shown only when it differs, with the extra cost and a one-line reason.

**How "long run" is measured.** Long-run cost = cost now + expected cost of the risk the plan leaves open.
The main case is **keeping a truck in reserve**: sending the whole fleet now leaves nothing for the next failure.
The history week supports this:
- Pump failures cluster: S2, S4 and S5 each had 2–3 within an hour, sharing 2 trucks.
- Worn pumps fail next: 3 of 5 failed the moment the wear ended, and a 4th 38 h later.

**Risk of another failure in the same system while the trucks are busy.** Computed from the loaded dataset, never hardcoded:
- pumps in that system the detector currently flags as wearing out (strongest signal)
- failures in that system in the last hour or two (cluster signal)
- the system's failure rate observed so far in the telemetry

**Expected saving of holding a truck** = P(another failure) × (that facility's simulated cost with 0 trucks − with 1 truck).
Use its current tank level, and assume the failure starts now (worst case).

**When to recommend it:** only if the expected saving is greater than the extra cost now.

**UI card**, e.g.: "+$390 now · keeps 1 truck free · S2 lost 2 pumps in 30 min · expected saving $1,900."

**Operator knobs**, all labelled as assumptions and editable in the UI:
- reserve risk weight
- restart penalty per shut-in well
- night trucking penalty

Defaults keep "Cheapest now" exactly SPEC-optimal.

**Demo scenario to verify with the simulator during build.** S2 on Thursday:
- At 09:30 the cheapest per-event plan sends S2's last truck to S2_FACILITY_C.
- Long-run view: hold it (shut in S2_WELL_C1 instead), because S2 just lost 2 pumps in 30 min.
- At 10:00 S2_PUMP_A fails, and the held truck saves it.
- Rough math: about $905 + $905 + $3,384 vs $905 + ~$1,300 + $905, so **~$2,000 saved**.
- In the time machine, you watch the payoff happen 30 minutes later.

### API
- `GET /api/datasets`
- `POST /api/datasets/upload` (the 3 files)
- `GET /api/{ds}/network` (network + ops + time range)
- `GET /api/{ds}/state?at=` (asset status, ordered alerts, events)
- `GET /api/{ds}/series?asset&metric&from&to`
- `GET /api/{ds}/plan/{event}?at=` (best, alternatives, level traces)
- `POST /api/{ds}/simulate` (custom what-if)
- `POST /api/{ds}/agent/run` (SSE steps)
- `POST /api/{ds}/agent/approve`
- `GET /api/{ds}/events.json?at=`

Agree on the JSON shapes in Hour 1 so the frontend can build against mock data.

---

## Sentinel: the Nemotron agent (NVIDIA track)

**Event-driven, not prompt-driven.** The always-on part is the Python detector, not the LLM.
Nemotron wakes only when the detector finds something, then pulls the data it needs through tools.

```
telemetry (replayed tick by tick in the time machine, or the final "now")
   │
   ▼
Watcher: detect(ds, t) on every 5-min tick, no LLM
   │  triggers: new event · status change · countdown passes warning · confidence jumps
   ▼
Sentinel (Nemotron Super) starts a run on its own
   │  tools fetch only what it needs: readings, baselines, fleet,
   │  then optimizer + simulator + long-run check
   ▼
Verify → approval queue → ntfy/Discord → shift handoff
```

Why the LLM doesn't watch the raw data:
- The detector is exact (13/13 failures) and checkable.
- 195 sensors every 5 min would hit rate limits.
- The LLM adds value in the multi-step reasoning, explaining and acting.

Trigger runs are debounced: one run per event change, cached per (dataset, event, t).
The UI streams each step as a visible trace with expandable tool calls.

1. **Triage:** `list_active_alerts(at)` → pick the highest risk and say why.
2. **Investigate:** `get_readings(asset, metrics, window)`, `compare_to_baseline(asset)`.
   Confirm the category (leak vs wear: is the flow balance normal?) and write the evidence. Uses Nano.
   **Borderline-alert review:** when the detector's confidence on an early leak or wear signal is low,
   Sentinel (Super) checks the related sensors and decides **raise now** or **keep watching**, with a reason
   ("discharge down 6 psi over 3 h of pumping, flow balance normal → likely wear, raising at 62%").
   This cuts false alarms and gives Nemotron a real part in detection. The detector still owns the numbers.
3. **Plan:** `get_fleet_status(system, at)`, `optimize_plan(system, at)` (cheapest now),
   `assess_long_run(system, plan)` (reserve risk + expected saving). Uses Super.
   When the two differ, Sentinel explains the trade-off in plain English and recommends one.
4. **Verify:** `simulate_plan(facility, plan)` → peak level stays under shutdown, compared against doing nothing.
   If the check fails, re-plan with constraints or escalate to the operator.
5. **Act (operator approves):** `draft_truck_dispatch`, `schedule_shut_in(well, at)`, `open_inspection_ticket(asset, evidence)`
   go into the **Approval queue** (a LangGraph `interrupt()`). On approve, `notify_field_crew` sends an
   ntfy push (to judges' phones by QR) + a Discord log, and sets a reminder (e.g. "Shut in S4_WELL_A2 at 12:51").
6. **Report:** `write_handoff()` → a 30-second shift handoff + `events.json`.

**Multi-step tasks operators can ask for:**
- "Should we add a truck to S2?" → `run_scenario(fleet=2,3,4)` replays the week → cost saved vs truck cost, and a recommendation.
- "What if S4's disposal site goes down for 4 h?" → scenario → every tank's countdown and the trucks needed.

**Guardrails:**
- Numbers come only from tool results. `grounding.py` pulls every $/%/time out of the answer and checks it against tool outputs, then shows a "✓ grounded" badge, or retries.
- Low temperature for tool calls.
- The console works fully with no LLM. Cache agent results per (dataset, at, prompt), and handle rate limits.

---

## UI (Command view = the demo)

- **Top:** Avon skyline, one district per system. Buildings = wells. Windows lit = producing.
  It flickers past the warning limit and goes dark on shut-in. A live "$ lost / hour" ticker sits beside it.
- **Left:** alert list in SPEC order, with countdown rings and confidence chips.
- **Center:** dark map: facilities, pipelines to the disposal site, truck yard. Assets glow by state.
- **Right, when an alert is selected:**
  - A **decision banner** ("Do this now: …") with a checklist
  - **Cheapest now** vs **Best long run** cards side by side (the second appears only when it differs), with the extra cost and the reason
  - A cost comparison bar chart
  - **Show your work** (plans checked, the cheapest, why the others lost)
  - **What-if tank chart** (do nothing vs the plan, limit lines from network.json)
- **Bottom:** **Time machine** scrubber over the telemetry range, with play at 60× / 600×. Everything recomputes as of t.
- **Sentinel panel:** step trace, tool calls, approval queue, chat box.
- **Other views:** asset trend drawer (multi-sensor overlay, event shading), `/field` mobile view, `/handoff` page.
- **Stretch:** Training mode (replay a crisis, the player decides against a clock, graded against the optimizer).

---

## Team split & 24-hour timeline

| Person | Owns |
|---|---|
| P1 Data | `data.py`, `detect.py`, the detection half of `score.py`, evidence/confidence |
| P2 Algorithms | `sim.py`, `optimize.py`, `alerts.py`, the plan half of `score.py`, `/simulate` |
| P3 Agent + API + pitch | `api.py`, `agent/*`, notify, grounding, Devpost, pitch |
| P4 Frontend/UX | the React app, map, charts, skyline, time machine, mobile. P3 helps on the Sentinel panel |

- **H0–1:**
  - Ask the NVIDIA mentors:
    - "Which Nemotron model do you recommend for multi-step tool calling? Are there hackathon credits?"
    - "Do judges care about NeMo Agent Toolkit vs a custom loop?"
    - "What separates a great agent from a chatbot for your judges?"
  - Get the NVIDIA API key and verify the model slugs.
  - Set up the repo skeleton and agree on the API contract.
- **H1–6:**
  - Detector v1 (failures + leaks).
  - Simulator reproduces the EVENT_9 numbers ($905 / $1,958 / ~$5,016, trip 04:49).
  - Nemotron tool-call hello world.
  - UI shell on mock JSON.
- **H6–12:**
  - Scorer: 23/23 on full_week.
  - Joint optimizer matches the references on full_week + 3 snapshots.
  - Sentinel end to end on `snapshot_2026-10-08T1200`.
  - Frontend wired to the real API.
- **H12–14:** full dry run on `mock_test_2026-10-10T1530`. **Freeze the core.**
- **H14–20:** time machine, what-if, skyline, deck.gl truck animation, approvals + ntfy push, handoff, fleet-sizing task, mobile view, dataset upload.
- **H20–22:** bug bash, deploy, README with GIFs, Devpost, backup video.
- **H22–24:** rehearse the pitch 3×. Sleep in shifts.

---

## Verification

- `python backend/scripts/score.py history/full_week` → 23/23 events, 0 false positives, onset error stats,
  and plan cost = reference for each pump failure (parse `~$` from `reference_action`).
- The same for the 3 snapshots: ongoing set, `order`, `level_final_pct`, `hours_to_limit_final` (±tolerance), plan costs.
- pytest:
  - The sim reproduces EVENT_9.
  - Changing `shutdown` in a temp copy of network.json changes the countdown (proves nothing is hardcoded).
  - `grep` for literals like `92`, `1050`, `S1_` in the app code → none.
- Mock test: blind run first, then compare with the full-week key events active at 10-10 15:30.
- Agent:
  - 5 canned prompts per snapshot → correct tool sequence and grounded badge.
  - Remove the API key → the console still works.
- At judging: drop in `test/`, pick it in the switcher, check it loads in under 10 s, then demo.
- Long run:
  - With the knobs at 0 and no at-risk pump, long-run = cheapest (pytest).
  - The S2 Thursday scenario reproduces a saving in the simulator. Put the real number in the pitch, not the rough estimate.
- Branding check before each deploy:
  - `grep -ri "comic" frontend/ backend/` → no matches
  - `grep -ri "avon corps" frontend/ backend/` → no matches

## 3-minute demo

1. **0:00 Hook.** "Saturday, 7 AM, Avon. Three S4 pumps fail within an hour. Two trucks." The skyline district flickers.
2. **0:30 Plan.** The alerts are in order. Open the top one: decision banner + Show your work.
3. **1:15 What-if + long run.** The replay shows the tank staying under the line.
   Then the S2 Thursday moment: Sentinel spends a bit more to hold a truck, and 30 min later another pump fails and that truck saves it.
   "EOG told us spending more now is fine if it's better in the long run."
4. **1:40 Sentinel.** The trace runs on its own. Judges scan the QR code, you approve, and *their* phones buzz with the dispatch.
5. **2:15 Proof.** Scorer slide (23/23, matches optimal on every snapshot). Switch to `test/` live.
6. **2:45 Close.** "Built for the night shift." Show the handoff. Quote any feedback from EOG mentors.

---

## Sources (Nemotron access; verify against build.nvidia.com at Hour 0)
- https://docs.sglang.io/cookbook/autoregressive/NVIDIA/Nemotron3-Nano.md
- https://unsloth.ai/docs/models/nemotron-3
- https://docs.vast.ai/examples/text-generation/nemotron-3-super
- https://radhakishan404.is-a.dev/articles/nvidia-nemotron-breakdown/
