import { FormEvent, ReactNode, useEffect, useMemo, useState } from "react";
import { api, listItems } from "../api/api";
import type { PlatformUser } from "../api/api";
import type { Entity, Role } from "../types/types";
import { Badge, Field, FunctionHelp, Message, Panel, SelectField, Table, Textarea } from "../components";
import { TranslateTree, useTranslation } from "../i18n";
import { beneficiaryLabel, distributionEventLabel, enrollmentLabel, householdLabel, nfiBeneficiaryLabel, nfiEntitlementLabel, programLabel, readableCode, valueOf } from "../utils";
export function ReviewerDeliveryPage({ role }: { role: Role }) {
  const [nfi, setNfi] = useState<Entity[]>([]);
  const [events, setEvents] = useState<Entity[]>([]);
  const [issues, setIssues] = useState<Entity[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const load = async () => {
    try {
      const [nfiResponse, eventResponse, issueResponse] = await Promise.all([
        api.list<Entity>("nfi-entitlements"),
        api.list<Entity>("distribution-events"),
        api.list<Entity>("distribution-issues"),
      ]);
      setNfi(listItems(nfiResponse));
      setEvents(listItems(eventResponse));
      setIssues(listItems(issueResponse));
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Could not load delivery records.",
      );
    }
  };
  useEffect(() => {
    void load();
  }, []);
  const selected = nfi.find((row) => row.id === selectedId);
  const record = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setStatus("saving");
    setError("");
    try {
      await api.create("distribution-issues", {
        household: selected?.household,
        beneficiary: selected?.beneficiary,
        entitlement: selected?.id,
        item: selected?.item,
        event: form.get("event"),
        planned_quantity: selected?.quantity,
        actual_quantity: Number(form.get("actual_quantity")),
        delivery_status: form.get("delivery_status"),
        notes: form.get("notes"),
      });
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
  return (
    <div className="row g-3">
      <div className="col-12">
        <Panel title="Reviewer NFI delivery decisions">
          <p className="section-lead">
            Record the final delivery result for approved household NFI
            entitlements. Stock, entitlement status, and the audit trail are
            updated by the backend.
          </p>
          <Message status={status} error={error} />
        </Panel>
      </div>
      {role === "REVIEWER" && (
        <div className="col-xl-5">
          <Panel title="Record delivery outcome">
            <form onSubmit={record}>
              <SelectField
                label="NFI entitlement"
                name="entitlement"
                options={nfi.map((row) => ({
                  value: row.id,
                  label: `${nfiEntitlementLabel(row)} (${valueOf(row, "quantity")})`,
                }))}
                value={selectedId}
                onChange={setSelectedId}
                required
              />
              <SelectField
                label="Distribution event"
                name="event"
                options={events.map((row) => ({
                  value: row.id,
                  label: distributionEventLabel(row),
                }))}
                required
              />
              <Field
                label="Actual quantity received"
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
              <button className="btn btn-primary" disabled={!selected}>
                Record reviewer decision
              </button>
            </form>
          </Panel>
        </div>
      )}
      <div className={role === "REVIEWER" ? "col-xl-7" : "col-12"}>
        <Panel title="Delivery history">
          <Table
            headings={[
              "Entitlement",
              "Event",
              "Program",
              "Household",
              "Household size",
              "Member count",
              "Planned",
              "Actual",
              "Item",
              "Warehouse",
              "Result",
              "Members",
              "Notes",
            ]}
            rows={issues.map((row) => [
              nfiEntitlementLabel(row),
              distributionEventLabel(row),
              valueOf(row, "program_name") || "Program",
              valueOf(row, "household_reference") || "Household",
              valueOf(row, "household_size"),
              valueOf(row, "household_member_count"),
              valueOf(row, "planned_quantity"),
              valueOf(row, "actual_quantity"),
              valueOf(row, "item_name") || "NFI item",
              valueOf(row, "warehouse_name") || "Warehouse",
              <Badge value={valueOf(row, "delivery_status")} />,
              (row.household_members as Entity[] | undefined)
                ?.map((member) => member.full_name)
                .filter(Boolean)
                .join(", ") || "Not recorded",
              valueOf(row, "notes"),
            ])}
          />
        </Panel>
      </div>
    </div>
  );
}
