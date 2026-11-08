/**
 * ApprovalQueue (task T6.9 + T5.5): placeholder until that task lands.
 *
 * Will show: Actions Sentinel drafted, waiting for the operator to approve or reject. Approve sends the push to the field crew.
 * Data:      The 'drafts' event from the agent stream; POST /api/{ds}/agent/approve.
 * Build with: shadcn Card + Button.
 */
import { Placeholder } from "@/components/Placeholder";

/** Actions Sentinel drafted, waiting for the operator to approve or reject. */
export function ApprovalQueue({ className }: { className?: string }) {
  return (
    <Placeholder
      name="ApprovalQueue"
      task="T6.9 + T5.5"
      description="Actions Sentinel drafted, waiting for the operator to approve or reject. Approve sends the push to the field crew."
      className={className}
    />
  );
}
