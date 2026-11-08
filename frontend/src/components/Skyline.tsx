/**
 * Skyline (task T6.12): placeholder until that task lands.
 *
 * Will show: Avon skyline: one district per system, buildings = wells, lights dim as wells shut in, plus a live $ lost/hour ticker.
 * Data:      useNetwork (systems, wells) + useNetworkState (facilities[].wells_producing, lost_revenue_per_hour_total).
 * Build with: SVG + framer-motion.
 */
import { Placeholder } from "@/components/Placeholder";

/** Avon skyline. */
export function Skyline({ className }: { className?: string }) {
  return (
    <Placeholder
      name="Skyline"
      task="T6.12"
      description="Avon skyline: one district per system, buildings = wells, lights dim as wells shut in, plus a live $ lost/hour ticker."
      className={className}
    />
  );
}
