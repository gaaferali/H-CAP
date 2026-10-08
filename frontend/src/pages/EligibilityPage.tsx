import { FormEvent, ReactNode, useEffect, useMemo, useState } from "react";
import { api, listItems } from "../api/api";
import type { PlatformUser } from "../api/api";
import type { Entity, Role } from "../types/types";
import { Badge, Field, FunctionHelp, Message, Panel, SelectField, Table, Textarea } from "../components";
import { TranslateTree, useTranslation } from "../i18n";
import { beneficiaryLabel, distributionEventLabel, enrollmentLabel, householdLabel, nfiBeneficiaryLabel, nfiEntitlementLabel, programLabel, readableCode, valueOf } from "../utils";
export function EligibilityPage({ role }: { role: Role }) {
  const [programs, setPrograms] = useState<Entity[]>([]);
  const [households, setHouseholds] = useState<Entity[]>([]);
  const [selectedProgram, setSelectedProgram] = useState("");
  const [eligibility, setEligibility] = useState<Entity[]>([]);
  const [enrollments, setEnrollments] = useState<Entity[]>([]);
  const [signals, setSignals] = useState<Entity[]>([]);
  const [tasks, setTasks] = useState<Entity[]>([]);
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const canDecide = role !== "FINANCE";
  const load = async () => {
    try {
      const [
        programResponse,
        householdResponse,
        eligibilityResponse,
        enrollmentResponse,
      ] = await Promise.all([
        api.list<Entity>("programs"),
        api.list<Entity>("households"),
        api.list<Entity>("household-eligibility"),
        api.list<Entity>("household-enrollments"),
      ]);
      setPrograms(listItems(programResponse));
      setHouseholds(listItems(householdResponse));
      setEligibility(listItems(eligibilityResponse));
      setEnrollments(listItems(enrollmentResponse));
      if (canDecide) {
        const [signalResponse, taskResponse] = await Promise.all([
          api.list<Entity>("ai-signals"),
          api.list<Entity>("review-tasks"),
        ]);
        setSignals(listItems(signalResponse));
        setTasks(listItems(taskResponse));
      } else {
        setSignals([]);
        setTasks([]);
      }
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Could not load eligibility work.",
      );
    }
  };
  useEffect(() => {
    void load();
  }, [canDecide]);
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    setStatus("saving");
    setError("");
    try {
      await api.create("household-eligibility", {
        program: form.get("program"),
        household: form.get("household"),
        status: form.get("eligibility_status"),
      });
      formElement.reset();
      await load();
      setStatus("saved");
    } catch (reason) {
      setStatus("failed");
      setError(
        reason instanceof Error
          ? reason.message
          : "Could not create the eligibility review.",
      );
    }
  };
  const updateEligibility = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    setStatus("saving");
    setError("");
    try {
      await api.update(
        "household-eligibility",
        String(form.get("eligibility_enrollment")),
        { status: form.get("updated_eligibility_status") },
      );
      await load();
      setStatus("saved");
    } catch (reason) {
      setStatus("failed");
      setError(
        reason instanceof Error
          ? reason.message
          : "Could not update the eligibility status.",
      );
    }
  };
  return (
    <div className="row g-3">
      <div className="col-lg-5">
        {canDecide && <Panel title="Eligibility review">
          <form onSubmit={submit}>
            <SelectField
              label="Program"
              name="program"
              options={programs.map((program) => ({
                value: program.id,
                label: programLabel(program),
              }))}
              value={selectedProgram}
              onChange={setSelectedProgram}
              required
            />
            <SelectField
              label="Household"
              name="household"
              options={households.filter((item) => valueOf(item, "program") === selectedProgram).map((item) => ({
                value: item.id,
                label: householdLabel(item),
              }))}
              required
            />
            <SelectField
              label="Eligibility status"
              name="eligibility_status"
              options={["PENDING", "ELIGIBLE", "INELIGIBLE", "ON_HOLD"].map(
                (value) => ({ value, label: value }),
              )}
              required
            />
            <button className="btn btn-primary">Create human review</button>
          </form>
          <Message status={status} error={error} />
        </Panel>}
        {canDecide && <Panel title="Update eligibility status">
          <p className="section-lead">
             Select an existing household eligibility review to save a new status.
          </p>
          <form onSubmit={updateEligibility}>
            <SelectField
              label="Household eligibility"
              name="eligibility_enrollment"
              options={eligibility.map((item) => ({
                value: item.id,
                label: `${valueOf(item, "household_reference") || "Household"} — ${valueOf(item, "program_name") || "Program"}`,
              }))}
              required
            />
            <SelectField
              label="New eligibility status"
              name="updated_eligibility_status"
              options={["PENDING", "ELIGIBLE", "INELIGIBLE", "ON_HOLD"].map(
                (value) => ({ value, label: value }),
              )}
              required
            />
            <button className="btn btn-primary">Save eligibility status</button>
          </form>
          <Message status={status} error={error} />
        </Panel>}
      </div>
      <div className="col-lg-7">
        <Panel title="Eligibility and advisory work">
          <Table
            headings={[
              "Household",
              "Program",
              "Eligibility",
              "Members",
            ]}
            rows={eligibility.map((item) => [
              valueOf(item, "household_reference") || "Household",
              valueOf(item, "program_name"),
              <Badge
                value={valueOf(item, "status") || "PENDING"}
              />,
              valueOf(item, "member_count") || 0,
            ])}
          />
          <Table
            headings={["Open advisory signals", "Human review tasks", "Status"]}
            rows={[
              ...signals
                .slice(0, 5)
                .map((signal) => [
                  valueOf(signal, "signal_type"),
                  valueOf(signal, "reason"),
                  <Badge value={signal.status ?? "OPEN"} />,
                ]),
              ...tasks
                .slice(0, 5)
                .map((task) => [
                  valueOf(task, "task_type"),
                  valueOf(task, "resolution"),
                  <Badge value={task.status ?? "OPEN"} />,
                ]),
            ]}
          />
        </Panel>
      </div>
    </div>
  );
}
