/**
 * Savings replay page (route "/replay", task T6.10; engine T3.7).
 *
 * Will show what Nightwatch would have saved over the whole window: three big totals (did nothing,
 * cheapest per event, long run), a cumulative-cost chart, and a per-event table whose rows jump the
 * time machine to that moment. Data: useReplay(dataset).
 */
import { Placeholder } from "@/components/Placeholder";

/** Placeholder until T6.10. */
export function Replay() {
  return (
    <div className="mx-auto max-w-5xl p-4">
      <Placeholder
        name="Savings replay"
        task="T6.10"
        description="Did nothing vs cheapest per event vs long run: three totals, a cumulative chart, and a per-event table."
      />
    </div>
  );
}
