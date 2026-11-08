/**
 * TrendDrawer (task T6.6): the sensor history of the selected asset.
 *
 * Pick an asset (click an alert or a facility) and choose one of its sensors: the chart plots the full
 * recording, shades the incident periods at that facility (red pump failure, amber leak, pink worn pump),
 * marks the time-machine position, and draws the tank warning / shutdown limits on tank level (limits come
 * from network.json, never typed in). Zoom with the mouse wheel or the slider underneath.
 *
 * Which sensors an asset has comes from where it sits in network.json (tank, pump, pipeline, well,
 * facility): asset IDs are never parsed. Metric names are from SPEC "telemetry.csv".
 * Data: useNetwork(ds), useSeries(ds, asset, metric), useNetworkState(ds, at) -> events for shading.
 */
import ReactECharts from "echarts-for-react";
import { useState } from "react";

import { type Event, useNetwork, useNetworkState, useSeries } from "@/api/client";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { themeColor } from "@/lib/describe";
import { useActiveDatasetId, useConsole } from "@/state/store";

/** Sensors per kind of asset (SPEC telemetry.csv). */
const METRICS = {
  tank: ["level_pct"],
  pump: ["status", "flow_bpd", "discharge_psi"],
  pipeline: ["receive_flow_bpd", "pressure_psi"],
  well: ["water_bpd", "status"],
  facility: ["inflow_bpd"],
} as const;

type Kind = keyof typeof METRICS;

/** Add a transparent alpha channel to a #rrggbb colour (for shaded incident periods). */
const faded = (hex: string) => (/^#[0-9a-f]{6}$/i.test(hex) ? `${hex}33` : hex);

/** Everything the chart needs, already looked up. */
interface TrendInput {
  points: number[][];
  assetId: string;
  metric: string;
  kind: Kind;
  facilityId: string;
  events: Event[];
  /** Latest time shown by the console (end of ongoing incident bands), unix seconds. */
  nowUnix: number;
  /** Time-machine position, or null for "now". */
  at: number | null;
  /** Tank limits from network.json, in % full. */
  limits: { warning: number; shutdown: number } | undefined;
}

/** Build the ECharts option: the series, shaded incident bands and the marker lines. */
function trendOption(t: TrendInput) {
  const muted = themeColor("--muted-foreground", "#9ca3af");
  const grid = themeColor("--border", "#1f2937");
  const primary = themeColor("--primary", "#22d3ee");
  const danger = themeColor("--destructive", "#ef4444");
  const warn = themeColor("--warning", "#f59e0b");
  const accent = themeColor("--accent", "#f472b6");
  const colour = { pump_failure: danger, pipeline_leak: warn, pump_degradation: accent, other: muted };
  const shaded = t.events
    .filter((e) => e.facility_id === t.facilityId)
    .map((e) => [
      { xAxis: e.onset_unix * 1000, itemStyle: { color: faded(colour[e.category]) } },
      { xAxis: (e.end_unix ?? t.nowUnix) * 1000 },
    ]);
  const lines = [
    ...(t.at !== null ? [{ xAxis: t.at * 1000, name: "Selected time", lineStyle: { color: primary } }] : []),
    ...(t.kind === "tank" && t.metric === "level_pct" && t.limits
      ? [
          { yAxis: t.limits.warning, name: "Warning", lineStyle: { color: warn, type: "dashed" } },
          { yAxis: t.limits.shutdown, name: "Shutdown", lineStyle: { color: danger, type: "dashed" } },
        ]
      : []),
  ];
  return {
    useUTC: true,
    backgroundColor: "transparent",
    animation: false,
    grid: { left: 52, right: 16, top: 16, bottom: 56 },
    tooltip: { trigger: "axis" },
    dataZoom: [{ type: "inside" }, { type: "slider", height: 16, bottom: 6, borderColor: grid }],
    xAxis: { type: "time", axisLabel: { color: muted }, axisLine: { lineStyle: { color: grid } } },
    yAxis: { type: "value", scale: true, axisLabel: { color: muted }, splitLine: { lineStyle: { color: grid } } },
    series: [
      {
        name: `${t.assetId} ${t.metric}`,
        type: "line",
        showSymbol: false,
        step: t.metric === "status" ? "end" : false,
        lineStyle: { width: 1.5, color: primary },
        data: t.points.map(([time, v]) => [time * 1000, v]),
        markArea: { silent: true, data: shaded },
        markLine: { silent: true, symbol: "none", label: { color: muted, formatter: "{b}" }, data: lines },
      },
    ],
  };
}

/** Sensor chart for the selected asset. */
export function TrendDrawer({ className }: { className?: string }) {
  const ds = useActiveDatasetId();
  const at = useConsole((s) => s.atUnix);
  const selected = useConsole((s) => s.selectedAssetId);
  const network = useNetwork(ds);
  const state = useNetworkState(ds, at);
  const [metricChoice, setMetricChoice] = useState<string | null>(null);

  // Where is every asset in the network? (kind + the facility it belongs to)
  const assets = new Map<string, { kind: Kind; facilityId: string }>();
  for (const sys of network.data?.network.systems ?? []) {
    for (const f of sys.facilities) {
      assets.set(f.id, { kind: "facility", facilityId: f.id });
      assets.set(f.tank.id, { kind: "tank", facilityId: f.id });
      assets.set(f.pump.id, { kind: "pump", facilityId: f.id });
      assets.set(f.pipeline.id, { kind: "pipeline", facilityId: f.id });
      for (const w of f.wells) assets.set(w.id, { kind: "well", facilityId: f.id });
    }
  }

  // Default to the most urgent alert's pump or pipeline until the operator picks something.
  const assetId = selected ?? state.data?.alerts[0]?.event.asset_id ?? null;
  const info = assetId ? assets.get(assetId) : undefined;
  const metrics: readonly string[] = info ? METRICS[info.kind] : [];
  const metric = metricChoice && metrics.includes(metricChoice) ? metricChoice : (metrics[0] ?? "");
  const series = useSeries(ds, info ? assetId : null, metric);

  const option =
    series.data && info && assetId
      ? trendOption({
          points: series.data.points,
          assetId,
          metric,
          kind: info.kind,
          facilityId: info.facilityId,
          events: state.data?.events ?? [],
          nowUnix: state.data?.at_unix ?? 0,
          at,
          limits: network.data?.network.defaults.tank_limits_pct,
        })
      : null;

  return (
    <Card className={className}>
      <CardHeader>
        <CardTitle>Trends{assetId ? `: ${assetId}` : ""}</CardTitle>
        <CardDescription>Sensor history of the selected asset (UTC). Shaded bands are incidents at its facility.</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {!assetId && <p className="text-sm text-muted-foreground">Click an alert or a facility to see its sensors.</p>}
        <div className="flex flex-wrap gap-2">
          {metrics.map((m) => (
            <Button key={m} size="sm" variant={m === metric ? "default" : "outline"} onClick={() => setMetricChoice(m)}>
              {m}
            </Button>
          ))}
        </div>
        {series.isLoading && <p className="text-sm text-muted-foreground">Loading…</p>}
        {series.error && <p className="text-sm text-destructive">Could not load the series: {series.error.message}</p>}
        {option && <ReactECharts option={option} style={{ height: 260 }} notMerge />}
      </CardContent>
    </Card>
  );
}
