import { FormEvent, useEffect, useMemo, useState } from "react";

import { api, listItems } from "../api/api";
import { Badge, Field, Message, Panel, SelectField, Table, Textarea } from "../components";
import type { Entity, Role } from "../types/types";
import { householdLabel, programLabel, valueOf } from "../utils";
import { useTranslation } from "../i18n";

type MedicalView = "overview" | "inventory" | "entitlements" | "distribution" | "equipment";
type Dashboard = {
  program_count: number; product_count: number; batch_count: number; available_quantity: number;
  pending_entitlements: number; open_alerts: number;
};

const option = (rows: Entity[], label: (row: Entity) => string) => rows.map((row) => ({ value: row.id, label: label(row) }));

// Shows medical inventory, entitlements, distribution, and equipment by selected view.
export function MedicalPage({ role, view }: { role: Role; view: MedicalView }) {
  const translate = useTranslation();
  const [data, setData] = useState<Record<string, Entity[]>>({});
  const [dashboard, setDashboard] = useState<Dashboard | null>(null);
  const [programId, setProgramId] = useState("");
  const [productId, setProductId] = useState("");
  const [warehouseId, setWarehouseId] = useState("");
  const [householdId, setHouseholdId] = useState("");
  const [distributionEntitlementId, setDistributionEntitlementId] = useState("");
  const [distributionAllocationId, setDistributionAllocationId] = useState("");
  const [equipmentId, setEquipmentId] = useState("");
  const [recipientType, setRecipientType] = useState("HOUSEHOLD");
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const canWrite = ["ADMIN", "MANAGER"].includes(role);
  const canApprove = ["ADMIN", "MANAGER", "REVIEWER"].includes(role);

  const resources = [
    "programs", "warehouses", "households", "beneficiaries", "household-modality-decisions",
    "medical-products", "medical-batches", "medical-stock-movements", "medical-entitlements",
    "distribution-events", "medical-distribution-allocations", "medical-distribution-issues",
    "medical-alerts", "medical-equipment", "medical-equipment-movements",
    "medical-temperature-observations", "medical-controlled-authorizations", "users",
  ]
    .filter((resource) => !(resource === "households" && role === "AUDITOR"))
    .filter((resource) => !(resource === "users" && !["ADMIN", "MANAGER"].includes(role)));
  // Gets the medical dashboard and all records required by the current workspace.
  const load = async () => {
    setLoading(true); setError("");
    try {
      const [summary, ...responses] = await Promise.all([
        api.get<Dashboard>("/medical-dashboard/"),
        ...resources.map((resource) => api.list<Entity>(resource)),
      ]);
      setDashboard(summary);
      setData(Object.fromEntries(resources.map((resource, index) => [resource, listItems(responses[index])])));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not load Medical Assistance data.");
    } finally { setLoading(false); }
  };
  useEffect(() => { void load(); }, []);

  const programs = (data.programs ?? []).filter((row) => Boolean(row.medical_enabled));
  const warehouses = (data.warehouses ?? []).filter((row) => !programId || valueOf(row, "program") === programId);
  const products = (data["medical-products"] ?? []).filter((row) => !programId || valueOf(row, "program") === programId);
  const batches = (data["medical-batches"] ?? []).filter((row) => !programId || valueOf(row, "program") === programId);
  const households = data.households ?? [];
  const beneficiaries = (data.beneficiaries ?? []).filter((row) => !householdId || valueOf(row, "household") === householdId);
  const approvedMedicalHouseholds = useMemo(() => {
    const decisions = (data["household-modality-decisions"] ?? []).filter((row) =>
      valueOf(row, "program") === programId && valueOf(row, "modality") === "MEDICAL" && valueOf(row, "status") === "APPROVED",
    );
    return decisions.map((decision) => households.find((row) => row.id === valueOf(decision, "household"))).filter(Boolean) as Entity[];
  }, [data, households, programId]);
  const medicalEntitlements = (data["medical-entitlements"] ?? []).filter(
    (row) => !programId || valueOf(row, "program") === programId,
  );
  const allocatableMedicalEntitlements = medicalEntitlements.filter(
    (row) => valueOf(row, "status") === "ALLOCATED",
  );
  const selectedDistributionEntitlement = allocatableMedicalEntitlements.find(
    (row) => row.id === distributionEntitlementId,
  );
  const compatibleDistributionEvents = (data["distribution-events"] ?? []).filter(
    (row) =>
      Boolean(selectedDistributionEntitlement) &&
      valueOf(row, "program") === valueOf(selectedDistributionEntitlement, "program") &&
      valueOf(row, "warehouse") === valueOf(selectedDistributionEntitlement, "warehouse") &&
      !["CLOSED", "CANCELLED"].includes(valueOf(row, "status")),
  );
  const medicalAllocations = (data["medical-distribution-allocations"] ?? []).filter((row) => {
    const entitlement = (data["medical-entitlements"] ?? []).find(
      (candidate) => candidate.id === valueOf(row, "entitlement"),
    );
    return !programId || valueOf(entitlement, "program") === programId;
  });
  const issuableMedicalAllocations = medicalAllocations.filter(
    (row) => valueOf(row, "status") === "ALLOCATED",
  );
  const medicalIssues = (data["medical-distribution-issues"] ?? []).filter((row) => {
    const allocation = (data["medical-distribution-allocations"] ?? []).find(
      (candidate) => candidate.id === valueOf(row, "allocation"),
    );
    const entitlement = (data["medical-entitlements"] ?? []).find(
      (candidate) => candidate.id === valueOf(allocation, "entitlement"),
    );
    return !programId || valueOf(entitlement, "program") === programId;
  });
  const medicalEquipment = (data["medical-equipment"] ?? []).filter(
    (row) => !programId || valueOf(row, "program") === programId,
  );
  const selectedEquipment = medicalEquipment.find((row) => row.id === equipmentId);
  const equipmentWarehouses = (data.warehouses ?? []).filter(
    (row) =>
      Boolean(selectedEquipment) &&
      valueOf(row, "program") === valueOf(selectedEquipment, "program"),
  );

  // Saves one medical form and reloads the lists with the backend result.
  const submit = (resource: string, payload: (form: FormData) => Record<string, unknown>) => async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault(); const element = event.currentTarget; const form = new FormData(element);
    setStatus("saving"); setError("");
    try { await api.create(resource, payload(form)); element.reset(); await load(); setStatus("saved"); }
    catch (reason) { setStatus("failed"); setError(reason instanceof Error ? reason.message : "The record could not be saved."); }
  };
  const runAction = async (path: string, payload: Record<string, unknown> = {}) => {
    setStatus("saving"); setError("");
    try { await api.action(path, payload); await load(); setStatus("saved"); }
    catch (reason) { setStatus("failed"); setError(reason instanceof Error ? reason.message : "The action could not be completed."); }
  };
  const batchOperation = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const batch = String(form.get("batch"));
    const operation = String(form.get("operation"));
    const action = operation.startsWith("ADJUST_") ? "adjust" : operation.toLowerCase();
    await runAction(`/medical-batches/${batch}/${action}/`, {
      quantity: Number(form.get("quantity") || 0),
      direction: operation.replace("ADJUST_", ""),
      destination_warehouse: form.get("destination_warehouse"),
      reason: form.get("reason"),
    });
  };

  const programSelect = (
    <SelectField label="Medical Assistance program" name="program" options={option(programs, programLabel)} value={programId}
      onChange={(value) => {
        setProgramId(value);
        setProductId("");
        setWarehouseId("");
        setHouseholdId("");
        setDistributionEntitlementId("");
        setDistributionAllocationId("");
        setEquipmentId("");
      }} required />
  );

  const overview = (
    <>
      <div className="kpi-grid medical-kpis">
        {[
          ["Medical programs", dashboard?.program_count], ["Active products", dashboard?.product_count],
          ["Tracked batches", dashboard?.batch_count], ["Available units", dashboard?.available_quantity],
          ["Pending entitlements", dashboard?.pending_entitlements], ["Open medical alerts", dashboard?.open_alerts],
        ].map(([label, value]) => <div className="kpi" key={String(label)}><strong>{value ?? "—"}</strong><span>{translate(String(label))}</span></div>)}
      </div>
      <Panel title="Medical alerts" action={canWrite ? <button className="btn btn-sm btn-outline-primary" onClick={() => void runAction("/medical-dashboard/")}>Refresh alerts</button> : undefined}>
        <Table loading={loading} headings={["Type", "Product", "Batch", "Warehouse", "Message", "Status", "Actions"]}
          rows={(data["medical-alerts"] ?? []).map((row) => [valueOf(row, "alert_type_display"), valueOf(row, "product_name"), valueOf(row, "batch_lot_number") || "—", valueOf(row, "warehouse_name") || "—", valueOf(row, "message"), <Badge value={valueOf(row, "status")} />, canWrite && valueOf(row, "status") === "OPEN" ? <button className="btn btn-sm btn-outline-primary" onClick={() => void api.update("medical-alerts", row.id, { status: "ACKNOWLEDGED" }).then(load)}>Acknowledge</button> : "—"])} />
      </Panel>
    </>
  );

  const inventory = (
    <div className="row g-3">
      {canWrite && <div className="col-xl-5"><Panel title="Register medical product">
        <form onSubmit={submit("medical-products", (form) => ({ program: form.get("program"), sku: form.get("sku"), name: form.get("name"), category: form.get("category"), unit_of_measurement: form.get("unit_of_measurement"), storage_requirements: form.get("storage_requirements"), refrigeration_required: form.get("refrigeration_required") === "on", controlled: form.get("controlled") === "on", minimum_stock_threshold: Number(form.get("minimum_stock_threshold") || 0), active: true }))}>
          {programSelect}<Field label="SKU" name="sku" required /><Field label="Product name" name="name" required />
          <SelectField label="Category" name="category" options={["MEDICINE", "THERAPEUTIC_NUTRITION", "CONSUMABLE", "EQUIPMENT"].map((value) => ({ value, label: value }))} required />
          <Field label="Unit of measurement" name="unit_of_measurement" required /><Textarea label="Storage requirements" name="storage_requirements" />
          <div className="form-check"><input className="form-check-input" type="checkbox" name="refrigeration_required" id="medical-cold" /><label className="form-check-label" htmlFor="medical-cold">Cold-chain product</label></div>
          <div className="form-check mb-3"><input className="form-check-input" type="checkbox" name="controlled" id="medical-controlled" /><label className="form-check-label" htmlFor="medical-controlled">Controlled product</label></div>
          <Field label="Low-stock threshold" name="minimum_stock_threshold" type="number" /><button className="btn btn-primary">Save product</button>
        </form>
      </Panel></div>}
      {canWrite && <div className="col-xl-7"><Panel title="Receive medical batch">
        <form onSubmit={submit("medical-batches", (form) => ({ program: programId, product: productId, warehouse: warehouseId, lot_number: form.get("lot_number"), manufacturing_date: form.get("manufacturing_date") || null, expiry_date: form.get("expiry_date") || null, received_quantity: Number(form.get("received_quantity")), supplier_source: form.get("supplier_source"), storage_location: form.get("storage_location"), intended_distribution_location: form.get("intended_distribution_location"), status: "AVAILABLE" }))}>
          {programSelect}<SelectField label="Product" name="product" options={option(products, (row) => `${row.name} (${valueOf(row, "sku")})`)} value={productId} onChange={setProductId} required />
          <SelectField label="Warehouse" name="warehouse" options={option(warehouses, (row) => row.name ?? "Warehouse")} value={warehouseId} onChange={setWarehouseId} required />
          <div className="row"><div className="col-md-6"><Field label="Lot number" name="lot_number" required /></div><div className="col-md-6"><Field label="Received quantity" name="received_quantity" type="number" required /></div>
          <div className="col-md-6"><Field label="Manufacturing date" name="manufacturing_date" type="date" /></div><div className="col-md-6"><Field label="Expiry date" name="expiry_date" type="date" /></div></div>
          <Field label="Supplier or source" name="supplier_source" required /><Field label="Storage location" name="storage_location" /><Field label="Intended distribution location" name="intended_distribution_location" />
          <button className="btn btn-primary">Receive batch</button>
        </form>
      </Panel></div>}
      <div className="col-12"><Panel title="Medical inventory by batch"><Table loading={loading} headings={["Program", "Product", "SKU", "Lot", "Warehouse", "Expiry", "Received", "Available", "Status"]}
        rows={batches.map((row) => [valueOf(row, "program_name"), valueOf(row, "product_name"), valueOf(row, "product_sku"), valueOf(row, "lot_number"), valueOf(row, "warehouse_name"), valueOf(row, "expiry_date") || "No expiry", valueOf(row, "received_quantity"), valueOf(row, "available_quantity"), <Badge value={valueOf(row, "status")} />])} /></Panel></div>
      {canWrite && <div className="col-xl-6"><Panel title="Controlled stock operation"><form onSubmit={(event) => void batchOperation(event)}>
        <SelectField label="Medical batch" name="batch" options={option(batches, (row) => `${valueOf(row, "product_name")} — ${valueOf(row, "lot_number")} — ${valueOf(row, "warehouse_name")}`)} required />
        <SelectField label="Operation" name="operation" options={["ADJUST_IN", "ADJUST_OUT", "TRANSFER", "QUARANTINE", "DISPOSE"].map((value) => ({ value, label: value }))} required />
        <Field label="Quantity (adjustment or transfer)" name="quantity" type="number" /><SelectField label="Destination warehouse (transfer only)" name="destination_warehouse" options={option(warehouses, (row) => row.name ?? "Warehouse")} /><Textarea label="Reason" name="reason" required /><button className="btn btn-primary">Apply controlled operation</button>
      </form></Panel></div>}
      {canWrite && <div className="col-xl-6"><Panel title="Cold-chain temperature observation"><form onSubmit={submit("medical-temperature-observations", (form) => ({ batch: form.get("batch"), temperature: form.get("temperature"), observed_at: form.get("observed_at") || new Date().toISOString(), quarantine_decision: form.get("quarantine_decision") === "on", notes: form.get("notes") }))}>
        <SelectField label="Medical batch" name="batch" options={option(batches.filter((row) => products.find((product) => product.id === valueOf(row, "product"))?.refrigeration_required), (row) => `${valueOf(row, "product_name")} — ${valueOf(row, "lot_number")}`)} required /><Field label="Observed temperature" name="temperature" type="number" required /><Field label="Observed time" name="observed_at" type="datetime-local" /><div className="form-check mb-3"><input className="form-check-input" type="checkbox" name="quarantine_decision" id="quarantine-excursion" /><label className="form-check-label" htmlFor="quarantine-excursion">Quarantine if outside the safe range</label></div><Textarea label="Notes" name="notes" /><button className="btn btn-primary">Record temperature</button>
      </form></Panel></div>}
      {role === "ADMIN" && <div className="col-12"><Panel title="Controlled-product authorizations"><form className="row align-items-end" onSubmit={submit("medical-controlled-authorizations", (form) => ({ program: form.get("program"), user: form.get("user"), can_receive: form.get("can_receive") === "on", can_issue: form.get("can_issue") === "on", permit_reference: form.get("permit_reference"), valid_from: form.get("valid_from"), valid_until: form.get("valid_until") || null, active: true }))}>
        <div className="col-md-3">{programSelect}</div><div className="col-md-3"><SelectField label="Authorized user" name="user" options={option(data.users ?? [], (row) => `${row.full_name} — ${valueOf(row, "role")}`)} required /></div><div className="col-md-2"><Field label="Permit reference" name="permit_reference" required /></div><div className="col-md-2"><Field label="Valid from" name="valid_from" type="date" required /></div><div className="col-md-2"><Field label="Valid until" name="valid_until" type="date" /></div><div className="col-12 d-flex gap-3 mb-3"><label><input type="checkbox" name="can_receive" /> Can receive</label><label><input type="checkbox" name="can_issue" /> Can issue</label></div><div className="col-12"><button className="btn btn-primary">Grant authorization</button></div>
      </form><Table headings={["Program", "User", "Permit reference", "Can receive", "Can issue", "Valid until", "Status"]} rows={(data["medical-controlled-authorizations"] ?? []).map((row) => [valueOf(row, "program_name"), valueOf(row, "user_name"), valueOf(row, "permit_reference"), row.can_receive ? "Yes" : "No", row.can_issue ? "Yes" : "No", valueOf(row, "valid_until") || "No expiry", <Badge value={row.active ? "ACTIVE" : "INACTIVE"} />])} /></Panel></div>}
      <div className="col-12"><Panel title="Append-only medical stock movements"><Table loading={loading} headings={["Time", "Product", "Batch", "From", "To", "Quantity", "Movement", "Reason", "Responsible user"]}
        rows={(data["medical-stock-movements"] ?? []).map((row) => [new Date(valueOf(row, "created_at")).toLocaleString(), valueOf(row, "product_name"), valueOf(row, "batch_lot_number"), valueOf(row, "source_warehouse_name") || "—", valueOf(row, "destination_warehouse_name") || "—", valueOf(row, "quantity"), valueOf(row, "movement_type"), valueOf(row, "reason"), valueOf(row, "responsible_user_name")])} /></Panel></div>
    </div>
  );

  const entitlements = (
    <div className="row g-3">
      {canApprove && <div className="col-xl-5"><Panel title="Create medical entitlement">
        <form onSubmit={submit("medical-entitlements", (form) => ({ program: programId, recipient_type: recipientType, household: householdId, beneficiary: recipientType === "INDIVIDUAL" ? form.get("beneficiary") : null, product: productId, warehouse: warehouseId, quantity: Number(form.get("quantity")), notes: form.get("notes"), status: "PENDING" }))}>
          {programSelect}<SelectField label="Recipient type" name="recipient_type" options={[{ value: "HOUSEHOLD", label: "Household" }, { value: "INDIVIDUAL", label: "Individual beneficiary" }]} value={recipientType} onChange={setRecipientType} required />
          <SelectField label="Approved medical household" name="household" options={option(approvedMedicalHouseholds, householdLabel)} value={householdId} onChange={setHouseholdId} required />
          {recipientType === "INDIVIDUAL" && <SelectField label="Beneficiary" name="beneficiary" options={option(beneficiaries, (row) => `${row.full_name} (${valueOf(row, "number")})`)} required />}
          <SelectField label="Medical product" name="product" options={option(products, (row) => row.name ?? "Product")} value={productId} onChange={setProductId} required />
          <SelectField label="Warehouse" name="warehouse" options={option(warehouses, (row) => row.name ?? "Warehouse")} value={warehouseId} onChange={setWarehouseId} required />
          <Field label="Quantity" name="quantity" type="number" required /><Textarea label="Clinical or delivery notes" name="notes" /><button className="btn btn-primary">Save entitlement</button>
        </form>
      </Panel></div>}
      <div className={canApprove ? "col-xl-7" : "col-12"}><Panel title="Medical entitlements"><Table loading={loading} headings={["Program", "Recipient", "Beneficiary", "Product", "Warehouse", "Batch", "Quantity", "Status", "Actions"]}
        rows={medicalEntitlements.map((row) => [valueOf(row, "program_name"), valueOf(row, "household_reference"), valueOf(row, "beneficiary_name") || "Household", valueOf(row, "product_name"), valueOf(row, "warehouse_name"), valueOf(row, "batch_lot_number") || "Not allocated", valueOf(row, "quantity"), <Badge value={valueOf(row, "status")} />, canApprove ? <div className="d-flex gap-1">{valueOf(row, "status") === "PENDING" && <button className="btn btn-sm btn-outline-primary" onClick={() => void runAction(`/medical-entitlements/${row.id}/approve/`)}>Approve</button>}{valueOf(row, "status") === "APPROVED" && <button className="btn btn-sm btn-primary" onClick={() => void runAction(`/medical-entitlements/${row.id}/allocate/`)}>Allocate FEFO</button>}</div> : "—"])} /></Panel></div>
    </div>
  );

  const distribution = (
    <div className="row g-3">
      {canWrite && <div className="col-lg-4"><Panel title="Plan medical distribution event"><form onSubmit={submit("distribution-events", (form) => ({ program: programId, warehouse: warehouseId, location: form.get("location"), event_date: form.get("event_date"), distribution_team: form.get("distribution_team"), status: "OPEN", entitlement_ids: [] }))}>
        {programSelect}<SelectField label="Warehouse" name="warehouse" options={option(warehouses, (row) => row.name ?? "Warehouse")} value={warehouseId} onChange={setWarehouseId} required /><Field label="Distribution location" name="location" required /><Field label="Event date" name="event_date" type="date" required /><Field label="Distribution team" name="distribution_team" required /><button className="btn btn-primary">Create event</button>
      </form></Panel></div>}
      {canWrite && <div className="col-lg-4"><Panel title="Assign allocated entitlement"><form onSubmit={submit("medical-distribution-allocations", (form) => {
        const entitlement = (data["medical-entitlements"] ?? []).find((row) => row.id === String(form.get("entitlement")));
        return { entitlement: form.get("entitlement"), event: form.get("event"), batch: valueOf(entitlement, "batch"), quantity: Number(valueOf(entitlement, "quantity")) };
      })}>{programSelect}<SelectField label="Allocated entitlement" name="entitlement" options={option(allocatableMedicalEntitlements, (row) => `${valueOf(row, "household_reference")} — ${valueOf(row, "product_name")} × ${valueOf(row, "quantity")}`)} value={distributionEntitlementId} onChange={setDistributionEntitlementId} required />
        <SelectField label="Distribution event" name="event" options={option(compatibleDistributionEvents, (row) => `${valueOf(row, "location")} — ${valueOf(row, "event_date")} — ${valueOf(row, "warehouse_name") || "Warehouse"}`)} disabled={!distributionEntitlementId} required />
        {distributionEntitlementId && compatibleDistributionEvents.length === 0 && <p className="form-text">No open distribution event matches this entitlement's program and warehouse.</p>}
        <button className="btn btn-primary" disabled={!distributionEntitlementId || compatibleDistributionEvents.length === 0}>Assign to event</button>
      </form></Panel></div>}
      {canWrite && <div className="col-lg-4"><Panel title="Record medical delivery"><form onSubmit={submit("medical-distribution-issues", (form) => ({ allocation: form.get("allocation"), actual_quantity: Number(form.get("actual_quantity")), delivery_status: form.get("delivery_status"), distribution_date: form.get("distribution_date"), evidence: form.get("evidence"), notes: form.get("notes") }))}>
        {programSelect}<SelectField label="Distribution allocation" name="allocation" options={option(issuableMedicalAllocations, (row) => `${valueOf(row, "household_reference")} — ${valueOf(row, "product_name")} — ${valueOf(row, "event_location")} — ${valueOf(row, "batch_lot_number")}`)} value={distributionAllocationId} onChange={setDistributionAllocationId} required />
        <Field label="Actual quantity" name="actual_quantity" type="number" required /><SelectField label="Delivery status" name="delivery_status" options={["RECEIVED", "PARTIALLY_RECEIVED", "NOT_RECEIVED", "NO_SHOW", "REJECTED", "DAMAGED"].map((value) => ({ value, label: value }))} required /><Field label="Distribution date" name="distribution_date" type="date" required /><Textarea label="Evidence" name="evidence" /><Textarea label="Notes" name="notes" /><button className="btn btn-primary">Record delivery</button>
      </form></Panel></div>}
      <div className="col-12"><Panel title="Medical distribution allocations"><Table loading={loading} headings={["Program", "Household", "Beneficiary", "Product", "Warehouse", "Batch", "Event", "Quantity", "Status"]} rows={medicalAllocations.map((row) => [valueOf(row, "program_name"), valueOf(row, "household_reference"), valueOf(row, "beneficiary_name") || "Household", valueOf(row, "product_name"), valueOf(row, "warehouse_name"), valueOf(row, "batch_lot_number"), valueOf(row, "event_location"), valueOf(row, "quantity"), <Badge value={valueOf(row, "status")} />])} /></Panel></div>
      <div className="col-12"><Panel title="Medical delivery records"><Table loading={loading} headings={["Program", "Household", "Beneficiary", "Product", "Batch", "Actual quantity", "Delivery status", "Date", "Evidence"]} rows={medicalIssues.map((row) => [valueOf(row, "program_name"), valueOf(row, "household_reference"), valueOf(row, "beneficiary_name") || "Household", valueOf(row, "product_name"), valueOf(row, "batch_lot_number"), valueOf(row, "actual_quantity"), <Badge value={valueOf(row, "delivery_status")} />, valueOf(row, "distribution_date"), valueOf(row, "evidence") || "—"])} /></Panel></div>
    </div>
  );

  const equipment = (
    <div className="row g-3">
      {canWrite && <div className="col-xl-5"><Panel title="Register medical equipment"><form onSubmit={submit("medical-equipment", (form) => ({ program: programId, warehouse: warehouseId, product: form.get("product") || null, name: form.get("name"), asset_identifier: form.get("asset_identifier"), serial_number: form.get("serial_number"), category: form.get("equipment_category"), condition: "GOOD", availability: "AVAILABLE", active: true }))}>
        {programSelect}<SelectField label="Warehouse" name="warehouse" options={option(warehouses, (row) => row.name ?? "Warehouse")} value={warehouseId} onChange={setWarehouseId} required /><SelectField label="Equipment product (optional)" name="product" options={option(products.filter((row) => valueOf(row, "category") === "EQUIPMENT"), (row) => row.name ?? "Equipment")} /><Field label="Equipment name" name="name" required /><Field label="Asset identifier" name="asset_identifier" required /><Field label="Serial number" name="serial_number" /><Field label="Equipment category" name="equipment_category" required /><button className="btn btn-primary">Register equipment</button>
      </form></Panel></div>}
      <div className={canWrite ? "col-xl-7" : "col-12"}><Panel title="Medical equipment register"><Table loading={loading} headings={["Program", "Equipment", "Asset ID", "Serial", "Category", "Warehouse", "Condition", "Availability", "Responsible person"]} rows={medicalEquipment.map((row) => [valueOf(row, "program_name"), valueOf(row, "name"), valueOf(row, "asset_identifier"), valueOf(row, "serial_number") || "—", valueOf(row, "category"), valueOf(row, "warehouse_name"), <Badge value={valueOf(row, "condition")} />, <Badge value={valueOf(row, "availability")} />, valueOf(row, "responsible_person_name") || "Unassigned"])} /></Panel></div>
      {canWrite && <div className="col-xl-5"><Panel title="Record equipment movement"><form onSubmit={submit("medical-equipment-movements", (form) => ({ equipment: form.get("equipment"), movement_type: form.get("movement_type"), source_warehouse: form.get("source_warehouse") || null, destination_warehouse: form.get("destination_warehouse") || null, assigned_to: form.get("assigned_to") || null, condition: form.get("condition"), notes: form.get("notes") }))}>
        {programSelect}<SelectField label="Equipment" name="equipment" options={option(medicalEquipment, (row) => `${row.name} — ${valueOf(row, "asset_identifier")} — ${valueOf(row, "warehouse_name")}`)} value={equipmentId} onChange={setEquipmentId} required /><SelectField label="Movement type" name="movement_type" options={["RECEIVE", "ASSIGN", "RETURN", "TRANSFER", "MAINTENANCE", "RETIRE"].map((value) => ({ value, label: value }))} required /><SelectField label="Source warehouse" name="source_warehouse" options={option(equipmentWarehouses, (row) => row.name ?? "Warehouse")} disabled={!equipmentId} /><SelectField label="Destination warehouse" name="destination_warehouse" options={option(equipmentWarehouses, (row) => row.name ?? "Warehouse")} disabled={!equipmentId} /><SelectField label="Assigned user" name="assigned_to" options={option(data.users ?? [], (row) => row.full_name ?? "User")} /><SelectField label="Condition" name="condition" options={["GOOD", "NEEDS_MAINTENANCE", "DAMAGED", "RETIRED"].map((value) => ({ value, label: value }))} required /><Textarea label="Notes" name="notes" /><button className="btn btn-primary" disabled={!equipmentId}>Record movement</button>
      </form></Panel></div>}
      <div className={canWrite ? "col-xl-7" : "col-12"}><Panel title="Equipment movement history"><Table loading={loading} headings={["Equipment", "Movement", "From", "To", "Assigned user", "Condition", "Responsible user", "Time"]} rows={(data["medical-equipment-movements"] ?? []).map((row) => [valueOf(row, "equipment_name"), valueOf(row, "movement_type"), valueOf(row, "source_warehouse_name") || "—", valueOf(row, "destination_warehouse_name") || "—", valueOf(row, "assigned_to_name") || "—", valueOf(row, "condition"), valueOf(row, "responsible_user_name"), new Date(valueOf(row, "created_at")).toLocaleString()])} /></Panel></div>
    </div>
  );

  return <div className="medical-workspace">{view === "overview" ? overview : view === "inventory" ? inventory : view === "entitlements" ? entitlements : view === "distribution" ? distribution : equipment}<Message status={status} error={error} /></div>;
}
