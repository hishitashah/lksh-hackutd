/**
 * Small shared helpers for the UI.
 *
 * `cn` merges Tailwind class names and resolves conflicts (e.g. "p-2" + "p-4" -> "p-4"),
 * the standard shadcn/ui helper used by every component in src/components/ui.
 */
import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/** Combine class names conditionally and let later Tailwind classes win over earlier ones. */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}

/** Format unix seconds as a UTC clock time like "13:25" (the data is all UTC; never use local time). */
export function formatUtcTime(unix: number): string {
  return new Date(unix * 1000).toISOString().slice(11, 16);
}

/** Format unix seconds as "Thu 10-08 12:00 UTC" for headers and the time machine. */
export function formatUtcDateTime(unix: number): string {
  const d = new Date(unix * 1000);
  const day = d.toLocaleDateString("en-US", { weekday: "short", timeZone: "UTC" });
  return `${day} ${d.toISOString().slice(5, 10)} ${d.toISOString().slice(11, 16)} UTC`;
}

/** Format dollars with no cents, e.g. 1298.33 -> "$1,298". */
export function formatUsd(value: number): string {
  return `$${Math.round(value).toLocaleString("en-US")}`;
}
