/**
 * Sentinel run state (task T6.9), using zustand.
 *
 * Why a store: the Sentinel panel, the approval queue and the handoff page all show the same run, so
 * the run lives here once instead of in one component. The stream itself is read by `streamAgentRun`
 * in src/api/client.ts; this store only holds what it delivered:
 *   - steps:          latest version of each step, keyed by `index` (a step arrives twice: running, then final)
 *   - recommendation: the orchestrator's decision
 *   - drafts:         actions waiting for the operator (updated in place after approve / reject)
 *   - handoff:        the 30-second shift handoff text
 * Nothing here is computed: every value is exactly what the backend sent.
 */
import { useEffect } from "react";
import { create } from "zustand";

import {
  type AgentStep,
  type DraftAction,
  type Recommendation,
  streamAgentRun,
} from "@/api/client";
import { useActiveDatasetId } from "@/state/store";

interface SentinelState {
  /** Dataset the current run belongs to (a different dataset means a stale run). */
  datasetId: string | null;
  /** Time-machine position the run analysed, unix seconds; null = the dataset's "now". */
  atUnix: number | null;
  /** Run ID from the `done` event; needed to approve drafts. Null until the run finishes. */
  runId: string | null;
  running: boolean;
  /** Set when the stream could not be read (for example the backend is down). */
  error: string | null;
  steps: Record<number, AgentStep>;
  recommendation: Recommendation | null;
  drafts: DraftAction[];
  handoff: string | null;

  /** Start a new run (aborts any run still streaming). */
  start: (ds: string, at: number | null) => Promise<void>;
  /** Replace one draft with its updated version (after approve / reject). */
  setDraft: (draft: DraftAction) => void;
}

/** The run currently streaming, so a newer run can cancel it. */
let controller: AbortController | null = null;

export const useSentinel = create<SentinelState>((set) => ({
  datasetId: null,
  atUnix: null,
  runId: null,
  running: false,
  error: null,
  steps: {},
  recommendation: null,
  drafts: [],
  handoff: null,

  start: async (ds, at) => {
    controller?.abort();
    const mine = new AbortController();
    controller = mine;
    set({
      datasetId: ds,
      atUnix: at,
      runId: null,
      running: true,
      error: null,
      steps: {},
      recommendation: null,
      drafts: [],
      handoff: null,
    });
    try {
      await streamAgentRun(
        ds,
        at,
        {
          onStep: (step) => set((s) => ({ steps: { ...s.steps, [step.index]: step } })),
          onRecommendation: (recommendation) => set({ recommendation }),
          onDrafts: (drafts) => set({ drafts }),
          onDone: (done) => set({ runId: done.run_id, handoff: done.handoff }),
        },
        mine.signal,
      );
    } catch (err) {
      if (!mine.signal.aborted) set({ error: err instanceof Error ? err.message : String(err) });
    } finally {
      if (controller === mine) set({ running: false });
    }
  },

  setDraft: (draft) => set((s) => ({ drafts: s.drafts.map((d) => (d.id === draft.id ? draft : d)) })),
}));

/**
 * Make sure a Sentinel run exists for the active dataset: starts one automatically the first time a
 * dataset is shown (BRIEF: "start the run automatically once per dataset"), and again after a dataset switch.
 * Reads the store through getState() so React StrictMode's double effect doesn't start two runs.
 */
export function useEnsureSentinelRun(): void {
  const ds = useActiveDatasetId();
  useEffect(() => {
    if (ds && useSentinel.getState().datasetId !== ds) void useSentinel.getState().start(ds, null);
  }, [ds]);
}
