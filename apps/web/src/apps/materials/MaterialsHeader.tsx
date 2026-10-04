import type { ReactNode } from "react";
import { Link } from "react-router";
import "./materials.css";

export function MaterialsHeader({ backTo, backLabel, subtitle, actions }: {
  backTo: string; backLabel: string; subtitle: string; actions?: ReactNode;
}) {
  return <header className="topbar materials-header">
    <Link className="route-back-button" to={backTo}>← {backLabel}</Link>
    <div className="brand-mark" aria-hidden="true">D</div>
    <div className="materials-brand"><strong>Dotty</strong><span>{subtitle}</span></div>
    {actions && <div className="materials-header-actions">{actions}</div>}
  </header>;
}
