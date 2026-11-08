/**
 * NetworkMap (task T6.3): placeholder until that task lands.
 *
 * Will show: Dark map of every system: facilities, pipelines to the disposal site, truck yards. Assets glow by state; click to select.
 * Data:      useNetwork (lat/lon from network.json) + useNetworkState (tank levels, pump status). Never hardcode S1-S5.
 * Build with: deck.gl (@deck.gl/react, @deck.gl/layers) on MapLibre (react-map-gl/maplibre), CARTO dark-matter basemap.
 */
import { Placeholder } from "@/components/Placeholder";

/** Dark map of every system. */
export function NetworkMap({ className }: { className?: string }) {
  return (
    <Placeholder
      name="NetworkMap"
      task="T6.3"
      description="Dark map of every system: facilities, pipelines to the disposal site, truck yards. Assets glow by state; click to select."
      className={className}
    />
  );
}
