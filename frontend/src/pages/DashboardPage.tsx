import { FormEvent, ReactNode, useEffect, useMemo, useState } from "react";
import { api, listItems } from "../api/api";
import type { PlatformUser } from "../api/api";
import type { Entity, Role } from "../types/types";
import { Badge, Field, FunctionHelp, Message, Panel, SelectField, Table, Textarea } from "../components";
import { TranslateTree, useTranslation } from "../i18n";
import { beneficiaryLabel, distributionEventLabel, enrollmentLabel, householdLabel, nfiBeneficiaryLabel, nfiEntitlementLabel, programLabel, readableCode, valueOf } from "../utils";
export function Dashboard({ user }: { user: PlatformUser }) {
  const [tenants, setTenants] = useState<Entity[]>([]);
  const [programs, setPrograms] = useState<Entity[]>([]);
  const [tenantId, setTenantId] = useState("");
  const [programId, setProgramId] = useState("");
  const [summary, setSummary] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState("");
  const isPlatform = user.is_superuser;

  useEffect(() => {
    const load = async () => {
      try {
        const [programRecords, tenantRecords] = await Promise.all([
          api.list<Entity>("programs"),
          isPlatform
            ? api.list<Entity>("tenants")
            : Promise.resolve([] as Entity[]),
        ]);
        setPrograms(listItems(programRecords));
        setTenants(
          isPlatform
            ? listItems(tenantRecords as Entity[] | { results: Entity[] })
            : [],
        );
      } catch (reason) {
        setError(
          reason instanceof Error
            ? reason.message
            : "Could not load dashboard scope.",
        );
      }
    };
    void load();
  }, [isPlatform]);

  const visiblePrograms = useMemo(
    () =>
      tenantId
        ? programs.filter((program) => valueOf(program, "tenant") === tenantId)
        : programs,
    [programs, tenantId],
  );
  useEffect(() => {
    if (
      programId &&
      !visiblePrograms.some((program) => program.id === programId)
    )
      setProgramId("");
  }, [programId, visiblePrograms]);
  useEffect(() => {
    const load = async () => {
      const query = new URLSearchParams();
      if (tenantId) query.set("tenant", tenantId);
      if (programId) query.set("program", programId);
      try {
        setSummary(
          await api
            .get<{
              dashboards: Record<string, unknown>;
              scope: Record<string, unknown>;
            }>(`/reports/${query.toString() ? `?${query}` : ""}`)
            .then((result) => ({ ...result.dashboards, scope: result.scope })),
        );
      } catch (reason) {
        setError(
          reason instanceof Error
            ? reason.message
            : "Could not load dashboard data.",
        );
      }
    };
    void load();
  }, [tenantId, programId]);

  const scope = (summary?.scope as Record<string, unknown> | undefined) ?? {};
  const metricKeys =
    user.role === "FIELD_OFFICER"
      ? ["beneficiaries", "approvals"]
      : user.role === "SUPPORT"
        ? ["beneficiaries", "complaints", "operational_exceptions"]
        : user.role === "REVIEWER"
          ? ["beneficiaries", "approvals", "failed"]
          : [
              "beneficiaries",
              "approvals",
              "paid",
              "pending",
              "failed",
              "amounts_approved",
              "amounts_distributed",
              "budget_planned",
              "budget_actual",
              "budget_remaining",
            ];
  const assignedComplaints = Array.isArray(summary?.assigned_complaints)
    ? (summary.assigned_complaints as Array<Record<string, unknown>>)
    : [];
  const roleMessage = user.is_superuser
    ? "Platform dashboard: select all tenants, one tenant, or one program."
    : user.role === "FINANCE"
      ? "Finance dashboard: simulated distribution, reconciliation, and budget figures for authorized programs."
      : user.role === "MANAGER"
        ? "Tenant dashboard: operational figures for your organization and selected program."
        : user.role === "FIELD_OFFICER"
          ? "Field dashboard: registration and permitted follow-up work."
          : user.role === "SUPPORT"
            ? "Support dashboard: complaints and PDM follow-up work."
            : user.role === "REVIEWER"
              ? "Reviewer dashboard: human eligibility and advisory review work."
              : "Read-only tenant-scoped operational dashboard.";
  return (
    <>
      <Panel
        title={
          user.is_superuser ? "Platform dashboard" : "Operational dashboard"
        }
      >
        <div className="row">
          <div className="col-md-6">
            {isPlatform && (
              <SelectField
                label="Tenant"
                name="dashboard-tenant"
                options={tenants.map((tenant) => ({
                  value: tenant.id,
                  label: tenant.name ?? "Unnamed tenant",
                }))}
                value={tenantId}
                onChange={(value) => {
                  setTenantId(value);
                  setProgramId("");
                }}
              />
            )}
          </div>
          <div className="col-md-6">
            <SelectField
              label="Program"
              name="dashboard-program"
              options={visiblePrograms.map((program) => ({
                value: program.id,
                label: programLabel(program),
              }))}
              value={programId}
              onChange={setProgramId}
            />
          </div>
        </div>
        <p className="mb-0">{roleMessage}</p>
        <p className="form-text mb-0">
          Scope: {String(scope.tenant_name ?? "Loading")} /{" "}
          {String(scope.program_name ?? "Loading")}
        </p>
      </Panel>
      <Message status="" error={error} />
      <section className="kpi-grid">
        {metricKeys.map((key) => (
          <article className="kpi" key={key}>
            <strong>{String(summary?.[key] ?? "—")}</strong>
            <span>{key.replaceAll("_", " ")}</span>
          </article>
        ))}
      </section>
      {["ADMIN", "SUPPORT", "MANAGER"].includes(user.role) && (
        <Panel title="Assigned complaints">
          <Table
            headings={[
              "Beneficiary",
              "Program",
              "Category",
              "Severity",
              "Status",
              "Resolution",
            ]}
            rows={assignedComplaints.map((complaint) => [
              String(complaint["beneficiary__full_name"] ?? "Beneficiary"),
              String(
                complaint["beneficiary__household__program__name"] ?? "Program",
              ),
              String(complaint.category ?? ""),
              <Badge value={String(complaint.severity ?? "MEDIUM")} />,
              <Badge value={String(complaint.status ?? "OPEN")} />,
              String(complaint.resolution_notes ?? "Pending follow-up"),
            ])}
          />
        </Panel>
      )}
    </>
  );
}
