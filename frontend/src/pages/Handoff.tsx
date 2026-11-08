/**
 * Shift handoff page (route "/handoff", tasks T5.3 + T6.9).
 *
 * Will show Sentinel's 30-second plain-English handoff for the next operator (from the report step
 * of a run, AgentRunResult.handoff) and a button to download events.json.
 */
import { Placeholder } from "@/components/Placeholder";

/** Placeholder until the handoff is wired. */
export function Handoff() {
  return (
    <div className="mx-auto max-w-3xl p-4">
      <Placeholder
        name="Shift handoff"
        task="T5.3 + T6.9"
        description="Sentinel's 30-second handoff for the next operator, plus an events.json download."
      />
    </div>
  );
}
