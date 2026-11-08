/**
 * TankWhatIf (task T6.7): the selected tank's level over time, "do nothing" versus the recommended plan.
 *
 * The planner simulates both exactly; this chart only draws those traces. The warning and shutdown lines come
 * from the traces (they are network.json's tank limits), never from typed-in numbers. Times are UTC.
 *
 * Data: usePlan(ds, eventId, at) -> traces[] ({label, points [[unix, level %]], warning_pct, shutdown_pct}).
 * Built with echarts-for-react.
 */
import ReactECharts from "echarts-for-react";
import { useMemo } from "react";

import { usePlan } from "@/api/client";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { themeColor } from "@/lib/describe";
import { useActiveEvent } from "@/state/store";

/** Level-over-time chart with the two tank limits. */
export function TankWhatIf({ className }: { className?: string }) {
  const { ds, at, event } = useActiveEvent();
  const isFailure = event?.category === "pump_failure";
  const { data, isLoading } = usePlan(ds, isFailure && event ? event.id : null, at);

  const option = useMemo(() => {
    if (!data || data.traces.length === 0) return null;
    const muted = themeColor("--muted-foreground", "#9ca3af");
    const grid = themeColor("--border", "#1f2937");
    const warning = themeColor("--warning", "#f59e0b");
    const danger = themeColor("--destructive", "#ef4444");
    const palette = [danger, themeColor("--ok", "#22c55e"), themeColor("--primary", "#22d3ee")];
    const { warning_pct, shutdown_pct } = data.traces[0];
    return {
      useUTC: true,
      backgroundColor: "transparent",
      animation: false,
      grid: { left: 44, right: 24, top: 34, bottom: 28 },
      legend: { top: 0, textStyle: { color: muted } },
      tooltip: {
        trigger: "axis",
        valueFormatter: (v: number) => `${Number(v).toFixed(1)}%`,
      },
      xAxis: {
        type: "time",
        axisLabel: { color: muted, hideOverlap: true },
        axisLine: { lineStyle: { color: grid } },
      },
      yAxis: {
        type: "value",
        min: 0,
        max: 100,
        axisLabel: { color: muted, formatter: "{value}%" },
        splitLine: { lineStyle: { color: grid } },
      },
      series: data.traces.map((trace, i) => ({
        name: trace.label,
        type: "line",
        showSymbol: false,
        lineStyle: { width: 2.5, color: palette[i % palette.length] },
        itemStyle: { color: palette[i % palette.length] },
        data: trace.points.map(([t, level]) => [t * 1000, level]),
        // Draw the limits once, on the first trace.
        markLine:
          i === 0
            ? {
                silent: true,
                symbol: "none",
                label: { color: muted, formatter: "{b}", position: "insideEndTop" },
                data: [
                  { yAxis: warning_pct, name: "Warning", lineStyle: { color: warning, type: "dashed" } },
                  { yAxis: shutdown_pct, name: "Shutdown", lineStyle: { color: danger, type: "dashed" } },
                ],
              }
            : undefined,
      })),
    };
  }, [data]);

  return (
    <Card className={className}>
      <CardHeader>
        <CardTitle>What-if: tank level</CardTitle>
        <CardDescription>Do nothing vs the recommended plan, until the repair ends (UTC)</CardDescription>
      </CardHeader>
      <CardContent>
        {!isFailure && <p className="text-sm text-muted-foreground">Select a pump failure to see its tank.</p>}
        {isFailure && isLoading && <p className="text-sm text-muted-foreground">Loading…</p>}
        {option && <ReactECharts option={option} style={{ height: 240 }} notMerge />}
      </CardContent>
    </Card>
  );
}
