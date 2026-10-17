import { FormEvent, useEffect, useState } from "react";
import { api, listItems } from "../api/api";
import type { Entity, Role } from "../types/types";
import { Badge, Field, Message, Panel, SelectField, Table } from "../components";
import { programLabel, valueOf } from "../utils";
// Shows program setup and the program records available to the signed-in role.
export function ProgramsPage({ role }: { role: Role }) {
  const [programs, setPrograms] = useState<Entity[]>([]);
  const [selected, setSelected] = useState("");
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  // Gets the latest programs and returns them to the setup and finance views.
  const load = async () => {
    try {
      setPrograms(listItems(await api.list<Entity>("programs")));
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Could not load programs.",
      );
    }
  };
  useEffect(() => {
    void load();
  }, []);
  if (role === "ADMIN" || role === "MANAGER")
    return <ProgramSetup programs={programs} reload={load} />;
  const create = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    const cashEnabled = form.get("cash_enabled") === "on";
    const nfiEnabled = form.get("nfi_enabled") === "on";
    const otherEnabled = form.get("other_assistance_enabled") === "on";
    setStatus("saving");
    setError("");
    try {
      const program = await api.create<Entity>("programs", {
        name: form.get("name"),
        country: form.get("country"),
        country_code: form.get("country_code"),
        currency: form.get("currency"),
        reporting_currency: form.get("reporting_currency"),
        currency_type: "PROGRAM",
        exchange_rate: "1",
        transfer_amount: form.get("transfer_amount"),
        payment_cycle: form.get("payment_cycle"),
        status: "DRAFT",
        cash_enabled: cashEnabled,
        nfi_enabled: nfiEnabled,
        other_assistance_enabled: otherEnabled,
        workflow_config: { payment_enabled: cashEnabled },
        country_pack: {},
      });
      await api.create("budgets", {
        program: program.id,
        currency: form.get("currency"),
        status: "DRAFT",
        planned_total: form.get("planned_total"),
        actual_total: "0",
        line_items: [],
      });
      setSelected(program.id);
      formElement.reset();
      await load();
      setStatus("saved");
    } catch (reason) {
      setStatus("failed");
      setError(
        reason instanceof Error
          ? reason.message
          : "Could not create the program.",
      );
    }
  };
  const saveChannel = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!selected) {
      setError(
        "Select a program before configuring a simulated payment channel.",
      );
      return;
    }
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    setStatus("saving");
    setError("");
    try {
      await api.programChannel(selected, {
        channel_type: form.get("channel_type"),
        provider_name: form.get("provider_name"),
        currency: form.get("currency"),
        fee_amount: form.get("fee_amount") || 0,
        is_active: true,
      });
      formElement.reset();
      await load();
      setStatus("saved");
    } catch (reason) {
      setStatus("failed");
      setError(
        reason instanceof Error
          ? reason.message
          : "Could not save the simulated channel.",
      );
    }
  };
  const deleteProgram = async (program: Entity) => {
    if (
      !window.confirm(
        `Delete ${programLabel(program)} and all associated households, beneficiaries, payments, complaints, and records? This cannot be undone.`,
      )
    )
      return;
    setStatus("saving");
    setError("");
    try {
      await api.remove("programs", program.id);
      setSelected("");
      await load();
      setStatus("saved");
    } catch (reason) {
      setStatus("failed");
      setError(
        reason instanceof Error
          ? reason.message
          : "The program could not be deleted.",
      );
    }
  };
  if (role === "FINANCE")
    return (
      <div className="row g-3">
        <div className="col-xl-5">
          <Panel title="Configure simulated payment channel">
            <p className="form-text">
              Finance can configure only simulated channels. Program creation
              remains with tenant administrators and managers.
            </p>
            <SelectField
              label="Program"
              name="channel-program"
              options={programs.map((program) => ({
                value: program.id,
                label: programLabel(program),
              }))}
              value={selected}
              onChange={setSelected}
              required
            />
            <form onSubmit={saveChannel}>
              <SelectField
                label="Channel type"
                name="channel_type"
                options={["BANK", "MOBILE_MONEY", "CASH", "VOUCHER"].map(
                  (value) => ({ value, label: value }),
                )}
                required
              />
              <Field
                label="Simulated provider name"
                name="provider_name"
                required
              />
              <Field label="Channel currency" name="currency" required />
              <Field label="Fixed fee per successful transaction" name="fee_amount" type="number" required />
              <button className="btn btn-outline-primary" disabled={status === "saving"}>
                Save simulated channel
              </button>
            </form>
            <Message status={status} error={error} />
          </Panel>
        </div>
        <div className="col-xl-7">
          <Panel title="Authorized programs">
            <Table
              headings={["Program", "Country", "Currency", "Status"]}
              rows={programs.map((program) => [
                programLabel(program),
                valueOf(program, "country"),
                program.currency ?? "—",
                <Badge value={program.status ?? "DRAFT"} />,
              ])}
            />
          </Panel>
        </div>
      </div>
    );
  if (!["ADMIN", "MANAGER"].includes(role))
    return (
      <Panel title="Programs">
        <Table
          headings={["Program", "Country", "Currency", "Status"]}
          rows={programs.map((program) => [
            programLabel(program),
            valueOf(program, "country"),
            program.currency ?? "—",
            <Badge value={program.status ?? "DRAFT"} />,
          ])}
        />
      </Panel>
    );
  return (
    <div className="row g-3">
      <div className="col-xl-7">
        <Panel title="Create program">
          <form onSubmit={create}>
            <div className="row">
              <div className="col-md-6">
                <Field label="Program name" name="name" required />
              </div>
              <div className="col-md-6">
                <Field label="Country" name="country" required />
              </div>
              <div className="col-md-4">
                <Field label="Country code" name="country_code" required />
              </div>
              <div className="col-md-4">
                <Field label="Program currency" name="currency" required />
              </div>
              <div className="col-md-4">
                <Field
                  label="Reporting currency"
                  name="reporting_currency"
                  required
                />
              </div>
              <div className="col-md-6">
                <Field
                  label="Transfer amount"
                  name="transfer_amount"
                  type="number"
                  required
                />
              </div>
              <div className="col-md-3">
                <SelectField
                  label="Payment cycle"
                  name="payment_cycle"
                  options={["ONE_TIME", "MONTHLY", "QUARTERLY"].map(
                    (value) => ({ value, label: value }),
                  )}
                  required
                />
              </div>
              <div className="col-md-3">
                <Field
                  label="Budget envelope"
                  name="planned_total"
                  type="number"
                  required
                />
              </div>
            </div>
            <div className="form-check mb-3">
              <input
                className="form-check-input"
                id="payment-enabled"
                name="payment_enabled"
                type="checkbox"
                defaultChecked
              />
              <label className="form-check-label" htmlFor="payment-enabled">
                Enable simulated payments for this program
              </label>
            </div>
            <button className="btn btn-primary">
              Create program and budget
            </button>
          </form>
          <Message status={status} error={error} />
        </Panel>
      </div>
      <div className="col-xl-5">
        <Panel title="Configure simulated payment channel">
          <SelectField
            label="Program"
            name="channel-program"
            options={programs.map((program) => ({
              value: program.id,
              label: programLabel(program),
            }))}
            value={selected}
            onChange={setSelected}
          />
          <form onSubmit={saveChannel}>
            <SelectField
              label="Channel type"
              name="channel_type"
              options={["BANK", "MOBILE_MONEY", "CASH", "VOUCHER"].map(
                (value) => ({ value, label: value }),
              )}
              required
            />
            <Field
              label="Simulated provider name"
              name="provider_name"
              required
            />
            <Field label="Channel currency" name="currency" required />
            <Field label="Fixed fee per successful transaction" name="fee_amount" type="number" required />
            <button className="btn btn-outline-primary">
              Save simulated channel
            </button>
          </form>
        </Panel>
      </div>
      <div className="col-12">
        <Panel title="Programs">
          <Table
            headings={["Program", "Country", "Currency", "Status", "Actions"]}
            rows={programs.map((program) => [
              programLabel(program),
              valueOf(program, "country"),
              program.currency ?? "—",
              <Badge value={program.status ?? "DRAFT"} />,
              <button
                className="btn btn-sm btn-outline-danger"
                onClick={() => void deleteProgram(program)}
              >
                Delete
              </button>,
            ])}
          />
        </Panel>
      </div>
      <Message status={status} error={error} />
    </div>
  );
}

