/**
 * CountdownRing (minimal in T0.1; the animated ring is task T6.4).
 *
 * Shows hours left until a tank reaches its shutdown limit, colored by urgency.
 * The value comes straight from the API (Event.hours_to_limit.p50); this component does no math
 * beyond formatting. The urgency thresholds below are display choices, not physics.
 */
import { cn } from "@/lib/utils";

/** Display-only urgency bands (hours). Not used for any decision. */
const URGENT_H = 1.5;
const SOON_H = 3;

/** Hours to shutdown, e.g. "1.4h", red when urgent, amber when soon. */
export function CountdownRing({ hours }: { hours: number }) {
  const tone = hours <= URGENT_H ? "text-destructive border-destructive" : hours <= SOON_H ? "text-warning border-warning" : "text-ok border-ok";
  return (
    <span
      title={`${hours.toFixed(2)} hours until the tank reaches its shutdown limit`}
      className={cn("flex size-14 shrink-0 items-center justify-center rounded-full border-2 font-mono text-sm", tone)}
    >
      {hours.toFixed(1)}h
    </span>
  );
}
