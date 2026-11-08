/**
 * Field view (route "/field"): the phone view for an inspector or truck driver (task T6.11).
 *
 * Built for a 375 px wide screen with big tap targets and large text. It shows:
 *   - the most urgent shutdown countdown on the network (from the alert list), and
 *   - the live order feed: approved orders appear within about a second of the operator pressing Approve.
 *
 * Live feed: useFieldFeed() loads GET /dispatches, then listens on GET /field/stream with the browser's built-in
 * EventSource (works on iPhone, no app or permission). The stream reconnects on its own.
 * Only operator-approved orders are ever in this feed.
 */
import { Droplets, Radio, Search, Truck } from "lucide-react";

import { useFieldFeed, useNetworkState } from "@/api/client";
import type { DraftAction } from "@/api/client";
import { CountdownRing } from "@/components/CountdownRing";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import { useActiveDatasetId } from "@/state/store";

/** Big icon for an order's kind. */
function OrderIcon({ kind }: { kind: DraftAction["kind"] }) {
  const cls = "size-8 shrink-0";
  if (kind === "dispatch_truck") return <Truck className={cn(cls, "text-primary")} />;
  if (kind === "shut_in_wells") return <Droplets className={cn(cls, "text-warning")} />;
  return <Search className={cn(cls, "text-accent")} />;
}

/** "10:32 UTC" from the wall-clock ISO timestamp the backend stamped when the order was sent. */
function sentAt(iso: string | null | undefined): string {
  return iso ? `${iso.slice(11, 16)} UTC` : "";
}

/** The phone page: urgent countdown + live orders. */
export function Field() {
  const ds = useActiveDatasetId();
  const { orders, connected, error } = useFieldFeed(ds);
  const state = useNetworkState(ds, null);
  const urgent = state.data?.alerts.find((a) => a.event.hours_to_limit) ?? null;

  return (
    <div className="mx-auto flex max-w-md flex-col gap-4 p-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">Field orders</h1>
        <Badge variant={connected ? "ok" : "outline"}>
          <Radio className="mr-1 size-3" />
          {connected ? "Live" : "Connecting…"}
        </Badge>
      </div>

      {urgent?.event.hours_to_limit && (
        <Card>
          <CardContent className="flex items-center gap-4 p-4">
            <CountdownRing hours={urgent.event.hours_to_limit.p50} />
            <div className="flex flex-col">
              <span className="text-xs text-muted-foreground">Next shutdown</span>
              <span className="text-lg font-semibold">{urgent.event.facility_id}</span>
            </div>
          </CardContent>
        </Card>
      )}

      {error && <p className="text-sm text-destructive">Could not load orders: {error.message}</p>}
      {orders.length === 0 && (
        <Card>
          <CardContent className="p-6 text-center text-muted-foreground">
            No orders yet. Approved actions appear here the moment the operator sends them.
          </CardContent>
        </Card>
      )}

      <ul className="flex flex-col gap-3">
        {orders.map((o) => (
          <li key={o.id}>
            <Card className="border-primary/50">
              <CardContent className="flex items-start gap-3 p-4">
                <OrderIcon kind={o.kind} />
                <div className="flex flex-col gap-2">
                  <span className="text-lg font-medium leading-snug">{o.label}</span>
                  <span className="text-xs text-muted-foreground">Sent {sentAt(o.dispatched_at)}</span>
                </div>
              </CardContent>
            </Card>
          </li>
        ))}
      </ul>
    </div>
  );
}
