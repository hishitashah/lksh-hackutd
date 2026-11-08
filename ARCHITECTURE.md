# Avon Nightwatch: Complete Backend Architecture & Implementation Specification

> **This is the team's source of truth until the end of the project.** It replaces the earlier plan.md, tasks.md,
> backend-next.md and TONIGHT.md. Where this document and the existing repository/frontend contract disagree, the
> existing repository and frontend contract take priority (see the end of this file).

## Instructions for Claude

You are helping develop the backend for Avon Nightwatch, an AI-powered oilfield water-network monitoring, problem
investigation, and decision-support system.

Before writing or modifying code, read the existing repository documentation, inspect the current backend
implementation, and understand the existing frontend API contract.

Your task is to help implement the backend architecture described below, step by step, while preserving
compatibility with the existing frontend.

Do not replace existing functionality without first understanding it. Do not invent telemetry fields, costs,
operational thresholds, or API response fields. Use the existing repository's data models, fixtures, specifications,
and documentation as the source of truth.

## 1. Project overview

### 1.1 What are we building?

Avon Nightwatch is an intelligent monitoring and decision-support system for an oilfield's produced-water infrastructure.

The system monitors a network containing:
- Oilfield wells
- Water collection facilities
- Storage tanks
- Pumps
- Pipelines
- Disposal sites
- Water transportation trucks

The system receives telemetry data from the infrastructure and continuously analyzes it to identify abnormal
operating conditions.

When a problem is detected, the system activates multiple specialized AI agents to investigate the incident from
different perspectives.

The agents return their findings to a central orchestrator.

The orchestrator evaluates the findings, compares possible interventions, and identifies the most appropriate
solution based on cost, environmental consequences, operational feasibility, and production considerations.

The resulting recommendation is presented to a human operator for review and approval.

Approved actions can then be sent to field personnel through the existing field application.

### 1.2 The main objective

The system must answer four important questions:
1. What is going wrong in the water network?
2. How serious is the problem, and how much time remains before it causes further operational consequences?
3. What are the available ways to resolve or manage the problem?
4. Which solution provides the best overall outcome while controlling cost and operational and environmental consequences?

The system should not simply identify a failure. It should help the operator make an informed decision about what to do next.

## 2. Overall system architecture

The architecture consists of three major stages.

### Stage 1: Continuous monitoring and anomaly detection
A monitoring and recognition system continuously evaluates incoming telemetry data.

It learns or establishes what normal operating behavior looks like and identifies deviations from that behavior.

When it detects a potential problem, it creates an incident and triggers the problem-solving workflow.

### Stage 2: Parallel specialized AI agents
Once an incident is detected, multiple specialized AI agents investigate the same incident.

The intended specialized agents are:
- Cost-Effectiveness Agent
- Environmental Impact Agent
- Feasibility Agent
- Production Impact Agent

Each agent evaluates the incident from its own perspective and returns a structured assessment.

For the current MVP, prioritize implementing:
- Cost-Effectiveness Agent
- Environmental Impact Agent

Feasibility and Production Impact are part of the intended full architecture. Design the interfaces so these agents
can be added later without rewriting the entire system.

### Stage 3: Central orchestration and decision-making
All specialized agent outputs are sent to a central orchestrator.

The orchestrator evaluates their findings, compares the available intervention plans, considers constraints and
trade-offs, and selects the best overall solution.

The orchestrator is responsible for making the final recommendation.

There should not be an additional, separate final AI model after the orchestrator.

The orchestrator itself is the final decision-making component.

The final recommendation is returned to the frontend for the operator to review.

## 3. Stage one: continuous telemetry monitoring and anomaly detection

This is the first and most important stage.

The system must continuously monitor the oilfield network rather than waiting for a user to manually report a problem.

### 3.1 Incoming telemetry
The backend receives telemetry from the existing network data sources.

The repository contains historical telemetry in `history/full_week/telemetry.csv`.

The known telemetry structure includes: `timestamp`, `ts_unix`, `asset_id`, `metric`, `value`.

The historical dataset contains readings at five-minute intervals.

The backend should be designed to process incoming telemetry continuously, whether the data arrives from a live
sensor stream, an API, or a replayed historical dataset.

For the MVP, support the repository's existing historical and fixture-based data sources.

Do not assume that historical CSV replay is the same thing as a production live sensor connection.

