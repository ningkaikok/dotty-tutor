import type { HTMLAttributes, ReactNode } from "react";
import { classes } from "./classes";
export function PageShell({ className, ...props }: HTMLAttributes<HTMLElement>) {
  return <main {...props} className={classes("ui-page", className)} data-theme="bright" />;
}
export function PageHeader({ title, description, eyebrow, actions, className, ...props }: Omit<HTMLAttributes<HTMLElement>, "title"> & { title: ReactNode; description?: ReactNode; eyebrow?: ReactNode; actions?: ReactNode }) {
  return <header {...props} className={classes("ui-page-header", className)}><div>{eyebrow && <span className="ui-eyebrow">{eyebrow}</span>}<h1>{title}</h1>{description && <p>{description}</p>}</div>{actions && <div className="ui-actions">{actions}</div>}</header>;
}
export function Card({ className, ...props }: HTMLAttributes<HTMLElement>) {
  return <section {...props} className={classes("ui-card", className)} />;
}
/** `icon` is decorative and hidden from assistive tech; the title carries the meaning. */
export function EmptyState({ title, children, action, icon, className }: { title: string; children?: ReactNode; action?: ReactNode; icon?: ReactNode; className?: string }) {
  return <Card className={classes("ui-empty", className)}>{icon && <span className="ui-empty-icon" aria-hidden="true">{icon}</span>}<h2>{title}</h2>{children && <p>{children}</p>}{action}</Card>;
}
export function Skeleton({ label = "正在读取…", rows = 3 }: { label?: string; rows?: number }) {
  return <div role="status" aria-busy="true" aria-label={label} className="ui-skeleton">{Array.from({ length: rows }, (_, index) => <span aria-hidden="true" key={index} />)}</div>;
}
