/**
 * ApprovalQueue (task T6.9 + T5.5): the actions Sentinel drafted, waiting for the operator.
 *
 * Nothing is dispatched until the operator presses Approve (CLAUDE.md rule 6). Approve sends the order to the
 * field crew's phones at once (status "dispatched"); Reject keeps it from ever reaching the field.
 * If the backend refuses (409: already dispatched, or more trucks than the system has) its `detail` text is shown
 * under the card and the draft stays pending.
 *
 * Data: drafts + runId from src/state/sentinel.ts; useApprove() posts the decision. The QR code opens the
 * phone view (/field) on the field crew's device.
 */
import { Check, Search, Truck, Droplets, X } from "lucide-react";
import { QRCodeSVG } from "qrcode.react";
import { useState } from "react";

import { useApprove } from "@/api/client";
import type { DraftAction } from "@/api/client";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useSentinel } from "@/state/sentinel";
import { useActiveDatasetId } from "@/state/store";

/** Badge colour per draft status. */
const STATUS_VARIANT: Record<string, "ok" | "warning" | "destructive" | "outline"> = {
  pending: "warning",
  dispatched: "ok",
  approved: "ok",
  rejected: "destructive",
  completed: "ok",
};

/** Icon for a draft's kind. */
function KindIcon({ kind }: { kind: DraftAction["kind"] }) {
  if (kind === "dispatch_truck") return <Truck className="size-4 text-primary" />;
  if (kind === "shut_in_wells") return <Droplets className="size-4 text-warning" />;
  return <Search className="size-4 text-muted-foreground" />;
}

/** The drafted actions with Approve / Reject buttons. */
export function ApprovalQueue({ className }: { className?: string }) {
  const ds = useActiveDatasetId();
  const { drafts, runId, running, setDraft } = useSentinel();
  const approve = useApprove(ds);
  const [errors, setErrors] = useState<Record<string, string>>({});
  // The QR opens the phone view on the same dataset (the phone has its own browser state, so it needs ?ds=).
  const fieldUrl = `${window.location.origin}/field${ds ? `?ds=${encodeURIComponent(ds)}` : ""}`;

  /** Send the operator's decision; show the backend's reason if it refuses. */
  async function decide(draft: DraftAction, yes: boolean) {
    if (!runId) return;
    setErrors((e) => ({ ...e, [draft.id]: "" }));
    try {
      setDraft(await approve.mutateAsync({ run_id: runId, action_id: draft.id, approve: yes }));
    } catch (err) {
      setErrors((e) => ({ ...e, [draft.id]: err instanceof Error ? err.message : String(err) }));
    }
  }

  return (
    <Card className={className}>
      <CardHeader>
        <CardTitle>Approval queue</CardTitle>
        <CardDescription>Nothing reaches the field crew until you approve it</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {drafts.length === 0 && (
          <p className="text-sm text-muted-foreground">{running ? "Waiting for Sentinel…" : "No actions to approve."}</p>
        )}
        <ul className="flex flex-col gap-2">
          {drafts.map((d) => (
            <li key={d.id} className="flex flex-col gap-2 rounded-md border border-border p-2.5">
              <div className="flex items-start gap-2">
                <KindIcon kind={d.kind} />
                <span className="flex-1 text-sm leading-snug">{d.label}</span>
                <Badge variant={STATUS_VARIANT[d.status] ?? "outline"}>{d.status}</Badge>
              </div>
              {d.status === "pending" && (
                <div className="flex gap-2">
                  <Button size="sm" disabled={!runId || approve.isPending} onClick={() => decide(d, true)}>
                    <Check className="size-4" /> Approve
                  </Button>
                  <Button size="sm" variant="outline" disabled={!runId || approve.isPending} onClick={() => decide(d, false)}>
                    <X className="size-4" /> Reject
                  </Button>
                </div>
              )}
              {errors[d.id] && <p className="text-xs text-destructive">{errors[d.id]}</p>}
            </li>
          ))}
        </ul>
        <div className="flex items-center gap-3 border-t border-border pt-3">
          <div className="rounded-md bg-white p-1.5">
            <QRCodeSVG value={fieldUrl} size={72} />
          </div>
          <p className="text-xs text-muted-foreground">
            Field crew: scan to open the live order feed on a phone ({window.location.host}/field).
          </p>
        </div>
      </CardContent>
    </Card>
  );
}