### 3.2 Establishing normal operating behavior
The monitoring system needs to understand what normal behavior looks like for the different assets in the network.

For example:
- A tank normally increases in level as water enters it.
- A pump may switch between running and stopped states.
- A pump's flow and discharge pressure should follow expected operating patterns.
- Pipeline pressure and receiving flow should remain within expected operating behavior.
- Well water production and facility inflow should generally be consistent with their operating conditions.

The system should use historical normal-operation telemetry to establish baseline behavior.

Possible approaches include:
- Statistical baselines
- Rolling averages and standard deviations
- Time-series anomaly detection
- Machine-learning anomaly-detection models
- Asset-specific thresholds derived from historical data
- A combination of these methods

The implementation should choose an approach that is appropriate for the available dataset and can be evaluated reliably.

Do not automatically assume that an LLM should perform numerical anomaly detection.

A dedicated time-series or anomaly-detection model may be more appropriate for identifying deviations in numerical sensor data.

An NVIDIA Nemotron-based model can be used for interpreting and explaining the detected conditions, but it should
not be expected to replace reliable numerical monitoring logic.

### 3.3 Continuous monitoring workflow
The monitoring process should follow this sequence:
1. Receive new telemetry.
2. Validate the incoming records.
3. Identify the associated asset and metric.
4. Retrieve the appropriate baseline or expected operating behavior.
5. Evaluate the new reading against historical patterns and relevant operating rules.
6. Identify deviations that may indicate a developing problem.
7. Consider multiple readings and supporting evidence rather than treating every unusual reading as a confirmed failure.
8. Assign a detection result and confidence or severity where supported.
9. Generate an incident when the evidence meets the detection criteria.
10. Send the incident to the problem-solving workflow.

The system must avoid generating duplicate incidents every time another abnormal reading arrives for the same ongoing problem.

Implement incident lifecycle management, including:
- New incident
- Active incident
- Updated incident
- Resolved incident

The exact status names should follow the existing backend models where possible.

### 3.4 Problems the monitoring system should recognize
The existing project focuses on three problem categories:

**A. Pump failure.** Possible evidence includes:
- Pump status indicating a fault
- Unexpected loss of flow
- Abnormal discharge pressure
- Tank level continuing to increase while the pump is expected to discharge water

**B. Pipeline leak.** Possible evidence includes:
- Unexpected differences between sending and receiving flow
- Abnormal pressure changes
- Inconsistent pipeline behavior

**C. Pump wear.** Possible evidence includes:
- Gradual changes in pump performance
- Increasing discharge pressure or other abnormal pressure behavior
- Declining flow performance
- Deviations from the pump's normal historical operating pattern

These are examples of possible indicators, not universal diagnostic rules.

Use the actual dataset, specifications, and available sensor fields to establish the detection logic.

### 3.5 Tank shutdown countdown
An important feature is estimating how long a tank has before reaching its shutdown limit.

The system should use the existing tank-level telemetry and the repository's operational logic to estimate the time remaining.

The estimate should account for the available operating information and uncertainty.

The existing frontend contract expects `event.hours_to_limit.p50`. This is the median estimate of hours remaining until the limit.

Do not hardcode a universal shutdown percentage or calculate the countdown independently in the frontend.

The backend should supply the appropriate estimate.

## 4. Stage two: problem-solving AI agents

Once an incident is created, the problem-solving workflow should activate the specialized agents.

**Important:**
- All four specialized agents are intended to evaluate the same incident in the full architecture.
- They should not simply wait for the previous agent to finish before starting.
- Where possible, execute them in parallel because their investigations are independent.
- The orchestrator will collect their results.

For the MVP, implement the Cost-Effectiveness and Environmental Impact agents first.

### 4.1 Shared agent architecture
Each specialized agent should receive a common incident context.

This context should include, where available:
- Incident ID
- Incident category
- Affected asset
- Facility ID
- Current telemetry
- Historical telemetry
- Detection evidence
- Tank level
- Estimated time to shutdown
- Relevant network information
- Available intervention options
- Relevant operational constraints

Each agent should have a clearly defined responsibility.

Each agent should return structured information rather than an unstructured paragraph alone.

The result should include:
- Agent name
- Incident ID
- Analysis status
- Findings
- Supporting evidence
- Relevant metrics
- Assessment
- Uncertainties or missing information
- Suggested interventions
- Explanation

