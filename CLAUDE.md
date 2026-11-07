# Project: HackUTD-ZeroDay water facility monitoring hackathon

Synthetic produced-water dataset for a hackathon challenge (HackUTD: ZeroDay,
cyberpunk city of Avon theme). README.md is the team-facing pitch, kept short
and readable for students with no oil and gas background. SPEC.md is the source
of truth for schemas, physics and formulas.

## Rules
- `organizer/` and any `test/` or ground-truth files are organizer-only.
  Never commit them to the public repo (add them to .gitignore).
- telemetry.csv columns: timestamp, ts_unix, asset_id, metric, value.
  No quality column, no alarm rows, all values numeric.
- All times UTC on a 5-minute grid, 1791158400 to 1791762900 (2,016 steps),
  i.e. Mon 2026-10-05 to Sun 2026-10-11. Kept clear of the hackathon (Nov 7)
  so teams that use the system clock get obviously wrong results.
- No priority levels, no point-based scoring rubric, no linked events. README
  "How judging works" describes the judging process without points.
- The alerts view orders only pump failures (soonest shutdown first); leaks and
  worn pumps follow in any order. events.json is optional (Go further).
- All coordinates, prices, well rates and pressure offsets are placeholders.
- Tank limits come only from `defaults.tank_limits_pct`; no per-tank overrides.
- Incident IDs (EVENT_1.. history, T-xx test) are numbered in onset order with no gaps.
- Labels are taken from the simulator, not hand calculations. Edit timing and
  targets in the incidents file, then regenerate.
- Team-facing docs and answer keys don't say how to detect a problem (no
  signatures, thresholds or `signal` fields): teams work it out from the labelled
  week. They do give the rules of the world: sensor meanings, equipment behavior,
  truck and shut-in costs, repair times, the alerts view order.
- Trucks: first trip arrives `response_min` after the plan starts, then one every
  `cycle_min`; each trip removes `truck_capacity_bbl` and costs a flat
  `cost_per_trip`. A truck is busy until `cycle_min` after its last trip.
- No hubs. Each system has one `disposal_site` (location and fixed pressure, no
  sensors, never fails). Three problem types: pipeline_leak, pump_degradation,
  pump_failure. Several tanks filling at once comes from pump failures in one
  system within about an hour (S2, S4, S5 in history; S6, S8, S7 in test), so
  later ones find trucks still out on earlier ones.

## Layout
- `history/full_week/`: team-facing week (S1 to S5): network, ops, telemetry, answer_key.json.
- `history/snapshot_<cut>/`: the week cut early, with answer_key.json (ongoing pump failures carry `order`).
  `history/mock_test_<cut>/`: a cut with no answer key, shaped like test/; its key is
  `organizer/mock_test_answer_key.json`. `history/README.md` explains the folders.
  network.json and ops.json are identical in every history folder.
- `test/`: test week (S6 to S11) handed out at judging: network, ops, telemetry.
- `organizer/generate.py`: telemetry simulator. Incidents set onset/end; each pump
  failure sets the tank level at onset; it warns when a target is unreachable.
- `organizer/plan_search.py`: cheapest truck/shut-in plan per pump failure
  (SPEC.md Plans); ongoing events plan from the final row and share
  their system's fleet; resolved events see trucks busy on earlier plans.
- `organizer/detector.py`: rules-based baseline detector; scores a dataset against
  its labels (e.g. `python3 organizer/detector.py history/full_week/network.json history/full_week/telemetry.csv history/full_week/answer_key.json`).
- `organizer/make_snapshots.py`: builds the snapshot and mock test folders from full_week.
- network.json has no time range: teams take it from the telemetry. The window
  is `WINDOW_START`/`WINDOW_END` in generate.py (`--end` cuts it short), and
  plan_search's "now" is `WINDOW_END` or `--now`.
- `organizer/*_incidents.json`: generator input. `organizer/test_labels.json`: test answer key.

## Regenerate
```
python3 organizer/generate.py history/full_week/network.json organizer/history_incidents.json organizer/telemetry_history.csv --labels history/full_week/answer_key.json
cp organizer/telemetry_history.csv history/full_week/telemetry.csv
python3 organizer/plan_search.py history/full_week/network.json history/full_week/ops.json organizer/history_incidents.json history/full_week/answer_key.json --write

python3 organizer/generate.py test/network.json organizer/test_incidents.json test/telemetry.csv 8 --labels organizer/test_labels.json
python3 organizer/plan_search.py test/network.json test/ops.json organizer/test_incidents.json organizer/test_labels.json --write

python3 organizer/make_snapshots.py 2026-10-08T12:00:00Z 2026-10-09T03:30:00Z 2026-10-10T09:00:00Z --mock-test 2026-10-10T15:30:00Z
```
Run generate before plan_search: plan_search reads the levels generate writes.
Any WARNING line from generate means a target needs a new onset time. A tank's
level at a given time depends on its earlier failures, so adding or moving one
failure can make a later target on the same tank unreachable.

## Known open items
- None. Both weeks generate cleanly; a rules-based baseline detector finds every
  labelled event with no false positives.
