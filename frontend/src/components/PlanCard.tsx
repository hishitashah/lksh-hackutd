/**
 * PlanCard (task T6.5): placeholder until that task lands.
 *
 * Will show: Decision banner ('Do this now: ...') with a checklist, and Cheapest now vs Best long run cards with the extra cost and reason.
 * Data:      usePlan(selected event) -> cheapest, long_run, long_run_assessment. Show numbers exactly as the API returns them.
 * Build with: shadcn Card + Badge.
 */
import { Placeholder } from "@/components/Placeholder";

/** Decision banner ('Do this now. */
export function PlanCard({ className }: { className?: string }) {
  return (
    <Placeholder
      name="PlanCard"
      task="T6.5"
      description="Decision banner ('Do this now: ...') with a checklist, and Cheapest now vs Best long run cards with the extra cost and reason."
      className={className}
    />
  );
}
