import { useEffect, useState } from "react";
import { api, listItems } from "../api/api";
import type { Entity, Role } from "../types/types";
import { Badge, Message, Panel, Table } from "../components";
import { nfiBeneficiaryLabel, valueOf } from "../utils";
// Shows the available cash, NFI, and medical delivery workspaces for this role.
export function AssistancePage({ role }: { role: Role }) {
  const [cash, setCash] = useState<Entity[]>([]);
  const [nfi, setNfi] = useState<Entity[]>([]);
  const [items, setItems] = useState<Entity[]>([]);
  const [events, setEvents] = useState<Entity[]>([]);
  const [error, setError] = useState("");
  const load = async () => {
    try {
      const [cashResponse, nfiResponse, itemResponse, eventResponse] =
        await Promise.all([
          api.list<Entity>("cash-entitlements"),
          api.list<Entity>("nfi-entitlements"),
          api.list<Entity>("nfi-items"),
          api.list<Entity>("distribution-events"),
        ]);
      setCash(listItems(cashResponse));
      setNfi(listItems(nfiResponse));
      setItems(listItems(itemResponse));
      setEvents(listItems(eventResponse));
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Could not load assistance records.",
      );
    }
  };
  useEffect(() => {
    void load();
  }, []);
  return (
    <div className="row g-3">
      <div className="col-12">
        <Panel title="Assistance delivery">
          <p className="section-lead">
            Household eligibility and enrollment control cash and NFI assistance;
            members are retained as secondary identity context.
          </p>
          <Message status="" error={error} />
        </Panel>
      </div>
      <div className="col-xl-6">
        <Panel title="Cash entitlements">
          <Table
            headings={["Program", "Household", "Amount", "Currency", "Status"]}
            rows={cash.map((item) => [
              valueOf(item, "program_name") || "Program",
              valueOf(item, "household_reference") || "Household",
              valueOf(item, "amount"),
              valueOf(item, "currency"),
              <Badge value={valueOf(item, "status")} />,
            ])}
          />
        </Panel>
      </div>
      <div className="col-xl-6">
        <Panel title="NFI entitlements">
          <Table
            headings={["Program", "Household", "Item", "Quantity", "Status"]}
            rows={nfi.map((item) => [
              valueOf(item, "program_name") || "Program",
              valueOf(item, "household_reference") || nfiBeneficiaryLabel(item),
              valueOf(item, "item_name") || "NFI item",
              valueOf(item, "quantity"),
              <Badge value={valueOf(item, "status")} />,
            ])}
          />
        </Panel>
      </div>
      <div className="col-xl-6">
        <Panel title="NFI catalogue and stock">
          <Table
            headings={["Item", "Available", "Active"]}
            rows={items.map((item) => [
              valueOf(item, "name"),
              valueOf(item, "available_quantity"),
              valueOf(item, "active"),
            ])}
          />
        </Panel>
      </div>
      <div className="col-xl-6">
        <Panel title="Distribution events">
          <Table
            headings={["Location", "Date", "Status"]}
            rows={events.map((item) => [
              valueOf(item, "location"),
              valueOf(item, "event_date"),
              <Badge value={valueOf(item, "status")} />,
            ])}
          />
        </Panel>
      </div>
      <div className="col-12">
        <Panel title="Other Assistance / Third Modality">
          <p>
            Coming Soon — This assistance type is planned but not currently
            implemented.
          </p>
          <small>Role access: {role}</small>
        </Panel>
      </div>
    </div>
  );
}
