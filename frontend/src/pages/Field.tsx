/**
 * Field view (route "/field"): the phone view for an inspector or truck driver (task T6.11).
 *
 * Will show one large card per alert (big countdown, the action to take, an approve button) with
 * big tap targets, usable at 375 px wide. It's the start page when the app is installed as a PWA.
 *
 * Live dispatch feed (T5.5 + T6.11): approved orders and scheduled reminders arrive as cards within
 * about a second, via the browser's built-in EventSource on GET /api/{ds}/field/stream (initial list
 * from GET /api/{ds}/dispatches). No app, download or permission is needed, so it works on iPhone too.
 * The QR code on the console simply opens this page.
 */
import { Placeholder } from "@/components/Placeholder";

/** Placeholder until T6.11. */
export function Field() {
  return (
    <div className="mx-auto max-w-md p-4">
      <Placeholder
        name="Field view"
        task="T6.11"
        description="Phone view: one card per alert with a big countdown, the action, and an approve button. Installable PWA; opened from the console's QR code."
      />
    </div>
  );
}
