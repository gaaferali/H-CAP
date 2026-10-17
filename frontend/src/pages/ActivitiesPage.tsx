import { FormEvent, useEffect, useState } from "react";
import { api, listItems } from "../api/api";
import type { PlatformUser } from "../api/api";
import type { Entity, Role } from "../types/types";
import { Badge, Field, Message, Panel, SelectField, Table } from "../components";
import { programLabel, valueOf } from "../utils";
// Shows program activities, progress, and the dependencies between them.
export function ActivitiesPage({ role }: { role: Role }) {
  const [programs, setPrograms] = useState<Entity[]>([]);
  const [activities, setActivities] = useState<Entity[]>([]);
  const [dependencies, setDependencies] = useState<Entity[]>([]);
  const [users, setUsers] = useState<PlatformUser[]>([]);
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const canWrite = role === "ADMIN" || role === "MANAGER";
  const canReadDependencies = ["ADMIN", "MANAGER", "AUDITOR"].includes(role);
  const activityStatus = (value: unknown) =>
    role === "REVIEWER"
      ? ({
          NOT_STARTED: "Start",
          IN_PROGRESS: "In progress",
          COMPLETED: "Finish",
          BLOCKED: "Blocked",
        }[String(value)] ?? String(value))
      : String(value);
  const load = async () => {
    try {
      const [programResponse, activityResponse] = await Promise.all([
        api.list<Entity>("programs"),
        api.list<Entity>("program-activities"),
      ]);
      setPrograms(listItems(programResponse));
      setActivities(listItems(activityResponse));
      if (canReadDependencies)
        setDependencies(
          listItems(await api.list<Entity>("activity-dependencies")),
        );
      else setDependencies([]);
      if (canWrite) setUsers(listItems(await api.users()));
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Could not load program activities.",
      );
    }
  };
  useEffect(() => {
    void load();
  }, [canReadDependencies, canWrite]);
  const createActivity = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    setStatus("saving");
    setError("");
    try {
      await api.create("program-activities", {
        program: form.get("program"),
        name: form.get("name"),
        workstream: form.get("workstream"),
        owner: form.get("owner") || null,
        status: form.get("activity_status"),
        progress: form.get("progress") || 0,
        risk: form.get("risk") || "",
      });
      formElement.reset();
      await load();
      setStatus("saved");
    } catch (reason) {
      setStatus("failed");
      setError(
        reason instanceof Error
          ? reason.message
          : "Could not save the activity.",
      );
    }
  };
  const createDependency = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    setStatus("saving");
    setError("");
    try {
      await api.create("activity-dependencies", {
        predecessor: form.get("predecessor"),
        successor: form.get("successor"),
        relationship_type: "FS",
        lag_days: Number(form.get("lag_days") || 0),
        reason: form.get("reason") || "",
      });
      formElement.reset();
      await load();
      setStatus("saved");
    } catch (reason) {
      setStatus("failed");
      setError(
        reason instanceof Error
          ? reason.message
          : "Could not save the dependency. A dependency cannot cross tenants, programs, or create a cycle.",
      );
    }
  };
  const activityOptions = activities.map((activity) => ({
    value: activity.id,
    label: `${valueOf(activity, "name")} — ${valueOf(activity, "program_name") || "Program"}`,
  }));
  return (
    <div className="row g-3">
      {canWrite && (
        <div className="col-lg-5">
          <Panel title="Add program activity">
            <form onSubmit={createActivity}>
              <SelectField
                label="Program"
                name="program"
                options={programs.map((program) => ({
                  value: program.id,
                  label: programLabel(program),
                }))}
                required
              />
              <Field label="Activity name" name="name" required />
              <Field label="Workstream" name="workstream" />
              <SelectField
                label="Owner"
                name="owner"
                options={users.map((user) => ({
                  value: user.id,
                  label: `${user.full_name} (${user.role})`,
                }))}
              />
              <SelectField
                label="Status"
                name="activity_status"
                options={[
                  "NOT_STARTED",
                  "IN_PROGRESS",
                  "BLOCKED",
                  "COMPLETED",
                ].map((value) => ({ value, label: value }))}
                required
              />
              <Field label="Progress (%)" name="progress" type="number" />
              <Field label="Risk" name="risk" />
              <button className="btn btn-primary">Save activity</button>
            </form>
          </Panel>
          <Panel title="Add finish-to-start dependency">
            <form onSubmit={createDependency}>
              <SelectField
                label="Predecessor activity"
                name="predecessor"
                options={activityOptions}
                required
              />
              <SelectField
                label="Successor activity"
                name="successor"
                options={activityOptions}
                required
              />
              <Field label="Lag days" name="lag_days" type="number" />
              <Field label="Reason" name="reason" />
              <button className="btn btn-outline-primary">
                Save dependency
              </button>
            </form>
          </Panel>
          <Message status={status} error={error} />
        </div>
      )}
      <div className={canWrite ? "col-lg-7" : "col-12"}>
        <Panel title="Precedence diagram activity list">
          <Table
            headings={[
              "Activity",
              "Program",
              "Owner",
              "Status",
              "Progress",
              "Risk",
            ]}
            rows={activities.map((activity) => [
              valueOf(activity, "name"),
              valueOf(activity, "program_name") || "Program",
              valueOf(activity, "owner_name") || "Unassigned",
              <Badge
                value={activityStatus(activity.status ?? "NOT_STARTED")}
              />,
              `${valueOf(activity, "progress") || 0}%`,
              valueOf(activity, "risk") || "—",
            ])}
          />
        </Panel>
        {canReadDependencies && (
          <Panel title="Dependencies">
            <Table
              headings={["Predecessor", "Successor", "Relationship", "Lag"]}
              rows={dependencies.map((dependency) => [
                valueOf(dependency, "predecessor_name") || "Activity",
                valueOf(dependency, "successor_name") || "Activity",
                valueOf(dependency, "relationship_type") || "FS",
                valueOf(dependency, "lag_days"),
              ])}
            />
          </Panel>
        )}
      </div>
    </div>
  );
}
