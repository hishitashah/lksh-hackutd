"""Stage 1: continuous telemetry monitoring and anomaly detection (ARCHITECTURE.md §3, Phase 2).

* ``telemetry_processor`` - load + validate a dataset's telemetry, time range, replay ticks (§3.1, §3.3 steps 1-3)
* ``baseline``            - each asset's normal operating behavior learned from history (§3.2)
* ``anomaly_detector``    - pump failure / pipeline leak / worn pump detection → ``Event`` (§3.3-3.4)
* ``countdown``           - hours until a tank reaches its shutdown limit, with uncertainty (§3.5)
* ``incident_manager``    - incident lifecycle new → active → updated → resolved, no duplicates (§3.3)

Numbers here come from defined rules on real readings; no LLM is involved (§3.2).
"""
