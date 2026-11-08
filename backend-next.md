# Backend next step: the "H6 thin slice" (P1–P3)

> **Superseded for tonight by [TONIGHT.md](TONIGHT.md)** (Person 1–4 assignments, cut scope, 2:30 AM freeze).
> The file-level details below still apply to the parts TONIGHT.md keeps.

**Goal:** one real pump failure goes **detected → countdown → cheapest plan → API → first Sentinel explanation**,
checked by the scorer and tests (the H6 checkpoint in `tasks.md`). The frontend already renders `/state` alerts, so it
shows real data as soon as `/state` is real.

## What exists now (M0)

- **`backend/nightwatch/models.py`:** the full data contract (Event, Alert, FacilityState, StateResponse, Plan,
  PlanResponse, SimTrace, Replay*, ExportEvent, AgentStep, DraftAction, plus the network/ops file models).
- **`config.py`:** settings (data dirs, mock flag, Nemotron models/URLs, Discord, GitHub).
- **`api.py`:**
  - Real: `/health`, `/datasets`, `/{ds}/network`.
  - Mock-or-501: `/state`, `/series`, `/plan`, `/simulate`, `/replay`, `/events.json`, the `/agent/*` routes.
- **`mocks.py` + `fixtures/`**, `scripts/export_openapi.py`, `scripts/check_nvidia.py`, 38 tests.
- **Not built:** telemetry loading, detection, simulator, optimizer, scorer, any Nemotron call.

## Rules for this slice

- Read every number from the dataset. "Now" = the last telemetry timestamp.
- `answer_key.json` only in `scripts/score.py` and tests.
- **Routes become real as their code lands:** a built route ignores `NIGHTWATCH_MOCK`, and only unbuilt routes keep mock-or-501.
- Comment standard from `CLAUDE.md`: file header, docstrings with units, why-comments citing SPEC.

## Before coding

- `git pull`.
- Create an NVIDIA key at build.nvidia.com, run `cp .env.example .env`, paste the key, then run `make check-nvidia`
  and fix the model names in `.env` if needed.

---

## P1: data + detection + scorer (T1.1, T1.3, T2.1, T2.7)

### `backend/nightwatch/data.py` (new, T1.1)
- Move `scan_datasets()` / `_is_dataset()` here from `api.py`. `api.py` imports them.
- `@dataclass(frozen=True) class Dataset`:
  - fields: `id`, `folder`, `network: Network`, `ops: Ops`, `times: np.ndarray` (int64 unix, sorted),
    `values: dict[str, np.ndarray]` (key `"ASSET.metric"`, aligned to `times`), `facilities: dict[str, Facility]`,
    `system_of: dict[str, str]` (facility → system)
  - property `time_range -> TimeRange`; `step_s` = the most common gap
  - `index_at(t)`: last index with `times <= t`, clamped
  - `series(asset, metric)`, `value_at(asset, metric, t)`, `window(asset, metric, t0, t1) -> (times, values)`
- `load_dataset(folder) -> Dataset`:
  - read with pandas
  - check the 5 columns and raise `ValueError` with a clear message
  - `df.pivot(index="ts_unix", columns=["asset_id", "metric"], values="value")`
- `get_dataset(ds_id) -> Dataset`: in-memory cache keyed by (path, telemetry mtime). 404 is handled in `api.py`.
- Delete `read_time_range` from `api.py`. `/network` uses `ds.time_range`, and the existing test values must still pass.

### `backend/nightwatch/detect.py` (new, T2.1; leak/wear land later in T2.3/T2.4)
- `detect(ds, at) -> list[Event]`: uses only rows with `times <= at`. Loops over facilities.
- `_pump_failures(ds, facility, at)`:
  - Runs of pump `status == 2`. `onset` = first row of the run.
  - `end` = first row after the run, if it's ≤ `at`; otherwise the event is `ongoing`.
  - Event `id = f"{asset_id}@{onset_unix}"`, stable across time-machine steps.
  - **If ongoing:**
    - `level_pct`: a linear fit over the last ≤6 level readings since onset, evaluated at `at`. The sensor is noisy:
      it reads 78.49 when the true value is 78.6.
    - `hours_to_limit.p50 = hours_to_limit(level, shutdown, fill_rate)`, from the shared formulas in `sim.py`.
      The inflow is the **baseline** water of wells still producing (`status == 1` at `at`).
      This reproduces the snapshot answer key: 1.419 / 2.317 / 2.599 h vs 1.42 / 2.33 / 2.58.
    - If the facility is already shut in: hours = 0.
    - `trips_at_unix = at + hours·3600`.
    - `lost_revenue_per_hour`: from `sim.py`.
  - `confidence = 1.0`. `evidence` = e.g. "Pump fault (status 2) since 09:00; tank 78.5%, rising 9.5%/h".
