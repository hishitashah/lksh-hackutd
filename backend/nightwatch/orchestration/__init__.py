"""Stage 3: central orchestration and decision-making (ARCHITECTURE.md §9, §14 ``orchestration/``).

* ``agent_runner``    - runs the specialist agents concurrently with timeouts; failures become visible statuses
* ``decision_engine`` - deterministic decision steps: validate, hard constraints, cost, environment, rank, select
* ``orchestrator``    - ties it together, asks Nemotron for the operator explanation, returns the Recommendation

The orchestrator is the final decision-maker: there is no other model after it (§9).
"""
