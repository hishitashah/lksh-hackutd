/**
 * TimeMachine (task T6.8): scrub the whole console to any moment in the dataset.
 *
 * The slider spans the telemetry's time range (first reading to "now"). Moving it writes store.setAt(unix) and
 * every panel re-renders as of that moment (alerts, plans, map). The right end means "now" (null in the store).
 * "Play" steps forward through the recording; "Jump to" lists the incidents the detector found, so you can land
 * on the moment a failure began. Nothing here changes the data: the recording is only replayed (README).
 *
 * Data: useNetwork(ds) -> time_range {start_unix, now_unix, step_s}; useNetworkState(ds, null) -> events for the
 * jump list. Times are UTC.
 */
import { Pause, Play } from "lucide-react";
import { useEffect, useState } from "react";

import { useNetwork, useNetworkState } from "@/api/client";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { formatUtcDateTime } from "@/lib/utils";
import { useActiveDatasetId, useConsole } from "@/state/store";

/** Playback speeds: how many reading steps (step_s each, 5 min in the data) the clock advances per second. */
const SPEEDS = [
  { label: "1x", stepsPerSecond: 1 },
  { label: "4x", stepsPerSecond: 4 },
  { label: "12x", stepsPerSecond: 12 },
];

/** Slider over the dataset's time range, with play and a jump-to-incident list. */
export function TimeMachine({ className }: { className?: string }) {
  const ds = useActiveDatasetId();
  const at = useConsole((s) => s.atUnix);
  const setAt = useConsole((s) => s.setAt);
  const network = useNetwork(ds);
  const nowState = useNetworkState(ds, null);
  const range = network.data?.time_range;

  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(SPEEDS[0].stepsPerSecond);

  /** Move the console to `unix`; the end of the range means "now" (null). */
  const moveTo = (unix: number) => {
    if (!range) return;
    setAt(unix >= range.now_unix ? null : Math.max(unix, range.start_unix));
  };

  // Playback: advance `speed` steps each second until the end of the recording.
  useEffect(() => {
    if (!playing || !range) return;
    const timer = setInterval(() => {
      const current = useConsole.getState().atUnix ?? range.now_unix;
      const next = current + speed * range.step_s;
      if (next >= range.now_unix) {
        setAt(null);
        setPlaying(false);
      } else {
        setAt(next);
      }
    }, 1000);
    return () => clearInterval(timer);
  }, [playing, speed, range, setAt]);

  if (!range) {
    return (
      <Card className={className}>
        <CardContent className="p-4 text-sm text-muted-foreground">Loading time range…</CardContent>
      </Card>
    );
  }

  const value = at ?? range.now_unix;
  const incidents = (nowState.data?.events ?? []).filter((e) => e.category === "pump_failure");

  return (
    <Card className={className}>
      <CardHeader>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <CardTitle>Time machine</CardTitle>
          <span className="font-mono text-sm text-primary">{formatUtcDateTime(value)}</span>
        </div>
        <CardDescription>
          {at === null ? "Showing now (the last reading)" : "Replaying the past: plans are for that moment"}
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <input
          type="range"
          aria-label="Time machine position"
          className="w-full accent-[var(--primary)]"
          min={range.start_unix}
          max={range.now_unix}
          step={range.step_s}
          value={value}
          onChange={(e) => moveTo(Number(e.target.value))}
        />
        <div className="flex flex-wrap items-center gap-2">
          <Button size="sm" variant="outline" onClick={() => setPlaying((p) => !p)}>
            {playing ? <Pause className="size-4" /> : <Play className="size-4" />}
            {playing ? "Pause" : "Play"}
          </Button>
          <select
            aria-label="Playback speed"
            className="h-8 rounded-md border border-border bg-card px-2 text-sm"
            value={speed}
            onChange={(e) => setSpeed(Number(e.target.value))}
          >
            {SPEEDS.map((s) => (
              <option key={s.label} value={s.stepsPerSecond}>
                {s.label}
              </option>
            ))}
          </select>
          <Button size="sm" variant="ghost" disabled={at === null} onClick={() => setAt(null)}>
            Back to now
          </Button>
          {incidents.length > 0 && (
            <select
              aria-label="Jump to a pump failure"
              className="ml-auto h-8 max-w-[260px] rounded-md border border-border bg-card px-2 text-sm"
              value=""
              onChange={(e) => e.target.value && moveTo(Number(e.target.value))}
            >
              <option value="">Jump to a pump failure…</option>
              {incidents.map((e) => (
                <option key={e.id} value={e.onset_unix}>
                  {formatUtcDateTime(e.onset_unix)} · {e.facility_id}
                </option>
              ))}
            </select>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
