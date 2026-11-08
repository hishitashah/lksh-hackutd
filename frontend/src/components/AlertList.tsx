/**
 * AlertList (wired in T0.1; styled and finished in T6.4).
 *
 * Shows every ongoing problem in the alerts-view order the API returns (SPEC "Alerts view": pump
 * failures first, soonest shutdown first; then leaks and worn pumps). The frontend never re-sorts:
 * the order comes from the backend (alerts.py, T2.7).
 *
 * Data: useNetworkState(dataset, time-machine position) -> alerts[].
 * Click an alert to select it; the plan panel and trend drawer follow the selection (store).
 *
 * This is the M0 end-to-end proof: backend fixture -> FastAPI -> generated types -> this list.
 */
import { CountdownRing } from "@/components/CountdownRing";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { type Alert, useNetworkState } from "@/api/client";
import { cn } from "@/lib/utils";
import { useActiveDatasetId, useConsole } from "@/state/store";

/** Human label + badge color for each problem category. */
const CATEGORY: Record<Alert["event"]["category"], { label: string; variant: "destructive" | "warning" | "accent" | "outline" }> = {
  pump_failure: { label: "Pump failure", variant: "destructive" },
  pipeline_leak: { label: "Leak", variant: "warning" },
  pump_degradation: { label: "Pump wearing out", variant: "accent" },
  other: { label: "Other", variant: "outline" },
};

/** The ordered list of ongoing alerts, with loading and error states. */
export function AlertList({ className }: { className?: string }) {
  const ds = useActiveDatasetId();
  const at = useConsole((s) => s.atUnix);
  const { data, isLoading, error } = useNetworkState(ds, at);

  return (
    <Card className={className}>
      <CardHeader>
        <CardTitle>Alerts</CardTitle>
        <CardDescription>
          {data ? `${data.alerts.length} ongoing · pump failures first, soonest shutdown first` : "Ongoing problems"}
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-2">
        {isLoading && <p className="text-sm text-muted-foreground">Loading…</p>}
        {error && <p className="text-sm text-destructive">Could not load alerts: {error.message}</p>}
        {data?.alerts.length === 0 && <p className="text-sm text-ok">All clear.</p>}
        {data?.alerts.map((alert) => <AlertRow key={alert.event.id} alert={alert} />)}
      </CardContent>
    </Card>
  );
}

/** One alert: category, where it is, countdown or confidence, and the recommended action. */
function AlertRow({ alert }: { alert: Alert }) {
  const selected = useConsole((s) => s.selectedEventId === alert.event.id);
  const selectEvent = useConsole((s) => s.selectEvent);
  const selectAsset = useConsole((s) => s.selectAsset);
  const { event } = alert;
  const category = CATEGORY[event.category];

  return (
    <button
      type="button"
      onClick={() => {
        selectEvent(event.id);
        selectAsset(event.asset_id);
      }}
      className={cn(
        "flex w-full items-center gap-3 rounded-md border border-border p-3 text-left transition-colors hover:bg-muted",
        selected && "border-primary bg-muted",
      )}
    >
      {/* Pump failures show the countdown; slow problems show detector confidence instead. */}
      {event.hours_to_limit ? (
        <CountdownRing hours={event.hours_to_limit.p50} />
      ) : (
        <span className="w-14 text-center font-mono text-xs text-muted-foreground">
          {Math.round(event.confidence * 100)}%
        </span>
      )}
      <span className="flex min-w-0 flex-1 flex-col gap-1">
        <span className="flex items-center gap-2">
          {alert.order != null && <span className="font-mono text-xs text-muted-foreground">#{alert.order}</span>}
          <Badge variant={category.variant}>{category.label}</Badge>
          <span className="truncate text-sm font-medium">{event.facility_id}</span>
        </span>
        <span className="truncate text-xs text-muted-foreground">{alert.action_label}</span>
      </span>
    </button>
  );
}
