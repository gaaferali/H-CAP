import { FormEvent, useEffect, useState } from "react";
import { api, listItems } from "../api/api";
import type { Entity, Role } from "../types/types";
import { Badge, Field, Message, Panel, SelectField, Table } from "../components";
import { useTranslation } from "../i18n";
import { beneficiaryLabel, householdLabel, programLabel, valueOf } from "../utils";
// Shows household or beneficiary registration after loading the related program data.
export function Intake({ beneficiary, role }: { beneficiary: boolean; role: Role }) {
  const translate = useTranslation();
  const resource = beneficiary ? "beneficiaries" : "households";
  const title = beneficiary ? "Register beneficiary" : "Register household";
  const [records, setRecords] = useState<Entity[]>([]);
  const [programs, setPrograms] = useState<Entity[]>([]);
  const [households, setHouseholds] = useState<Entity[]>([]);
  const [selectedProgram, setSelectedProgram] = useState("");
  const [selectedHousehold, setSelectedHousehold] = useState(() =>
    beneficiary
      ? (sessionStorage.getItem("hcap_selected_household_id") ?? "")
      : "",
  );
  const [createdHousehold, setCreatedHousehold] = useState<Entity | null>(null);
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const [formOpen, setFormOpen] = useState(!beneficiary);
  const [search, setSearch] = useState("");
  const canWrite = !["FINANCE", "REVIEWER", "AUDITOR"].includes(role);
  // Gets saved records, programs, and households used by the form and table.
  const load = async () => {
    try {
      const [recordResponse, programResponse, householdResponse] = await Promise.all([
        api.list<Entity>(resource),
        api.list<Entity>("programs"),
        api.list<Entity>("households"),
      ]);
      setRecords(listItems(recordResponse));
      setPrograms(listItems(programResponse));
      setHouseholds(listItems(householdResponse));
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Could not load intake records.",
      );
    }
  };
  useEffect(() => {
    void load();
  }, [beneficiary]);
  useEffect(() => {
    if (
      beneficiary &&
      selectedHousehold &&
      households.length &&
      !households.some((household) => household.id === selectedHousehold)
    )
      setSelectedHousehold("");
  }, [beneficiary, households, selectedHousehold]);
  const save = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    setStatus("saving");
    setError("");
    try {
      if (beneficiary) {
        const nationalId = String(form.get("national_id") || "").trim();
        const payload: Record<string, unknown> = {
          household: selectedHousehold,
          national_id: nationalId,
          number: nationalId,
          full_name: form.get("full_name"),
          gender: form.get("gender"),
          date_of_birth: form.get("date_of_birth") || null,
          phone_number: form.get("phone_number"),
          consent_given: form.get("consent_given") === "on",
        };
        if (role !== "FIELD_OFFICER")
          payload.verification_status = form.get("verification_status");
        await api.create<Entity>("beneficiaries", payload);
      } else {
        const household = await api.create<Entity>("households", {
          program: form.get("program"),
          household_size: Number(form.get("household_size")),
          location: form.get("location"),
          registration_date: form.get("registration_date"),
        });
        setCreatedHousehold(household);
      }
      formElement.reset();
      await load();
      if (beneficiary) setFormOpen(false);
      setStatus("saved");
    } catch (reason) {
      setStatus("failed");
      setError(
        reason instanceof Error
          ? reason.message
          : "Correct the highlighted fields and try again.",
      );
    }
  };
  const addBeneficiary = () => {
    if (!createdHousehold) return;
    sessionStorage.setItem("hcap_selected_household_id", createdHousehold.id);
    location.hash = "#/intake/beneficiaries";
  };
  const canExitBeneficiary =
    beneficiary && ["ADMIN", "MANAGER"].includes(role);
  const visibleRecords = records.filter((record) => {
    const household = households.find((row) => row.id === valueOf(record, "household"));
    const recordProgram = valueOf(record, "program") || valueOf(household, "program");
    const matchesProgram = !selectedProgram || recordProgram === selectedProgram;
    const needle = search.trim().toLowerCase();
    const matchesSearch = !needle || [record.full_name, record.number, record.registration_reference, record.location]
      .some((value) => String(value ?? "").toLowerCase().includes(needle));
    return matchesProgram && matchesSearch;
  });
  const exitBeneficiary = async (record: Entity) => {
    const reason = window.prompt(`Enter the audit reason for exiting ${beneficiaryLabel(record)} from future assistance:`);
    if (!reason) return;
    setStatus("saving");
    setError("");
    try {
      await api.action(`/beneficiaries/${record.id}/exit/`, { reason });
      await load();
      setStatus("saved");
    } catch (reason) {
      setStatus("failed");
      setError(
        reason instanceof Error
          ? reason.message
          : "The beneficiary could not be exited.",
      );
    }
  };
  const withdrawHousehold = async (record: Entity) => {
    const reason = window.prompt(`Enter the audit reason for withdrawing ${householdLabel(record)} from this program:`);
    if (!reason) return;
    setStatus("saving");
    setError("");
    try {
      await api.action(`/households/${record.id}/withdraw/`, { reason });
      await load();
      setStatus("saved");
    } catch (reasonValue) {
      setStatus("failed");
      setError(reasonValue instanceof Error ? reasonValue.message : "The household could not be withdrawn.");
    }
  };
  if (beneficiary && role === "REVIEWER")
    return (
      <Panel title="Beneficiaries">
        <SelectField label="Program" name="review-program" options={programs.map((program) => ({ value: program.id, label: programLabel(program) }))} value={selectedProgram} onChange={setSelectedProgram} />
        <input className="form-control mb-3" aria-label={translate("Search beneficiaries")} placeholder={translate("Search by name or beneficiary number")} value={search} onChange={(event) => setSearch(event.target.value)} />
        <Table
          headings={[
            "National ID",
            "Household",
            "Full name",
            "Gender",
            "Phone number",
            "Program",
            "Verification",
          ]}
          rows={visibleRecords.map((record) => [
            valueOf(record, "national_id_reference") || "—",
            valueOf(record, "household_reference") || "Household",
            record.full_name ?? "—",
            valueOf(record, "gender") || "—",
            valueOf(record, "masked_phone") ||
              valueOf(record, "phone_number") ||
              "Not recorded",
            valueOf(record, "program_name") || "—",
            <Badge
              value={valueOf(record, "verification_status") || "PENDING"}
            />,
          ])}
        />
        <Message status="" error={error} />
      </Panel>
    );
  return (
    <div className="row g-3 stacked-sections">
      {beneficiary && <div className="col-12">
        <Panel title="Beneficiary registration">
          <SelectField label="Program" name="beneficiary-program-filter" options={programs.filter((program) => valueOf(program, "status") === "ACTIVE").map((program) => ({ value: program.id, label: programLabel(program) }))} value={selectedProgram} onChange={(value) => { setSelectedProgram(value); setSelectedHousehold(""); setFormOpen(false); }} required />
          {selectedProgram && <p className="form-text">{programLabel(programs.find((row) => row.id === selectedProgram) ?? { id: selectedProgram })} — only households and beneficiaries from this program are shown.</p>}
          {canWrite && selectedProgram && <button type="button" className="btn btn-primary" onClick={() => setFormOpen((open) => !open)}>{formOpen ? "Close beneficiary form" : "Add beneficiary"}</button>}
        </Panel>
      </div>}
      {canWrite && (!beneficiary || (selectedProgram && formOpen)) && <div className="col-12">
        <Panel title={title}>
          <form className="workflow-form" onSubmit={save}>
            {beneficiary ? (
              <>
                <SelectField
                  label="Household"
                  name="household"
                  options={households.filter((household) => valueOf(household, "program") === selectedProgram).map((household) => ({
                    value: household.id,
                    label: householdLabel(household),
                  }))}
                  value={selectedHousehold}
                  onChange={setSelectedHousehold}
                  required
                />
                <Field label="National ID" name="national_id" required />
                <Field label="Full name" name="full_name" required />
                <SelectField
                  label="Gender"
                  name="gender"
                  options={[
                    { value: "F", label: "Female" },
                    { value: "M", label: "Male" },
                  ]}
                />
                <Field label="Date of birth" name="date_of_birth" type="date" />
                <Field label="Phone number" name="phone_number" />
                <div className="form-check mb-3">
                  <input
                    className="form-check-input"
                    id="consent"
                    name="consent_given"
                    type="checkbox"
                  />
                  <label className="form-check-label" htmlFor="consent">
                    Consent given
                  </label>
                </div>
                {role !== "FIELD_OFFICER" && (
                  <SelectField
                    label="Verification status"
                    name="verification_status"
                    options={["PENDING", "VERIFIED", "REJECTED"].map(
                      (value) => ({ value, label: value }),
                    )}
                  />
                )}
              </>
            ) : (
              <>
                <SelectField
                  label="Program"
                  name="program"
                  options={programs.map((program) => ({
                    value: program.id,
                    label: programLabel(program),
                  }))}
                  required
                />
                <Field
                  label="Household size"
                  name="household_size"
                  type="number"
                  required
                />
                <Field label="Location" name="location" required />
                <Field
                  label="Registration date"
                  name="registration_date"
                  type="date"
                  required
                />
              </>
            )}
            <button className="btn btn-primary">
              Save {beneficiary ? "beneficiary" : "household"}
            </button>
          </form>
          {createdHousehold && (
            <div className="alert alert-success mt-3" role="status">
              <strong>Household saved:</strong>{" "}
              {householdLabel(createdHousehold)}
              <div className="mt-2">
                <button className="btn btn-success" onClick={addBeneficiary}>
                  Add beneficiary to this household
                </button>
              </div>
            </div>
          )}
          <Message status={status} error={error} />
        </Panel>
      </div>}
      <div className="col-12">
        <Panel title={beneficiary ? "Beneficiaries" : "Households"}>
          <input className="form-control mb-3" aria-label={translate("Search records")} placeholder={translate(beneficiary ? "Search by name or beneficiary number" : "Search by reference or location")} value={search} onChange={(event) => setSearch(event.target.value)} />
          {beneficiary ? (
            <Table
              headings={[
                "National ID",
                "Household",
                "Full name",
                "Gender",
                "Phone number",
                "Program",
                "Verification",
                "Actions",
              ]}
              rows={visibleRecords.map((record) => [
                valueOf(record, "national_id_reference") || "—",
                valueOf(record, "household_reference") || "Household",
                record.full_name ?? "—",
                valueOf(record, "gender") || "—",
                valueOf(record, "masked_phone") ||
                  valueOf(record, "phone_number") ||
                  "Not recorded",
                valueOf(record, "program_name") || "—",
                <Badge
                  value={valueOf(record, "verification_status") || "PENDING"}
                />,
                canExitBeneficiary && valueOf(record, "status") !== "EXITED" ? (
                  <button
                    className="btn btn-sm btn-outline-danger"
                    onClick={() => void exitBeneficiary(record)}
                  >
                    Exit from future assistance
                  </button>
                ) : (
                  "—"
                ),
              ])}
            />
          ) : (
            <Table
              headings={[
                "Registration reference",
                "Program",
                "Household size",
                "Location",
                "Registration date",
                "Status",
                "Actions",
              ]}
              rows={visibleRecords.map((record) => [
                valueOf(record, "registration_reference") || "System reference",
                valueOf(record, "program_name") || "Program",
                valueOf(record, "household_size"),
                valueOf(record, "location"),
                valueOf(record, "registration_date"),
                <Badge value={valueOf(record, "participation_status") || "ACTIVE"} />,
                ["ADMIN", "MANAGER"].includes(role) && valueOf(record, "participation_status") !== "WITHDRAWN" ? (
                  <button className="btn btn-sm btn-outline-danger" onClick={() => void withdrawHousehold(record)}>Withdraw</button>
                ) : "—",
              ])}
            />
          )}
        </Panel>
      </div>
    </div>
  );
}
