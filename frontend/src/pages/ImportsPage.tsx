import { useEffect, useState } from "react";
import { api, listItems } from "../api/api";
import type { Entity } from "../types/types";
import { Badge, Message, Panel, SelectField, Table } from "../components";
import { programLabel, readableCode } from "../utils";

const readableImportValue = (key: string, value: unknown) => {
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (key === "status") return readableCode(value);
  if (value === null || value === undefined || value === "") return "Not recorded";
  return String(value);
};

export function ImportsPage() {
  const [programs, setPrograms] = useState<Entity[]>([]);
  const [programId, setProgramId] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [confirmationToken, setConfirmationToken] = useState("");
  const [result, setResult] = useState<Record<string, unknown> | null>(null);
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  useEffect(() => {
    api
      .list<Entity>("programs")
      .then((response) => setPrograms(listItems(response)))
      .catch((reason) =>
        setError(
          reason instanceof Error ? reason.message : "Could not load programs.",
        ),
      );
  }, []);
  const preview = async () => {
    if (!file || !programId) {
      setError("Select a program and a CSV or XLSX file.");
      return;
    }
    setStatus("saving");
    setError("");
    try {
      const response = await api.importFile<Record<string, unknown>>(
        file,
        programId,
      );
      setResult(response);
      setConfirmationToken(String(response.confirmation_token ?? ""));
      setStatus("saved");
    } catch (reason) {
      setStatus("failed");
      setError(
        reason instanceof Error
          ? reason.message
          : "Could not preview the import.",
      );
    }
  };
  const confirm = async () => {
    if (!file || !programId || !confirmationToken) return;
    setStatus("saving");
    setError("");
    try {
      setResult(
        await api.importFile<Record<string, unknown>>(
          file,
          programId,
          confirmationToken,
        ),
      );
      setConfirmationToken("");
      setStatus("saved");
    } catch (reason) {
      setStatus("failed");
      setError(
        reason instanceof Error ? reason.message : "Could not save valid rows.",
      );
    }
  };
  return (
    <Panel title="Controlled CSV/XLSX import">
      <p>
        Required columns: client_generated_id, household_size, location,
        national_id (or legacy beneficiary_number), full_name. The import file
        has its own client reference column; online household registration does
        not require it.
      </p>
      <SelectField
        label="Program"
        name="import-program"
        options={programs.map((program) => ({
          value: program.id,
          label: programLabel(program),
        }))}
        value={programId}
        onChange={(value) => {
          setProgramId(value);
          setConfirmationToken("");
        }}
        required
      />
      <div className="mb-3">
        <label className="form-label" htmlFor="import-file">
          CSV or XLSX file *
        </label>
        <input
          className="form-control"
          id="import-file"
          type="file"
          accept=".csv,.xlsx"
          onChange={(event) => {
            setFile(event.target.files?.[0] ?? null);
            setConfirmationToken("");
          }}
        />
      </div>
      <button className="btn btn-primary me-2" onClick={() => void preview()}>
        Preview and validate
      </button>
      {confirmationToken && (
        <button className="btn btn-success" onClick={() => void confirm()}>
          Confirm and save valid rows
        </button>
      )}
      {result && (
        <Table
          headings={["Result", "Value"]}
          rows={Object.entries(result)
            .filter(
              ([key]) =>
                !["errors", "preview_rows", "confirmation_token"].includes(key),
            )
            .map(([key, value]) => [
              readableCode(key),
              key === "status" ? <Badge value={String(value)} /> : readableImportValue(key, value),
            ])}
        />
      )}
      {Array.isArray(result?.preview_rows) && result.preview_rows.length > 0 && (
        <>
          <h3 className="mt-4">Validated row preview</h3>
          <p className="form-text">
            Review these normalized values before confirming the import. National IDs remain protected references.
          </p>
          <Table
            headings={["Client reference", "Full name", "Location", "Household size", "Verification", "Consent"]}
            rows={(result.preview_rows as Record<string, unknown>[]).map((row) => [
              String(row.client_id || "Not recorded"),
              String(row.full_name || "Not recorded"),
              String(row.location || "Not recorded"),
              String(row.size || "Not recorded"),
              <Badge value={String(row.verification_status || "PENDING")} />,
              row.consent_given ? "Yes" : "No",
            ])}
          />
        </>
      )}
      {Array.isArray(result?.errors) && (
        <Table
          headings={["Row", "Field", "Message"]}
          rows={(
            result.errors as Array<{
              row: number;
              field: string;
              message: string;
            }>
          ).map((item) => [String(item.row), item.field, item.message])}
        />
      )}
      <Message status={status} error={error} />
    </Panel>
  );
}
