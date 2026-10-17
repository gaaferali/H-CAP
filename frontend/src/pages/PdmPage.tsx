import { FormEvent, useEffect, useMemo, useState } from "react";
import { api, listItems } from "../api/api";
import type { PlatformUser } from "../api/api";
import type { Entity } from "../types/types";
import { Badge, Field, Message, Panel, SelectField, Table } from "../components";
import { programLabel, valueOf } from "../utils";
// Shows post-distribution feedback and readable payment impact summaries.
export function PdmPage({ user }: { user: PlatformUser }) {
  const [tenants, setTenants] = useState<Entity[]>([]);
  const [programs, setPrograms] = useState<Entity[]>([]);
  const [responses, setResponses] = useState<Entity[]>([]);
  const [tenantId, setTenantId] = useState("");
  const [programId, setProgramId] = useState("");
  const [summary, setSummary] = useState<Record<string, unknown> | null>(null);
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const canEnter = ["ADMIN", "FIELD_OFFICER", "MANAGER"].includes(user.role);
  const queryForSelection = () => {
    const query = new URLSearchParams();
    if (user.is_superuser && tenantId) query.set("tenant", tenantId);
    if (programId) query.set("program", programId);
    return query;
  };
  // Gets programs, feedback records, and the summary for the selected scope.
  const load = async () => {
    try {
      const query = queryForSelection();
      const [programResponse, tenantResponse, responseRecords] =
        await Promise.all([
          api.list<Entity>("programs"),
          user.is_superuser
            ? api.list<Entity>("tenants")
            : Promise.resolve([] as Entity[]),
          api.get<Entity[]>(`/pdm/${query.toString() ? `?${query}` : ""}`),
        ]);
      setPrograms(listItems(programResponse));
      setTenants(
        user.is_superuser
          ? listItems(tenantResponse as Entity[] | { results: Entity[] })
          : [],
      );
      setResponses(responseRecords);
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Could not load PDM records.",
      );
    }
  };
  const loadSummary = async () => {
    const query = queryForSelection();
    try {
      setError("");
      setSummary(
        await api.get(`/pdm/summary/${query.toString() ? `?${query}` : ""}`),
      );
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Could not load PDM summary.",
      );
    }
  };
  useEffect(() => {
    void load();
  }, [user.is_superuser, tenantId, programId]);
  useEffect(() => {
    if (programId) void loadSummary();
    else setSummary(null);
  }, [user.is_superuser, tenantId, programId]);
  const visiblePrograms = useMemo(
    () =>
      user.is_superuser
        ? tenantId
          ? programs.filter(
              (program) => valueOf(program, "tenant") === tenantId,
            )
          : []
        : programs,
    [programs, tenantId, user.is_superuser],
  );
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    setStatus("saving");
    setError("");
    try {
      await api.create("pdm", {
        program: form.get("program"),
        received_count: form.get("received_count"),
        access_problem_rate: form.get("access_problem_rate") || 0,
        satisfaction: form.get("satisfaction") || 0,
      });
      formElement.reset();
      await load();
      await loadSummary();
      setStatus("saved");
    } catch (reason) {
      setStatus("failed");
      setError(
        reason instanceof Error
          ? reason.message
          : "Could not submit the PDM response.",
      );
    }
  };
  const summaryChannels =
    Array.isArray(summary?.channels) && summary.channels.length
      ? (summary.channels as string[]).join(", ")
      : "Not available";
  const summaryLocations =
    Array.isArray(summary?.locations) && summary.locations.length
      ? (summary.locations as string[]).join(", ")
      : "Not available";
  const number = (key: string) => String(summary?.[key] ?? 0);
  return (
    <div className="row g-3">
      {canEnter && (
        <div className="col-lg-5">
          <Panel title="Post-Distribution Monitoring">
            <p className="form-text">
              Record and review whether assistance reached people as intended
              after distribution.
            </p>
            <form onSubmit={submit}>
              <SelectField
                label="Program"
                name="program"
                options={visiblePrograms.map((program) => ({
                  value: program.id,
                  label: programLabel(program),
                }))}
                value={programId}
                onChange={setProgramId}
                required
              />
              <div className="alert alert-light border" role="status">
                <div>
                  <strong>Beneficiaries paid:</strong>{" "}
                  {number("paid_beneficiaries")}
                </div>
                <div>
                  <strong>Already recorded:</strong>{" "}
                  {number("recorded_recipients")}
                </div>
                <div>
                  <strong>Available to record:</strong>{" "}
                  {number("remaining_recipients")}
                </div>
              </div>
              <Field
                label="People who received assistance"
                name="received_count"
                type="number"
                required
                help="Enter no more than the available recipients."
              />
              <div className="mb-3">
                <label className="form-label">Received rate</label>
                <div className="form-control-plaintext">
                  {number("received_rate")}%
                </div>
              </div>
              <div className="mb-3">
                <label className="form-label">Amount received</label>
                <div className="form-control-plaintext">
                  {number("amount_received")}
                </div>
              </div>
              <Field
                label="Access problem rate"
                name="access_problem_rate"
                type="number"
              />
              <div className="mb-3">
                <label className="form-label">Complaint rate</label>
                <div className="form-control-plaintext">
                  {number("complaint_rate")}%
                </div>
              </div>
              <Field label="Satisfaction" name="satisfaction" type="number" />
              <button
                className="btn btn-primary"
                disabled={
                  !programId || Number(summary?.remaining_recipients ?? 0) < 1
                }
              >
                Submit PDM response
              </button>
            </form>
            <Message status={status} error={error} />
          </Panel>
        </div>
      )}
      <div className={canEnter ? "col-lg-7" : "col-12"}>
        <Panel title="PDM summary">
          <div className="row">
            <div className="col-md-6">
              {user.is_superuser && (
                <SelectField
                  label="Tenant"
                  name="pdm-tenant"
                  options={tenants.map((tenant) => ({
                    value: tenant.id,
                    label: tenant.name ?? "Unnamed tenant",
                  }))}
                  value={tenantId}
                  onChange={(value) => {
                    setTenantId(value);
                    setProgramId("");
                  }}
                />
              )}
            </div>
            <div className="col-md-6">
              <SelectField
                label="Program"
                name="pdm-program"
                options={visiblePrograms.map((program) => ({
                  value: program.id,
                  label: programLabel(program),
                }))}
                value={programId}
                onChange={setProgramId}
              />
            </div>
          </div>
          <button
            className="btn btn-outline-primary mb-3"
            onClick={() => void loadSummary()}
          >
            View calculated summary
          </button>
          {summary && (
            <div className="row g-2">
              {[
                ["Budget amount", "budget_amount"],
                ["Payment entitlements", "total_payment_entitlements"],
                ["Committed payment amount", "committed_payment_amount"],
                ["Payment funding distributed", "successful_payment_funding"],
                ["Successful transaction fees", "successful_fees"],
                ["Total budget used", "budget_used"],
                ["Remaining budget", "budget_remaining"],
                ["Successful transactions", "successful_transactions"],
                ["Failed transactions", "failed_transactions"],
                ["Pending transactions", "pending_transactions"],
                ["Payment delivery reviews", "payment_delivery_reviews"],
                ["Received / partial / not received", "payments_received"],
                ["NFI entitled quantity", "nfi_entitled_quantity"],
                ["NFI delivered quantity", "nfi_distributed_quantity"],
                ["NFI pending delivery", "nfi_pending_delivery"],
              ].map(([label, key]) => (
                <div className="col-md-4" key={key}>
                  <div className="border rounded p-3 h-100">
                    <small>{label}</small>
                    <div className="fs-4 fw-semibold">
                      {key === "payments_received"
                        ? `${number("payments_received")} / ${number("payments_partially_received")} / ${number("payments_not_received")}`
                        : number(key)}
                    </div>
                  </div>
                </div>
              ))}
              <div className="col-md-4">
                <div className="border rounded p-3 h-100">
                  <small>Recipients recorded</small>
                  <div className="fs-4 fw-semibold">
                    {number("recorded_recipients")} /{" "}
                    {number("paid_beneficiaries")}
                  </div>
                  <small>{number("received_rate")}% received</small>
                </div>
              </div>
              <div className="col-md-4">
                <div className="border rounded p-3 h-100">
                  <small>Amount received</small>
                  <div className="fs-4 fw-semibold">
                    {number("amount_received")}
                  </div>
                  <small>Distributed: {number("distributed_amount")}</small>
                </div>
              </div>
              <div className="col-md-4">
                <div className="border rounded p-3 h-100">
                  <small>Complaints</small>
                  <div className="fs-4 fw-semibold">{number("complaints")}</div>
                  <small>{number("complaint_rate")}% complaint rate</small>
                </div>
              </div>
              <div className="col-md-6">
                <div className="border rounded p-3 h-100">
                  <strong>Channel</strong>
                  <div>{summaryChannels}</div>
                </div>
              </div>
              <div className="col-md-6">
                <div className="border rounded p-3 h-100">
                  <strong>Operational location</strong>
                  <div>{summaryLocations}</div>
                </div>
              </div>
            </div>
          )}
        </Panel>
        {summary && Array.isArray(summary.payment_transactions) && (
          <Panel title="Payment transaction financial detail">
            <Table headings={["Household", "Program", "Payment", "Fee", "Total impact", "Currency", "Status", "Channel"]} rows={(summary.payment_transactions as Entity[]).map((row) => [valueOf(row, "household_reference") || "Household", valueOf(row, "program_name") || "Program", valueOf(row, "amount"), valueOf(row, "fee"), valueOf(row, "total_impact"), valueOf(row, "currency"), <Badge value={valueOf(row, "status")} />, valueOf(row, "channel") || "Not recorded"])}/>
          </Panel>
        )}
        <Panel title="PDM responses">
          <Table
            headings={[
              "Program",
              "Recipients",
              "Channel",
              "Operational location",
              "Received rate",
              "Amount received",
              "Satisfaction",
            ]}
            rows={responses.map((response) => [
              valueOf(response, "program__name") || "Program",
              valueOf(response, "received_count"),
              valueOf(response, "channel") || "Not available",
              valueOf(response, "location") || "Not available",
              `${valueOf(response, "received_rate")}%`,
              valueOf(response, "amount_received"),
              valueOf(response, "satisfaction"),
            ])}
          />
        </Panel>
        <Message status="" error={error} />
      </div>
    </div>
  );
}
