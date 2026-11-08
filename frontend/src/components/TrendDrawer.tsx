/**
 * TrendDrawer (task T6.6): placeholder until that task lands.
 *
 * Will show: Any sensor of the selected asset over time, several sensors overlaid, with event periods shaded.
 * Data:      useSeries(ds, store.selectedAssetId, metric) + useNetworkState -> events for shading.
 * Build with: echarts-for-react with dataZoom.
 */
import { Placeholder } from "@/components/Placeholder";

/** Any sensor of the selected asset over time, several sensors overlaid, with event periods shaded. */
export function TrendDrawer({ className }: { className?: string }) {
  return (
    <Placeholder
      name="TrendDrawer"
      task="T6.6"
      description="Any sensor of the selected asset over time, several sensors overlaid, with event periods shaded."
      className={className}
    />
  );
}