function ProgramSetup({
  programs,
  reload,
}: {
  programs: Entity[];
  reload: () => Promise<void>;
}) {
  const [selected, setSelected] = useState("");
  const [cashEnabled, setCashEnabled] = useState(true);
  const [warehouses, setWarehouses] = useState<Entity[]>([]);
  const [items, setItems] = useState<Entity[]>([]);
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const program = programs.find((row) => row.id === selected);
  const lifecycleAction = async (action: "activate" | "pause" | "close" | "reopen" | "delete") => {
    if (!program) return;
    const reason = window.prompt("Enter the audit reason for this program change:", "Operational program management");
    if (!reason) return;
    setStatus("saving");
    setError("");
    try {
      if (action === "delete") await api.removeWithReason("programs", program.id, reason);
      else await api.action(`/programs/${program.id}/${action}/`, { reason });
      if (action === "delete") setSelected("");
      await reload();
      setStatus("saved");
    } catch (reasonValue) {
      setStatus("failed");
      setError(reasonValue instanceof Error ? reasonValue.message : "Could not change the program lifecycle.");
    }
  };
  const loadSetup = async () => {
    if (!selected || !(program?.nfi_enabled || program?.medical_enabled)) {
      setWarehouses([]);
      setItems([]);
      return;
    }
    try {
      const [warehouseResponse, itemResponse] = await Promise.all([
        api.list<Entity>("warehouses"),
        api.list<Entity>("nfi-items"),
      ]);
      setWarehouses(
        listItems(warehouseResponse).filter(
          (row) => valueOf(row, "program") === selected,
        ),
      );
      setItems(
        listItems(itemResponse).filter(
          (row) => valueOf(row, "program") === selected,
        ),
      );
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Could not load NFI setup.",
      );
    }
  };
  useEffect(() => {
    void loadSetup();
  }, [selected, program?.nfi_enabled, program?.medical_enabled]);
  const create = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();

    const formElement = event.currentTarget;
    const form = new FormData(formElement);

    const value = (name: string) => String(form.get(name) ?? "").trim();

    // These names match the checkbox names in your JSX.
    const paymentEnabled = form.has("cash_enabled");
    const nfiEnabled = form.has("nfi_enabled");
    const medicalEnabled = form.has("medical_enabled");
    const otherAssistanceEnabled = form.has("other_assistance_enabled");

    setStatus("saving");
    setError("");

    try {
      if (paymentEnabled) {
        if (!value("payment_cycle")) {
          throw new Error("Payment cycle is required when payment is enabled.");
        }

        if (!value("planned_total")) {
          throw new Error(
            "Budget envelope is required when payment is enabled.",
          );
        }
      }

      const created = await api.create<Entity>("programs", {
        name: value("name"),
        country: value("country"),
        country_code: value("country_code"),
        currency: value("currency"),
        reporting_currency: value("reporting_currency"),
        currency_type: "PROGRAM",
        exchange_rate: "1",

        ...(paymentEnabled
          ? {
              transfer_amount: null,
              payment_cycle: value("payment_cycle"),
            }
          : {
              transfer_amount: null,
              payment_cycle: "",
            }),

        status: "DRAFT",
        cash_enabled: paymentEnabled,
        nfi_enabled: nfiEnabled,
        medical_enabled: medicalEnabled,
        other_assistance_enabled: otherAssistanceEnabled,

        workflow_config: {
          payment_enabled: paymentEnabled,
        },

        country_pack: {},
      });

      // Create a budget only for Payment / Cash programs.
      if (paymentEnabled) {
        await api.create("budgets", {
          program: created.id,
          currency: value("currency"),
          status: "DRAFT",
          planned_total: value("planned_total"),
          line_items: [],
        });
      }

      formElement.reset();
      setCashEnabled(true);

      await reload();
      setSelected(created.id);
      setStatus("saved");
    } catch (reason) {
      setStatus("failed");

      setError(
        reason instanceof Error
          ? reason.message
          : "Could not create the program.",
      );
    }
  };

  const createWarehouse = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!selected) return;
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    try {
      await api.create("warehouses", {
        program: selected,
        name: form.get("warehouse_name"),
        location: form.get("warehouse_location"),
        person_in_charge: form.get("person_in_charge"),
        responsible_team: form.get("responsible_team"),
        active: true,
      });
      formElement.reset();
      await loadSetup();
      setStatus("saved");
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Could not save the warehouse.",
      );
    }
  };
  const updateProgram = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!program) return;
    const form = new FormData(event.currentTarget);
    setStatus("saving");
    setError("");
    try {
      await api.update("programs", program.id, {
        name: form.get("edit_name"),
        country: form.get("edit_country"),
        start_date: form.get("edit_start_date") || null,
        end_date: form.get("edit_end_date") || null,
        cash_enabled: form.get("edit_cash_enabled") === "on",
        nfi_enabled: form.get("edit_nfi_enabled") === "on",
        medical_enabled: form.get("edit_medical_enabled") === "on",
        payment_cycle: form.get("edit_cash_enabled") === "on" ? valueOf(program, "payment_cycle") || "ONE_TIME" : "",
      });
      await reload();
      setStatus("saved");
    } catch (reasonValue) {
      setStatus("failed");
      setError(reasonValue instanceof Error ? reasonValue.message : "Could not update the program.");
    }
  };
  const createItem = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!selected) return;
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    const quantity = Number(form.get("initial_quantity"));
    try {
      await api.create("nfi-items", {
        program: selected,
        warehouse: form.get("warehouse"),
        name: form.get("item_name"),
        item_type: form.get("item_type"),
        unit: form.get("unit"),
        description: form.get("description"),
        initial_quantity: quantity,
        available_quantity: quantity,
        active: true,
      });
      formElement.reset();
      await loadSetup();
      setStatus("saved");
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Could not save the NFI item.",
      );
    }
  };
  return (
    <div className="row g-3">
      <div className="col-xl-7">
        <Panel title="Create program">
          <form onSubmit={create}>
            <div className="row">
              <div className="col-md-6">
                <Field label="Program name" name="name" required />
              </div>
              <div className="col-md-6">
                <Field label="Country" name="country" required />
              </div>
              <div className="col-md-4">
                <Field label="Country code" name="country_code" required />
              </div>
              <div className="col-md-4">
                <Field label="Program currency" name="currency" required />
              </div>
              <div className="col-md-4">
                <Field
                  label="Reporting currency"
                  name="reporting_currency"
                  required
                />
              </div>
              {cashEnabled && (
                <>
                  <div className="col-md-6">
                    <SelectField
                      label="Payment cycle"
                      name="payment_cycle"
                      options={["ONE_TIME", "MONTHLY", "QUARTERLY"].map(
                        (value) => ({ value, label: value }))}
                      required
                    />
                  </div>
                  <div className="col-md-6">
                    <Field label="Budget amount" name="planned_total" type="number" required />
                  </div>
                </>
              )}
            </div>
            <fieldset className="mb-3">
              <legend className="fs-6">Assistance modalities</legend>
              {[
                ["cash_enabled", "Payment / Cash", true],
                ["nfi_enabled", "NFI / In-Kind", false],
                ["medical_enabled", "Medical Assistance", false],
                [
                  "other_assistance_enabled",
                  "Other Assistance / Third Modality",
                  false,
                ],
              ].map(([name, label, checked]) => (
                <div className="form-check" key={String(name)}>
                  <input
                    className="form-check-input"
                    id={String(name)}
                    name={String(name)}
                    type="checkbox"
                    defaultChecked={Boolean(checked)}
                    onChange={
                      name === "cash_enabled"
                        ? (event) => setCashEnabled(event.target.checked)
                        : undefined
                    }
                  />
                  <label className="form-check-label" htmlFor={String(name)}>
                    {String(label)}
                  </label>
                </div>
              ))}
            </fieldset>
            <button className="btn btn-primary">
              {cashEnabled ? "Create program and budget" : "Create program"}
            </button>
          </form>
          <Message status={status} error={error} />
        </Panel>
      </div>
      <div className="col-xl-5">
        <Panel title="Program continuation">
          <SelectField
            label="Program"
            name="setup-program"
            options={programs.map((row) => ({
              value: row.id,
              label: programLabel(row),
            }))}
            value={selected}
            onChange={setSelected}
          />
          <p className="form-text">
            Payment, NFI, and Medical Assistance setup appears only when enabled for the selected
            program.
          </p>
          {program?.cash_enabled && (
            <p className="text-success">
              Payment configuration is enabled; use the existing Payments page
              for channel and simulator setup.
            </p>
          )}
          {program?.other_assistance_enabled && (
            <div className="alert alert-info">
              Other Assistance / Third Modality — Coming Soon / Not Implemented.
            </div>
          )}
          {program?.medical_enabled && (
            <p className="text-success">
              Medical Assistance is enabled; use the Medical Assistance pages for products, batches, entitlements, and delivery.
            </p>
          )}
          {program && (
            <div className="mt-3">
              <div className="d-flex flex-wrap gap-2 align-items-center">
                <Badge value={valueOf(program, "status") || "DRAFT"} />
                {valueOf(program, "status") === "DRAFT" && <button type="button" className="btn btn-sm btn-success" onClick={() => void lifecycleAction("activate")}>Activate</button>}
                {valueOf(program, "status") === "ACTIVE" && <button type="button" className="btn btn-sm btn-outline-warning" onClick={() => void lifecycleAction("pause")}>Pause</button>}
                {valueOf(program, "status") === "PAUSED" && <button type="button" className="btn btn-sm btn-success" onClick={() => void lifecycleAction("activate")}>Resume</button>}
                {["ACTIVE", "PAUSED"].includes(valueOf(program, "status")) && <button type="button" className="btn btn-sm btn-outline-danger" onClick={() => void lifecycleAction("close")}>Close program</button>}
                {valueOf(program, "status") === "CLOSED" && <button type="button" className="btn btn-sm btn-outline-primary" onClick={() => void lifecycleAction("reopen")}>Reopen as paused</button>}
                {valueOf(program, "status") === "DRAFT" && <button type="button" className="btn btn-sm btn-outline-danger" onClick={() => void lifecycleAction("delete")}>Delete unused draft</button>}
              </div>
              <p className="form-text mb-0">Closing preserves all assistance, payment, inventory, and audit history. Programs with in-flight work cannot be closed.</p>
            </div>
          )}
        </Panel>
      </div>
      {(program?.nfi_enabled || program?.medical_enabled) && (
        <>
          <div className="col-xl-5">
            <Panel title="Warehouses">
              <form onSubmit={createWarehouse}>
                <Field label="Warehouse name" name="warehouse_name" required />
                <Field label="Location" name="warehouse_location" required />
                <Field label="Person in charge" name="person_in_charge" />
                <Field label="Responsible team" name="responsible_team" />
                <button className="btn btn-outline-primary">
                  Add warehouse
                </button>
              </form>
              <Table
                headings={["Warehouse", "Location", "Status"]}
                rows={warehouses.map((row) => [
                  row.name ?? "Warehouse",
                  valueOf(row, "location"),
                  <Badge
                    value={
                      String(row.active) === "true" ? "ACTIVE" : "INACTIVE"
                    }
                  />,
                ])}
              />
            </Panel>
          </div>
          {program?.nfi_enabled && <div className="col-xl-7">
            <Panel title="NFI catalogue and initial stock">
              <form onSubmit={createItem}>
                <SelectField
                  label="Warehouse"
                  name="warehouse"
                  options={warehouses.map((row) => ({
                    value: row.id,
                    label: row.name ?? "Warehouse",
                  }))}
                  required
                />
                <Field label="Item name" name="item_name" required />
                <Field label="Item type or category" name="item_type" />
                <Field label="Unit" name="unit" required />
                <Field label="Description" name="description" />
                <Field
                  label="Initial available quantity"
                  name="initial_quantity"
                  type="number"
                  required
                />
                <button className="btn btn-outline-primary">
                  Add item and opening stock
                </button>
              </form>
              <Table
                headings={["Item", "Initial stock", "Available"]}
                rows={items.map((row) => [
                  row.name ?? "Item",
                  valueOf(row, "initial_quantity"),
                  valueOf(row, "available_quantity"),
                ])}
              />
            </Panel>
          </div>}
        </>
      )}
      {program && (
        <div className="col-12">
          <Panel title="Edit program information and modalities">
            <form key={program.id} onSubmit={updateProgram}>
              <div className="row">
                <div className="col-md-6"><Field label="Program name" name="edit_name" defaultValue={valueOf(program, "name")} required /></div>
                <div className="col-md-6"><Field label="Country" name="edit_country" defaultValue={valueOf(program, "country")} required /></div>
                <div className="col-md-6"><Field label="Start date" name="edit_start_date" type="date" defaultValue={valueOf(program, "start_date")} /></div>
                <div className="col-md-6"><Field label="End date" name="edit_end_date" type="date" defaultValue={valueOf(program, "end_date")} /></div>
              </div>
              <div className="d-flex flex-wrap gap-3 mb-3">
                {[["edit_cash_enabled", "Payment / Cash", program.cash_enabled], ["edit_nfi_enabled", "NFI / In-Kind", program.nfi_enabled], ["edit_medical_enabled", "Medical Assistance", program.medical_enabled]].map(([name, label, enabled]) => (
                  <div className="form-check" key={String(name)}><input className="form-check-input" id={String(name)} name={String(name)} type="checkbox" defaultChecked={Boolean(enabled)} /><label className="form-check-label" htmlFor={String(name)}>{String(label)}</label></div>
                ))}
              </div>
              <button className="btn btn-outline-primary" disabled={status === "saving"}>Save program changes</button>
            </form>
          </Panel>
        </div>
      )}
      {selected && !(program?.nfi_enabled || program?.medical_enabled) && (
        <div className="col-12">
          <Panel title="NFI setup">
            <p className="form-text">
              NFI is disabled for this program; no warehouse or item setup is
              shown.
            </p>
          </Panel>
        </div>
      )}
      {error && (
        <div className="col-12">
          <Message status={status} error={error} />
        </div>
      )}
    </div>
  );
}
