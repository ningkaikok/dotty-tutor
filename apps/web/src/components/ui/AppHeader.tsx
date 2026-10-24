import type { ReactNode } from "react";
import { Button, ButtonLink } from "./Button";
import { classes } from "./classes";

/** Shared brand/back/action chrome; onBack preserves route-specific cleanup before leaving. */
export function AppHeader({ subtitle, backTo, onBack, backLabel = "返回入口", actions, className }: {
  subtitle: string; backTo?: string; onBack?: () => void; backLabel?: string; actions?: ReactNode; className?: string;
}) {
  return <header className={classes("ui-app-header", className)}>
    {onBack ? <Button variant="quiet" onClick={onBack}>← {backLabel}</Button> : backTo && <ButtonLink to={backTo} variant="quiet">← {backLabel}</ButtonLink>}
    <div className="ui-brand-mark" aria-hidden="true">D</div>
    <div className="ui-brand"><strong>Dotty Tutor</strong><span>{subtitle}</span></div>
    {actions && <div className="ui-header-actions">{actions}</div>}
  </header>;
}
