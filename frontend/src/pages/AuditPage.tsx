import { useEffect, useState } from "react";
import { api, listItems } from "../api/api";
import type { Entity } from "../types/types";
import { Message, Panel, Table } from "../components";
import { valueOf } from "../utils";
// Shows the append-only audit history returned by the reporting API.
export function AuditPage() {
  const [events, setEvents] = useState<Entity[]>([]);
  const [error, setError] = useState("");
  useEffect(() => {
    api
      .list<Entity>("audit-events")
      .then((response) => setEvents(listItems(response)))
      .catch((reason) =>
        setError(
          reason instanceof Error
            ? reason.message
            : "Could not load permitted audit history.",
        ),
      );
  }, []);
  return (
    <Panel title="Audit history">
      <Message status="" error={error} />
      <Table
        headings={["Action", "Entity", "Actor", "Time"]}
        rows={events.map((event) => [
          valueOf(event, "action"),
          valueOf(event, "entity_type"),
          valueOf(event, "actor_name") || "System",
          valueOf(event, "created_at"),
        ])}
      />
    </Panel>
  );
}
