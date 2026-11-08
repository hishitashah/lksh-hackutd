/**
 * ShowYourWork (task T6.5): placeholder until that task lands.
 *
 * Will show: Every plan the optimizer checked: the cheapest, the alternatives and do nothing, and why the others lost.
 * Data:      usePlan -> alternatives, do_nothing, cheapest.
 * Build with: shadcn Card; an ECharts bar chart for the cost comparison.
 */
import { Placeholder } from "@/components/Placeholder";

/** Every plan the optimizer checked. */
export function ShowYourWork({ className }: { className?: string }) {
  return (
    <Placeholder
      name="ShowYourWork"
      task="T6.5"
      description="Every plan the optimizer checked: the cheapest, the alternatives and do nothing, and why the others lost."
      className={className}
    />
  );
}
