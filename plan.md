# Avon Nightwatch: plan for EOG "Beat the Shutdown" + NVIDIA Nemotron

> **Pitch:** Most consoles tell you what's broken. Nightwatch decides what to do, thinks one failure
> ahead, proves its math, and gets the order to the field crew's phone, with a human approving every move.
>
> **What sets us apart:**
> 1. **Long-run planning:** it plans for the *next* failure, not just this one.
> 2. **Savings replay:** it shows in dollars what it would have saved on any week.
> 3. **Sentinel:** an agent that acts, checks its own plans, and can't make up numbers.
> 4. **Show your work:** every plan it rejected, with proof that the chosen one is the cheapest.

## Context

HackUTD: ZeroDay, EOG Resources track. The repo is data only: `history/` folders, each holding
`network.json`, `ops.json` and `telemetry.csv`, plus answer keys. There is no starter code. Judges want a
night-shift operator console that does three things:
1. Detects 3 problems from sensor data only: pipeline leak, pump wearing out, pump failure.
2. Predicts the time until a tank shuts in.
3. Picks the cheapest response: trucks, shutting in wells early, or doing nothing.

Judging criteria: creativity, UX and looks, usefulness, cost optimality, no hardcoding, following instructions.
A new network (`test/`, S6–S11) is dropped in before judging.

