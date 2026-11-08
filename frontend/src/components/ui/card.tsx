/**
 * Card (shadcn/ui pattern, task T0.1): the panel container used across the console.
 * Compose as <Card><CardHeader><CardTitle/><CardDescription/></CardHeader><CardContent/></Card>.
 */
import type * as React from "react";

import { cn } from "@/lib/utils";

/** Outer panel: dark surface, border, rounded corners. */
export function Card({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="card"
      className={cn("flex flex-col rounded-lg border border-border bg-card text-card-foreground", className)}
      {...props}
    />
  );
}

/** Top area holding the title and description. */
export function CardHeader({ className, ...props }: React.ComponentProps<"div">) {
  return <div data-slot="card-header" className={cn("flex flex-col gap-1 p-4 pb-2", className)} {...props} />;
}

/** Panel heading. */
export function CardTitle({ className, ...props }: React.ComponentProps<"div">) {
  return <div data-slot="card-title" className={cn("text-sm font-semibold tracking-wide", className)} {...props} />;
}

/** Secondary line under the title. */
export function CardDescription({ className, ...props }: React.ComponentProps<"div">) {
  return <div data-slot="card-description" className={cn("text-xs text-muted-foreground", className)} {...props} />;
}

/** Main body of the panel. */
export function CardContent({ className, ...props }: React.ComponentProps<"div">) {
  return <div data-slot="card-content" className={cn("p-4 pt-2", className)} {...props} />;
}
