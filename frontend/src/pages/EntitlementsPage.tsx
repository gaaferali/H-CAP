import { FormEvent, useEffect, useState } from "react";
import { api, listItems } from "../api/api";
import type { Entity, Role } from "../types/types";
import { Badge, Field, Message, Panel, SelectField, Table } from "../components";
import { nfiBeneficiaryLabel, programLabel, valueOf } from "../utils";
import { MedicalPage } from "./MedicalPage";

type EntitlementModality = "CASH" | "NFI" | "MEDICAL";

function EntitlementTabs({ value, onChange }: { value: EntitlementModality; onChange: (value: EntitlementModality) => void }) {
  return (
    <div className="workspace-tabs" role="tablist" aria-label="Assistance modality">
      {(["CASH", "NFI", "MEDICAL"] as EntitlementModality[]).map((modality) => (
        <button key={modality} type="button" className={`btn ${value === modality ? "btn-primary" : "btn-outline-primary"}`} onClick={() => onChange(modality)}>
          {modality === "CASH" ? "Cash" : modality === "NFI" ? "NFI" : "Medical"}
        </button>
      ))}
    </div>
  );
}

// Shows saved cash entitlements and the actions allowed for the current role.
function CashEntitlementsPanel({ role }: { role: Role }) {
  const [rows, setRows] = useState<Entity[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const loadCash = () => {
    setLoading(true);
    api.list<Entity>("cash-entitlements")
      .then((response) => setRows(listItems(response)))
      .catch((reason) => setError(reason instanceof Error ? reason.message : "Could not load cash entitlements."))
      .finally(() => setLoading(false));
  };
  useEffect(() => {
    loadCash();
  }, []);
  const canWrite = ["ADMIN", "MANAGER", "FINANCE"].includes(role);
  const correct = async (row: Entity) => {
    const amount = window.prompt("Enter the corrected cash amount:", valueOf(row, "amount"));
    if (!amount) return;
    const reason = window.prompt("Enter the audit reason for this correction:");
    if (!reason) return;
    try {
      await api.action(`/cash-entitlements/${row.id}/correct/`, { amount, reason });
      loadCash();
    } catch (reasonValue) {
      setError(reasonValue instanceof Error ? reasonValue.message : "Could not correct the cash entitlement.");
    }
  };
  const cancel = async (row: Entity) => {
    const reason = window.prompt("Enter the audit reason for cancelling this cash entitlement:");
    if (!reason) return;
    try {
      await api.action(`/cash-entitlements/${row.id}/cancel/`, { reason });
      loadCash();
    } catch (reasonValue) {
      setError(reasonValue instanceof Error ? reasonValue.message : "Could not cancel the cash entitlement.");
    }
  };
  return (
    <Panel title="Cash entitlements">
      <p className="section-lead">Cash amounts are created and submitted from the Payments page. This shared view is for review and status tracking.</p>
      <a className="btn btn-primary mb-3" href="#/payments">Open Payments</a>
      <Table loading={loading} headings={["Program", "Household", "Amount", "Currency", "Status", "Actions"]} rows={rows.map((row) => [
        valueOf(row, "program_name") || "Program",
        valueOf(row, "household_reference") || "Household",
        valueOf(row, "amount"),
        valueOf(row, "currency"),
        <Badge value={valueOf(row, "status")} />,
        canWrite && valueOf(row, "status") === "ACTIVE" ? <div className="d-flex flex-wrap gap-1"><button type="button" className="btn btn-sm btn-outline-primary" onClick={() => void correct(row)}>Correct</button><button type="button" className="btn btn-sm btn-outline-danger" onClick={() => void cancel(row)}>Cancel</button></div> : "—",
      ])} />
      <Message status="" error={error} />
    </Panel>
  );
}

// Gets eligible NFI beneficiaries and shows the saved item entitlements.
function NfiEntitlementsPanel({ role }: { role: Role }) {
  const [programs, setPrograms] = useState<Entity[]>([]);
  const [enrollments, setEnrollments] = useState<Entity[]>([]);
  const [warehouses, setWarehouses] = useState<Entity[]>([]);
  const [items, setItems] = useState<Entity[]>([]);
  const [entitlements, setEntitlements] = useState<Entity[]>([]);
  const [programId, setProgramId] = useState("");
  const [householdId, setHouseholdId] = useState("");
  const [warehouseId, setWarehouseId] = useState("");
  const [itemId, setItemId] = useState("");
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const load = async () => {
    setLoading(true);
    setError("");
    try {
      const [
        programResponse,
        enrollmentResponse,
        warehouseResponse,
        itemResponse,
        entitlementResponse,
      ] = await Promise.all([
        api.list<Entity>("programs"),
        api.list<Entity>("household-enrollments"),
        api.list<Entity>("warehouses"),
        api.list<Entity>("nfi-items"),
        api.list<Entity>("nfi-entitlements"),
      ]);
      setPrograms(listItems(programResponse));
      setEnrollments(listItems(enrollmentResponse));
      setWarehouses(listItems(warehouseResponse));
      setItems(listItems(itemResponse));
      setEntitlements(listItems(entitlementResponse));
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Could not load NFI entitlement data.",
      );
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => {
    void load();
  }, []);
  const validEnrollments = enrollments.filter(
    (row) =>
      Boolean(valueOf(row, "household")) &&
      valueOf(row, "program") === programId &&
      ["ACCEPTED", "APPROVED"].includes(valueOf(row, "status")) &&
      (["NFI", "CASH_NFI"].includes(valueOf(row, "assistance_modality")) ||
        ((row.modality_decision_details as Entity[] | undefined) ?? []).some((decision) => valueOf(decision, "modality") === "NFI" && valueOf(decision, "status") === "APPROVED")),
  );
  const eligibleHouseholds = validEnrollments.filter(
    (row, index, all) =>
      all.findIndex((candidate) => valueOf(candidate, "household") === valueOf(row, "household")) === index,
  );
  const programWarehouses = warehouses.filter(
    (row) => valueOf(row, "program") === programId && row.active !== false,
  );
  const itemOptions = items.filter(
    (row) =>
      valueOf(row, "program") === programId &&
      valueOf(row, "warehouse") === warehouseId &&
      row.active !== false &&
      Number(row.available_quantity ?? 0) > 0,
  );
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    setStatus("saving");
    setError("");
    try {
      await api.create("nfi-entitlements", {
        program: programId,
        household: householdId,
        warehouse: warehouseId,
        item: itemId,
        quantity: Number(form.get("quantity")),
        status: "ACTIVE",
      });
      formElement.reset();
      setWarehouseId("");
      setHouseholdId("");
      setItemId("");
      await load();
      setStatus("saved");
    } catch (reason) {
      setStatus("failed");
      setError(
        reason instanceof Error
          ? reason.message
          : "Could not save NFI entitlement.",
      );
    }
  };
  const canWrite = ["ADMIN", "MANAGER", "FINANCE"].includes(
    role,
  );
  const cancel = async (row: Entity) => {
    const reason = window.prompt("Enter the audit reason for cancelling this NFI entitlement:");
    if (!reason) return;
    try {
      await api.action(`/nfi-entitlements/${row.id}/cancel/`, { reason });
      await load();
      setStatus("saved");
    } catch (reasonValue) {
      setError(reasonValue instanceof Error ? reasonValue.message : "Could not cancel the NFI entitlement.");
    }
  };
  return (
    <div className="row g-3">
      <div className="col-12">
        <Panel title="Entitlement">
          <p className="section-lead">
            Manage household NFI entitlements. Payment amounts and budget reservations are managed on the Payments page.
          </p>
        </Panel>
      </div>
      {canWrite && (
        <div className="col-xl-5">
          <Panel title="Create NFI entitlement">
            <form onSubmit={submit}>
              <SelectField
                label="NFI-enabled program"
                name="program"
                options={programs.filter((row) => Boolean(row.nfi_enabled)).map((row) => ({
                  value: row.id,
                  label: programLabel(row),
                }))}
                value={programId}
                onChange={(value) => {
                  setProgramId(value);
                  setWarehouseId("");
                  setHouseholdId("");
                  setItemId("");
                }}
                required
              />
              <SelectField
                label="Approved NFI household"
                name="household"
                options={eligibleHouseholds.map((row) => ({
                  value: valueOf(row, "household"),
                  label: `${valueOf(row, "household_reference") || "Household"} — ${valueOf(row, "household_size") || "?"} household size — ${valueOf(row, "member_count") || 0} members — ${valueOf(row, "assistance_modality")}`,
                }))}
                value={householdId}
                onChange={setHouseholdId}
                required
              />
              {programId && eligibleHouseholds.length === 0 && (
                <p className="form-text">
                  No approved NFI households are available for this program.
                </p>
              )}
              <SelectField
                label="Warehouse"
                name="warehouse"
                options={programWarehouses.map((row) => ({
                  value: row.id,
                  label: `${row.name} — ${valueOf(row, "location")}`,
                }))}
                value={warehouseId}
                onChange={(value) => {
                  setWarehouseId(value);
                  setItemId("");
                }}
                required
              />
              <SelectField
                label="NFI item/category"
                name="item"
                options={itemOptions.map((row) => ({
                  value: row.id,
                  label: `${row.name} (${valueOf(row, "item_type")}): ${valueOf(row, "available_quantity")} available`,
                }))}
                value={itemId}
                onChange={setItemId}
                required
              />
              <Field
                label="Entitlement quantity"
                name="quantity"
                type="number"
                required
              />
              <button className="btn btn-primary" disabled={!programId || status === "saving"}>
                Reserve stock and save entitlement
              </button>
            </form>
            <Message status={status} error={error} />
          </Panel>
        </div>
      )}
      <div className={canWrite ? "col-xl-7" : "col-12"}>
        <Panel title="Saved NFI entitlements">
          <Table
            loading={loading}
            headings={[
              "Program",
              "Household",
              "Household size",
              "Member count",
              "Member",
              "Item",
              "Warehouse",
              "Quantity",
              "Status",
              "Actions",
            ]}
            rows={entitlements.map((row) => [
              valueOf(row, "program_name") || "Program",
              valueOf(row, "household_reference") || "Household",
              valueOf(row, "household_size"),
              valueOf(row, "household_member_count"),
              (row.household_members as Entity[] | undefined)
                ?.map((member) => member.full_name)
                .filter(Boolean)
                .join(", ") ||
                nfiBeneficiaryLabel(row) ||
                "Not recorded",
              valueOf(row, "item_name") || "NFI item",
              valueOf(row, "warehouse_name") || "Warehouse",
              valueOf(row, "quantity"),
              <Badge value={valueOf(row, "status")} />,
              canWrite && valueOf(row, "status") === "ACTIVE" ? <button type="button" className="btn btn-sm btn-outline-danger" onClick={() => void cancel(row)}>Cancel</button> : "—",
            ])}
          />
        </Panel>
        {!canWrite && <Message status="" error={error} />}
      </div>
    </div>
  );
}

// Switches between cash, NFI, and medical entitlement workspaces.
export function EntitlementsPage({ role, initialModality = "NFI" }: { role: Role; initialModality?: EntitlementModality }) {
  const [modality, setModality] = useState<EntitlementModality>(initialModality);
  return (
    <div className="row g-3">
      <div className="col-12">
        <Panel title="Entitlements">
          <p className="section-lead">Review Cash, NFI, and Medical assistance in one workspace. Select a modality to see only the relevant controls and records.</p>
          <EntitlementTabs value={modality} onChange={setModality} />
        </Panel>
      </div>
      <div className="col-12">
        {modality === "CASH" && <CashEntitlementsPanel role={role} />}
        {modality === "NFI" && <NfiEntitlementsPanel role={role} />}
        {modality === "MEDICAL" && <MedicalPage role={role} view="entitlements" />}
      </div>
    </div>
  );
}

