import { useEffect, useId, useRef, type ReactNode } from "react";
import { Button } from "./Button";
import { classes } from "./classes";

interface DialogProps {
  open: boolean;
  onClose: () => void;
  title: string;
  description?: ReactNode;
  children?: ReactNode;
  actions?: ReactNode;
  /** Confirmation dialogs pass false and put their own cancel action first, so it receives initial focus. */
  closeButton?: boolean;
  className?: string;
}

/** The browser owns focus trapping, Escape and return focus; callers own the open state. */
export function Dialog({ open, onClose, title, description, children, actions, closeButton = true, className }: DialogProps) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const descriptionId = useId();
  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);
  useEffect(() => { const dialog = ref.current; return () => { if (dialog?.open) dialog.close(); }; }, []);
  return (
    <dialog
      ref={ref}
      className={classes("ui-dialog", className)}
      aria-labelledby={titleId}
      aria-describedby={description ? descriptionId : undefined}
      onCancel={(event) => { event.preventDefault(); onClose(); }}
      onClose={onClose}
    >
      <header><h2 id={titleId}>{title}</h2>{closeButton && <Button autoFocus onClick={onClose}>关闭</Button>}</header>
      {description && <p id={descriptionId} className="ui-dialog-description">{description}</p>}
      {children && <div className="ui-dialog-body">{children}</div>}
      {actions && <footer className="ui-actions">{actions}</footer>}
    </dialog>
  );
}
