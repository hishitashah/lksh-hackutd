/**
 * UI state shared across the console (task T0.1), using zustand.
 *
 * Holds only what the *user* chose: which dataset, where the time machine is, what's selected.
 * Server data (alerts, plans, ...) lives in TanStack Query (src/api/client.ts), not here.
 */
import { create } from "zustand";

import { useDatasets } from "@/api/client";

interface ConsoleState {
  /** Dataset picked in the switcher, or null to use the first one the backend lists. */
  datasetId: string | null;
  /** Time-machine position in unix seconds, or null for the dataset's "now" (last telemetry row). */
  atUnix: number | null;
  /** Event selected in the alert list (drives the plan panel). */
  selectedEventId: string | null;
  /** Asset selected on the map or in an alert (drives the trend drawer). */
  selectedAssetId: string | null;

  /** Switch dataset. Resets time and selection, because IDs differ between networks. */
  setDataset: (id: string) => void;
  /** Move the time machine; null jumps back to "now". */
  setAt: (unix: number | null) => void;
  selectEvent: (id: string | null) => void;
  selectAsset: (id: string | null) => void;
}

export const useConsole = create<ConsoleState>((set) => ({
  datasetId: null,
  atUnix: null,
  selectedEventId: null,
  selectedAssetId: null,
  setDataset: (id) => set({ datasetId: id, atUnix: null, selectedEventId: null, selectedAssetId: null }),
  setAt: (unix) => set({ atUnix: unix }),
  selectEvent: (id) => set({ selectedEventId: id }),
  selectAsset: (id) => set({ selectedAssetId: id }),
}));

/**
 * The dataset the console should show: the user's pick, or else the first dataset the backend
 * lists. Derived (not stored) so no dataset ID is ever hardcoded in the UI.
 */
export function useActiveDatasetId(): string | null {
  const picked = useConsole((s) => s.datasetId);
  const { data } = useDatasets();
  return picked ?? data?.[0]?.id ?? null;
}
