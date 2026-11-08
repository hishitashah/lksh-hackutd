/**
 * SentinelPanel (task T6.9 + T5.3): placeholder until that task lands.
 *
 * Will show: Sentinel's live run: the six steps as they stream in, expandable tool calls, grounded badge, and a chat box.
 * Data:      POST /api/{ds}/agent/run (server-sent events: 'step', 'drafts', 'done'). Use a fetch-based SSE reader: EventSource is GET-only.
 * Build with: shadcn Card; framer-motion for step entrance.
 */
import { Placeholder } from "@/components/Placeholder";

/** Sentinel's live run. */
export function SentinelPanel({ className }: { className?: string }) {
  return (
    <Placeholder
      name="SentinelPanel"
      task="T6.9 + T5.3"
      description="Sentinel's live run: the six steps as they stream in, expandable tool calls, grounded badge, and a chat box."
      className={className}
    />
  );
}
