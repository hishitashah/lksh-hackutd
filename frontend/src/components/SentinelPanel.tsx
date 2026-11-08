/**
 * SentinelPanel (task T6.9): the live multi-agent run, streamed step by step.
 *
 * Shows what the AI system is doing and why (ARCHITECTURE.md §11): triage -> the specialist agents (cost,
 * environment, feasibility, production) -> the orchestrator -> the drafted actions. Agents that did not run
 * are greyed out as "not implemented" and never shown as done (CLAUDE.md rule 5). The final recommendation
 * says whether Nemotron or the rule-based fallback wrote its explanation (`llm_used`).
 *
 * Data: src/state/sentinel.ts (filled by the POST /agent/run stream; steps are replaced by `index`).
 * A run starts automatically once per dataset; the button re-runs it at the time-machine position.
 */
import { CheckCircle2, CircleDashed, Clock, Loader2, TriangleAlert } from "lucide-react";

import type { AgentStep } from "@/api/client";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { cn, formatUsd } from "@/lib/utils";
import { useSentinel, useEnsureSentinelRun } from "@/state/sentinel";
import { useActiveDatasetId, useConsole } from "@/state/store";

/** Icon for a step status: running, done, not_implemented, error or waiting_approval. */
function StepIcon({ status }: { status: string }) {
  if (status === "running") return <Loader2 className="size-4 animate-spin text-primary" />;
  if (status === "done") return <CheckCircle2 className="size-4 text-ok" />;
  if (status === "waiting_approval") return <Clock className="size-4 text-warning" />;
  if (status === "error") return <TriangleAlert className="size-4 text-destructive" />;
  return <CircleDashed className="size-4 text-muted-foreground" />;
}

/** One step: title, message, tool calls and (for specialist agents) the findings behind the answer. */
function StepRow({ step }: { step: AgentStep }) {
  const a = step.assessment;
  return (
    <li className={cn("flex gap-2.5 rounded-md border border-border p-2.5", step.status === "not_implemented" && "opacity-50")}>
      <span className="mt-0.5">
        <StepIcon status={step.status} />
      </span>
      <div className="flex min-w-0 flex-1 flex-col gap-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-sm font-medium">{step.title}</span>
          {step.status === "not_implemented" && <Badge variant="outline">not implemented</Badge>}
          {step.grounded && <Badge variant="ok">grounded</Badge>}
          {a?.llm_used && <Badge>Nemotron</Badge>}
        </div>
        <p className="text-xs leading-relaxed text-muted-foreground">{step.message}</p>
        {step.tool_calls.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {step.tool_calls.map((c, i) => (
              <Badge key={i} variant="outline" title={c.result_summary}>
                {c.name}
              </Badge>
            ))}
          </div>
        )}
        {a && a.status === "completed" && a.findings.length > 0 && (
          <details className="text-xs text-muted-foreground">
            <summary className="cursor-pointer text-primary">Findings and caveats</summary>
            <ul className="mt-1 list-disc space-y-1 pl-4">
              {a.findings.slice(0, 6).map((f) => (
                <li key={f}>{f}</li>
              ))}
              {a.uncertainties.slice(0, 3).map((u) => (
                <li key={u} className="italic">
                  Uncertain: {u}
                </li>
              ))}
            </ul>
          </details>
        )}
      </div>
    </li>
  );
}

/** The Sentinel run: steps, then the orchestrator's recommendation. */
export function SentinelPanel({ className }: { className?: string }) {
  useEnsureSentinelRun();
  const ds = useActiveDatasetId();
  const at = useConsole((s) => s.atUnix);
  const { running, error, steps, recommendation, start } = useSentinel();
  const ordered = Object.values(steps).sort((a, b) => a.index - b.index);

  return (
    <Card className={className}>
      <CardHeader>
        <div className="flex items-center justify-between gap-2">
          <CardTitle>Sentinel</CardTitle>
          <Button size="sm" variant="outline" disabled={!ds || running} onClick={() => ds && start(ds, at)}>
            {running ? "Investigating…" : at !== null ? "Re-run at selected time" : "Re-run"}
          </Button>
        </div>
        <CardDescription>AI agents investigate, the orchestrator decides, you approve</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {error && <p className="text-sm text-destructive">Run failed: {error}</p>}
        {!error && ordered.length === 0 && <p className="text-sm text-muted-foreground">Starting…</p>}
        <ul className="flex flex-col gap-2">
          {ordered.map((s) => (
            <StepRow key={s.index} step={s} />
          ))}
        </ul>

        {recommendation && (
          <div className="flex flex-col gap-2 rounded-md border border-primary/60 p-3">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-sm font-semibold">Recommendation</span>
              <Badge variant={recommendation.status === "completed" ? "ok" : "warning"}>{recommendation.status}</Badge>
              {recommendation.environmental_risk && (
                <Badge variant="outline">Environmental risk: {recommendation.environmental_risk}</Badge>
              )}
              <Badge variant={recommendation.llm_used ? "default" : "outline"}>
                {recommendation.llm_used ? "Written by Nemotron" : "Rule-based text (Nemotron off)"}
              </Badge>
            </div>
            {recommendation.selected_plans.length > 0 && (
              <p className="font-mono text-sm">
                {formatUsd(recommendation.estimated_cost_usd)}
                <span className="text-xs text-muted-foreground"> planned · cheapest possible {formatUsd(recommendation.cheapest_cost_usd)}</span>
              </p>
            )}
            <p className="text-xs leading-relaxed">{recommendation.explanation}</p>
            {recommendation.tradeoffs.length > 0 && (
              <ul className="list-disc space-y-1 pl-4 text-xs text-muted-foreground">
                {recommendation.tradeoffs.map((t) => (
                  <li key={t}>{t}</li>
                ))}
              </ul>
            )}
            {recommendation.missing_assessments.length > 0 && (
              <p className="text-xs text-warning">
                Not assessed: {recommendation.missing_assessments.join(", ")}. These analyses are not implemented, so they are
                not assumed.
              </p>
            )}
            <details className="text-xs text-muted-foreground">
              <summary className="cursor-pointer text-primary">Decision policy</summary>
              <p className="mt-1">{recommendation.decision_policy}</p>
            </details>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
