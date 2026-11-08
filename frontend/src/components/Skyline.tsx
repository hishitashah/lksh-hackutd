/**
 * Skyline (task T6.12): the city of Avon at a glance, with a live "$ lost per hour" ticker.
 *
 * One district per system, one building per well. A building's windows are lit while that well produces and
 * go dark as wells shut in, so a tripping tank visibly dims its district. The ticker is the revenue lost right
 * now from shut-in wells, exactly as the API reports it (`lost_revenue_per_hour_total`).
 *
 * Data: useNetworkState(ds, at) -> facilities[] ({system_id, wells_producing, wells_total}) and the total.
 * No system or well counts are hardcoded: any network draws its own skyline. Building heights are only
 * decoration (a repeating pattern), they carry no data.
 */
import { useNetworkState } from "@/api/client";
import { Card, CardContent } from "@/components/ui/card";
import { cn, formatUsd } from "@/lib/utils";
import { useActiveDatasetId, useConsole } from "@/state/store";

/** Decorative building heights (px), repeated so neighbouring buildings differ. */
const HEIGHTS = [34, 52, 40, 62, 46, 56, 38];

/** One building: lit when its well is producing. */
function Building({ lit, index }: { lit: boolean; index: number }) {
  return (
    <span
      className={cn("w-4 rounded-t-sm border border-b-0 transition-colors duration-700", lit ? "border-primary/40 bg-primary/25" : "border-border bg-muted/40")}
      style={{ height: HEIGHTS[index % HEIGHTS.length] }}
    >
      <span className={cn("m-0.5 block h-1 rounded-[1px] transition-colors duration-700", lit ? "bg-warning shadow-[0_0_6px_var(--warning)]" : "bg-border")} />
      <span className={cn("m-0.5 mt-1 block h-1 rounded-[1px] transition-colors duration-700", lit ? "bg-warning/80" : "bg-border")} />
    </span>
  );
}

/** The skyline strip across the top of the console. */
export function Skyline({ className }: { className?: string }) {
  const ds = useActiveDatasetId();
  const at = useConsole((s) => s.atUnix);
  const { data } = useNetworkState(ds, at);

  // Group facilities by system, in the order the API lists them.
  const districts = new Map<string, { producing: number; total: number }[]>();
  for (const f of data?.facilities ?? []) {
    const list = districts.get(f.system_id) ?? [];
    list.push({ producing: f.wells_producing, total: f.wells_total });
    districts.set(f.system_id, list);
  }
  const lost = data?.lost_revenue_per_hour_total ?? 0;

  return (
    <Card className={className}>
      <CardContent className="flex flex-wrap items-end justify-between gap-4 p-4">
        <div className="flex flex-wrap items-end gap-6">
          {[...districts.entries()].map(([systemId, facilities]) => {
            let n = 0;
            return (
              <div key={systemId} className="flex flex-col gap-1">
                <div className="flex items-end gap-0.5 border-b border-border">
                  {facilities.flatMap((f) =>
                    Array.from({ length: f.total }, (_, w) => <Building key={`${systemId}-${n}`} lit={w < f.producing} index={n++} />),
                  )}
                </div>
                <span className="font-mono text-[10px] text-muted-foreground">{systemId}</span>
              </div>
            );
          })}
        </div>
        <div className="flex flex-col items-end">
          <span className={cn("font-mono text-3xl font-semibold", lost > 0 ? "text-destructive" : "text-ok")}>
            {formatUsd(lost)}
            <span className="text-sm font-normal text-muted-foreground"> / hour</span>
          </span>
          <span className="text-xs text-muted-foreground">{lost > 0 ? "revenue lost to shut-in wells" : "no revenue lost right now"}</span>
        </div>
      </CardContent>
    </Card>
  );
}