Use typed data structures for these outputs.

Do not allow one agent to silently overwrite another agent's findings.

## 5. Agent one: Cost-Effectiveness AI

**Priority: implement for MVP.**

### 5.1 Purpose
The Cost-Effectiveness Agent evaluates the financial consequences of the available intervention options.

Its main responsibility is to determine the cost associated with each possible way of handling an incident.

It should identify which interventions are financially attractive while accounting for the operational consequences
of waiting or doing nothing.

### 5.2 Information it receives
The agent should receive:
- Incident information
- Facility and asset information
- Current tank levels
- Estimated time to shutdown
- Available truck resources
- Truck capacity
- Truck response time
- Truck travel time
- Cost per trip
- Relevant operating costs
- Available intervention plans
- Historical incident information where useful

Use actual values from the repository's fixtures, operational data, and backend models.

Do not invent financial figures.

### 5.3 Intervention options to evaluate
The existing project identifies possible strategies such as:

**Option A: Send water trucks.** Evaluate:
- Number of trucks required
- Number of trips
- Truck capacity
- Response time
- Travel time
- Total transportation cost
- Whether the available trucks can complete the required intervention in time

**Option B: Shut in selected wells early.** Evaluate:
- Which wells could be shut in
- Expected water inflow reduction
- Operational consequences
- Associated cost or production impact, where supported by the data

**Option C: Do nothing.** Evaluate:
- Expected tank-level progression
- Whether shutdown is likely
- Potential costs associated with waiting
- Potential consequences of an eventual automatic shutdown

Do not assume that doing nothing is always the cheapest or most expensive option.

Calculate and compare it using the available operational model.

### 5.4 Expected output
The agent should return structured results such as:
- Estimated total cost
- Cost breakdown
- Trucks required
- Number of trips
- Estimated intervention timing
- Relevant operational consequences
- Comparison of alternatives
- Recommended financially attractive options
- Explanation of the calculation

The frontend already expects plan information such as: `cost_total`, `cost_breakdown.trucks`,
`cost_breakdown.shut_in`, `cost_breakdown.trip`, `actions`, `tank_trips`, `peak_level_pct`.

Use the existing Plan model and its field definitions.

Do not create incompatible replacement fields.

### 5.5 Important requirement
Cost calculations must be based on deterministic backend logic and actual operational data.

An LLM may explain the calculations, but it should not invent prices or perform unsupported arithmetic.

The cost agent should be able to explain why one option costs less than another.

## 6. Agent two: Environmental Impact AI

**Priority: implement for MVP.**

### 6.1 Purpose
The Environmental Impact Agent evaluates the potential environmental consequences of an incident and the available
intervention options.

Its responsibility is to assess how each possible response could affect the environment.

The cheapest solution should not automatically be considered the best solution if it introduces unacceptable
environmental consequences.

### 6.2 Information it receives
The agent should receive:
- Incident category
- Incident severity
- Affected facility and asset
- Detection evidence
- Relevant pressure and flow readings
- Tank level
- Historical trends
- Available intervention options
- Environmental information available in the dataset

### 6.3 What it evaluates
Depending on the incident and available evidence, the agent should evaluate:

**A. Potential environmental consequences.** For example:
- Potential for produced water to escape containment
- Potential consequences of a pipeline leak
- Risks associated with continued operation of faulty equipment
- Risks associated with tank capacity being exceeded
- Consequences of delaying intervention

**B. Environmental impact of possible interventions.** Compare the environmental consequences of:
- Sending trucks
- Shutting in selected wells
- Delaying intervention
- Other supported operational alternatives

**C. Risk classification.** The agent should provide a clear assessment of environmental concern.

Possible classifications might include: Low, Moderate, High, Critical.

Only use classifications that are supported by the implementation and its defined criteria.

### 6.4 Expected output
Return structured information including:
- Environmental risk level
- Main environmental concerns
- Supporting telemetry evidence
- Potential consequences
- Comparison of intervention options
- Recommended environmental safeguards
- Uncertainties and missing data
- Explanation in plain English

The environmental agent must distinguish between:
- An observed condition
- A possible consequence
- A confirmed environmental incident

Do not claim that a spill or contamination event occurred simply because a sensor reading is abnormal.

Do not fabricate environmental measurements or regulatory conclusions.

## 7. Agent three: Feasibility AI

