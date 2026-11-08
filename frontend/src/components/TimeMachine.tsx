/**
 * TimeMachine (task T6.8): placeholder until that task lands.
 *
 * Will show: Scrubber over the dataset's time range with play at 60x / 600x. Moving it re-renders everything as of that time.
 * Data:      useNetwork -> time_range (start_unix, now_unix, step_s); writes store.setAt().
 * Build with: shadcn slider/button; requestAnimationFrame for play.
 */
import { Placeholder } from "@/components/Placeholder";

/** Scrubber over the dataset's time range with play at 60x / 600x. */
export function TimeMachine({ className }: { className?: string }) {
  return (
    <Placeholder
      name="TimeMachine"
      task="T6.8"
      description="Scrubber over the dataset's time range with play at 60x / 600x. Moving it re-renders everything as of that time."
      className={className}
    />
  );
}
