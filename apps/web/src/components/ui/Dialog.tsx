import { useEffect, useId, useRef, type ReactNode } from "react";
import { Button } from "./Button";

/** The browser owns focus trapping, Escape and return focus; callers own the open state. */
export function Dialog({ open, onClose, title, children, actions }: { open: boolean; onClose: () => void; title: string; children: ReactNode; actions?: ReactNode }) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);
  useEffect(() => { const dialog = ref.current; return () => { if (dialog?.open) dialog.close(); }; }, []);
  return <dialog ref={ref} className="ui-dialog" aria-labelledby={titleId} onCancel={(event) => { event.preventDefault(); onClose(); }} onClose={onClose}><header><h2 id={titleId}>{title}</h2><Button autoFocus onClick={onClose}>关闭</Button></header><div className="ui-dialog-body">{children}</div>{actions && <footer className="ui-actions">{actions}</footer>}</dialog>;
}
