/**
 * NetworkMap (task T6.3): every system on a dark map.
 *
 * Draws facilities, the pipeline from each facility to its system's disposal site, the disposal sites and the
 * truck yards, straight from network.json (coordinates are the organizers' placeholders). Facilities glow by state:
 *   red (pulsing)  pump fault or the tank has tripped (wells shut in)
 *   amber          a leak or a worn pump is ongoing there
 *   green          healthy
 * and show their live tank level. Leaking pipelines turn amber. Click a facility to select it: the plan panel
 * follows its pump failure, and the trend drawer opens its pump.
 *
 * The basemap is a plain dark background on purpose: it needs no tile server or internet, so it works at the
 * judging venue, and the placeholder coordinates would not match real roads anyway.
 *
 * Data: useNetwork(ds) (positions) + useNetworkState(ds, at) (tank level, pump status, shut_in, events).
 * Never hardcodes system or facility IDs: any network is drawn.
 */
import { Database, Truck } from "lucide-react";
import { setWorkerUrl, type StyleSpecification } from "maplibre-gl";
import workerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url";
import "maplibre-gl/dist/maplibre-gl.css";
import Map, { Layer, Marker, Source } from "react-map-gl/maplibre";
import { useMemo } from "react";

import { useNetwork, useNetworkState } from "@/api/client";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { themeColor } from "@/lib/describe";
import { cn } from "@/lib/utils";
import { useActiveDatasetId, useConsole } from "@/state/store";

// MapLibre 6 guesses its worker file from its own URL, which breaks once Vite pre-bundles it ("Worker failed to
// load"). Handing it a Vite-bundled worker URL works the same in `npm run dev` and in the production build.
setWorkerUrl(workerUrl);

/** A map style with only a dark background (no external tiles). */
const DARK_STYLE: StyleSpecification = {
  version: 8,
  sources: {},
  layers: [{ id: "background", type: "background", paint: { "background-color": "#0b0f17" } }],
};

/** The dark map of every system. */
export function NetworkMap({ className }: { className?: string }) {
  const ds = useActiveDatasetId();
  const at = useConsole((s) => s.atUnix);
  const selectedAsset = useConsole((s) => s.selectedAssetId);
  const selectEvent = useConsole((s) => s.selectEvent);
  const selectAsset = useConsole((s) => s.selectAsset);
  const network = useNetwork(ds);
  const state = useNetworkState(ds, at);

  const systems = network.data?.network.systems;

  // Everything that depends on the data, computed once per response.
  const view = useMemo(() => {
    if (!systems) return null;
    const ongoing = (state.data?.events ?? []).filter((e) => e.status === "ongoing");
    const leaking = new Set(ongoing.filter((e) => e.category === "pipeline_leak").map((e) => e.asset_id));
    const points: [number, number][] = [];
    const lines = [];
    for (const sys of systems) {
      points.push([sys.truck_yard[1], sys.truck_yard[0]], [sys.disposal_site.lon, sys.disposal_site.lat]);
      for (const f of sys.facilities) {
        points.push([f.lon, f.lat]);
        lines.push({
          type: "Feature" as const,
          properties: { leak: leaking.has(f.pipeline.id) },
          geometry: {
            type: "LineString" as const,
            coordinates: [
              [f.lon, f.lat],
              [sys.disposal_site.lon, sys.disposal_site.lat],
            ],
          },
        });
      }
    }
    const lons = points.map((p) => p[0]);
    const lats = points.map((p) => p[1]);
    return {
      ongoing,
      bounds: [
        [Math.min(...lons), Math.min(...lats)],
        [Math.max(...lons), Math.max(...lats)],
      ] as [[number, number], [number, number]],
      pipelines: { type: "FeatureCollection" as const, features: lines },
    };
  }, [systems, state.data]);

  const facilityState = new globalThis.Map((state.data?.facilities ?? []).map((f) => [f.facility_id, f]));
  const warning = themeColor("--warning", "#f59e0b");

  return (
    <Card className={cn("overflow-hidden", className)}>
      <CardHeader>
        <CardTitle>Network</CardTitle>
        <CardDescription>Facilities, pipelines to each disposal site, and truck yards. Click a facility.</CardDescription>
      </CardHeader>
      <CardContent className="relative min-h-[300px] flex-1 p-0">
        {network.error && <p className="p-4 text-sm text-destructive">Could not load the network: {network.error.message}</p>}
        {systems && view && (
          <Map
            key={ds}
            initialViewState={{ bounds: view.bounds, fitBoundsOptions: { padding: 50 } }}
            mapStyle={DARK_STYLE}
            style={{ position: "absolute", inset: 0 }}
            attributionControl={false}
          >
            <Source id="pipelines" type="geojson" data={view.pipelines}>
              <Layer
                id="pipelines"
                type="line"
                paint={{
                  "line-color": ["case", ["get", "leak"], warning, "#475569"],
                  "line-width": ["case", ["get", "leak"], 2.5, 1.5],
                }}
              />
            </Source>

            {systems.map((sys) => (
              <div key={sys.id}>
                <Marker longitude={sys.disposal_site.lon} latitude={sys.disposal_site.lat}>
                  <span title={`${sys.disposal_site.id} (disposal site)`} className="flex size-6 items-center justify-center rounded-md bg-primary/20 text-primary">
                    <Database className="size-3.5" />
                  </span>
                </Marker>
                <Marker longitude={sys.truck_yard[1]} latitude={sys.truck_yard[0]}>
                  <span title={`${sys.id} truck yard`} className="flex size-6 items-center justify-center rounded-md bg-muted text-muted-foreground">
                    <Truck className="size-3.5" />
                  </span>
                </Marker>
                {sys.facilities.map((f) => {
                  const fs = facilityState.get(f.id);
                  const slow = view.ongoing.some((e) => e.facility_id === f.id && e.category !== "pump_failure");
                  const critical = fs ? fs.pump_status === 2 || fs.shut_in : false;
                  const failure = view.ongoing.find((e) => e.facility_id === f.id && e.category === "pump_failure");
                  return (
                    <Marker key={f.id} longitude={f.lon} latitude={f.lat}>
                      <button
                        type="button"
                        title={`${f.id}${fs ? ` · tank ${fs.tank_level_pct.toFixed(1)}% · ${fs.wells_producing}/${fs.wells_total} wells producing` : ""}`}
                        onClick={() => {
                          selectAsset(f.pump.id);
                          if (failure) selectEvent(failure.id);
                        }}
                        className="flex flex-col items-center gap-0.5"
                      >
                        <span
                          className={cn(
                            "flex size-7 items-center justify-center rounded-full border-2 bg-card text-[10px] font-mono",
                            critical ? "animate-pulse border-destructive text-destructive" : slow ? "border-warning text-warning" : "border-ok text-ok",
                            selectedAsset === f.pump.id && "ring-2 ring-primary",
                          )}
                        >
                          {fs ? Math.round(fs.tank_level_pct) : "–"}
                        </span>
                      </button>
                    </Marker>
                  );
                })}
              </div>
            ))}
          </Map>
        )}
      </CardContent>
    </Card>
  );
}