**Priority: future implementation.** Design the architecture for it, but do not spend MVP development time
implementing the complete agent.

### 7.1 Purpose
The Feasibility Agent determines whether a proposed intervention can realistically be carried out.

It evaluates practical operational constraints. Examples include:
- Are enough trucks available?
- Can the trucks arrive before the tank reaches its shutdown limit?
- Is the required equipment available?
- Can multiple incidents be handled with the available resources?
- Are there scheduling or transportation limitations?

### 7.2 Expected output
The future agent should return:
- Feasibility assessment
- Required resources
- Resource availability
- Estimated timing
- Operational limitations
- Reasons an intervention may not be possible
- Suggested alternatives

Create an interface for this agent now, but it can return a clearly marked `not_implemented` or equivalent status
until the full implementation is ready.

Do not make the orchestrator assume that missing feasibility analysis means an intervention is feasible.

## 8. Agent four: Production Impact AI

**Priority: future implementation.** Design for it, but postpone full implementation.

### 8.1 Purpose
The Production Impact Agent evaluates how the incident and potential interventions affect oilfield production operations.

Examples include:
- Consequences of shutting in wells
- Potential production losses
- Impact of delaying intervention
- Differences between shutting in one well and multiple wells
- Trade-offs between maintaining production and managing water capacity

### 8.2 Expected output
The future agent should return:
- Expected production impact
- Affected wells
- Estimated production consequences
- Comparison of intervention options
- Potential production losses
- Explanation and uncertainty

Do not invent production figures.

Use the actual available well and production information.

## 9. Stage three: central AI orchestrator

This is the central decision-making component of Avon Nightwatch.

It receives the outputs of the specialized agents and evaluates the overall situation.

**Important:**
- The orchestrator is NOT another specialized agent that simply repeats the work of the other agents.
- It is the central coordinator and decision-maker.
- There should be no separate final AI model after the orchestrator.

### 9.1 Responsibilities
The orchestrator must:
1. Receive the incident.
2. Activate the available specialized agents.
3. Collect their results.
4. Verify that the required agent responses are available.
5. Compare the proposed interventions.
6. Evaluate financial consequences.
7. Evaluate environmental consequences.
8. Consider feasibility and production findings when those agents are implemented.
9. Identify conflicts between agent recommendations.
10. Apply hard operational and safety constraints.
11. Rank the valid intervention options.
12. Select the best overall solution.
13. Generate a clear explanation for the operator.
14. Return the final recommendation to the frontend.

### 9.2 Agent execution
The specialized agents should run concurrently where possible.

For the MVP:
- Cost-Effectiveness Agent runs.
- Environmental Impact Agent runs.
- Feasibility Agent is represented as not implemented.
- Production Impact Agent is represented as not implemented.

The orchestrator should know which agents actually ran and which have not yet been implemented.

It must not pretend that missing analysis exists.

Use asynchronous execution and explicit timeouts or error states.

If an agent fails, the orchestrator should identify the missing assessment and apply a defined fallback policy.

### 9.3 How the orchestrator should evaluate solutions
The orchestrator must not blindly choose the cheapest option.

It should evaluate the available alternatives against the relevant constraints. For example:

| Factor | What the orchestrator considers |
|---|---|
| Cost | Total financial cost of each option |
| Environment | Environmental consequences and risk |
| Feasibility | Whether the intervention can actually happen |
| Production | Effect on wells and production |
| Timing | Whether the intervention happens before shutdown |

For the MVP, the main implemented decision dimensions are cost and environment.

Feasibility and production should be treated as unavailable or pending rather than silently ignored.

### 9.4 Decision-making strategy
Use a combination of deterministic decision logic and the Nemotron model where appropriate.

Recommended architecture:
- **Step 1, validate alternatives:** check that each plan has the required information.
- **Step 2, apply hard constraints:** remove or flag interventions that violate supported operational or
  environmental constraints. For example, an option that cannot be completed before an unavoidable shutdown should
  not be presented as a guaranteed solution.
- **Step 3, compare cost:** use the actual calculated costs returned by the planning and cost-analysis logic.
- **Step 4, compare environmental consequences:** use the environmental agent's structured assessment and supporting evidence.
- **Step 5, rank valid options:** apply a transparent decision policy. Do not invent arbitrary weights without
  documenting them. If a weighted scoring system is used, make the weights configurable and explain how they affect
  the result. Hard safety and operational constraints should not be overridden merely because an option is inexpensive.