- Stubs `_leaks()` and `_worn_pumps()` return `[]`, with TODO T2.3/T2.4.

### `backend/nightwatch/alerts.py` (new, T2.7)
- `build_alerts(events, labels: dict[str, str]) -> list[Alert]`:
  - keep ongoing events only
  - pump failures sorted by `hours_to_limit.p50` ascending, ties by `lost_revenue_per_hour` descending, `order` = 1..n
  - then leaks and worn pumps with the label "Send inspector to {asset}"

### `backend/scripts/score.py` (new, T1.3, dev-only; the only app code that reads `answer_key.json`)
- `score.py <folder> [--category pump_failure] [--fail-under-recall 1.0]`
- Loads the dataset via `data.py` and runs `detect(ds, now)`.
- Matches each label to at most one detection with the same asset + category, onset within tolerance
  (pump failures: exact; leaks/wear: report the error in hours).
- Prints found / missed / false alarms, plus an onset-error table. Exits non-zero below the threshold, for CI.
- Plan-cost comparison comes later (`--plans`, owned by P2).

### Tests
- `tests/test_data.py`: all 5 history folders load in < 3 s; `now` = last row; series length = 2,016 for full_week.
- `tests/test_detect.py`: full_week has 13/13 pump failures with exact onset and end. Snapshot 10-08 12:00 has the 3 S2
  failures ongoing, with level ±0.5 and hours ±0.05 vs `level_final_pct` / `hours_to_limit_final`.
- `tests/test_alerts.py`: alert order matches `order` in all 3 snapshot keys.

## P2: simulator + single-event optimizer (T3.1, T3.2)

### `backend/nightwatch/sim.py` (new, T3.1). **Write the shared formulas first (hour 1); P1 imports them**
- Shared formulas:
  - `well_revenue_per_hour(well, prices)`
  - `facility_revenue_per_hour(facility, prices)`
  - `fill_rate_pct_per_hour(water_bpd, capacity_bbl)`
  - `hours_to_limit(level_pct, limit_pct, rate_pct_per_h)`
- `@dataclass TankScenario`: `facility_id`, `capacity_bbl`, `level_pct`, `start_unix`, `repair_end_unix`, `warning_pct`,
  `shutdown_pct`, `wells: list[WellRate(id, water_bph, revenue_per_h, producing)]`.
  Built by `scenario_for(ds, facility_id, at, repair_end)`, where `repair_end = onset + expected_repair_hours["pump_failure"]`.
- `@dataclass TruckPlan`: `trucks`, `trips_per_truck`, `response_min`, `cycle_min`, `capacity_bbl`, `cost_per_trip`.
- `simulate(sc, trucks: TruckPlan | None, shut_ins: dict[well_id, start_unix]) -> SimOutcome`:
  - outcome fields: `trips_at_unix`, `peak_level_pct`, `cost: CostBreakdown`, `trace: list[[t, pct]]`
  - **Exact piecewise-linear.** Walk the sorted event times (shut-in starts; truck arrivals at
    `start + response + k·cycle`, all trucks together, each removing `min(trucks·capacity, volume)`; repair end).
    Between events the level rises at the producing, not-shut-in wells' rate. Solve the exact shutdown-crossing time.
  - **After a trip:** the level holds and all wells are off until the repair ends. Later truck arrivals don't help,
    but they still cost (SPEC Plans).
  - **Cost:**
    - trucks = trucks × trips × `cost_per_trip`
    - each well loses `revenue_per_h × (repair_end − min(its shut-in start, trip time))`, so nothing is double-counted
  - Trace: every `step_s`, plus each event point.

### `backend/nightwatch/optimize.py` (new, T3.2: one event, all free trucks; joint planning T3.3/T3.4 comes next)
- `plan_single(ds, event, at, free_trucks) -> PlanResponse`.
- **Candidates:**
  - trucks n = 0..free, trips m = 0..M, where every arrival must come before the repair ends
  - × well subsets (all 2^wells), each with a common shut-in start: the **latest start that avoids the trip**
    (binary search to the minute) plus a 5-min grid, to cover plans that still trip
  - do nothing
- Simulate each with `sim.simulate` and sort by cost.
- Return:
  - `cheapest`, `do_nothing`, `alternatives` (next 5, distinct labels)
  - `traces` (do nothing + cheapest; `warning_pct` / `shutdown_pct` from `network.json`)
  - `long_run = None` until T3.6
