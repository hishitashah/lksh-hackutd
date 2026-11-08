"""Planning: deterministic cost and intervention math (ARCHITECTURE.md §5.5, §14 ``planning/``).

* ``cost_calculator``  - shared formulas: revenue per hour, tank fill rate (Phase 2 needs these already)
* ``intervention_planner`` - the tank simulator + plan search, including shared trucks (Phase 3, not built yet)

Every number the agents and the orchestrator use for costs comes from here, never from the LLM.
"""