- **Step 6, select the recommendation.** Return:
  - Selected intervention
  - Estimated total cost
  - Environmental assessment
  - Reasons for selection
  - Important trade-offs
  - Alternative options
  - Uncertainties
  - Missing agent assessments

### 9.5 Role of NVIDIA Nemotron
The project intends to use NVIDIA Nemotron as the underlying LLM technology for agent reasoning and orchestration.

Inspect the repository to determine which Nemotron model, API, inference service, or deployment configuration is already available.

Do not assume a specific model version.

Use the configured model and its supported interface.

The specialized agents can use the same underlying Nemotron model with different instructions, tools, and
responsibilities. They do not necessarily require four separately trained foundation models.

The orchestrator can also use Nemotron to interpret the structured agent findings and produce a readable explanation.

However:
- Numerical calculations should remain deterministic.
- Cost totals should come from the backend planning logic.
- Sensor thresholds should come from defined operational rules or validated models.
- Environmental claims must be grounded in available evidence.
- The LLM must not invent missing values.
- The final decision should be traceable to the agent outputs and decision rules.

If Nemotron is unavailable, provide a clearly marked fallback or error state rather than silently pretending an AI analysis occurred.

### 9.6 Example orchestrator output
The following is an illustrative structure, not a new required API schema:

```json
{
  "incident_id": "INC_001",
  "status": "completed",
  "selected_plan": "Send two water trucks",
  "estimated_cost": 1810,
  "environmental_assessment": "Moderate",
  "reasoning": [
    "The affected tank is approaching its operational limit.",
    "The selected plan provides additional water-handling capacity.",
    "The plan has a lower estimated cost than the evaluated alternatives."
  ],
  "tradeoffs": [
    "Truck transportation incurs additional cost.",
    "The environmental agent identified risks associated with delaying intervention."
  ],
  "agent_results": {
    "cost": "completed",
    "environment": "completed",
    "feasibility": "not_implemented",
    "production": "not_implemented"
  },
  "requires_operator_approval": true
}
```

Use the actual repository models and frontend contract when implementing the final response. Do not copy this example blindly.

## 10. Human approval and field dispatch

The system is a decision-support application.

It must not automatically execute operational interventions based solely on an AI recommendation.

The operator must be able to review and approve or reject the proposed actions.

The workflow should be:
1. Incident is detected.
2. Specialized agents investigate.
3. Orchestrator selects a recommended intervention.
4. Sentinel presents a draft action.
5. Operator reviews the recommendation.
6. Operator approves or rejects it.
7. Approved actions are sent to the field dispatch system.
8. Field personnel receive the order on their phone interface.

Use the existing frontend contract: `POST /api/{ds}/agent/approve`. Expected request:

```json
{ "run_id": "...", "action_id": "...", "approve": true }
```

The approval endpoint must validate the request and return the updated DraftAction status.

Ensure that approval and dispatch are traceable.

The system should distinguish:
- AI recommendation
- Draft action
- Operator-approved action
- Dispatched action
- Completed action

Do not claim an order was dispatched simply because an AI recommendation was generated.

## 11. Sentinel: AI investigation and explanation

Sentinel is the user-facing AI investigation experience.

It should provide visibility into what the AI system is doing.

Sentinel should explain:
- What problem was detected
- Which agents were activated
- What each agent investigated
- What findings were returned
- How the orchestrator compared the alternatives
- Why the selected intervention was recommended

The existing frontend expects a streaming endpoint: `POST /api/{ds}/agent/run`.

The stream should emit structured events. The existing expected event types include:

- **A. Step events.** Each AgentStep should include fields such as `step`, `status`, `title`, `message`,
  `tool_calls`, `grounded`, `elapsed_s`.
- **B. Draft events.** DraftAction information should include `label`, `action`, `event_id`, `status`.
- **C. Done event.** The final event should communicate that the run has finished and provide the appropriate handoff information.

The frontend uses this stream to show live progress.

Implement the streaming format in a way that matches the existing frontend expectations.

The multi-agent step names are intended to include: Feasibility, Production, Safety, Referee.

For the MVP, reflect the agents that actually ran. Do not display fake completed steps for agents that have not been implemented.

## 12. Backend API and frontend integration

This is extremely important.

The frontend team has already received a detailed integration brief.

