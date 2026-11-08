/**
 * ShowYourWork (task T6.5): every plan the planner checked for the selected pump failure.
 *
 * Proves the recommendation is the cheapest: the chosen plan first, then the next-cheapest alternatives,
 * then "do nothing", each with its cost and whether the tank still trips. Labels such as
 * "needs a truck assigned to another failure" come from the backend, which knows the shared fleet.
 *
 * Data: usePlan(ds, eventId, at) -> cheapest, alternatives (already sorted by cost), do_nothing.
 * The bars only scale each cost to the largest one shown (display); no plan or cost is computed here.
 */
import { usePlan } from "@/api/client";
import type { Plan } from "@/api/client";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { cn, formatUsd } from "@/lib/utils";
import { useActiveEvent } from "@/state/store";

/** One plan row: label, tank outcome and a cost bar. */
function PlanRow({ plan, widest, tone }: { plan: Plan; widest: number; tone: "chosen" | "other" | "bad" }) {
  const width = widest > 0 ? Math.max(4, (plan.cost_total / widest) * 100) : 4;
  return (
    <li className={cn("flex flex-col gap-1.5 rounded-md border border-border p-2.5", tone === "chosen" && "border-primary")}>
      <div className="flex items-start justify-between gap-2">
        <span className="text-xs leading-snug">{plan.label}</span>
        <span className="shrink-0 font-mono text-sm">{formatUsd(plan.cost_total)}</span>
      </div>
      <div className="h-1.5 rounded-full bg-muted">
        <div
          className={cn("h-full rounded-full", tone === "chosen" ? "bg-primary" : tone === "bad" ? "bg-destructive" : "bg-muted-foreground")}
          style={{ width: `${width}%` }}
        />
      </div>
      <div className="flex gap-2">
        {tone === "chosen" && <Badge>Recommended</Badge>}
        {plan.tank_trips ? <Badge variant="destructive">Tank trips</Badge> : <Badge variant="ok">Stays under limit</Badge>}
      </div>
    </li>
  );
}

/** The list of options the planner compared. */
export function ShowYourWork({ className }: { className?: string }) {
  const { ds, at, event } = useActiveEvent();
  const isFailure = event?.category === "pump_failure";
  const { data, isLoading } = usePlan(ds, isFailure && event ? event.id : null, at);

  const rows = data ? [data.cheapest, ...data.alternatives, data.do_nothing] : [];
  const widest = rows.reduce((max, p) => Math.max(max, p.cost_total), 0);

  return (
    <Card className={className}>
      <CardHeader>
        <CardTitle>Show your work</CardTitle>
        <CardDescription>Every option the planner checked for this pump failure, cheapest first</CardDescription>
      </CardHeader>
      <CardContent>
        {!isFailure && <p className="text-sm text-muted-foreground">Select a pump failure to compare its options.</p>}
        {isFailure && isLoading && <p className="text-sm text-muted-foreground">Loading…</p>}
        {data && (
          <ul className="flex flex-col gap-2">
            <PlanRow plan={data.cheapest} widest={widest} tone="chosen" />
            {data.alternatives.map((p) => (
              <PlanRow key={p.label} plan={p} widest={widest} tone="other" />
            ))}
            <PlanRow plan={data.do_nothing} widest={widest} tone="bad" />
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
