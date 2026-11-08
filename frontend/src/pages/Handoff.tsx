/**
 * Shift handoff page (route "/handoff", tasks T5.3 + T6.9).
 *
 * Shows the plain-English handoff the next operator can read in 30 seconds. It is built by the backend from the
 * latest Sentinel run (every number in it comes from that run), so this page only displays it, plus the plan
 * the orchestrator selected and how many actions still wait for approval.
 * The events.json download is not offered: that route is cut from the demo and answers 501 with mock mode off.
 */
import { Copy } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { formatUsd } from "@/lib/utils";
import { useEnsureSentinelRun, useSentinel } from "@/state/sentinel";

/** The 30-second handoff. */
export function Handoff() {
  useEnsureSentinelRun();
  const { handoff, recommendation, drafts, running, error } = useSentinel();
  const [copied, setCopied] = useState(false);
  const waiting = drafts.filter((d) => d.status === "pending").length;

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-4 p-4">
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between gap-2">
            <CardTitle>Shift handoff</CardTitle>
            <Button
              size="sm"
              variant="outline"
              disabled={!handoff}
              onClick={async () => {
                if (!handoff) return;
                await navigator.clipboard.writeText(handoff);
                setCopied(true);
                setTimeout(() => setCopied(false), 1500);
              }}
            >
              <Copy className="size-4" /> {copied ? "Copied" : "Copy"}
            </Button>
          </div>
          <CardDescription>What the next operator needs to know, from Sentinel's latest run</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          {error && <p className="text-sm text-destructive">Could not build the handoff: {error}</p>}
          {!handoff && !error && <p className="text-sm text-muted-foreground">{running ? "Sentinel is investigating…" : "No handoff yet."}</p>}
          {handoff && <p className="text-lg leading-relaxed">{handoff}</p>}
          {recommendation && recommendation.selected_plans.length > 0 && (
            <ul className="flex flex-col gap-1.5 border-t border-border pt-3 text-sm">
              {recommendation.selected_plans.map((p) => (
                <li key={p.event_ids.join()} className="flex justify-between gap-3">
                  <span>{p.label}</span>
                  <span className="shrink-0 font-mono">{formatUsd(p.cost_total)}</span>
                </li>
              ))}
            </ul>
          )}
          {waiting > 0 && (
            <p className="text-sm text-warning">
              {waiting} action{waiting === 1 ? "" : "s"} still waiting for approval on the console.
            </p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
