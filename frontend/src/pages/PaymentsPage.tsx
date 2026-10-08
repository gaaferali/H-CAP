import { FormEvent, ReactNode, useEffect, useMemo, useState } from "react";
import { api, listItems } from "../api/api";
import type { PlatformUser } from "../api/api";
import type { Entity, Role } from "../types/types";
import { Badge, Field, FunctionHelp, Message, Panel, SelectField, Table, Textarea } from "../components";
import { TranslateTree, useTranslation } from "../i18n";
import { beneficiaryLabel, distributionEventLabel, enrollmentLabel, householdLabel, nfiBeneficiaryLabel, nfiEntitlementLabel, programLabel, readableCode, valueOf } from "../utils";
export function PaymentsPage({ role }: { role: Role }) {
  const [programs, setPrograms] = useState<Entity[]>([]);
  const [cashEntitlements, setCashEntitlements] = useState<Entity[]>([]);
  const [householdEnrollments, setHouseholdEnrollments] = useState<Entity[]>([]);
  const [batches, setBatches] = useState<Entity[]>([]);
  const [instructions, setInstructions] = useState<Entity[]>([]);
  const [channels, setChannels] = useState<Entity[]>([]);
  const [programId, setProgramId] = useState("");
  const [householdId, setHouseholdId] = useState("");
  const [channelId, setChannelId] = useState("");
  const [batchId, setBatchId] = useState("");
  const [events, setEvents] = useState<Entity[]>([]);
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [channelsLoading, setChannelsLoading] = useState(false);
  const canWrite = ["ADMIN", "FINANCE", "MANAGER"].includes(role);
  const load = async () => {
    setLoading(true);
    setError("");
    try {
      const [
        programResponse,
        cashEntitlementResponse,
        enrollmentResponse,
        batchResponse,
        instructionResponse,
      ] = await Promise.all([
        api.list<Entity>("programs"),
        api.list<Entity>("cash-entitlements"),
        api.list<Entity>("household-enrollments"),
        api.list<Entity>("payment-batches"),
        api.list<Entity>("payment-instructions"),
      ]);
      setPrograms(
        listItems(programResponse).filter((program) => program.cash_enabled),
      );
      setCashEntitlements(listItems(cashEntitlementResponse));
      setHouseholdEnrollments(listItems(enrollmentResponse));
      setBatches(listItems(batchResponse));
      const instructionRows = listItems(instructionResponse);
      setInstructions(instructionRows);
      const eventGroups = await Promise.all(
        instructionRows.map((instruction) =>
          api.get<Entity[]>(`/payment-instructions/${instruction.id}/events/`),
        ),
      );
      setEvents(eventGroups.flat());
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Could not load simulated payment records.",
      );
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => {
    void load();
  }, []);
  useEffect(() => {
    const loadChannels = async () => {
      if (!programId) {
        setChannels([]);
        setChannelsLoading(false);
        return;
      }
      setChannelsLoading(true);
      try {
        setError("");
        setChannels(
          await api.get<Entity[]>(`/programs/${programId}/channels/`),
        );
      } catch (reason) {
        setError(
          reason instanceof Error
            ? reason.message
            : "Could not load simulated payment channels.",
        );
      } finally {
        setChannelsLoading(false);
      }
    };
    void loadChannels();
  }, [programId]);
  const createBatch = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    setStatus("saving");
    setError("");
    try {
      await api.create("payment-batches", {
        program: form.get("program"),
        name: form.get("name"),
        status: "DRAFT",
        provider_response: {},
      });
      formElement.reset();
      await load();
      setStatus("saved");
    } catch (reason) {
      setStatus("failed");
      setError(
        reason instanceof Error
          ? reason.message
          : "Could not create the simulated payment batch.",
      );
    }
  };
  const createInstruction = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    const selectedHousehold = eligibleHouseholds.find(
      (item) => valueOf(item, "household") === householdId,
    );
    if (!programId || !householdId || !selectedHousehold) {
      setError("Select an eligible, approved household.");
      return;
    }
    setStatus("saving");
    setError("");
    try {
      const payload: Record<string, unknown> = {
        household: householdId,
        channel_config: form.get("channel_config"),
        amount: form.get("amount"),
        currency: form.get("currency"),
      };
      if (form.get("batch")) payload.batch = form.get("batch");
      await api.create("payment-instructions", payload);
      formElement.reset();
      await load();
      setStatus("saved");
    } catch (reason) {
      setStatus("failed");
      setError(
        reason instanceof Error
          ? reason.message
          : "Could not create the simulated payment instruction.",
      );
    }
  };
  const simulate = async (
    instructionId: string,
    outcome: "submit" | "success" | "failure" | "retry" | "reversal",
  ) => {
    setStatus("saving");
    setError("");
    try {
      await api.simulatePayment(instructionId, outcome);
      await load();
      setStatus("saved");
    } catch (reason) {
      setStatus("failed");
      setError(
        reason instanceof Error
          ? reason.message
          : "Could not simulate this outcome.",
      );
    }
  };
  const chooseStatus = (instruction: Entity, nextStatus: string) => {
    const outcome =
      nextStatus === "SUBMITTED"
        ? "submit"
        : nextStatus === "SUCCESS"
          ? "success"
          : nextStatus === "FAILED"
            ? "failure"
            : null;
    if (outcome && nextStatus !== instruction.status)
      void simulate(instruction.id, outcome);
  };
  const paymentStatusOptions = (instruction: Entity) => {
    const current = String(instruction.status ?? "CREATED");
    return current === "CREATED"
      ? ["CREATED", "SUBMITTED"]
      : current === "SUBMITTED"
        ? ["SUBMITTED", "SUCCESS", "FAILED"]
        : [current];
  };
  const eligibleHouseholds = householdEnrollments.filter(
    (item) =>
      valueOf(item, "program") === programId &&
      valueOf(item, "eligibility_status") === "ELIGIBLE" &&
      valueOf(item, "status") === "APPROVED" &&
      ["CASH", "CASH_NFI"].includes(valueOf(item, "assistance_modality")),
  ).filter(
    (item, index, all) =>
      all.findIndex(
        (candidate) => valueOf(candidate, "household") === valueOf(item, "household"),
      ) === index,
  );
  const selectedChannel = channels.find((channel) => channel.id === channelId);
  const paymentFee = Number(selectedChannel?.fee_amount ?? 0);
  const paymentCurrency = valueOf(programs.find((program) => program.id === programId), "currency");
  const selectedBatches = batches.filter(
    (batch) => !programId || valueOf(batch, "program") === programId,
  );
  return (
    <div className="row g-3">
      <div className="col-xl-5">
        {canWrite && (
          <>
            <Panel title="Create simulated payment batch">
              <form onSubmit={createBatch}>
                <SelectField
                  label="Program"
                  name="program"
                  options={programs.map((program) => ({
                    value: program.id,
                    label: programLabel(program),
                  }))}
                  required
                />
                <Field label="Batch name" name="name" required />
                <button className="btn btn-outline-primary">
                  Create simulated batch
                </button>
              </form>
            </Panel>
            <Panel title="Create simulated payment instruction">
              <SelectField
                label="Payment program"
                name="payment-program"
                options={programs.map((program) => ({
                  value: program.id,
                  label: programLabel(program),
                }))}
                value={programId}
                onChange={(value) => {
                  setProgramId(value);
                  setHouseholdId("");
                  setChannelId("");
                  setBatchId("");
                }}
                required
              />
              <form onSubmit={createInstruction}>
                <SelectField
                  label="Approved household"
                  name="payment-household"
                  options={eligibleHouseholds.map((item) => ({
                    value: valueOf(item, "household"),
                    label: `${valueOf(item, "household_reference") || "Household"} â€” size ${valueOf(item, "household_size") || "?"} â€” ${valueOf(item, "member_count") || 0} members â€” ${valueOf(item, "assistance_modality")}`,
                  }))}
                  value={householdId}
                  onChange={setHouseholdId}
                  disabled={!programId}
                  required
                />
                {!programId && (
                  <p className="form-text">
                    Select a payment program before choosing a household.
                  </p>
                )}
                {programId && eligibleHouseholds.length === 0 && (
                  <p className="form-text">
                    No eligible and approved cash households are available for this program.
                  </p>
                )}
                <SelectField
                  label="Simulated payment channel"
                  name="channel_config"
                  options={channels.map((channel) => ({
                    value: channel.id,
                    label: `${valueOf(channel, "provider_name")} (${valueOf(channel, "channel_type")}) â€” fee ${valueOf(channel, "fee_amount") || 0} ${valueOf(channel, "currency")}`,
                  }))}
                  value={channelId}
                  onChange={setChannelId}
                  disabled={!programId || channelsLoading}
                  required
                />
                {channelsLoading && <p className="form-text">Loading</p>}
                <SelectField
                  label="Payment batch"
                  name="batch"
                  options={selectedBatches.map((batch) => ({
                    value: batch.id,
                    label: batch.name ?? "Simulated batch",
                  }))}
                  value={batchId}
                  onChange={setBatchId}
                />
                <Field label="Payment amount" name="amount" type="number" required />
                <Field
                  label="Currency"
                  name="currency"
                  value={paymentCurrency}
                  readOnly
                  required
                />
                <div className="alert alert-light border">
                  <div><strong>Fee amount:</strong> {paymentFee} {paymentCurrency}</div>
                  <div><strong>Total budget impact:</strong> payment amount plus {paymentFee} {paymentCurrency} fee</div>
                </div>
                <p className="form-text">
                  This uses the built-in simulator only. No external payment
                  provider is called.
                </p>
                <button className="btn btn-primary" disabled={status === "saving" || channelsLoading || !channelId}>
                  Create simulated instruction
                </button>
              </form>
            </Panel>
          </>
        )}
      </div>
      <div className="col-xl-7">
        <Panel title="Simulated payment instructions">
          <Table
            loading={loading}
            headings={[
              "Household",
              "Household size",
              "Members",
              "Member context",
              "Program",
              "Channel",
              "Entitlement amount",
              "Fee",
              "Total impact",
              "Status",
              "Actions",
            ]}
            rows={instructions.map((item) => {
              const entitlement = cashEntitlements.find(
                (record) => record.id === valueOf(item, "cash_entitlement"),
              );
              const householdEnrollment = householdEnrollments.find(
                (record) => record.id === valueOf(item, "enrollment"),
              );
              return [
                valueOf(entitlement, "household_reference") ||
                  valueOf(householdEnrollment, "household_reference") ||
                  "Household",
                  valueOf(entitlement, "household_size") ||
                    valueOf(householdEnrollment, "household_size") ||
                    "â€”",
                  valueOf(entitlement, "household_member_count") ||
                    valueOf(householdEnrollment, "member_count") ||
                    0,
                  (entitlement?.household_members as Entity[] | undefined)
                    ?.map((member) => member.full_name)
                    .filter(Boolean)
                    .join(", ") ||
                    valueOf(item, "beneficiary_name") ||
                    "Not recorded",
                valueOf(item, "program_name") || "Program",
                valueOf(item, "channel_name") || "Simulated channel",
                `${valueOf(item, "amount")} ${valueOf(item, "currency")}`,
                `${valueOf(item, "fee_amount") || 0} ${valueOf(item, "currency")}`,
                `${valueOf(item, "total_financial_impact") || valueOf(item, "amount")} ${valueOf(item, "currency")}`,
                canWrite ? (
                  <select
                    className="form-select form-select-sm"
                    aria-label={`Status for ${valueOf(item, "beneficiary_name") || "beneficiary"}`}
                    value={String(item.status ?? "CREATED")}
                    onChange={(event) => chooseStatus(item, event.target.value)}
                    disabled={status === "saving"}
                  >
                    {paymentStatusOptions(item).map((option) => (
                      <option key={option} value={option}>
                        {option}
                      </option>
                    ))}
                  </select>
                ) : (
                  <Badge value={item.status ?? "CREATED"} />
                ),
                canWrite && valueOf(item, "status") === "FAILED" ? (
                  <button className="btn btn-sm btn-outline-secondary" onClick={() => void simulate(item.id, "retry")}>Retry</button>
                ) : "â€”",
              ];
            })}
          />
        </Panel>
        <Panel title="Payment history">
          <Table
            loading={loading}
            headings={["Action", "Household", "Program", "Payment", "Fee", "Total impact", "Status", "Performed by", "Time", "Payment reference", "Details"]}
            rows={events.map((event) => [
              valueOf(event, "action") || valueOf(event, "event_type"),
              valueOf(event, "household_reference") || "Household",
              valueOf(event, "program_name") || "Program",
              valueOf(event, "payment_amount"),
              valueOf(event, "fee_amount") || 0,
              valueOf(event, "total_financial_impact"),
              valueOf(event, "provider_status") || valueOf(event, "to_status"),
              valueOf(event, "actor_name") || "System",
              valueOf(event, "created_at") ? new Date(valueOf(event, "created_at")).toLocaleString() : "Not recorded",
              valueOf(event, "provider_transaction_id") || valueOf(event, "payment_reference") || "Not provided",
              valueOf(event, "redacted_payload") && (event.redacted_payload as Entity).no_external_provider_call
                ? "Simulation only; no external provider was contacted."
                : "Payment status recorded.",
            ])}
          />
        </Panel>
        <Message status={status} error={error} />
      </div>
    </div>
  );
}

