"""Stage 2: specialized agents that investigate incidents (ARCHITECTURE.md §4-8, §14 ``agents/``).

* ``base_agent``          - the shared incident context (§4.1) and the agent interface every agent implements
* ``cost_agent``          - Cost-Effectiveness Agent (§5, Phase 3)
* ``environmental_agent`` - Environmental Impact Agent (§6, Phase 4, not built yet)
* ``feasibility_agent`` / ``production_agent`` - future agents, reported as ``not_implemented`` (§7, §8, Phase 5)

Each agent returns a typed ``AgentAssessment``. Numbers come from deterministic logic (planner, detector, rules);
NVIDIA Nemotron only writes the explanation, and ``llm_used`` says whether it did (``nightwatch/llm.py``).
"""
