import type { ReactNode } from "react";
import { AppHeader } from "../../components/ui";
import "./materials.css";

export function MaterialsHeader({ backTo, backLabel, subtitle, actions }: {
  backTo: string; backLabel: string; subtitle: string; actions?: ReactNode;
}) {
  return <AppHeader backTo={backTo} backLabel={backLabel} subtitle={subtitle} actions={actions} className="materials-header" />;
}
