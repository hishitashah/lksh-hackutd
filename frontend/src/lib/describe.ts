/**
 * Plain-English wording for plan actions and a chart-colour helper (display only, no calculations).
 *
 * Every number printed here is a field the API sent (trucks, trips, start_after_min, ...). Nothing is
 * derived, so the frontend never disagrees with the planner.
 */
import type { PlanAction } from "@/api/client";

/** One action as a short sentence, e.g. "Send 1 truck to S2_FACILITY_C (1 trip each)". */
export function describeAction(a: PlanAction): string {
  switch (a.type) {
    case "dispatch_truck": {
      const trucks = a.trucks ?? 0;
      const trips = a.trips_per_truck ?? 0;
      return `Send ${trucks} truck${trucks === 1 ? "" : "s"} to ${a.facility_id} (${trips} trip${trips === 1 ? "" : "s"}${trucks > 1 ? " each" : ""})`;
    }
    case "shut_in_wells": {
      const when = a.start_after_min ? `${Math.round(a.start_after_min)} min from now` : "right away";
      return `Shut in ${(a.wells ?? []).join(" + ")} ${when}, until the repair ends`;
    }
    case "field_inspection":
      return `Send an inspector to ${a.asset_id}`;
    default:
      return "Do nothing";
  }
}

/**
 * Read a theme colour from the CSS variables in index.css (charts draw on a canvas, which can't use
 * Tailwind classes). Falls back to `fallback` outside the browser.
 */
export function themeColor(name: string, fallback: string): string {
  if (typeof document === "undefined") return fallback;
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return value || fallback;
}
