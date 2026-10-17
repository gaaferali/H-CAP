import { FormEvent, useEffect, useState } from "react";
import { api, listItems } from "../api/api";
import type { PlatformUser } from "../api/api";
import type { Entity, Role } from "../types/types";
import { Badge, Field, Message, Panel, SelectField, Table } from "../components";
// Shows tenant users and the form used to register a new platform user.
export function UsersPage({ system }: { system: boolean }) {
  const [users, setUsers] = useState<PlatformUser[]>([]);
  const [tenants, setTenants] = useState<Entity[]>([]);
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const roles: Role[] = system
    ? [
        "ADMIN",
        "FIELD_OFFICER",
        "FINANCE",
        "REVIEWER",
        "SUPPORT",
        "MANAGER",
        "AUDITOR",
      ]
    : ["FIELD_OFFICER", "FINANCE", "REVIEWER", "SUPPORT", "AUDITOR"];
  // Gets users and returns tenant choices only for a platform administrator.
  const load = async () => {
    try {
      setUsers(listItems(await api.users()));
      if (system) setTenants(listItems(await api.list<Entity>("tenants")));
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Could not load users.",
      );
    }
  };
  useEffect(() => {
    void load();
  }, [system]);
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    setStatus("saving");
    setError("");
    try {
      await api.createUser({
        full_name: String(form.get("full_name")),
        email: String(form.get("email")),
        role: String(form.get("role")) as Role,
        password: String(form.get("password")),
        ...(system ? { tenant_id: String(form.get("tenant")) } : {}),
      });
      formElement.reset();
      await load();
      setStatus("saved");
    } catch (reason) {
      setStatus("failed");
      setError(
        reason instanceof Error ? reason.message : "Could not create the user.",
      );
    }
  };
  const toggleActive = async (person: PlatformUser) => {
    try {
      await api.update("users", person.id, { is_active: !person.is_active });
      await load();
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Could not update user access.",
      );
    }
  };
  return (
    <div className="row g-3">
      <div className="col-lg-7">
        <Panel title={system ? "All tenant users" : "Tenant users"}>
          <Table
            headings={["Name", "Email", "Tenant", "Role", "Status", "Access"]}
            rows={users.map((person) => [
              person.full_name,
              person.email,
              person.tenant?.name ?? "Current tenant",
              person.role,
              <Badge value={person.is_active ? "ACTIVE" : "INACTIVE"} />,
              <button
                className="btn btn-sm btn-outline-secondary"
                onClick={() => void toggleActive(person)}
              >
                {person.is_active ? "Deactivate" : "Activate"}
              </button>,
            ])}
          />
        </Panel>
      </div>
      <div className="col-lg-5">
        <Panel title="Add tenant user">
          <form onSubmit={submit}>
            {system && (
              <SelectField
                label="Tenant"
                name="tenant"
                options={tenants.map((tenant) => ({
                  value: tenant.id,
                  label: tenant.name ?? "Unnamed tenant",
                }))}
                required
              />
            )}
            <Field label="Full name" name="full_name" required />
            <Field label="Work email" name="email" type="email" required />
            <SelectField
              label="Role"
              name="role"
              options={roles.map((value) => ({ value, label: value }))}
              required
            />
            <Field
              label="Temporary password"
              name="password"
              type="password"
              required
            />
            <button className="btn btn-primary">Create user</button>
          </form>
          <Message status={status} error={error} />
        </Panel>
      </div>
    </div>
  );
}
