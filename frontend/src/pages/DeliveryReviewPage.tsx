import { FormEvent, useEffect, useState } from "react";
import { api, listItems } from "../api/api";
import type { Entity, Role } from "../types/types";
import { Badge, Field, Message, Panel, SelectField, Table, Textarea } from "../components";
import { distributionEventLabel, nfiEntitlementLabel, programLabel, valueOf } from "../utils";
// Shows payment and NFI delivery records that need a reviewer decision.
export function DeliveryReviewPage({ role }: { role: Role }) {
  const [programs, setPrograms] = useState<Entity[]>([]);
  const [allocations, setAllocations] = useState<Entity[]>([]);
  const [issues, setIssues] = useState<Entity[]>([]);
  const [paymentInstructions, setPaymentInstructions] = useState<Entity[]>([]);
  const [paymentReviews, setPaymentReviews] = useState<Entity[]>([]);
  const [paymentEvents, setPaymentEvents] = useState<Entity[]>([]);
  const [selectedPaymentId, setSelectedPaymentId] = useState("");
  const [selectedId, setSelectedId] = useState("");
  const [programId, setProgramId] = useState("");
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  // Gets programs, allocations, issues, payments, and their saved reviews.
  const load = async () => {
    setLoading(true);
    setError("");
    try {
      const [programResponse, allocationResponse, issueResponse, paymentResponse, reviewResponse] = await Promise.all([
        api.list<Entity>("programs"),
        api.list<Entity>("distribution-allocations"),
        api.list<Entity>("distribution-issues"),
        api.list<Entity>("payment-instructions"),
        api.list<Entity>("payment-delivery-reviews"),
      ]);
      setPrograms(listItems(programResponse));
      setAllocations(listItems(allocationResponse));
      setIssues(listItems(issueResponse));
      const paymentRows = listItems(paymentResponse);
      setPaymentInstructions(paymentRows);
      setPaymentReviews(listItems(reviewResponse));
      const eventGroups = await Promise.all(paymentRows.map((payment) => api.get<Entity[]>(`/payment-instructions/${payment.id}/events/`)));
      setPaymentEvents(eventGroups.flat());
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Could not load reviewer delivery work.",
      );
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => {
    void load();
  }, []);
  const pending = allocations.filter(
    (row) =>
      (!programId || valueOf(row, "program_id") === programId) &&
      !issues.some(
        (issue) =>
          valueOf(issue, "event") === valueOf(row, "event") &&
          valueOf(issue, "entitlement") === valueOf(row, "entitlement"),
      ),
  );
  const selected = pending.find((row) => row.id === selectedId);
  const pendingPayments = paymentInstructions.filter(
    (row) => ["SUCCESS", "FAILED", "REVERSED"].includes(valueOf(row, "status")) &&
      (!programId || valueOf(row, "program_id") === programId) &&
      !paymentReviews.some((review) => valueOf(review, "payment_instruction") === row.id),
  );
  const selectedPayment = pendingPayments.find((row) => row.id === selectedPaymentId);
  const visiblePaymentReviews = paymentReviews.filter(
    (row) => !programId || valueOf(row, "program") === programId,
  );
  const visiblePaymentEvents = paymentEvents.filter(
    (row) => !programId || valueOf(row, "program_id") === programId || valueOf(row, "program_name") === valueOf(programs.find((program) => program.id === programId), "name"),
  );
  const visibleIssues = issues.filter(
    (row) => !programId || valueOf(row, "program_id") === programId,
  );
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!selected) return;
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    setStatus("saving");
    setError("");
    try {
      await api.create("distribution-issues", {
        household: selected.household,
        beneficiary: selected.beneficiary,
        entitlement: selected.entitlement,
        item: selected.item,
        event: selected.event,
        planned_quantity: selected.allocated_quantity,
        actual_quantity: Number(form.get("actual_quantity")),
        delivery_status: form.get("delivery_status"),
        notes: form.get("notes"),
      });
      formElement.reset();
      setSelectedId("");
      await load();
      setStatus("saved");
    } catch (reason) {
      setStatus("failed");
      setError(
        reason instanceof Error
          ? reason.message
          : "Could not record delivery outcome.",
      );
    }
  };
  const canWrite =
    role === "REVIEWER" || role === "ADMIN" || role === "MANAGER";
  const submitPaymentReview = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!selectedPayment) return;
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    setStatus("saving");
    setError("");
    try {
      await api.create("payment-delivery-reviews", {
        payment_instruction: selectedPayment.id,
        delivery_status: form.get("delivery_status"),
        note: form.get("note"),
        evidence: form.get("evidence"),
      });
      formElement.reset();
      setSelectedPaymentId("");
      await load();
      setStatus("saved");
    } catch (reason) {
      setStatus("failed");
      setError(reason instanceof Error ? reason.message : "Could not record payment delivery review.");
    }
  };
  return (
    <div className="row g-3">
      <div className="col-12">
        <Panel title="Delivery Review">
          <p className="section-lead">
            Review NFI delivery outcomes and household payment receipt decisions without changing entitlement or eligibility records.
          </p>
          {canWrite && (
            <SelectField
              label="Program"
              name="review_program"
              options={programs.map((program) => ({ value: program.id, label: programLabel(program) }))}
              value={programId}
              onChange={(value) => {
                setProgramId(value);
                setSelectedPaymentId("");
                setSelectedId("");
              }}
              required
            />
          )}
        </Panel>
      </div>
      {canWrite && (
        <div className="col-xl-5">
          <Panel title="Record payment delivery decision">
            <form onSubmit={submitPaymentReview}>
              <SelectField label="Completed payment" name="payment_instruction" options={pendingPayments.map((row) => ({ value: row.id, label: `${valueOf(row, "household_reference") || "Household"} — ${valueOf(row, "program_name") || "Program"} — ${valueOf(row, "amount")} ${valueOf(row, "currency")}` }))} value={selectedPaymentId} onChange={setSelectedPaymentId} required />
              {selectedPayment && (
                <div className="alert alert-light border">
                  <div><strong>Household:</strong> {valueOf(selectedPayment, "household_reference")}</div>
                  <div><strong>Size:</strong> {valueOf(selectedPayment, "household_size")}</div>
                  <div><strong>Members:</strong> {(selectedPayment.household_members as Entity[] | undefined)?.map((member) => member.full_name).filter(Boolean).join(", ") || "Not recorded"}</div>
                  <div><strong>Program:</strong> {valueOf(selectedPayment, "program_name")}</div>
                  <div><strong>Payment:</strong> {valueOf(selectedPayment, "amount")} {valueOf(selectedPayment, "currency")}</div>
                  <div><strong>Fee / total:</strong> {valueOf(selectedPayment, "fee_amount") || 0} / {valueOf(selectedPayment, "total_financial_impact")}</div>
                  <div><strong>Channel:</strong> {valueOf(selectedPayment, "channel_name")}</div>
                  <div><strong>Payment status:</strong> {valueOf(selectedPayment, "status")}</div>
                  <div><strong>Provider reference:</strong> {valueOf(selectedPayment, "provider_reference") || "—"}</div>
                </div>
              )}
              <SelectField label="Payment delivery result" name="delivery_status" options={["RECEIVED", "PARTIALLY_RECEIVED", "NOT_RECEIVED", "NO_SHOW", "REJECTED", "RESCHEDULED", "EXCEPTION"].map((value) => ({ value, label: value }))} required />
              <Textarea label="Reviewer note" name="note" />
              <Textarea label="Evidence reference" name="evidence" />
              <button className="btn btn-primary" disabled={!selectedPayment || status === "saving"}>Save payment review</button>
            </form>
          </Panel>
        </div>
      )}
      <div className={canWrite ? "col-xl-7" : "col-12"}>
        <Panel title="Payment delivery history">
          <Table loading={loading} headings={["Household", "Program", "Payment", "Fee", "Total", "Channel", "Payment status", "Review", "Reviewer", "Time"]} rows={visiblePaymentReviews.map((row) => [valueOf(row, "household_reference") || "Household", valueOf(row, "program_name") || "Program", valueOf(row, "payment_amount"), valueOf(row, "fee_amount"), valueOf(row, "total_financial_impact"), valueOf(row, "channel_name"), valueOf(row, "payment_status"), <Badge value={valueOf(row, "delivery_status")} />, valueOf(row, "reviewer_name"), valueOf(row, "reviewed_at")])} />
        </Panel>
        <Panel title="Payment events reviewed">
          <Table loading={loading} headings={["Action", "Household", "Program", "Status", "Performed by", "Time"]} rows={visiblePaymentEvents.map((row) => [valueOf(row, "action"), valueOf(row, "household_reference"), valueOf(row, "program_name"), valueOf(row, "provider_status") || valueOf(row, "to_status"), valueOf(row, "actor_name") || "System", valueOf(row, "created_at")])} />
        </Panel>
      </div>
      {canWrite && (
        <div className="col-xl-5">
          <Panel title="Record reviewer delivery decision">
            <form onSubmit={submit}>
              <SelectField
                label="Pending distribution allocation"
                name="allocation"
                options={pending.map((row) => ({
                  value: row.id,
                  label: `${valueOf(row, "household_reference") || "Household"} — ${valueOf(row, "program_name") || "Program"} — ${valueOf(row, "item_name") || "NFI item"} — planned ${valueOf(row, "allocated_quantity")}`,
                }))}
                value={selectedId}
                onChange={setSelectedId}
                disabled={loading}
                required
              />
              {selected && (
                <div className="alert alert-light border">
                  <div>
                    <strong>Entitlement:</strong>{" "}
                    {nfiEntitlementLabel(selected)}
                  </div>
                  <div>
                    <strong>Program:</strong>{" "}
                    {valueOf(selected, "program_name") || "Program"}
                  </div>
                  <div>
                    <strong>Household:</strong>{" "}
                    {valueOf(selected, "household_reference") || "Household"}
                  </div>
                  <div>
                    <strong>Household size:</strong>{" "}
                    {valueOf(selected, "household_size")}
                  </div>
                  <div>
                    <strong>Member count:</strong>{" "}
                    {valueOf(selected, "household_member_count")}
                  </div>
                  <div>
                    <strong>Members:</strong>{" "}
                    {(selected.household_members as Entity[] | undefined)
                      ?.map((member) => member.full_name)
                      .filter(Boolean)
                      .join(", ") || "Not recorded"}
                  </div>
                  <div>
                    <strong>Item:</strong>{" "}
                    {valueOf(selected, "item_name") || "NFI item"}
                  </div>
                  <div>
                    <strong>Warehouse:</strong>{" "}
                    {valueOf(selected, "warehouse_name") || "Warehouse"}
                  </div>
                  <div>
                    <strong>Event:</strong> {distributionEventLabel(selected)}
                  </div>
                  <div>
                    <strong>Planned quantity:</strong>{" "}
                    {valueOf(selected, "allocated_quantity")}
                  </div>
                </div>
              )}
              <Field
                label="Actual delivered quantity"
                name="actual_quantity"
                type="number"
                required
              />
              <SelectField
                label="Delivery result"
                name="delivery_status"
                options={[
                  "RECEIVED",
                  "PARTIALLY_RECEIVED",
                  "NOT_RECEIVED",
                  "NO_SHOW",
                  "REJECTED",
                  "DAMAGED",
                  "LOST",
                  "RESCHEDULED",
                ].map((value) => ({ value, label: value }))}
                required
              />
              <Textarea label="Reviewer note or evidence" name="notes" />
              <button className="btn btn-primary" disabled={!selected || status === "saving"}>
                Save delivery result
              </button>
            </form>
            <Message status={status} error={error} />
            {!loading && pending.length === 0 && (
              <p className="form-text">No pending distribution allocations are available.</p>
            )}
          </Panel>
        </div>
      )}
      <div className={canWrite ? "col-xl-7" : "col-12"}>
        <Panel title="Delivery history">
          <Table
            loading={loading}
            headings={[
              "Event",
              "Program",
              "Household",
              "Item",
              "Warehouse",
              "Entitlement",
              "Planned",
              "Actual",
              "Status",
              "Notes",
            ]}
            rows={visibleIssues.map((row) => [
              distributionEventLabel(row),
              valueOf(row, "program_name") || "Program",
              valueOf(row, "household_reference") || "Household",
              valueOf(row, "item_name") || "NFI item",
              valueOf(row, "warehouse_name") || "Warehouse",
              nfiEntitlementLabel(row),
              valueOf(row, "planned_quantity"),
              valueOf(row, "actual_quantity"),
              <Badge value={valueOf(row, "delivery_status")} />,
              valueOf(row, "notes"),
            ])}
          />
        </Panel>
        {!canWrite && <Message status="" error={error} />}
      </div>
    </div>
  );
}
