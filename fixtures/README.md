# fixtures/: mock data for building in parallel (task T0.3)

While `NIGHTWATCH_MOCK=1`, the API answers from these files instead of the real engine. This lets the
frontend (P4) and Sentinel (P3) be built from hour 1, before the detector (P1) and optimizer (P2) exist.

**The numbers are illustrative, not real outputs.** They are shaped like the real situation in
`history/snapshot_2026-10-08T1200` so the UI looks realistic. Never quote them in the pitch, and never
compare the scorer against them. Mock mode must be **off** for the demo; the UI shows a MOCK DATA badge while it's on.

| File | Model (`backend/nightwatch/models.py`) | Served by | What it shows |
|---|---|---|---|
| `state.json` | `StateResponse` | `GET /api/{ds}/state` | Thu 10-08 12:00: three S2 pump failures (1.42 / 2.33 / 2.58 h left), a leak on S5_PIPELINE_B, worn pumps S3_PUMP_A and S4_PUMP_C, plus four resolved events. Alerts are already in SPEC order |
| `plan.json` | `PlanResponse` | `GET /api/{ds}/plan/{id}`, `POST /simulate` | The S2_PUMP_C decision at 09:30 (the long-run demo story): cheapest = 1 truck ($905), best long run = shut in S2_WELL_C1 at 12:46 ($1,298) to keep a truck free, do nothing = trips at 14:20 (~$2,790), two alternatives, three tank traces |
| `series.json` | `SeriesResponse` | `GET /api/{ds}/series` | 24 h of S2_TANK_B level: normal sawtooth, then rising after the pump fails at 09:00 |
| `replay.json` | `ReplayResult` | `GET /api/{ds}/replay` | Savings replay for the week: did nothing ≈ $51.1k, cheapest ≈ $15.3k, long run ≈ $13.2k (long-run value is a guess) |
| `agent_run.json` | `AgentRunResult` | `POST /api/{ds}/agent/run` (streamed), `GET /agent/runs/{id}`, `POST /agent/approve`, `GET /dispatches` + `/field/stream` (drafts marked dispatched) | A full multi-agent Sentinel run on the S2 triple failure: triage → cost + environment agents (completed) → feasibility + production (`not_implemented`) → orchestrator → act, the `Recommendation` (status `partial`, missing feasibility + production), 4 pending drafts, and a handoff |

`GET /api/{ds}/events.json` is built from `state.json` + `plan.json` in `mocks.py`, so it has no file here.

## Editing a fixture

1. Edit the JSON so it still matches its model. Unknown keys are rejected on purpose, to catch typos.
2. Run `make test`. `backend/tests/test_models.py` validates every fixture.
3. If you need a new field, add it to `models.py` first (with a description), then run `make types`.
