/**
 * TankWhatIf (task T6.7): placeholder until that task lands.
 *
 * Will show: Tank level over time: do nothing vs the plan(s), with warning and shutdown lines from network.json.
 * Data:      usePlan -> traces[] (points, warning_pct, shutdown_pct).
 * Build with: echarts-for-react (line chart with markLine for the limits).
 */
import { Placeholder } from "@/components/Placeholder";

/** Tank level over time. */
export function TankWhatIf({ className }: { className?: string }) {
  return (
    <Placeholder
      name="TankWhatIf"
      task="T6.7"
      description="Tank level over time: do nothing vs the plan(s), with warning and shutdown lines from network.json."
      className={className}
    />
  );
}
