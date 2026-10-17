import { FormEvent, useEffect, useState } from "react";
import { api, listItems } from "../api/api";
import type { Entity, Role } from "../types/types";
import { Badge, Field, Message, Panel, SelectField, Table, Textarea } from "../components";
import { distributionEventLabel, nfiEntitlementLabel, programLabel, valueOf } from "../utils";
import { MedicalPage } from "./MedicalPage";

type DistributionModality = "NFI" | "MEDICAL";

// Shows NFI event planning, entitlement allocation, and saved distribution records.
function NfiDistributionEventsPanel({ role }: { role: Role }) {
  const [programs, setPrograms] = useState<Entity[]>([]);
  const [warehouses, setWarehouses] = useState<Entity[]>([]);
  const [entitlements, setEntitlements] = useState<Entity[]>([]);
  const [events, setEvents] = useState<Entity[]>([]);
  const [allocations, setAllocations] = useState<Entity[]>([]);
  const [programId, setProgramId] = useState("");
  const [warehouseId, setWarehouseId] = useState("");
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  // Gets programs, warehouses, entitlements, events, and allocations for this page.
  const load = async () => {
    setLoading(true);
    setError("");
    try {
      const [
        programResponse,
        warehouseResponse,
        entitlementResponse,
        eventResponse,
        allocationResponse,
      ] = await Promise.all([
        api.list<Entity>("programs"),
        api.list<Entity>("warehouses"),
        api.list<Entity>("nfi-entitlements"),
        api.list<Entity>("distribution-events"),
        api.list<Entity>("distribution-allocations"),
      ]);
      setPrograms(
        listItems(programResponse).filter((row) => Boolean(row.nfi_enabled)),
      );
      setWarehouses(listItems(warehouseResponse));
      setEntitlements(listItems(entitlementResponse));
      setEvents(listItems(eventResponse));
      setAllocations(listItems(allocationResponse));
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Could not load distribution events.",
      );
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => {
    void load();
  }, []);
  const eligible = entitlements.filter(
    (row) => {
      const allocated = allocations
        .filter((allocation) => valueOf(allocation, "entitlement") === row.id)
        .reduce(
          (total, allocation) =>
            total + Number(allocation.allocated_quantity ?? 0),
          0,
        );
      return (
        valueOf(row, "program") === programId &&
        valueOf(row, "warehouse") === warehouseId &&
        valueOf(row, "status") === "ACTIVE" &&
        Boolean(valueOf(row, "household")) &&
        Number(row.quantity ?? 0) - allocated > 0
      );
    },
  );
  const toggle = (id: string) =>
    setSelectedIds((current) =>
      current.includes(id)
        ? current.filter((row) => row !== id)
        : [...current, id],
    );
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    setStatus("saving");
    setError("");
    try {
      await api.create("distribution-events", {
        program: programId,
        warehouse: warehouseId,
        event_date: form.get("event_date"),
        location: form.get("location"),
        distribution_team: form.get("distribution_team"),
        notes: form.get("notes"),
        status: "PLANNED",
        entitlement_ids: selectedIds,
      });
      formElement.reset();
      setSelectedIds([]);
      await load();
      setStatus("saved");
    } catch (reason) {
      setStatus("failed");
      setError(
        reason instanceof Error
          ? reason.message
          : "Could not create distribution event.",
      );
    }
  };
  const canWrite = ["ADMIN", "MANAGER"].includes(role);
  return (
    <div className="row g-3">
      <div className="col-12">
        <Panel title="Distribution Events">
          <p className="section-lead">
            Events use exact existing NFI entitlements. Creating an event
            creates matching allocations only; it does not reserve stock a
            second time.
          </p>
        </Panel>
      </div>
      {canWrite && (
        <div className="col-xl-5">
          <Panel title="Create distribution event">
            <form onSubmit={submit}>
              <SelectField
                label="NFI-enabled program"
                name="program"
                options={programs.map((row) => ({
                  value: row.id,
                  label: programLabel(row),
                }))}
                value={programId}
                onChange={(value) => {
                  setProgramId(value);
                  setWarehouseId("");
                  setSelectedIds([]);
                }}
                required
              />
              <SelectField
                label="Warehouse"
                name="warehouse"
                options={warehouses
                  .filter((row) => valueOf(row, "program") === programId)
                  .map((row) => ({
                    value: row.id,
                    label: row.name ?? "Warehouse",
                  }))}
                value={warehouseId}
                onChange={(value) => {
                  setWarehouseId(value);
                  setSelectedIds([]);
                }}
                required
              />
              <Field
                label="Distribution date"
                name="event_date"
                type="date"
                required
              />
              <Field label="Location" name="location" required />
              <Field
                label="Responsible team"
                name="distribution_team"
                required
              />
              <Textarea label="Distribution notes" name="notes" />
              <div className="mb-3">
                <label className="form-label">
                  Existing household entitlements
                </label>
                {eligible.length ? (
                  eligible.map((row) => (
                    <div className="form-check" key={row.id}>
                      <input
                        className="form-check-input"
                        type="checkbox"
                        id={`entitlement-${row.id}`}
                        checked={selectedIds.includes(row.id)}
                        onChange={() => toggle(row.id)}
                      />
                      <label
                        className="form-check-label"
                        htmlFor={`entitlement-${row.id}`}
                      >
                        {nfiEntitlementLabel(row)} —
                        remaining{" "}
                        {Number(row.quantity ?? 0) -
                          allocations
                            .filter(
                              (allocation) =>
                                valueOf(allocation, "entitlement") === row.id,
                            )
                            .reduce(
                              (total, allocation) =>
                                total +
                                Number(allocation.allocated_quantity ?? 0),
                              0,
                            )}{" "}
                        of {valueOf(row, "quantity")}
                      </label>
                    </div>
                  ))
                ) : (
                  <p className="form-text">
                    {!programId
                      ? "Select a program to see eligible household entitlements."
                      : !warehouseId
                        ? "Select a warehouse to see remaining household entitlements."
                        : "No remaining NFI entitlements are available for this program and warehouse."}
                  </p>
                )}
              </div>
              <button
                className="btn btn-primary"
                disabled={!selectedIds.length || status === "saving"}
              >
                Create event from selected entitlements
              </button>
            </form>
            <Message status={status} error={error} />
          </Panel>
        </div>
      )}
      <div className={canWrite ? "col-xl-7" : "col-12"}>
        <Panel title="Distribution event history">
          <Table
            loading={loading}
            headings={[
              "Date",
              "Program",
              "Warehouse",
              "Location",
              "Status",
              "Notes",
            ]}
            rows={events.map((row) => [
              valueOf(row, "event_date"),
              valueOf(row, "program_name") || "Program",
              valueOf(row, "warehouse_name") || "Warehouse",
              valueOf(row, "location"),
              <Badge value={valueOf(row, "status")} />,
              valueOf(row, "notes"),
            ])}
          />
        </Panel>
        <Panel title="Event allocations">
          <Table
            loading={loading}
            headings={[
              "Event",
              "Program",
              "Household",
              "Household size",
              "Members",
              "Member names",
              "Item",
              "Planned",
              "Allocated",
            ]}
            rows={allocations.map((row) => [
              distributionEventLabel(row),
              valueOf(row, "program_name") || "Program",
              valueOf(row, "household_reference") || "Household",
              valueOf(row, "household_size"),
              valueOf(row, "household_member_count"),
              (row.household_members as Entity[] | undefined)
                ?.map((member) => member.full_name)
                .filter(Boolean)
                .join(", ") || "Not recorded",
              valueOf(row, "item_name") || "NFI item",
              valueOf(row, "planned_quantity"),
              valueOf(row, "allocated_quantity"),
            ])}
          />
        </Panel>
        {!canWrite && (
          <>
            <p className="form-text">
              Your existing role can view events but cannot create them.
            </p>
            <Message status="" error={error} />
          </>
        )}
      </div>
    </div>
  );
}

// Switches between the NFI and medical distribution workspaces.
export function DistributionEventsPage({ role, initialModality = "NFI" }: { role: Role; initialModality?: DistributionModality }) {
  const [modality, setModality] = useState<DistributionModality>(initialModality);
  return (
    <div className="row g-3">
      <div className="col-12">
        <Panel title="Distribution Events">
          <p className="section-lead">Plan and review NFI and Medical distribution records in one workspace. Completed issues remain immutable.</p>
          <div className="workspace-tabs" role="tablist" aria-label="Distribution modality">
            {(["NFI", "MEDICAL"] as DistributionModality[]).map((item) => (
              <button key={item} type="button" className={`btn ${modality === item ? "btn-primary" : "btn-outline-primary"}`} onClick={() => setModality(item)}>
                {item === "NFI" ? "NFI" : "Medical"}
              </button>
            ))}
          </div>
        </Panel>
      </div>
      <div className="col-12">
        {modality === "NFI" ? <NfiDistributionEventsPanel role={role} /> : <MedicalPage role={role} view="distribution" />}
      </div>
    </div>
  );
}
