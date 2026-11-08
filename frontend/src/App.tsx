/**
 * App shell (task T0.1): header + routes.
 *
 * Header: product name, navigation, the dataset switcher and the MOCK DATA badge.
 * Routes (plan.md "UI"):
 *   /         Command view (operator console, the demo screen)
 *   /field    phone view for the field crew (installable PWA start page)
 *   /handoff  shift handoff
 * The savings replay page (/replay) is hidden for the demo: its backend route was cut and answers 501 with mock
 * mode off. pages/Replay.tsx and useReplay are kept; restore the import, the NAV entry and the Route to bring it back.
 */
import { NavLink, Route, Routes } from "react-router-dom";

import { DatasetSwitcher } from "@/components/DatasetSwitcher";
import { MockBadge } from "@/components/MockBadge";
import { cn } from "@/lib/utils";
import { useActiveDatasetId } from "@/state/store";
import { Command } from "@/pages/Command";
import { Field } from "@/pages/Field";
import { Handoff } from "@/pages/Handoff";

/** Navigation entries shown in the header. */
const NAV = [
  { to: "/", label: "Console" },
  { to: "/handoff", label: "Handoff" },
  { to: "/field", label: "Field" },
];

/** The whole app: header on top, the active page below. */
export function App() {
  // Links carry ?ds=<dataset> so a reload (or a phone) lands on the same network.
  const ds = useActiveDatasetId();
  const search = ds ? `?ds=${encodeURIComponent(ds)}` : "";
  return (
    <div className="min-h-screen">
      <header className="flex flex-wrap items-center gap-4 border-b border-border px-4 py-3">
        <span className="text-base font-semibold tracking-wider text-primary">AVON NIGHTWATCH</span>
        <nav className="flex gap-1">
          {NAV.map((item) => (
            <NavLink
              key={item.to}
              to={{ pathname: item.to, search }}
              end={item.to === "/"}
              className={({ isActive }) =>
                cn("rounded-md px-3 py-1.5 text-sm text-muted-foreground hover:bg-muted", isActive && "bg-muted text-foreground")
              }
            >
              {item.label}
            </NavLink>
          ))}
        </nav>
        <div className="ml-auto flex items-center gap-3">
          <MockBadge />
          <DatasetSwitcher />
        </div>
      </header>
      <Routes>
        <Route path="/" element={<Command />} />
        <Route path="/field" element={<Field />} />
        <Route path="/handoff" element={<Handoff />} />
      </Routes>
    </div>
  );
}
