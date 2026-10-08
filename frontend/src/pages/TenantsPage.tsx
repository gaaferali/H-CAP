import { FormEvent, ReactNode, useEffect, useMemo, useState } from "react";
import { api, listItems } from "../api/api";
import type { PlatformUser } from "../api/api";
import type { Entity, Role } from "../types/types";
import { Badge, Field, FunctionHelp, Message, Panel, SelectField, Table, Textarea } from "../components";
import { TranslateTree, useTranslation } from "../i18n";
import { beneficiaryLabel, distributionEventLabel, enrollmentLabel, householdLabel, nfiBeneficiaryLabel, nfiEntitlementLabel, programLabel, readableCode, valueOf } from "../utils";
export function TenantsPage() {
  const [tenants, setTenants] = useState<Entity[]>([]);
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const load = async () => {
    try {
      setTenants(listItems(await api.list<Entity>("tenants")));
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Could not load tenants.",
      );
    }
  };
  useEffect(() => {
    void load();
  }, []);
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    setStatus("saving");
    setError("");
    try {
      const tenant = await api.create<Entity>("tenants", {
        name: form.get("name"),
        tenant_type: form.get("tenant_type"),
        default_currency: form.get("default_currency"),
        is_active: true,
      });
      await api.createUser({
        full_name: String(form.get("manager_name")),
        email: String(form.get("manager_email")),
        password: String(form.get("manager_password")),
        role: "MANAGER",
        tenant_id: tenant.id,
      });
      formElement.reset();
      await load();
      setStatus("saved");
    } catch (reason) {
      setStatus("failed");
      setError(
        reason instanceof Error
          ? reason.message
          : "Could not create the tenant and first manager.",
      );
    }
  };
  return (
    <div className="row g-3">
      <div className="col-lg-7">
        <Panel title="Tenants">
          <Table
            headings={["Tenant", "Type", "Currency", "Status"]}
            rows={tenants.map((tenant) => [
              tenant.name ?? "Unnamed tenant",
              valueOf(tenant, "tenant_type"),
              valueOf(tenant, "default_currency"),
              <Badge
                value={
                  valueOf(tenant, "is_active") === "true"
                    ? "ACTIVE"
                    : "INACTIVE"
                }
              />,
            ])}
          />
        </Panel>
      </div>
      <div className="col-lg-5">
        <Panel title="Create tenant and first manager">
          <form onSubmit={submit}>
            <Field label="Tenant name" name="name" required />
            <Field label="Tenant type" name="tenant_type" required />
            <Field label="Default currency" name="default_currency" required />
            <Field label="First manager name" name="manager_name" required />
            <Field
              label="First manager email"
              name="manager_email"
              type="email"
              required
            />
            <Field
              label="Temporary password"
              name="manager_password"
              type="password"
              required
            />
            <button className="btn btn-primary">Create tenant</button>
          </form>
          <Message status={status} error={error} />
        </Panel>
      </div>
    </div>
  );
}
