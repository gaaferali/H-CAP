import { FormEvent, ReactNode, useEffect, useMemo, useState } from "react";
import { api, listItems } from "../api/api";
import type { PlatformUser } from "../api/api";
import type { Entity, Role } from "../types/types";
import { Badge, Field, FunctionHelp, Message, Panel, SelectField, Table, Textarea } from "../components";
import { TranslateTree, useTranslation } from "../i18n";
import { beneficiaryLabel, distributionEventLabel, enrollmentLabel, householdLabel, nfiBeneficiaryLabel, nfiEntitlementLabel, programLabel, readableCode, valueOf } from "../utils";
export function EnrollmentModalityPage({ role }: { role: Role }) {
  const [enrollments, setEnrollments] = useState<Entity[]>([]);
  const [programs, setPrograms] = useState<Entity[]>([]);
  const [eligibility, setEligibility] = useState<Entity[]>([]);
  const [selectedProgramId, setSelectedProgramId] = useState("");
  const [selectedHouseholdId, setSelectedHouseholdId] = useState("");
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const canDecide = role !== "FINANCE";
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
      await api.create("household-enrollments", {
        household: form.get("household"),
        program: selectedProgramId,
        status: form.get("status"),
        assistance_modality: form.get("assistance_modality"),
      });
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
  const options =
    program?.cash_enabled && program?.nfi_enabled
      ? ["CASH", "NFI", "CASH_NFI"]
      : program?.cash_enabled
        ? ["CASH"]
        : program?.nfi_enabled
          ? ["NFI"]
          : [];
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
            <SelectField
              label="Decision"
              name="status"
              options={["PENDING", "APPROVED", "REJECTED", "SUSPENDED"].map((value) => ({
                value,
                label: value,
              }))}
              required
            />
            <SelectField
              label="Assistance modality"
              name="assistance_modality"
              options={options.map((value) => ({
                value,
                label:
                  value === "CASH"
                    ? "Payment"
                    : value === "NFI"
                      ? "NFI"
                      : "Payment + NFI",
              }))}
              required
              disabled={!options.length}
            />
            <p className="form-text">
              Only modalities enabled for the selected program are available.
              A household must first have an eligible household review.
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
              valueOf(item, "assistance_modality") || "CASH",
              <Badge value={valueOf(item, "status") || "PENDING"} />,
              ])}
          />
        </Panel>
      </div>
    </div>
  );
}
