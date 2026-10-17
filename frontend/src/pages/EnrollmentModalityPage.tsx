import { FormEvent, useEffect, useState } from "react";
import { api, listItems } from "../api/api";
import type { Entity, Role } from "../types/types";
import { Badge, Message, Panel, SelectField, Table, Textarea } from "../components";
import { programLabel, valueOf } from "../utils";
// Shows approved household enrollment and assistance modality decisions.
export function EnrollmentModalityPage({ role }: { role: Role }) {
  const [enrollments, setEnrollments] = useState<Entity[]>([]);
  const [programs, setPrograms] = useState<Entity[]>([]);
  const [eligibility, setEligibility] = useState<Entity[]>([]);
  const [selectedProgramId, setSelectedProgramId] = useState("");
  const [selectedHouseholdId, setSelectedHouseholdId] = useState("");
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const canDecide = role !== "FINANCE";
  // Gets saved enrollments and the eligible households available for a new decision.
  const load = async () => {
    try {
      const enrollmentResponse = await api.list<Entity>("household-enrollments");
      setEnrollments(listItems(enrollmentResponse));
      if (role !== "FINANCE") {
        const [programResponse, eligibilityResponse] = await Promise.all([
          api.list<Entity>("programs"),
          api.list<Entity>("household-eligibility"),
        ]);
        setPrograms(listItems(programResponse));
        setEligibility(listItems(eligibilityResponse));
      }
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Could not load enrollment decisions.",
      );
    }
  };
  useEffect(() => {
    void load();
  }, []);
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    setStatus("saving");
    setError("");
    try {
      const household = String(form.get("household"));
      const modalityDecisions = ["CASH", "NFI", "MEDICAL"].filter((modality) => form.get(`${modality.toLowerCase()}_status`)).map((modality) => ({
        modality,
        status: form.get(`${modality.toLowerCase()}_status`),
        note: form.get("note") || "",
      }));
      const payload = {
        household,
        program: selectedProgramId,
        modality_decisions: modalityDecisions,
        note: form.get("note") || "",
      };
      const existing = enrollments.find((row) => valueOf(row, "household") === household && valueOf(row, "program") === selectedProgramId);
      if (existing) await api.update("household-enrollments", existing.id, payload);
      else await api.create("household-enrollments", payload);
      formElement.reset();
      setSelectedHouseholdId("");
      await load();
      setStatus("saved");
    } catch (reason) {
      setStatus("failed");
      setError(
        reason instanceof Error
          ? reason.message
          : "Could not save the household enrollment.",
      );
    }
  };
  const program = programs.find((row) => row.id === selectedProgramId);
  const eligibleHouseholds = eligibility.filter(
    (row) =>
      valueOf(row, "program") === selectedProgramId &&
      valueOf(row, "status") === "ELIGIBLE",
  );
  const enabledModalities = [
    program?.cash_enabled ? ["cash", "Payment / Cash"] : null,
    program?.nfi_enabled ? ["nfi", "NFI / In-Kind"] : null,
    program?.medical_enabled ? ["medical", "Medical Assistance"] : null,
  ].filter(Boolean) as string[][];
  return (
    <div className="row g-3">
      {canDecide && <div className="col-lg-5">
        <Panel title="Household enrollment and modality">
          <form onSubmit={submit}>
            <SelectField
              label="Program"
              name="program"
              options={programs.map((item) => ({
                value: item.id,
                label: programLabel(item),
              }))}
              value={selectedProgramId}
              onChange={(value) => {
                setSelectedProgramId(value);
                setSelectedHouseholdId("");
              }}
              required
            />
            <SelectField
              label="Eligible household"
              name="household"
              options={eligibleHouseholds.map((item) => ({
                value: valueOf(item, "household"),
                label: `${valueOf(item, "household_reference") || "Household"} — ${valueOf(item, "member_count") || 0} members`,
              }))}
              value={selectedHouseholdId}
              onChange={setSelectedHouseholdId}
              disabled={!selectedProgramId}
              required
            />
            {enabledModalities.map(([key, label]) => <SelectField key={key} label={`${label} decision`} name={`${key}_status`} options={["PENDING", "APPROVED", "REJECTED", "SUSPENDED"].map((value) => ({ value, label: value }))} required />)}
            <Textarea label="Decision note" name="note" />
            <p className="form-text">
              Each enabled modality receives its own decision. A household must first have an eligible household review.
            </p>
            <button className="btn btn-primary">
              Save enrollment decision
            </button>
          </form>
          <Message status={status} error={error} />
        </Panel>
      </div>}
      <div className={canDecide ? "col-lg-7" : "col-12"}>
        <Panel title="Enrollment history">
          <Table
            headings={[
              "Household",
              "Program",
              "Members",
              "Modality",
              "Decision",
            ]}
            rows={enrollments
              .filter((item) => role !== "FINANCE" || ["APPROVED", "ACCEPTED"].includes(valueOf(item, "status")))
              .map((item) => [
              valueOf(item, "household_reference") || "Household",
              valueOf(item, "program_name"),
              valueOf(item, "member_count") || 0,
              Array.isArray(item.modality_decision_details) && item.modality_decision_details.length
                ? (item.modality_decision_details as Entity[]).map((decision) => `${valueOf(decision, "modality")}: ${valueOf(decision, "status")}`).join(" · ")
                : valueOf(item, "assistance_modality") || "CASH",
              <Badge value={valueOf(item, "status") || "PENDING"} />,
              ])}
          />
        </Panel>
      </div>
    </div>
  );
}
