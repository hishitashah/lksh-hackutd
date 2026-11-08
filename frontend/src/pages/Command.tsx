/**
 * Command view (route "/"): the operator console and the main demo screen (layout T0.1, design T6.1).
 *
 * Layout from plan.md "UI":
 *   top     Skyline (+ $ lost/hour)
 *   left    AlertList (ordered alerts)       center NetworkMap       right PlanCard, ShowYourWork, TankWhatIf
 *   bottom  TimeMachine                                              right SentinelPanel + ApprovalQueue
 *   drawer  TrendDrawer for the selected asset
 * On narrow screens the columns stack (the phone experience is the separate /field route).
 */
import { AlertList } from "@/components/AlertList";
import { ApprovalQueue } from "@/components/ApprovalQueue";
import { NetworkMap } from "@/components/NetworkMap";
import { PlanCard } from "@/components/PlanCard";
import { SentinelPanel } from "@/components/SentinelPanel";
import { ShowYourWork } from "@/components/ShowYourWork";
import { Skyline } from "@/components/Skyline";
import { TankWhatIf } from "@/components/TankWhatIf";
import { TimeMachine } from "@/components/TimeMachine";
import { TrendDrawer } from "@/components/TrendDrawer";

/** The full console grid. Each panel owns its own data fetching. */
export function Command() {
  return (
    <div className="flex flex-col gap-4 p-4">
      <Skyline />
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[340px_1fr_380px]">
        <AlertList />
        <div className="flex flex-col gap-4">
          <NetworkMap className="min-h-[360px]" />
          <TrendDrawer />
        </div>
        <div className="flex flex-col gap-4">
          <PlanCard />
          <ShowYourWork />
          <TankWhatIf />
          <SentinelPanel />
          <ApprovalQueue />
        </div>
      </div>
      <TimeMachine />
    </div>
  );
}
