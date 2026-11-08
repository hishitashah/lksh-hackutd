/**
 * Placeholder panel (task T0.1): marks where a feature will be built and which task owns it.
 *
 * Every stub component (NetworkMap, Skyline, ...) renders one of these until its task lands, so
 * the Command layout is complete from hour 1 and each teammate knows exactly where their work goes.
 * Delete the Placeholder usage when you build the real component.
 */
import type * as React from "react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { cn } from "@/lib/utils";

interface PlaceholderProps {
  /** Component name, e.g. "NetworkMap". */
  name: string;
  /** Task ID from tasks.md that builds it, e.g. "T6.3". */
  task: string;
  /** One line on what it will show. */
  description: string;
  className?: string;
  children?: React.ReactNode;
}

/** A dashed panel showing the component name, its task and what it will do. */
export function Placeholder({ name, task, description, className, children }: PlaceholderProps) {
  return (
    <Card className={cn("border-dashed", className)}>
      <CardHeader>
        <div className="flex items-center justify-between gap-2">
          <CardTitle>{name}</CardTitle>
          <Badge variant="outline">{task}</Badge>
        </div>
        <CardDescription>{description}</CardDescription>
      </CardHeader>
      {children ? <CardContent>{children}</CardContent> : null}
    </Card>
  );
}
