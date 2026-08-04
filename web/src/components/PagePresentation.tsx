import type { ComponentType, ReactNode, SVGProps } from "react";
import { Loader2Icon, TriangleAlertIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

type PageIcon = ComponentType<SVGProps<SVGSVGElement>>;

export function PageHeader({
  title,
  description,
  actions,
  className,
}: {
  title: string;
  description: string;
  actions?: ReactNode;
  className?: string;
}) {
  return (
    <header className={cn("mb-6 flex items-start justify-between gap-4", className)}>
      <div className="flex min-w-0 flex-col gap-1">
        <h1 className="text-2xl font-semibold">{title}</h1>
        <p className="text-sm text-muted-foreground">{description}</p>
      </div>
      {actions && <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div>}
    </header>
  );
}

export function PageEmptyState({
  icon: Icon,
  title,
  description,
  actions,
  className,
  testId,
}: {
  icon: PageIcon;
  title: string;
  description: string;
  actions?: ReactNode;
  className?: string;
  testId?: string;
}) {
  return (
    <div
      className={cn("flex flex-col items-center gap-2 py-12 text-center", className)}
      data-testid={testId}
    >
      <Icon className="size-8 text-muted-foreground/50" />
      <p className="text-sm font-medium">{title}</p>
      <p className="max-w-sm text-xs text-muted-foreground">{description}</p>
      {actions && <div className="mt-3 flex flex-wrap justify-center gap-2">{actions}</div>}
    </div>
  );
}

export function PageLoadingState({ label, className }: { label: string; className?: string }) {
  return (
    <div className={cn("flex items-center gap-2 py-12 text-sm text-muted-foreground", className)}>
      <Loader2Icon className="size-4 animate-spin" />
      {label}
    </div>
  );
}

export function PageErrorState({
  message,
  onRetry,
  className,
  testId,
}: {
  message: string;
  onRetry: () => void;
  className?: string;
  testId?: string;
}) {
  return (
    <div
      role="alert"
      data-testid={testId}
      className={cn(
        "flex items-center gap-2 rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm",
        className,
      )}
    >
      <TriangleAlertIcon className="size-4 shrink-0 text-destructive" />
      <span className="flex-1">{message}</span>
      <Button variant="outline" size="sm" onClick={onRetry}>
        Retry
      </Button>
    </div>
  );
}
