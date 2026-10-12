import { useId, type InputHTMLAttributes, type SelectHTMLAttributes, type TextareaHTMLAttributes, type ReactNode } from "react";
import { classes } from "./classes";

type FieldLabels = { label: ReactNode; hint?: ReactNode; error?: string };
function FieldFrame({ label, hint, error, id, className, children }: FieldLabels & { id: string; className?: string; children: ReactNode }) {
  return <div className={classes("ui-field", className)}><label htmlFor={id}>{label}</label>{children}
    {hint && <small id={`${id}-hint`}>{hint}</small>}{error && <small id={`${id}-error`} className="ui-field-error">{error}</small>}</div>;
}
function descriptionId(id: string, hint: ReactNode, error?: string, describedBy?: string) {
  return [describedBy, hint && `${id}-hint`, error && `${id}-error`].filter(Boolean).join(" ") || undefined;
}
/** Label, hint and validation errors stay associated with native input semantics. */
export function TextField({ label, hint, error, id, className, ...props }: InputHTMLAttributes<HTMLInputElement> & FieldLabels) {
  const generatedId = useId(); const fieldId = id ?? generatedId;
  return <FieldFrame label={label} hint={hint} error={error} id={fieldId} className={className}><input {...props} id={fieldId}
    aria-invalid={error ? true : props["aria-invalid"]} aria-describedby={descriptionId(fieldId, hint, error, props["aria-describedby"])} /></FieldFrame>;
}
export function SelectField({ label, hint, error, id, className, children, ...props }: SelectHTMLAttributes<HTMLSelectElement> & FieldLabels) {
  const generatedId = useId(); const fieldId = id ?? generatedId;
  return <FieldFrame label={label} hint={hint} error={error} id={fieldId} className={className}><select {...props} id={fieldId}
    aria-invalid={error ? true : props["aria-invalid"]} aria-describedby={descriptionId(fieldId, hint, error, props["aria-describedby"])}>{children}</select></FieldFrame>;
}
export function TextAreaField({ label, hint, error, id, className, ...props }: TextareaHTMLAttributes<HTMLTextAreaElement> & FieldLabels) {
  const generatedId = useId(); const fieldId = id ?? generatedId;
  return <FieldFrame label={label} hint={hint} error={error} id={fieldId} className={className}><textarea {...props} id={fieldId}
    aria-invalid={error ? true : props["aria-invalid"]} aria-describedby={descriptionId(fieldId, hint, error, props["aria-describedby"])} /></FieldFrame>;
}