The backend must maintain compatibility with the existing frontend hooks and models.

### 12.1 Source of truth
The backend data models are defined in `backend/nightwatch/models.py`.

The frontend TypeScript types are generated using `make types`.

The generated file is `frontend/src/api/types.ts`.

Never manually edit the generated TypeScript types.

If a new field is needed:
1. Add the field to the appropriate backend model.
2. Update the corresponding mock fixture.
3. Run `make types`.
4. Ensure the generated frontend types are updated.
5. Verify that the frontend still builds.

### 12.2 Existing frontend hooks
The frontend currently expects these hooks:
- `useNetworkState(ds, at)`
- `useNetwork(ds)`
- `usePlan(ds, eventId, at)`
- `useSeries(ds, asset, metric)`
- `useConsole()`
- `useReplay(ds)`
- `useHealth()`
- `useDatasets()`

Preserve the data shapes these hooks expect.

Do not rename existing fields without coordinating with the frontend developer.

### 12.3 Existing API areas
Inspect the existing API documentation and backend implementation before changing endpoints.

The frontend brief identifies the following areas:
- Network state
- Network topology
- Planning
- Historical telemetry series
- Time navigation
- AI agent execution
- AI action approval
- Savings replay
- Dispatches
- Field streaming
- Health checks
- Dataset management

API documentation is expected at http://localhost:8000/docs.

Do not create duplicate endpoints if an appropriate endpoint already exists.

## 13. Important data and operational rules

The backend must follow these rules:
- Use actual telemetry and fixtures.
- Do not hardcode facility IDs or system IDs.
- Do not hardcode tank shutdown or warning thresholds in multiple locations.
- Use the existing configuration and operational models.
- Do not invent prices, truck capacity, response times, or trip durations.
- All timestamps should follow the existing UTC convention.
- Support different network sizes.
- Support multiple simultaneous incidents.
- Avoid duplicate incident creation.
- Preserve historical incident information.
- Clearly distinguish mock data from real data.
- Make uncertainty visible.
- Keep agent results traceable.
- Require operator approval before dispatching actions.
- Do not claim that an agent ran if it did not.
- Do not claim that a recommendation is optimal unless the evaluated alternatives and decision criteria support that conclusion.

The judging environment may load different networks with different numbers of systems, facilities, and wells.

The implementation must therefore be dynamic rather than tailored to one example network.

## 14. Recommended backend code organization

First inspect the current backend structure.

Do not blindly create duplicate modules.

If the existing structure supports it, organize responsibilities into separate modules similar to:

```
backend/
  nightwatch/
    models.py
    monitoring/
      telemetry_processor.py
      baseline.py
      anomaly_detector.py
      incident_manager.py
    agents/
      base_agent.py
      cost_agent.py
      environmental_agent.py
      feasibility_agent.py
      production_agent.py
    orchestration/
      orchestrator.py
      decision_engine.py
      agent_runner.py
    planning/
      cost_calculator.py
      intervention_planner.py
    services/
      telemetry_service.py
      incident_service.py
      dispatch_service.py
    api/
      routes.py
    config.py
```

This is a proposed logical organization, not a requirement to replace the current repository structure.

The main goal is separation of responsibilities:
- The monitoring system should not contain all the agent logic.
- The agents should not contain the entire orchestrator.
- The orchestrator should not duplicate the cost calculator.
- Each component should have one clear responsibility.

## 15. Implementation plan: work step by step

Do not attempt to implement the entire architecture in one massive code change.

Work in small, testable stages.

### Phase 1: Repository inspection
Before coding:
- Read README.md.
- Read CLAUDE.md.
- Read SPEC.md.
- Read this document (it replaces plan.md and tasks.md).
- Inspect `backend/nightwatch/models.py`.
- Inspect existing backend API routes.
- Inspect existing fixtures.
- Inspect the frontend API client and generated types.
- Identify which parts are already implemented.

Produce a summary containing:
- Existing architecture
- Existing working endpoints
- Existing models
- Existing detection logic
- Existing agent functionality
- Missing functionality
- Potential conflicts with the frontend contract

Do not rewrite working components unnecessarily.

### Phase 2: Telemetry monitoring
Implement or improve:
- Telemetry ingestion.
- Data validation.
- Asset-specific baseline handling.
- Anomaly detection.
- Evidence collection.
- Incident creation.
- Duplicate incident prevention.
- Incident status management.
- Tank shutdown countdown estimates.

