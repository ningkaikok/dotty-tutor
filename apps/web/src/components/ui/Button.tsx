import type { ButtonHTMLAttributes } from "react";
import { Link, type LinkProps } from "react-router";
import { classes } from "./classes";
export type ButtonVariant = "primary" | "secondary" | "accent" | "danger" | "quiet";
/** Native button semantics; busy work prevents duplicate actions without replacing its accessible name. */
export function Button({ variant = "secondary", busy = false, disabled, className, children, type = "button", ...props }: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: ButtonVariant; busy?: boolean }) {
  return <button {...props} type={type} disabled={disabled || busy} aria-busy={busy || undefined} className={classes("ui-button", `ui-button-${variant}`, className)}>{busy && <span className="ui-spinner" aria-hidden="true" />}{children}</button>;
}
/** Navigation stays a link, preserving new-tab and keyboard behavior. */
export function ButtonLink({ variant = "secondary", className, ...props }: LinkProps & { variant?: ButtonVariant }) {
  return <Link {...props} className={classes("ui-button", `ui-button-${variant}`, className)} />;
}
