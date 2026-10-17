import type { ReactNode } from "react";
import type { Option } from "../types/types";
import { TranslateTree, useTranslation } from "../i18n";
export function Badge({ value }: { value: string }) {
  const translate = useTranslation();
  const tone = /FAILED|HIGH|CRITICAL|REJECTED/.test(value)
    ? "danger"
    : /PENDING|SUBMITTED|DRAFT|OPEN|HOLD/.test(value)
      ? "warning"
      : /ACTIVE|SUCCESS|VERIFIED|APPROVED|ELIGIBLE|RESOLVED|COMPLETED/.test(
            value,
          )
        ? "success"
        : "secondary";
  return <span className={`badge text-bg-${tone}`}>{translate(value)}</span>;
}

// Gives forms and record lists the same titled panel style across the platform.
export function Panel({
  title,
  children,
  action,
}: {
  title: string;
  children: ReactNode;
  action?: ReactNode;
}) {
  const translate = useTranslation();
  return (
    <section className="panel">
      <div className="panel-title">
        <h2>{translate(title)}</h2>
        {action}
      </div>
      <TranslateTree>{children}</TranslateTree>
    </section>
  );
}

export function FunctionHelp({ children }: { children: ReactNode }) {
  const translate = useTranslation();
  return (
    <details className="function-help">
      <summary>{translate("How to use this function")}</summary>
      <div className="function-help-body">{children}</div>
    </details>
  );
}

// Shows API records as a desktop table and readable cards on small screens.
export function Table({
  headings,
  rows,
  loading = false,
}: {
  headings: string[];
  rows: ReactNode[][];
  loading?: boolean;
}) {
  const translate = useTranslation();
  const tableClassName = headings.length > 7
    ? "table table-dense align-middle"
    : "table align-middle";
  return (
    <div className="table-responsive">
      <table className={tableClassName}>
        <thead>
          <tr>
            {headings.map((heading) => (
              <th scope="col" key={heading}>
                {translate(heading)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {loading ? (
            <tr>
              <td className="table-empty" colSpan={headings.length}>{translate("Loading")}</td>
            </tr>
          ) : rows.length ? (
            rows.map((row, rowIndex) => (
              <tr key={rowIndex}>
                {row.map((cell, cellIndex) => (
                  <td data-label={translate(headings[cellIndex] ?? "")} key={cellIndex}>
                    {typeof cell === "string" ? translate(cell) : cell}
                  </td>
                ))}
              </tr>
            ))
          ) : (
            <tr>
              <td className="table-empty" colSpan={headings.length}>
                {translate("No records found.")}
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

// Shows a short success, saving, or backend error message after an action.
export function Message({ status, error }: { status: string; error: string }) {
  const translate = useTranslation();
  if (!status && !error) return null;
  if (error)
    return (
      <div className="alert alert-danger mt-3" role="alert">
        {translate(error)}
      </div>
    );
  return (
    <div className="status-message mt-3" aria-live="polite">
      {status === "saving"
        ? translate("Saving. Do not submit again.")
        : status === "saved"
          ? translate("Saved successfully.")
          : ""}
    </div>
  );
}

export function Field({
  label,
  name,
  type = "text",
  required = false,
  help,
  defaultValue,
  value,
  disabled = false,
  readOnly = false,
}: {
  label: string;
  name: string;
  type?: string;
  required?: boolean;
  help?: string;
  defaultValue?: string;
  value?: string;
  disabled?: boolean;
  readOnly?: boolean;
}) {
  const translate = useTranslation();
  const id = `field-${name}`;
  const paymentOnly = [
    "transfer_amount",
    "payment_cycle",
    "planned_total",
  ].includes(name);
  return (
    <div className={`mb-3 ${paymentOnly ? "payment-only-field" : ""}`}>
      <label className="form-label" htmlFor={id}>
        {translate(label)}
        {required && !paymentOnly && " *"}
      </label>
      <input
        className="form-control"
        id={id}
        name={name}
        type={type}
        required={required && !paymentOnly}
        defaultValue={value === undefined ? defaultValue : undefined}
        value={value}
        disabled={disabled}
        readOnly={readOnly}
      />
      {help && <div className="form-text">{translate(help)}</div>}
    </div>
  );
}

export function SelectField({
  label,
  name,
  options,
  required = false,
  value,
  onChange,
  disabled = false,
}: {
  label: string;
  name: string;
  options: Option[];
  required?: boolean;
  value?: string;
  onChange?: (value: string) => void;
  disabled?: boolean;
}) {
  const translate = useTranslation();
  const id = `field-${name}`;
  const paymentOnly = ["payment_cycle"].includes(name);
  return (
    <div className={`mb-3 ${paymentOnly ? "payment-only-field" : ""}`}>
      <label className="form-label" htmlFor={id}>
        {translate(label)}
        {required && !paymentOnly && " *"}
      </label>
      <select
        className="form-select"
        id={id}
        name={name}
        required={required && !paymentOnly}
        value={value}
        onChange={(event) => onChange?.(event.target.value)}
        disabled={disabled}
      >
        <option value="">{translate("Select…")}</option>
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {translate(option.label)}
          </option>
        ))}
      </select>
    </div>
  );
}

export function Textarea({
  label,
  name,
  required = false,
  help,
  placeholder,
  value,
  onChange,
}: {
  label: string;
  name: string;
  required?: boolean;
  help?: string;
  placeholder?: string;
  value?: string;
  onChange?: (value: string) => void;
}) {
  const translate = useTranslation();
  const id = `field-${name}`;
  return (
    <div className="mb-3">
      <label className="form-label" htmlFor={id}>
        {translate(label)}
        {required && " *"}
      </label>
      <textarea
        className="form-control"
        id={id}
        name={name}
        required={required}
        rows={3}
        placeholder={placeholder ? translate(placeholder) : undefined}
        value={value}
        onChange={(event) => onChange?.(event.target.value)}
      />
      {help && <div className="form-text">{translate(help)}</div>}
    </div>
  );
}
