/**
 * MockBadge (task T0.3): a loud "MOCK DATA" label shown while the backend serves fixtures.
 *
 * Why: mock mode must be off for the demo. If this badge is visible, the numbers on screen are
 * illustrative fixtures, not real detector/optimizer output. Data: useHealth() -> mock.
 */
import { Badge } from "@/components/ui/badge";
import { useHealth } from "@/api/client";

/** Renders nothing unless the backend reports mock mode. */
export function MockBadge() {
  const { data } = useHealth();
  if (!data?.mock) return null;
  return (
    <Badge variant="warning" title="Backend is serving fixtures/ (NIGHTWATCH_MOCK=1). Turn off before the demo.">
      MOCK DATA
    </Badge>
  );
}
