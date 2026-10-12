import type { ReactNode } from "react";
import { ButtonLink } from "./Button";
import { classes } from "./classes";

/** Shared brand/back/action chrome; each route keeps its own page title and state. */
export function AppHeader({ subtitle, backTo, backLabel = "返回入口", actions, className }: {
  subtitle: string; backTo?: string; backLabel?: string; actions?: ReactNode; className?: string;
}) {
  return <header className={classes("ui-app-header", className)}>
    {backTo && <ButtonLink to={backTo} variant="quiet">← {backLabel}</ButtonLink>}
    <div className="ui-brand-mark" aria-hidden="true">D</div>
    <div className="ui-brand"><strong>Dotty Tutor</strong><span>{subtitle}</span></div>
    {actions && <div className="ui-header-actions">{actions}</div>}
  </header>;
}
