import { FormEvent, ReactNode, useEffect, useMemo, useState } from "react";
import { api, listItems } from "../api/api";
import type { PlatformUser } from "../api/api";
import type { Entity, Role } from "../types/types";
import { Badge, Field, FunctionHelp, Message, Panel, SelectField, Table, Textarea } from "../components";
import { TranslateTree, useTranslation } from "../i18n";
import { beneficiaryLabel, distributionEventLabel, enrollmentLabel, householdLabel, nfiBeneficiaryLabel, nfiEntitlementLabel, programLabel, readableCode, valueOf } from "../utils";
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
              key.replaceAll("_", " "),
              typeof value === "object" ? JSON.stringify(value) : String(value),
            ])}
        />
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