Test with the existing historical telemetry and fixtures.

### Phase 3: Cost-Effectiveness Agent
Implement the cost agent. It should:
- Receive incident context.
- Retrieve relevant operational information.
- Evaluate supported intervention options.
- Calculate costs using deterministic logic.
- Return structured results.
- Include a plain-English explanation.
- Provide uncertainty information where necessary.

Test it against known examples in the repository.

### Phase 4: Environmental Impact Agent
Implement the environmental agent. It should:
- Receive the same incident context.
- Analyze the incident evidence.
- Evaluate supported environmental consequences.
- Compare intervention alternatives.
- Return structured risk assessments.
- Include supporting evidence.
- Identify missing information and uncertainty.

Do not fabricate environmental measurements.

### Phase 5: Specialized agent framework
Create a shared agent interface.

Make it possible to add the Feasibility Agent and Production Impact Agent without changing the entire orchestration architecture.

For the MVP, mark the two future agents as not implemented.

### Phase 6: Orchestrator
Implement the orchestrator. It should:
- Receive an incident.
- Start the available agents concurrently.
- Collect their results.
- Handle timeouts and failures.
- Validate agent outputs.
- Compare intervention options.
- Apply hard constraints.
- Rank valid options.
- Select a recommendation.
- Generate a grounded explanation.
- Return the result in the appropriate backend model.

Test the orchestrator with different combinations of agent results.

### Phase 7: Sentinel streaming
Implement the agent execution endpoint. The endpoint should stream:
- Agent execution steps
- Agent results
- Orchestrator decision-making progress
- Draft actions
- Completion status

Ensure the frontend can consume the stream using its existing implementation.

### Phase 8: Approval and dispatch
Implement or complete:
- Draft action creation
- Approval
- Rejection
- Dispatch integration
- Dispatch status
- Field updates

Verify that only approved actions are dispatched.

### Phase 9: Testing and integration
Test the entire system using the existing mock fixtures. The test should cover:
- Normal telemetry.
- Pump failure.
- Pipeline leak.
- Pump wear.
- Multiple simultaneous incidents.
- Multiple pump failures sharing limited trucks.
- Tank approaching shutdown.
- Cost agent returning results.
- Environmental agent returning results.
- One agent failing.
- Orchestrator comparing alternatives.
- Operator approving a recommendation.
- Operator rejecting a recommendation.
- Approved action appearing in the field application.

Run the project's existing tests and build checks. Also verify `make types` and the frontend build.

## 16. Demo scenario: three pump failures sharing two trucks

The intended demonstration is especially important.

The project plan describes three pump failures in one system sharing two trucks.

The backend should support the following demonstration workflow:
1. **Monitoring.** The telemetry system continuously receives readings. It identifies abnormal pump behavior and creates three separate incidents.
2. **Incident investigation.** Each incident activates the specialized agents. The cost agent evaluates
   transportation and intervention costs. The environmental agent assesses the environmental consequences of the
   incidents and possible responses.
3. **Shared resource evaluation.** The planning and decision-making logic considers the fact that the three incidents
   share only two trucks. It must not independently assign the same available truck to multiple simultaneous trips
   as though the truck were available everywhere at once. Truck scheduling and availability must be considered when
   evaluating combined plans.
4. **Orchestration.** The orchestrator receives the available agent results and compares the overall intervention
   strategies. It identifies the best supported approach while accounting for shared truck resources, cost, timing,
   and environmental consequences.
5. **Operator recommendation.** The frontend receives: three incident alerts, agent investigation results, available
   intervention plans, cost comparisons, environmental assessments, the orchestrator recommendation, and a supporting explanation.
6. **Approval.** The operator reviews the recommendation and approves the relevant action.
7. **Field dispatch.** The approved order becomes available to the field crew through the field application.

This demonstration should show that the system can coordinate multiple incidents rather than treating every failure as an isolated event.

## 17. Testing and reliability requirements

The system must be testable and explainable. At minimum, include:

**Monitoring tests**
- Normal readings should not create false incidents unnecessarily.
- Abnormal readings should be identified.
- Repeated readings from one ongoing incident should not generate duplicate incidents.
- Different asset types should use appropriate monitoring logic.

