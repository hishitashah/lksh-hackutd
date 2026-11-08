/**
 * DatasetSwitcher (basic in T0.1; drag-and-drop upload added in T6.2).
 *
 * Lists every dataset folder the backend found (history/*, and test/ once it exists) and switches
 * the whole console to the chosen one. At judging: pull test/, refresh, pick "test" here.
 *
 * Data: useDatasets(). Writes store.setDataset().
 */
import { useDatasets } from "@/api/client";
import { useActiveDatasetId, useConsole } from "@/state/store";

/** A <select> of datasets. */
export function DatasetSwitcher() {
  const { data, error } = useDatasets();
  const active = useActiveDatasetId();
  const setDataset = useConsole((s) => s.setDataset);

  if (error) return <span className="text-xs text-destructive">Backend not reachable</span>;

  return (
    <label className="flex items-center gap-2 text-xs text-muted-foreground">
      Dataset
      <select
        className="h-8 rounded-md border border-border bg-card px-2 text-sm text-foreground"
        value={active ?? ""}
        onChange={(e) => setDataset(e.target.value)}
      >
        {data?.map((d) => (
          <option key={d.id} value={d.id}>
            {d.id}
          </option>
        ))}
      </select>
    </label>
  );
}
