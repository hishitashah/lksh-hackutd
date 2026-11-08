/**
 * Badge (shadcn/ui pattern, task T0.1): small labels for categories, task IDs and status.
 * Variants map to the theme's status colors: destructive (pump failure), warning (leak), etc.
 */
import { cva, type VariantProps } from "class-variance-authority";
import type * as React from "react";

import { cn } from "@/lib/utils";

// Kept private so this file only exports components (hot reload friendly).
const badgeVariants = cva("inline-flex items-center rounded-md px-2 py-0.5 text-xs font-medium whitespace-nowrap", {
  variants: {
    variant: {
      default: "bg-primary/15 text-primary",
      outline: "border border-border text-muted-foreground",
      destructive: "bg-destructive/15 text-destructive",
      warning: "bg-warning/15 text-warning",
      accent: "bg-accent/15 text-accent",
      ok: "bg-ok/15 text-ok",
    },
  },
  defaultVariants: { variant: "default" },
});

/** A small colored label. */
export function Badge({ className, variant, ...props }: React.ComponentProps<"span"> & VariantProps<typeof badgeVariants>) {
  return <span data-slot="badge" className={cn(badgeVariants({ variant }), className)} {...props} />;
}