- Labels: "1 truck, 1 trip · $905", "Shut in S4_WELL_B1 at 23:06 · $478", "1 truck, 1 trip + shut in X at HH:MM · $975".

### Tests
- `tests/test_sim.py`, the README worked example (EVENT_9, S1_FACILITY_C, 50% at 00:20, repair 06:20):
  - do nothing: trip at 04:49 ±1 min, ≈ $5,016 ±$20
  - S1_WELL_C2 shut in at 02:25: no trip, ≈ $1,958
  - 1 truck, 1 trip (arrives 01:35): no trip, $905
- `tests/test_optimize.py`: cheapest cost = the reference `~$` for EVENT_6 ($0), 9, 11, 14, 15 ($1,810), 18 ($478),
  19 and 22, planned at onset with the full fleet. Tolerance ±1%.

## P3: API wiring + first Nemotron run (T1.2, T4.1, T5.1–T5.3 partial)

### `backend/nightwatch/state.py` (new)
- `build_state(ds, at) -> StateResponse`:
  - `FacilityState` per facility from `ds.value_at`: level, pump status, `inflow_bpd`, wells producing/total,
    `shut_in` = all wells 0
  - events from `detect`
  - labels from `optimize.plan_single(...).cheapest.label` for each ongoing failure (full fleet until T3.4)
  - alerts via `alerts.build_alerts`
  - `lost_revenue_per_hour_total` = revenue of shut-in facilities
  - cache per (ds.id, at)

### `backend/nightwatch/api.py` (edit)
- `/state` and `/plan/{event_id}`: real, ignoring the mock flag. `at` defaults to `now` and is clamped to the time range.
  - `/plan` on a leak or worn pump → 400 "Plans are for pump failures; leaks and worn pumps get an inspection".
  - Unknown event → 404.
- `/series`: real via `ds.window`.
- Everything else stays mock-or-501.
- `/agent/run`: real graph if `NVIDIA_API_KEY` is set, otherwise the existing mock stream. Same SSE event names (`step`, `drafts`, `done`).

### `backend/nightwatch/agent/` (new package)
- `llm.py`: `super_llm()` / `nano_llm()`, cached `ChatNVIDIA(model, base_url, api_key, temperature=0.2)` from settings.
- `tools.py`: `make_tools(ds, at)` returns LangChain `@tool`s that return compact JSON strings:
  - `list_active_alerts()` → `build_state`
  - `get_readings(asset, metric, hours)` → `ds.window`
  - `get_plan(event_id)` → `plan_single`
- `graph.py`:
  - a LangGraph `StateGraph` with 4 nodes: triage → investigate → plan → report
  - each node runs a short tool-calling loop (max 4 tool calls) and emits one `AgentStep`
  - `run(ds, at) -> AsyncIterator[AgentStep]`
  - verify/act/specialists/referee come in T5.4, T5.5, T5.11

### Tests
- `tests/test_api.py` (update): `/state`, `/plan` and `/series` are real now (remove them from `MOCK_ROUTES`).
  - The snapshot state has 3 S2 failures in order.
  - `/plan` for S2_PUMP_B costs $905.
  - The 501 test covers only unbuilt routes.
- `tests/test_tools.py`: each tool returns valid JSON on the snapshot.
- No live LLM in CI. The agent run is checked manually.

---

## Done when (the backend's H6 checkpoint)

1. `make test`: all old tests plus `test_data`, `test_detect`, `test_alerts`, `test_sim`, `test_optimize`, `test_tools` pass.
2. `cd backend && uv run python scripts/score.py ../history/full_week --category pump_failure --fail-under-recall 1.0`
   → 13/13, exact onsets. Then add this step to CI (`.github/workflows/ci.yml` has a marked spot).
3. `NIGHTWATCH_MOCK=0 make dev`, then:
   - `curl localhost:8000/api/snapshot_2026-10-08T1200/state` → S2_PUMP_B, S2_PUMP_C, S2_PUMP_A, with ~1.42 / 2.33 / 2.58 h
   - `curl "localhost:8000/api/full_week/plan/S1_PUMP_C@1791418800?at=1791418800"` → cheapest $905, do nothing ≈ $5,016
   - The frontend alert list shows the real alerts.
4. With a key: `curl -N -X POST localhost:8000/api/snapshot_2026-10-08T1200/agent/run` streams 4 real Nemotron steps,
   and every number in them matches `/state` or `/plan`.
5. `make lint`, `make branding`, and `make types` (commit the regenerated types if models changed).
