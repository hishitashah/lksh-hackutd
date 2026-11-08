/**
 * PlanCard (task T6.5): the decision banner for the selected alert.
 *
 * Shows what to do NOW for the alert the operator picked (or the most urgent one):
 *   - pump failure  -> the cheapest valid plan from the planner (its shared-truck joint plan), its cost split,
 *                      whether the tank still trips, and the cost of doing nothing next to it;
 *   - leak / worn pump -> an inspection, with the detector's plain-English evidence and confidence.
 *
 * Data: useActiveEvent() (alert list) + usePlan(ds, eventId, at). Plans are only fetched for pump failures
 * (the API answers 400 for the others). Every number is printed exactly as the API sent it: this file does
 * no cost, plan or countdown math (CLAUDE.md rule 4); the two totals are shown side by side, never subtracted.
 */
import { Truck, Droplets, Search } from "lucide-react";

import { usePlan } from "@/api/client";
import type { Plan, PlanAction } from "@/api/client";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { describeAction } from "@/lib/describe";
import { formatUsd, formatUtcTime } from "@/lib/utils";
import { useActiveEvent } from "@/state/store";

/** Icon for one action type. */
function ActionIcon({ action }: { action: PlanAction }) {
  if (action.type === "dispatch_truck") return <Truck className="size-4 text-primary" />;
  if (action.type === "shut_in_wells") return <Droplets className="size-4 text-warning" />;
  return <Search className="size-4 text-muted-foreground" />;
}

/** One-line outcome for a plan: does the tank still reach shutdown? */
function TankOutcome({ plan }: { plan: Plan }) {
  if (plan.tank_trips) {
    return (
      <Badge variant="destructive">
        Tank still trips{plan.trips_at_unix ? ` at ${formatUtcTime(plan.trips_at_unix)} UTC` : ""}
      </Badge>
    );
  }
  return <Badge variant="ok">Tank stays under the limit · peak {plan.peak_level_pct.toFixed(1)}%</Badge>;
}

/** The decision banner. */
export function PlanCard({ className }: { className?: string }) {
  const { ds, at, event, alert, isLoading } = useActiveEvent();
  const isFailure = event?.category === "pump_failure";
  const plan = usePlan(ds, isFailure && event ? event.id : null, at);

  return (
    <Card className={className}>
      <CardHeader>
        <CardTitle>Do this now</CardTitle>
        <CardDescription>
          {event ? `${event.facility_id} · ${event.asset_id}` : "The recommended action for the selected alert"}
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {isLoading && <p className="text-sm text-muted-foreground">Loading…</p>}
        {!isLoading && !event && <p className="text-sm text-ok">All clear: nothing needs action.</p>}

        {event && !isFailure && (
          <>
            <div className="flex items-center gap-2 text-sm font-medium">
              <Search className="size-4 text-warning" />
              {alert?.action_label}
            </div>
            <ul className="list-disc space-y-1 pl-5 text-xs text-muted-foreground">
              {event.evidence.map((line) => (
                <li key={line}>{line}</li>
              ))}
            </ul>
            <Badge variant="outline">Detector confidence {Math.round(event.confidence * 100)}%</Badge>
          </>
        )}

        {isFailure && plan.isLoading && <p className="text-sm text-muted-foreground">Planning…</p>}
        {isFailure && plan.error && <p className="text-sm text-destructive">Could not load the plan: {plan.error.message}</p>}

        {plan.data && (
          <>
            <ul className="flex flex-col gap-1.5">
              {plan.data.cheapest.actions.map((a, i) => (
                <li key={i} className="flex items-center gap-2 text-sm font-medium">
                  <ActionIcon action={a} />
                  {describeAction(a)}
                </li>
              ))}
            </ul>
            <div className="flex items-baseline justify-between gap-2">
              <span className="font-mono text-3xl font-semibold text-primary">{formatUsd(plan.data.cheapest.cost_total)}</span>
              <span className="text-right text-xs text-muted-foreground">cheapest valid plan</span>
            </div>
            <div className="flex flex-wrap gap-2">
              <TankOutcome plan={plan.data.cheapest} />
              {plan.data.cheapest.cost_breakdown.trucks > 0 && (
                <Badge variant="outline">Trucks {formatUsd(plan.data.cheapest.cost_breakdown.trucks)}</Badge>
              )}
              {plan.data.cheapest.cost_breakdown.shut_in > 0 && (
                <Badge variant="outline">Shut-ins {formatUsd(plan.data.cheapest.cost_breakdown.shut_in)}</Badge>
              )}
              {plan.data.cheapest.cost_breakdown.trip > 0 && (
                <Badge variant="outline">Tank trip {formatUsd(plan.data.cheapest.cost_breakdown.trip)}</Badge>
              )}
            </div>
            <div className="flex items-center justify-between rounded-md border border-border px-3 py-2 text-xs">
              <span className="text-muted-foreground">If nothing is done</span>
              <span className="font-mono text-destructive">{formatUsd(plan.data.do_nothing.cost_total)}</span>
            </div>
          </>
        )}
      </CardContent>
    </Card>
  );
}
