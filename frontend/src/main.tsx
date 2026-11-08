/**
 * Frontend entry point (task T0.1). Mounts <App/> with the app-wide providers:
 *   - QueryClientProvider: TanStack Query cache for all API data (src/api/client.ts).
 *   - BrowserRouter: client-side routes (/, /field, /replay, /handoff).
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";

import { App } from "@/App";
import "@/index.css";

// One shared cache. Data refetches when its query key changes (e.g. dataset or time-machine position),
// not on window focus, so the console doesn't flicker while the operator switches windows.
const queryClient = new QueryClient({
  defaultOptions: { queries: { refetchOnWindowFocus: false, retry: 1 } },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);
