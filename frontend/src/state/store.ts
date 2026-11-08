/**
 * UI state shared across the console (task T0.1), using zustand.
 *
 * Holds only what the *user* chose: which dataset, where the time machine is, what's selected.
 * Server data (alerts, plans, ...) lives in TanStack Query (src/api/client.ts), not here.
 */
import { create } from "zustand";

import { useDatasets, useNetworkState } from "@/api/client";

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

/** The dataset named in the page address (?ds=...), so a phone opening /field?ds=... sees the same network. */
function datasetFromUrl(): string | null {
  return typeof window === "undefined" ? null : new URLSearchParams(window.location.search).get("ds");
}

/** Keep ?ds= in the address bar in step with the picked dataset (no page reload, no history entry). */
function writeDatasetToUrl(id: string): void {
  const url = new URL(window.location.href);
  url.searchParams.set("ds", id);
  window.history.replaceState(null, "", url);
}

export const useConsole = create<ConsoleState>((set) => ({
  datasetId: datasetFromUrl(),
  atUnix: null,
  selectedEventId: null,
  selectedAssetId: null,
  setDataset: (id) => {
    writeDatasetToUrl(id);
    set({ datasetId: id, atUnix: null, selectedEventId: null, selectedAssetId: null });
  },
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

/**
 * The alert the plan panels should show: the one the operator clicked, otherwise the first alert. The
 * backend already orders alerts (most urgent pump failure first), so "first" is the most urgent one.
 * Returns `alert: null` when nothing is ongoing. Shared by PlanCard, ShowYourWork and TankWhatIf.
 */
export function useActiveEvent() {
  const ds = useActiveDatasetId();
  const at = useConsole((s) => s.atUnix);
  const selectedId = useConsole((s) => s.selectedEventId);
  const { data, isLoading } = useNetworkState(ds, at);
  const alerts = data?.alerts ?? [];
  const alert = alerts.find((a) => a.event.id === selectedId) ?? alerts[0] ?? null;
  return { ds, at, alert, event: alert?.event ?? null, isLoading };
}