**Agent tests**
- Cost agent returns valid structured costs.
- Environmental agent returns evidence-based assessments.
- Missing information is handled explicitly.
- Agent errors are surfaced rather than hidden.

**Orchestrator tests**
- All available agent results are considered.
- Missing agents are identified.
- The orchestrator does not invent missing assessments.
- Invalid interventions are not recommended.
- The cheapest option is not automatically selected if it violates a hard constraint.
- The final explanation matches the actual decision logic.

**Integration tests**
- API responses match the frontend contract.
- Generated TypeScript types remain synchronized.
- Sentinel streaming works.
- Approval requests update action status.
- Dispatches are only created after approval.

## 18. How Claude should work with the team

Follow these collaboration rules:
- Do not assume the frontend is empty.
- Do not change frontend code unless explicitly requested.
- Do not rename API fields without coordinating with the frontend developer.
- Do not modify generated TypeScript types by hand.
- Keep the existing API contract compatible.
- Explain every significant architectural decision.
- Implement one phase at a time.
- After each phase, list the files changed.
- Run relevant tests after making changes.
- Report what is complete, what is incomplete, and what still needs implementation.
- If a required field or model is missing, identify it and propose the smallest compatible addition.
- Do not invent operational data to make a demo appear successful.
- Use mock data transparently when real integrations are unavailable.
- Keep future agents extensible without pretending they are already implemented.

## 19. MVP priorities

The team has limited development time. Prioritize the features below.

| Component | MVP priority |
|---|---|
| Continuous telemetry monitoring | Essential |
| Anomaly detection | Essential |
| Incident creation | Essential |
| Tank shutdown countdown | Essential |
| Cost-Effectiveness Agent | Essential |
| Environmental Impact Agent | Essential |
| Central orchestrator | Essential |
| Intervention comparison | Essential |
| Recommendation explanation | Essential |
| Operator approval | Essential |
| Sentinel progress stream | Essential |
| Field dispatch integration | High |
| Feasibility Agent | Future |
| Production Impact Agent | Future |
| Advanced model training | Only if justified by data and time |

The architecture should support the complete four-agent design even though the MVP implements only two specialized agents.

## 20. Final expectation

The final backend should operate as one coordinated intelligent system. The intended architecture is:

```
TELEMETRY INPUT
      |
      v
CONTINUOUS MONITORING
      |
      v
ANOMALY DETECTION
      |
      v
INCIDENT CREATION
      |
      v
PROBLEM-SOLVING WORKFLOW
      |
      +-----------------+-----------------+-----------------+
      |                 |                 |                 |
      v                 v                 v                 v
  COST AGENT     ENVIRONMENT AGENT  FEASIBILITY AGENT  PRODUCTION AGENT
                                    (future phase)     (future phase)
      |                 |                 |                 |
      +-----------------+-----------------+-----------------+
                        |
                        v
               CENTRAL ORCHESTRATOR
                        |
                        v
               COMPARE ALL FINDINGS
                        |
                        v
               SELECT BEST SOLUTION
                        |
                        v
             EXPLAIN RECOMMENDATION
                        |
                        v
                  HUMAN APPROVAL
                        |
                        v
                  FIELD DISPATCH
```

The most important architectural requirement:

**The monitoring system detects the problem. The specialized agents investigate the problem from their individual
perspectives. The orchestrator evaluates their findings and determines the best overall solution. The operator
receives the recommendation and decides whether to approve it.**

The goal is not simply to build an AI chatbot.

The goal is to build a coordinated, evidence-based, multi-agent decision-support system that can monitor an oilfield
water network, identify developing problems, evaluate intervention strategies, compare trade-offs, and recommend an
appropriate response.

Start by inspecting the existing repository and reporting the current implementation status. Then proceed through
the phases in order, prioritizing the working MVP over unnecessary complexity.

---

**Important:** before implementing anything, compare this architecture specification against the actual repository,
especially CLAUDE.md, SPEC.md, `backend/nightwatch/models.py`, the existing API routes, and
`frontend/src/api/client.ts`.

The existing repository and frontend contract take priority over any example architecture or JSON schema in this document.

Do not duplicate existing functionality, replace existing models, rename API fields, or create incompatible endpoints.

First report:
1. What is already implemented.
2. What is partially implemented.
3. What is missing.
4. How the proposed multi-agent architecture fits into the existing code.
5. What changes are necessary for the MVP.

Wait for confirmation before making major architectural changes.
