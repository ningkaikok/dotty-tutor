import type { HTMLAttributes } from "react";
import { classes } from "./classes";
export type Tone = "neutral" | "brand" | "success" | "warning" | "info" | "danger";
export function Badge({ tone = "neutral", className, ...props }: HTMLAttributes<HTMLSpanElement> & { tone?: Tone }) {
  return <span {...props} className={classes("ui-badge", `ui-tone-${tone}`, className)} />;
}
/** Error feedback is announced immediately; other updates use a polite status region. */
export function Notice({ tone = "info", className, role, ...props }: HTMLAttributes<HTMLDivElement> & { tone?: Tone }) {
  return <div {...props} role={role ?? (tone === "danger" ? "alert" : "status")} className={classes("ui-notice", `ui-tone-${tone}`, className)} />;
}