Team of 4, ~24 hours, submitting to EOG, NVIDIA and CBRE agent track. The Nemotron agent must be
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
| GPU hosting | **NVIDIA Brev** ($60 credits per member): self-host Nemotron 3 Nano on one GPU with vLLM (FP8 build, or NVIDIA's ready-made Brev launchable, which uses the NVFP4 build). Super stays on the hosted build.nvidia.com API | Nano gets the most calls (evidence, borderline reviews, handoffs), so self-hosting removes free-tier rate limits and latency spikes during the demo. vLLM serves the same OpenAI-compatible API, so switching is one `base_url` setting. Super (120B) needs several GPUs and would burn the credits fast |
| Frontend | React + TypeScript + Vite | Aesthetics are judged, and a custom animated UI (skyline, time machine, map) is what stands out. Streamlit/Dash would be faster but look like every other team's. No SSR needed, so Vite over Next.js |
| UI kit | Tailwind + shadcn/ui | Polished dark-mode components in minutes, with full control over the cyberpunk look. Font: team decides later (never Comic Sans) |
| Map | deck.gl on MapLibre (`react-map-gl`), CARTO dark-matter basemap (no token) | GPU layers for glowing pipelines with animated flow and trucks moving along routes (`TripsLayer`). Far stronger visually than Leaflet markers |
| Charts | Apache ECharts (`echarts-for-react`) | Smooth with 2k-point series. Built-in zoom, limit lines, shaded event bands, dark theme |
| Skyline | SVG + Framer Motion | Light and easy to drive from state |
| Client state | TanStack Query (server data), Zustand (selected asset, time-machine t) | Caching + refetch when t changes, plus a tiny global store |
| Platform | Responsive **web app** + installable **PWA** (`vite-plugin-pwa`: web manifest, icons, service worker) | Big-screen console for the control room, plus a phone view for the field from one codebase. Judges open a link with no install. "Add to Home Screen" makes it launch full screen like a native app. A native app would cost build and install time that isn't scored |
| Field notifications | **Live `/field` page** fed by server-sent events (primary), ntfy.sh push (backup, random topic name), Discord webhook (log shown on screen) | Judges scan a QR code and see the dispatch appear on **their own phone** with no permissions or app. Real push alerts need app installs or permissions, so ntfy runs on a teammate's phone. A Twilio trial only texts pre-verified numbers |
| Work orders | GitHub Issues REST API (a fine-grained token scoped to one repo) | A real external system Sentinel opens, de-duplicates and closes on its own. It stands in for a work-order tool like ServiceNow, and judges can open the issues |
| Tooling | uv (Python), npm, pytest, ruff, eslint | uv installs fast on hackathon wifi. npm is already installed for everyone (pnpm wasn't), so there's no extra setup |
| Deploy | Local run = primary demo. Backup: frontend on Vercel, backend Docker on Fly.io or Railway (always on) | Render's free tier sleeps, which means a 30–60 s cold start in front of judges |

- **Dataset upload:** besides the folder switcher, the UI accepts the 3 files by drag and drop, so the deployed copy can load `test/` at judging too.
- **Nemotron access:** build.nvidia.com API key, OpenAI-compatible endpoint `https://integrate.api.nvidia.com/v1`.
  Slugs: `nvidia/nemotron-3-super-120b-a12b`, `nvidia/nemotron-3-nano-30b-a3b`.
  **Hour 0:** check them with `GET /v1/models`, fall back to whichever Nemotron models are listed,
  and check the model card for tool calling and the reasoning on/off switch.
- **Brev budget ($60 × 4 members):**
  - Check the GPU hourly price on brev.nvidia.com first, and pick the smallest GPU that fits the Nano build you use.
  - **Stop the instance whenever it's idle.** It bills while running.
  - Use one member's credits for development. Keep another member's untouched for the judging window.
  - Settings: `NANO_BASE_URL` / `SUPER_BASE_URL`. If Brev fails, fall back to the hosted API automatically.

```
backend/nightwatch/
  data.py       load folder → wide arrays per (asset, metric); time range; "now"; per-folder cache
  detect.py     causal detector: detect(ds, at) → events (onset, status, confidence, evidence[])
  sim.py        continuous-time piecewise-linear tank simulator (trip time to the minute)
  optimize.py   exact plan search per system (joint over concurrent failures, shared fleet)
  alerts.py     alerts view ordering; lost revenue/h; countdown
  export.py     events.json (SPEC shape)
  replay.py     savings replay: did nothing vs cheapest per event vs long run, over the whole window
  api.py        FastAPI routes
  agent/llm.py        ChatNVIDIA clients (Super + Nano), retries, response cache
  agent/tools.py      LangChain tools wrapping the modules above (the LLM never does math)
  agent/graph.py      Sentinel LangGraph (6 nodes below), interrupt() for approval, streamed over SSE
  agent/grounding.py  every number in the agent's output must appear in a tool result
  agent/notify.py     ntfy.sh push + Discord webhook; scheduled reminders
backend/scripts/score.py   DEV ONLY: compare detector/optimizer output with answer_key.json
backend/tests/             pytest: sim reproduces EVENT_9; optimizer matches references
frontend/src/  pages: Command, Field (mobile), Replay, Handoff, Training(stretch)
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

### Cheapest now vs best in the long run (our design decision)

Planning one event at a time can be short-sighted: it can spend a system's last truck right before
the next pump fails. We show both answers and back the long-run one with numbers from the data.
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

### Savings replay: "what Nightwatch would have saved"

The telemetry is a recording of a week when **nobody acted**, so it already shows what doing nothing cost.
The replay re-runs the whole week as if Nightwatch had been on shift. Each plan is made at the moment
it would have been made, using only data up to that point, and trucks stay busy from earlier plans.

It shows three totals side by side:

| | What it means | History week (from the data) |
|---|---|---|
| **Did nothing** | Lost oil and gas sales from the automatic shut-ins in the recording | ≈ **$51,100** (17 facility-hours shut in) |
| **Cheapest per event** | The SPEC-optimal plan for each failure | ≈ **$15,300** (70% saved) |
| **Nightwatch long run** | Our long-run planner | Should beat cheapest per event (S2 Thursday alone saves ~$2,000). Use the real number from the sim |

- **Why it stands out:** it turns the app into a business case ("this would have saved $35k+ this week"). It also proves the long-run planner across the whole week, not just one anecdote. It works live on `test/`: "on your new network, Nightwatch would have saved $X."
- **How it helps the company:**
  1. *Proves it's worth paying for:* any past week shows the dollars it would have saved.
  2. *Builds operator trust:* operators watch it handle real past crises before relying on it live.
  3. *Finds where money leaks:* the per-event breakdown shows the costliest facilities and pumps. E.g. S4_PUMP_C wore out, then failed twice in the history week: a replacement candidate, not a trucking problem.
  4. *Supports spending decisions:* "would a 3rd truck in S2 pay for itself?" becomes a quick replay comparison.
  5. *Tests changes safely:* try new prices, faster repairs or a bigger fleet on past weeks before changing the field.
  - Pitch line: "It tells EOG what the problems cost, what Nightwatch would have saved, and where to invest next, using their own data."
- **No answer key needed:** "did nothing" comes from the telemetry (wells at status 0 during a pump failure × revenue per hour). The other two come from our sim and optimizer.
- **Cheap to build:** it reuses `optimize.py` in `at_onset_sequential` mode, which the scorer already needs.
- **UI:** a `/replay` page.
  - Three big numbers.
  - A cumulative-cost chart over the week (three lines).
  - A per-event table (date, facility, did nothing vs our plan, saved), where each row jumps the time machine to that moment.
  - A shut-in hours bar per system.
- **Sentinel task:** "Write the weekly ops review." Sentinel calls `run_replay`, picks the biggest wins and any events where the long-run choice cost more, explains them, and writes a one-page review.

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
- `GET /api/{ds}/dispatches` (approved actions so far, for `/field` on page load)
- `GET /api/{ds}/field/stream` (SSE over GET: each newly approved action, live)
- `GET /api/{ds}/replay?mode=cheapest|long_run` (savings replay: totals, per-event rows, cumulative series)

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

### How Sentinel meets the NVIDIA track requirements

| NVIDIA requirement | How Nightwatch meets it |
|---|---|
| **Uses Nemotron** | Super is the orchestrator, the referee, the borderline-alert reviewer and runs long tasks. Nano runs the three specialists, the evidence text and the handoff. Nano is self-hosted on NVIDIA Brev |
| **Beyond a chatbot** | Sentinel starts **on its own** when the detector fires; nobody types a prompt. It runs in the background as the time machine plays. The chat box is optional |
| **Multi-step workflows** | A 6-step graph whose Plan step is **multi-agent**: 3 specialists in parallel + a referee. Verify has a **self-correction loop**. Long tasks (fleet sizing, weekly review) are broken into many tool calls |
| **Tool integration (external APIs, used intelligently)** | Internal tools (detector, optimizer, simulator) + external services: **GitHub Issues** as the work-order system (opens, de-duplicates and closes tickets itself), **ntfy** (pushes tailored to each role, plus scheduled reminders), Discord log. Stretch: OSRM road routing |
| **Real-world applicability** | Produced-water operations like EOG's: dollar impact, field crews, and a human approves every field action |

### Agent graph (one run per system; systems run in parallel)

```
Triage → Investigate ─→ Cost (exact Python optimizer + long-run check)
                          │
             ┌────────────┼──────────────┐       all three in parallel (Nemotron Nano)
       Feasibility    Production    Safety & environment
             └────────────┼──────────────┘
                          ▼
                 Referee (Nemotron Super) → Verify ──fails?──┐ (retry with the reason, max 2,
                          ▲                                  │  then escalate to the operator)
                          └──────────────────────────────────┘
                          ▼ passes
                 Act (approval queue → GitHub Issues, ntfy, Discord) → Report
```

1. **Triage:** `list_active_alerts(at)` → pick the highest risk and say why.
2. **Investigate:** `get_readings(asset, metrics, window)`, `compare_to_baseline(asset)`.
   Confirm the category (leak vs wear: is the flow balance normal?) and write the evidence. Uses Nano.
   **Borderline-alert review:** when the detector's confidence on an early leak or wear signal is low,
   Sentinel (Super) checks the related sensors and decides **raise now** or **keep watching**, with a reason
   ("discharge down 6 psi over 3 h of pumping, flow balance normal → likely wear, raising at 62%").
   This cuts false alarms and gives Nemotron a real part in detection. The detector still owns the numbers.
3. **Plan (multi-agent, from the team whiteboard):**
   - **Cost (core, exact Python, no LLM):** `optimize_plan(system, at)` returns cheapest now + alternatives +
     do nothing. `assess_long_run(system, plan)` returns the reserve risk and expected saving.
   - **Three specialist agents, run in parallel (Nemotron Nano).** Each scores every candidate plan, using only its own tools:
     - **Feasibility:** `get_fleet_status`, `simulate_plan`. Can it actually be done (trucks free, arrival
       before the tank trips, before the repair ends)?
     - **Production:** `get_production_impact(plan)`. Barrels of oil and mcf of gas lost, and from which wells
       (rates from `network.json`).
     - **Safety & environment:**
       - `estimate_spill(asset)`: for leaks, salt water spilled so far = missing flow × hours, from telemetry.
         A real, reportable number that raises urgency.
       - `get_truck_exposure(plan)`: night trips and drive distance from the yard (coordinates in `network.json`).
       - Explains the risk to people, animals and land in plain words. Every number comes from tools.
   - **Referee agent (Nemotron Super):** weighs the cost result and the three assessments, then picks **one of the
     optimizer's plans** (it can never invent one). If the pick isn't the cheapest, it states the extra cost and why.
   - **Scale:**
     - One Sentinel run per system, all in parallel, because systems don't share trucks.
     - Inside a system, the referee plans simultaneous failures together, because they share the fleet
       (the board's "domino effect").
4. **Verify (self-correcting):** `simulate_plan(facility, plan)` checks the peak stays under shutdown and compares
   against doing nothing, then the grounding check runs. If either fails, the graph loops back to the referee
   with the reason (max 2 retries), then escalates to the operator. The retry is visible in the trace.
5. **Act (operator approves):** drafted actions go into the **Approval queue** (a LangGraph `interrupt()`).
   Draft tools: `draft_truck_dispatch`, `schedule_shut_in(well, at)`, `create_work_order(asset, evidence, priority)`.
   On approve:
   - **GitHub Issues = the work-order system.** One issue per leak or worn pump, with the evidence.
     Sentinel searches for an open issue on the same asset first and comments on it instead of duplicating,
     then closes it itself when the detector marks the event resolved.
   - **Live `/field` feed:** the approved action is added to the dispatch list and streamed to every open
     `/field` page (`GET /api/{ds}/field/stream`). This is what judges see on their own phones.
   - **ntfy pushes tailored to each role** (real phone notifications, e.g. on a teammate's phone with the ntfy app):
     - The driver gets the facility, the trips and the ETA. The inspector gets the evidence.
     - Scheduled reminders use ntfy's delayed delivery, e.g. "Shut in S2_WELL_C1 at 12:46".
     - In the demo, scale the delay to the time-machine speed.
   - **Discord log** shown on screen.
6. **Report:** `write_handoff()` → a 30-second shift handoff + `events.json`. Uses Nano.

**Stretch external services (display or advice only; never changes a cost):**
- **OSRM** road routes from the truck yard to the facility, for the truck animation.
- **Weather** (Open-Meteo) at the facility: flags conditions that could slow night trucking, as a note to the operator.

**Multi-step tasks operators can ask for:**
- "Should we add a truck to S2?" → `run_scenario(fleet=2,3,4)` replays the week → cost saved vs truck cost, and a recommendation.
- "What if S4's disposal site goes down for 4 h?" → scenario → every tank's countdown and the trucks needed.
- "Write the weekly ops review." → `run_replay` → biggest savings, where the long-run choice cost more and why, and a one-page review.

**Guardrails:**
- Numbers come only from tool results. `grounding.py` pulls every $/%/time out of the answer and checks it against tool outputs, then shows a "✓ grounded" badge, or retries.
- The referee can only choose among plans the optimizer produced. "Cheapest now" is always shown, so the EOG optimality check never depends on the LLM.
- External actions (GitHub, ntfy, Discord) run only after operator approval, except closing a work order whose event the detector has marked resolved.
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
- **Other views:** asset trend drawer (multi-sensor overlay, event shading), `/handoff` page.
- **`/field` (phone view, installable PWA):** for an inspector or truck driver standing at a tank.
  - One card per alert: big countdown, the action to take, an approve button.
  - Large tap targets.
  - **Live dispatch feed (the main demo path).** When the operator approves an action on the console, a big
    card appears on every open `/field` page within about a second, e.g. "Truck 1 → S2_FACILITY_B, arrives ~13:10".
    - No permissions, no app, no install: it works in any phone browser.
    - Uses `GET /api/{ds}/field/stream` (server-sent events over GET, so the browser's built-in `EventSource`
      works and reconnects on its own) plus `GET /api/{ds}/dispatches` for the list when the page opens.
    - The newest card slides in and briefly flashes. Android phones also vibrate (`navigator.vibrate`);
      iPhones ignore that, which is fine.
  - The QR code on the console just opens `/field`. Judges scan it and watch orders arrive as you approve.
  - Phones can't reach `localhost`: use the deployed URL, or a tunnel like `cloudflared` to the laptop.
    PWA install additionally needs HTTPS (both options provide it).
  - ntfy is the **backup**: a teammate's phone with the ntfy app sits on the table and buzzes for real.
- **Stretch:** Training mode (replay a crisis, the player decides against a clock, graded against the optimizer).

---

## Team split & 24-hour timeline

| Person | Owns |
|---|---|
| P1 Data | `data.py`, `detect.py`, the detection half of `score.py`, evidence/confidence. After the H12 freeze: Sentinel's specialist tools (T5.10) |
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
  - P3: Nano running on Brev (launchable or vLLM), switchable with the hosted API by `base_url`.
  - UI shell on mock JSON.
- **H6–12:**
  - Scorer: 23/23 on full_week.
  - Joint optimizer matches the references on full_week + 3 snapshots.
  - Sentinel end to end on `snapshot_2026-10-08T1200`.
  - Frontend wired to the real API.
- **H12–14:** full dry run on `mock_test_2026-10-10T1530`. **Freeze the core.**
- **H13–16 (P2's top priority after the core): long-run planner.**
  - `assess_long_run`, the data for the "Cheapest now vs Best long run" cards, and the operator knobs.
  - Verify the S2 Thursday scenario in the sim.
  - It's our main differentiator, so build it **before** any skyline or animation polish.
- **H16–18: savings replay.**
  - P2: `replay.py` (reuses the optimizer).
  - P4: `/replay` page.
  - P3: the "weekly ops review" Sentinel task.
- **H14–20 (P1, P3, P4 in parallel while P2 builds the above):** time machine, what-if, skyline, deck.gl truck animation, approvals + live `/field` feed (+ ntfy backup), handoff, fleet-sizing task, `/field` phone view + PWA install (~30 min), dataset upload.
- **H20–22:** bug bash, deploy, README with GIFs, Devpost, backup video.
  Start the judging-window Brev instance 30 min before judging and warm it up with one run.
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
  - Flip Nano's `base_url` between Brev and hosted → same behavior.
  - Stop the Brev instance mid-run → it falls back to hosted.
- At judging: drop in `test/`, pick it in the switcher, check it loads in under 10 s, then demo.
- Phone / PWA:
  - Chrome Lighthouse marks the deployed URL "installable".
  - On a real phone (iPhone + Android if possible): scan the QR code, add to home screen, and it opens full screen.
  - `/field` is usable at 375 px wide.
  - An approval on the console makes the card appear on `/field` within ~1 s on both an iPhone and an Android
    phone, with no permissions granted.
  - Turning the phone's Wi-Fi off and on: the feed reconnects and shows anything approved meanwhile.
  - Backup: the teammate's ntfy phone buzzes on the same approval.
- Long run:
  - With the knobs at 0 and no at-risk pump, long-run = cheapest (pytest).
  - The S2 Thursday scenario reproduces a saving in the simulator. Put the real number in the pitch, not the rough estimate.
- Savings replay:
  - "Did nothing" total from telemetry ≈ $51,100 on full_week.
  - "Cheapest per event" total = sum of the answer key's reference costs (≈ $15,300).
  - "Long run" total ≤ cheapest, or the page explains each event where it cost more.
- Branding check before each deploy:
  - `grep -ri "comic" frontend/ backend/` → no matches
  - `grep -ri "avon corps" frontend/ backend/` → no matches

## Defend every decision (judges will ask "why")

EOG judges will ask why we built things the way we did. Every answer must be **our own reasoning, backed by our
data or tests**. Don't cite mentors. Each owner practices their answers out loud, in 30 seconds or less.
The results below (23/23, matches every reference, $ saved) are **targets**. Before judging, replace them with
the numbers the scorer and replay actually produce. Never claim a number you haven't measured.

| Question | Our answer | Who |
|---|---|---|
| Why rules + statistics instead of ML for detection? | Only 23 labelled events, and judging uses an unseen network. ML would overfit to S1–S5. Rules built on how the equipment behaves (flow in vs flow out, pressure) carry over to any network and produce plain-English evidence. Result: 23/23, 0 false alarms | P1 |
| How did you set thresholds without overfitting? | Thresholds are multiples of each asset's own measured noise and baseline, learned from whatever dataset is loaded. None are tuned to specific S1–S5 assets. Tested blind on the mock test | P1 |
| How do you know the plan is really the cheapest? | We try every valid plan (trucks × trips × wells × start times). The space is small, so the search is exhaustive. It matched the reference cost on every labelled event and snapshot | P2 |
| Why not an optimization solver (MILP)? | At this size, exhaustive search is exact, runs in milliseconds, and lets us show every rejected plan. A solver adds setup and hides the reasoning. We'd switch if the fleet and facility count grew by orders of magnitude | P2 |
| Why ever recommend spending more than the cheapest plan? | Planning one event at a time can spend the last truck right before the next failure. In the data, failures clustered (2–3 within an hour in three systems), and 4 of 5 worn pumps later failed. We only suggest it when the expected saving beats the extra cost, we show both plans, and the cheapest stays the default | P2 / P3 |
| Why not let the AI detect problems or do the math? | Exactness (optimality is judged), data size (~393k rows), speed (the time machine needs milliseconds) and reliability (the console works with no API). The AI does what code can't: investigate, weigh options, explain, draft actions | P3 |
| What makes Sentinel more than a chatbot? | It starts on its own from detector events, runs 6 steps with tools, checks its plan in the simulator, waits for approval, then acts (push to the field crew) and writes the handoff | P3 |
| How do you stop the AI from making up numbers? | Numbers only come from tool results. A grounding check rejects any number that isn't in a tool output, and the UI shows a badge | P3 |
| Why self-host Nano on Brev but not Super? | Nano gets the most calls and fits on one GPU, which removes rate limits. Super needs several GPUs and would use up the credits, and it's called far less, so the hosted API is fine | P3 |
| Why LangGraph? | Its graph maps one-to-one onto our 6 steps, runs the three specialists in parallel, supports the Verify→Referee retry loop, streams each step to the UI, and has a built-in pause for human approval | P3 |
| Why several agents instead of one? | Each specialist looks at one thing (feasibility, production, safety) with only its own tools, so its reasoning stays focused and checkable. The referee makes the trade-off in one visible place. The cost math stays in exact Python | P3 |
| Why can't the referee make up its own plan? | Optimality is judged. Only the optimizer's plans are guaranteed valid and correctly costed, so the referee chooses among them and must justify any pick that isn't the cheapest | P3 |
| Why does a person have to approve? | Trucks and shut-ins cost real money and affect field safety. The agent drafts, a person decides, and one tap keeps it fast | P3 |
| Why a web app, not a native mobile app? | The control room uses big screens. The field gets an installable phone view (PWA) from the same codebase, and judges need no install | P4 |
| Why this layout, and why the skyline? | Decision first ("do this now"), data second. The skyline makes lost revenue visible at a glance | P4 |
| How does it handle a network it's never seen? | Every number comes from the 3 files. Dataset switcher + upload. Tested blind on the mock test, then live on `test/` | All |
| How can you claim savings if nobody acted in the data? | That's why we can: the recording *is* the "did nothing" case. We replay the week with our plans, each made using only the data available at that moment, with trucks busy from earlier plans. Same simulator and rules as the live console | P2 |
| What would you do with more time / in production? | Live sensor feeds instead of CSV, failure risk learned over many weeks, real road routing for trucks, operator feedback to tune thresholds | All |

## 3-minute demo

1. **0:00 Hook.** "Saturday, 7 AM, Avon. Three S4 pumps fail within an hour. Two trucks." The skyline district flickers.
2. **0:30 Plan.** The alerts are in order. Open the top one: decision banner + Show your work.
3. **1:15 What-if + long run.** The replay shows the tank staying under the line.
   Then the S2 Thursday moment: Sentinel spends a bit more to hold a truck, and 30 min later another pump fails and that truck saves it.
   Explain it in our own words: "We only hold a truck when the expected saving beats the extra cost now. Here's the math."
4. **1:40 Sentinel.** The trace runs on its own.
   - The three specialists light up side by side, then the referee gives its verdict.
   - Judges scan the QR code, which opens the `/field` phone view.
   - You approve on the console, and the dispatch appears on *their* phones within a second, while a
     teammate's phone buzzes (ntfy) and a GitHub work order opens for the leak.
5. **2:15 Proof.**
   - Scorer slide: 23/23, matches optimal on every snapshot.
   - Savings replay: "Doing nothing cost $51k this week. Nightwatch: $15k or less."
   - Switch to `test/` live and show its replay number.
6. **2:45 Close.** "Built for the night shift." Show the handoff.

---

## Sources (Nemotron access; verify against build.nvidia.com at Hour 0)
- https://docs.sglang.io/cookbook/autoregressive/NVIDIA/Nemotron3-Nano.md
- https://unsloth.ai/docs/models/nemotron-3
- https://docs.vast.ai/examples/text-generation/nemotron-3-super
- https://radhakishan404.is-a.dev/articles/nvidia-nemotron-breakdown/
