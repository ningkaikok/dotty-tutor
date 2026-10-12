import type { ReactNode } from "react";
import { NavLink } from "react-router";
import { classes } from "./classes";
/** Route-aware navigation never highlights a parent alongside its exact child. */
export function Navigation({ items, label, children, className }: { items: ReadonlyArray<{ to: string; label: string }>; label: string; children?: ReactNode; className?: string }) {
  return <nav aria-label={label} className={classes("ui-navigation", className)}>{items.map((item) => <NavLink end to={item.to} key={item.to} className={({ isActive }) => classes("ui-nav-link", isActive && "active")}>{item.label}</NavLink>)}{children}</nav>;
}
