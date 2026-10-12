import { FormEvent, useEffect, useState } from "react";
import { api, listItems } from "../api/api";
import type { Entity, Role } from "../types/types";
import { Badge, Field, FunctionHelp, Message, Panel, SelectField, Table, Textarea } from "../components";
import { beneficiaryLabel, programLabel, readableCode, valueOf } from "../utils";

const formatDateTime = (value: unknown) => {
  if (!value) return "Not recorded";
  const date = new Date(String(value));
  return Number.isNaN(date.getTime())
    ? String(value)
    : date.toLocaleString(document.documentElement.lang || undefined);
};

const humanResultValue = (value: unknown): string => {
  if (value === null || value === undefined || value === "") return "Not recorded";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (Array.isArray(value)) {
    if (!value.length) return "None";
    const records = value.filter(
      (item): item is Record<string, unknown> => !!item && typeof item === "object" && !Array.isArray(item),
    );
    const statusKey = records.length
      ? ["eligibility_status", "status", "priority"].find((key) => key in records[0])
      : undefined;
    if (statusKey) {
      const counts = records.reduce<Record<string, number>>((summary, item) => {
        const label = readableCode(item[statusKey] ?? "Unknown");
        summary[label] = (summary[label] ?? 0) + 1;
        return summary;
      }, {});
      return Object.entries(counts).map(([label, count]) => `${label}: ${count}`).join("; ");
    }
    return value.map((item) => humanResultValue(item)).join("; ");
  }
  if (typeof value === "object") {
    return Object.entries(value as Record<string, unknown>)
      .filter(([key]) => !key.endsWith("_id") && key !== "id")
      .map(([key, item]) => `${readableCode(key)}: ${humanResultValue(item)}`)
      .join("; ");
  }
  return String(value);
};

export function AutomationPage({ role }: { role: Role }) {
  const [data, setData] = useState<{
    status: string;
    message: string;
    rules: Entity[];
    signals_count: number;
    review_tasks_open: number;
    advisory_only: boolean;
  } | null>(null);
  const [beneficiaries, setBeneficiaries] = useState<Entity[]>([]);
  const [batches, setBatches] = useState<Entity[]>([]);
  const [programs, setPrograms] = useState<Entity[]>([]);
  const [signals, setSignals] = useState<Entity[]>([]);
  const [reviewTasks, setReviewTasks] = useState<Entity[]>([]);
  const [reconciliationItems, setReconciliationItems] = useState<Entity[]>([]);
  const [pipeline, setPipeline] = useState<Record<string, unknown> | null>(
    null,
  );
  const [result, setResult] = useState<Record<string, unknown> | null>(null);
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const [selectedAutomationAction, setSelectedAutomationAction] = useState("");
  const canDeduplicate = ["ADMIN", "MANAGER", "REVIEWER"].includes(role);
  const canReconcile = ["ADMIN", "MANAGER", "FINANCE", "AUDITOR"].includes(
    role,
  );
  const canUseCopilot = ["ADMIN", "MANAGER", "REVIEWER", "AUDITOR"].includes(
    role,
  );
  const canExecuteRules = ["ADMIN", "MANAGER"].includes(role);
  const canInspectPipeline = ["ADMIN", "MANAGER", "AUDITOR"].includes(role);

  const load = async () => {
    try {
      const statusResponse = await api.get<typeof data>("/ai/status/");
      setData(statusResponse);
      const programResponse = await api.list<Entity>("programs");
      setPrograms(listItems(programResponse));
      if (canDeduplicate) {
        const [beneficiaryResponse, signalResponse, taskResponse] =
          await Promise.all([
            api.list<Entity>("beneficiaries"),
            api.list<Entity>("ai-signals"),
            api.list<Entity>("review-tasks"),
          ]);
        setBeneficiaries(listItems(beneficiaryResponse));
        setSignals(listItems(signalResponse));
        setReviewTasks(listItems(taskResponse));
      } else {
        setBeneficiaries([]);
        setSignals([]);
        setReviewTasks([]);
      }
      if (canReconcile) {
        const [batchResponse, reconciliationResponse] = await Promise.all([
          api.list<Entity>("payment-batches"),
          api.list<Entity>("reconciliation-items"),
        ]);
        setBatches(listItems(batchResponse));
        setReconciliationItems(listItems(reconciliationResponse));
      } else {
        setBatches([]);
        setReconciliationItems([]);
      }
      if (canInspectPipeline)
        setPipeline(
          await api.get<Record<string, unknown>>("/pipeline/status/"),
        );
      else setPipeline(null);
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Could not load AI and automation status.",
      );
    }
  };

  useEffect(() => {
    void load();
  }, [role]);

  const run = async (
    event: FormEvent<HTMLFormElement>,
    path: string,
    payload: (form: FormData) => Record<string, unknown>,
  ) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setStatus("saving");
    setError("");
    try {
      setResult(await api.post<Record<string, unknown>>(path, payload(form)));
      await load();
      setStatus("saved");
    } catch (reason) {
      setStatus("failed");
      setError(
        reason instanceof Error
          ? reason.message
          : "Could not run the controlled service.",
      );
    }
  };

  const createRule = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    setStatus("saving");
    setError("");
    try {
      await api.create("automation-rules", {
        program: form.get("rule-program") || null,
        event_name: form.get("event_name"),
        action_name: form.get("action_name"),
        conditions: {},
        action_config: {},
        is_active: form.get("rule_active") === "on",
        priority: Number(form.get("rule_priority") || 100),
      });
      formElement.reset();
      await load();
      setStatus("saved");
    } catch (reason) {
      setStatus("failed");
      setError(
        reason instanceof Error
          ? reason.message
          : "Could not save the automation rule.",
      );
    }
  };

  const activeRules = (data?.rules ?? []).filter(
    (rule) => valueOf(rule, "is_active") === "true",
  );
  const openSignals = signals.filter((signal) => signal.status === "OPEN");
  const openReviewTasks = reviewTasks.filter((task) =>
    ["OPEN", "ASSIGNED"].includes(String(task.status)),
  );
  const unresolvedReconciliationItems = reconciliationItems.filter((item) =>
    ["OPEN", "IN_REVIEW"].includes(String(item.status)),
  );
  const canResolveReconciliation = ["ADMIN", "MANAGER", "FINANCE"].includes(
    role,
  );
  const automationResultRows =
    result && result.outcome
      ? (() => {
          const plannedAction =
            result.planned_action && typeof result.planned_action === "object"
              ? (result.planned_action as Record<string, unknown>)
              : {};
          const eventPayload =
            result.event_payload && typeof result.event_payload === "object"
              ? (result.event_payload as Record<string, unknown>)
              : {};
          const rows: [string, string][] = [
            [
              "Outcome",
              result.outcome === "HUMAN_REVIEW_TASK_CREATED"
                ? "Human review task created"
                : result.outcome === "DRY_RUN_NOT_EXECUTED"
                  ? "Dry run — no automatic action executed"
                  : readableCode(result.outcome),
            ],
          ];
          rows.push(["Action requested", readableCode(plannedAction.action)]);
          rows.push([
            "What happened",
            String(
              result.message ??
                "The controlled automation result was recorded.",
            ),
          ]);
          if (result.outcome === "HUMAN_REVIEW_TASK_CREATED") {
            rows.push([
              "Review queue",
              "The task is now open for a human reviewer.",
            ]);
            rows.push([
              "Review type",
              readableCode(eventPayload.task_type || "DATA_QUALITY"),
            ]);
            rows.push([
              "Priority",
              readableCode(eventPayload.priority || "MEDIUM"),
            ]);
          } else if (result.outcome === "DRY_RUN_NOT_EXECUTED") {
            rows.push([
              "Review queue",
              "No review task was created by this action.",
            ]);
            rows.push([
              "Next step",
              "Use a CREATE_REVIEW_TASK rule when a human review task is required.",
            ]);
          }
          return rows;
        })()
      : null;
  const resultRows =
    automationResultRows ??
    (result
      ? Object.entries(result).flatMap(([key, value]) => {
          if (["advisory_only", "verified", "review_task_created"].includes(key))
            return [];
          if (key === "beneficiary_id") {
            const beneficiary = beneficiaries.find((item) => item.id === String(value));
            return [["Beneficiary", beneficiary ? beneficiaryLabel(beneficiary) : "Selected beneficiary"]];
          }
          if (key === "batch_id") {
            const batch = batches.find((item) => item.id === String(value));
            return [[
              "Payment batch",
              batch
                ? `${valueOf(batch, "name") || "Batch"} — ${valueOf(batch, "program_name") || "Program"}`
                : "Selected payment batch",
            ]];
          }
          if (key.endsWith("_id") || ["tenant", "rule"].includes(key)) return [];
          if (key === "metrics" && value && typeof value === "object")
            return Object.entries(value as Record<string, unknown>).map(
              ([metric, metricValue]) => [
                `Verified ${readableCode(metric)}`,
                humanResultValue(metricValue),
              ],
            );
          if (
            key === "reconciliation_summary" &&
            value &&
            typeof value === "object"
          )
            return Object.entries(value as Record<string, unknown>)
              .filter(([summaryKey]) => summaryKey !== "items")
              .map(([summaryKey, summaryValue]) => [
                readableCode(summaryKey),
                humanResultValue(summaryValue),
              ]);
          if (key === "signals" && Array.isArray(value))
            return [["Signals created", String(value.length)]];
          return [
            [
              readableCode(key),
              key === "status" || key === "outcome"
                ? readableCode(value)
                : humanResultValue(value),
            ],
          ];
        })
      : []);
  return (
    <div className="row g-3">
      {canExecuteRules && (
        <div className="col-12">
          <Panel title="Configure automation rule">
            <p className="form-text">
              Create a rule that connects an event to a controlled action.
              CREATE_REVIEW_TASK creates a human review task; other actions
              remain dry runs.
            </p>
            <form className="row g-2" onSubmit={createRule}>
              <div className="col-md-3">
                <SelectField
                  label="Program"
                  name="rule-program"
                  options={programs.map((program) => ({
                    value: program.id,
                    label: programLabel(program),
                  }))}
                />
              </div>
              <div className="col-md-3">
                <SelectField
                  label="Event"
                  name="event_name"
                  options={[
                    "PAYMENT_FAILED",
                    "DUPLICATE_FLAG",
                    "HIGH_SEVERITY_COMPLAINT",
                    "STALLED_REVIEW",
                  ].map((value) => ({ value, label: value }))}
                  required
                />
              </div>
              <div className="col-md-3">
                <SelectField
                  label="Action"
                  name="action_name"
                  options={[
                    "CREATE_REVIEW_TASK",
                    "RETRY",
                    "ESCALATE",
                    "REMINDER",
                  ].map((value) => ({ value, label: value }))}
                  required
                />
              </div>
              <div className="col-md-2">
                <Field
                  label="Priority"
                  name="rule_priority"
                  type="number"
                  defaultValue="100"
                  required
                />
              </div>
              <div className="col-md-1 d-flex align-items-end">
                <div className="form-check mb-3">
                  <input
                    className="form-check-input"
                    id="rule-active"
                    name="rule_active"
                    type="checkbox"
                    defaultChecked
                  />
                  <label className="form-check-label" htmlFor="rule-active">
                    Active
                  </label>
                </div>
              </div>
              <div className="col-12">
                <button className="btn btn-outline-primary">
                  Save automation rule
                </button>
              </div>
            </form>
            <Message status={status} error={error} />
          </Panel>
        </div>
      )}
      <div className="col-12">
        <Panel title="AI and automation status">
          <div className="alert alert-secondary">
            <strong>{data?.status ?? "Loading"}.</strong>{" "}
            {data?.message ?? "Loading advisory service status."}
          </div>
          <p>
            <strong>Advisory AI signals:</strong> {data?.signals_count ?? 0}{" "}
            <span className="ms-3">
              <strong>Open human reviews:</strong>{" "}
              {data?.review_tasks_open ?? 0}
            </span>
          </p>
          <Table
            headings={[
              "Program",
              "Configured event",
              "Controlled action",
              "Priority",
              "Status",
            ]}
            rows={(data?.rules ?? []).map((rule) => [
              valueOf(rule, "program_name") || "All tenant programs",
              readableCode(valueOf(rule, "event_name")),
              readableCode(valueOf(rule, "action_name")),
              valueOf(rule, "priority"),
              <Badge
                value={
                  valueOf(rule, "is_active") === "true" ? "ACTIVE" : "DISABLED"
                }
              />,
            ])}
          />
        </Panel>
      </div>
      {canInspectPipeline && (
        <div className="col-12">
          <Panel title="Data pipeline status">
            <Table
              headings={["Pipeline stage", "Status"]}
              rows={
                Array.isArray(pipeline?.pipeline)
                  ? (pipeline.pipeline as string[]).map((stage) => [
                      readableCode(stage),
                      readableCode(pipeline?.status ?? "completed"),
                    ])
                  : []
              }
            />
            <p className="form-text">
              <span>
                {String(
                  (pipeline?.records as Record<string, unknown> | undefined)
                    ?.beneficiaries ?? 0,
                )}
              </span>{" "}
              beneficiary records,{" "}
              <span>
                {String(
                  (pipeline?.records as Record<string, unknown> | undefined)
                    ?.signals ?? 0,
                )}
              </span>{" "}
              advisory signals, and{" "}
              <span>
                {String(
                  (pipeline?.records as Record<string, unknown> | undefined)
                    ?.review_tasks ?? 0,
                )}
              </span>{" "}
              review tasks are in the permitted scope.
            </p>
          </Panel>
        </div>
      )}
      {canDeduplicate && (
        <div className="col-lg-6">
          <Panel title="Duplicate review">
            <FunctionHelp>
              <p>
                Choose a beneficiary to compare with other beneficiaries in the
                same authorized program. The result can create an advisory
                signal and a human review task; it never deletes or approves
                records.
              </p>
            </FunctionHelp>
            <form
              onSubmit={(event) => {
                const form = new FormData(event.currentTarget);
                void run(
                  event,
                  `/ai/deduplicate/${String(form.get("beneficiary"))}/`,
                  () => ({}),
                );
              }}
            >
              <SelectField
                label="Beneficiary"
                name="beneficiary"
                options={beneficiaries.map((item) => ({
                  value: item.id,
                  label: `${beneficiaryLabel(item)} — ${valueOf(item, "program_name")}`,
                }))}
                required
              />
              <p className="form-text">
                The detector creates advisory signals and human review tasks
                only.
              </p>
              <button className="btn btn-outline-primary">
                Run duplicate check
              </button>
            </form>
          </Panel>
        </div>
      )}
      {canReconcile && (
        <>
          <div className="col-lg-6">
            <Panel title="Advisory risk review">
              <FunctionHelp>
                <p>
                  Choose a simulated payment batch. The scan creates advisory
                  risk signals for human review and does not change payment
                  status.
                </p>
              </FunctionHelp>
              <form
                onSubmit={(event) =>
                  run(event, "/ai/anomalies/", (form) => ({
                    batch_id: String(form.get("batch")),
                  }))
                }
              >
                <SelectField
                  label="Simulated payment batch"
                  name="batch"
                  options={batches.map((batch) => ({
                    value: batch.id,
                    label: `${batch.name ?? "Batch"} — ${valueOf(batch, "program_name")}`,
                  }))}
                  required
                />
                <button className="btn btn-outline-primary">
                  Run advisory risk scan
                </button>
              </form>
            </Panel>
          </div>
          <div className="col-lg-6">
            <Panel title="Simulated reconciliation">
              <FunctionHelp>
                <p>
                  Choose a payment batch and enter the simulated provider report
                  as JSON. Each reference needs an amount and status. The result
                  identifies matched payments and exceptions for human
                  resolution.
                </p>
              </FunctionHelp>
              <form
                onSubmit={(event) =>
                  run(event, "/ai/reconcile/", (form) => {
                    const raw = String(
                      form.get("provider_report_data") || "",
                    ).trim();
                    try {
                      const parsed = JSON.parse(raw);
                      if (
                        !parsed ||
                        Array.isArray(parsed) ||
                        typeof parsed !== "object"
                      )
                        throw new Error();
                      return {
                        batch_id: String(form.get("batch")),
                        provider_report_data: parsed,
                      };
                    } catch {
                      throw new Error(
                        "Provider report must be a JSON object. Use double quotes and include amount and status for each provider reference.",
                      );
                    }
                  })
                }
              >
                <SelectField
                  label="Simulated payment batch"
                  name="batch"
                  options={batches.map((batch) => ({
                    value: batch.id,
                    label: `${batch.name ?? "Batch"} — ${valueOf(batch, "program_name")}`,
                  }))}
                  required
                />
                <Textarea
                  label="Controlled simulated provider report"
                  name="provider_report_data"
                  required
                  placeholder='{"SIM-reference":{"amount":"100","status":"SUCCESS"}}'
                  help="Use JSON only: each provider reference must contain amount and status."
                />
                <button className="btn btn-outline-primary">
                  Reconcile simulated batch
                </button>
              </form>
            </Panel>
          </div>
        </>
      )}
      {canUseCopilot && (
        <div className="col-lg-6">
          <Panel title="AI reporting copilot">
            <FunctionHelp>
              <p>
                Choose a program and ask for verified beneficiaries, approvals,
                payments, failures, complaints, or budget figures. The result is
                a verified metrics draft for people to interpret; it does not
                make decisions.
              </p>
            </FunctionHelp>
            <form
              onSubmit={(event) =>
                run(event, "/ai/copilot/", (form) => ({
                  program_id: String(form.get("program")),
                  question: String(form.get("question")),
                }))
              }
            >
              <SelectField
                label="Program"
                name="program"
                options={programs.map((program) => ({
                  value: program.id,
                  label: programLabel(program),
                }))}
                required
              />
              <Textarea label="Reporting question" name="question" required />
              <button className="btn btn-outline-primary">
                Get verified draft
              </button>
            </form>
          </Panel>
        </div>
      )}
      {canExecuteRules && (
        <div className="col-lg-6">
          <Panel title="Execute controlled automation rule">
            <FunctionHelp>
              <p>
                Choose an active rule. CREATE_REVIEW_TASK adds a human task to
                the queue. RETRY, ESCALATE, and REMINDER are recorded as dry
                runs only, so they do not change payment or eligibility.
              </p>
            </FunctionHelp>
            <form
              onSubmit={(event) =>
                run(event, "/ai/automation/execute/", (form) => {
                  const selectedRule = activeRules.find(
                    (rule) => rule.id === String(form.get("rule")),
                  );
                  const action = String(selectedRule?.action_name ?? "");
                  return {
                    rule_id: String(form.get("rule")),
                    idempotency_key: crypto.randomUUID(),
                    event_payload:
                      action === "CREATE_REVIEW_TASK"
                        ? {
                            task_type: String(form.get("task_type")),
                            priority: String(form.get("priority")),
                          }
                        : {},
                  };
                })
              }
            >
              <SelectField
                label="Active rule"
                name="rule"
                options={activeRules.map((rule) => ({
                  value: rule.id,
                  label: `${valueOf(rule, "event_name")} → ${valueOf(rule, "action_name")}`,
                }))}
                value={
                  activeRules.some(
                    (rule) => rule.id === selectedAutomationAction,
                  )
                    ? selectedAutomationAction
                    : undefined
                }
                onChange={(ruleId) => {
                  setSelectedAutomationAction(ruleId);
                }}
                required
              />
              {(activeRules.find((rule) => rule.id === selectedAutomationAction)
                ?.action_name ?? "") === "CREATE_REVIEW_TASK" && (
                <>
                  <SelectField
                    label="Human review type"
                    name="task_type"
                    options={[
                      "DATA_QUALITY",
                      "DUPLICATE_REVIEW",
                      "RISK_REVIEW",
                      "ELIGIBILITY_REVIEW",
                      "PAYMENT_EXCEPTION",
                      "RECONCILIATION",
                      "COMPLAINT_ESCALATION",
                    ].map((value) => ({ value, label: value }))}
                    required
                  />
                  <SelectField
                    label="Priority"
                    name="priority"
                    options={["LOW", "MEDIUM", "HIGH", "CRITICAL"].map(
                      (value) => ({ value, label: value }),
                    )}
                    required
                  />
                </>
              )}
              <p className="form-text">
                A CREATE_REVIEW_TASK rule adds work to the human review queue.
                RETRY, ESCALATE, and REMINDER are recorded as dry runs and do
                not change payment or eligibility.
              </p>
              <button
                className="btn btn-outline-primary"
                disabled={!activeRules.length}
              >
                Execute controlled rule
              </button>
            </form>
          </Panel>
        </div>
      )}
      {canDeduplicate && (
        <div className="col-lg-6">
          <Panel title="Record advisory signal review">
            <FunctionHelp>
              <p>
                Select an open signal created by duplicate or risk detection,
                choose the human outcome, and record the evidence or decision
                note. This closes the signal; it does not automatically change
                eligibility or payment.
              </p>
            </FunctionHelp>
            <form
              onSubmit={(event) => {
                const form = new FormData(event.currentTarget);
                void run(
                  event,
                  `/ai-signals/${String(form.get("signal"))}/review/`,
                  (submittedForm) => ({
                    status: String(submittedForm.get("signal_status")),
                    review_note: String(submittedForm.get("review_note")),
                  }),
                );
              }}
            >
              <SelectField
                label="Open advisory signal"
                name="signal"
                options={openSignals.map((signal) => ({
                  value: signal.id,
                  label: `${readableCode(valueOf(signal, "signal_type"))} — ${valueOf(signal, "entity_label") || valueOf(signal, "program_name") || "Tenant record"}`,
                }))}
                required
              />
              <SelectField
                label="Human review outcome"
                name="signal_status"
                options={["REVIEWED", "ACCEPTED", "DISMISSED"].map((value) => ({
                  value,
                  label: value,
                }))}
                required
              />
              <Textarea label="Review note" name="review_note" required />
              <button
                className="btn btn-outline-primary"
                disabled={!openSignals.length}
              >
                Save human review
              </button>
            </form>
          </Panel>
        </div>
      )}
      {canDeduplicate && (
        <div className="col-lg-6">
          <Panel title="Resolve review task">
            <FunctionHelp>
              <p>
                Select an open task, review the related records, and write what
                was decided or what follow-up is required. Resolving the task
                records the human action in the audit history.
              </p>
            </FunctionHelp>
            <form
              onSubmit={(event) => {
                const form = new FormData(event.currentTarget);
                void run(
                  event,
                  `/review-tasks/${String(form.get("review_task"))}/resolve/`,
                  (submittedForm) => ({
                    resolution: String(submittedForm.get("task_resolution")),
                  }),
                );
              }}
            >
              <SelectField
                label="Open review task"
                name="review_task"
                options={openReviewTasks.map((task) => ({
                  value: task.id,
                  label: `${readableCode(valueOf(task, "task_type"))} — ${valueOf(task, "entity_label") || valueOf(task, "program_name") || "Tenant record"}`,
                }))}
                required
              />
              <Textarea
                label="Resolution note"
                name="task_resolution"
                required
              />
              <button
                className="btn btn-outline-primary"
                disabled={!openReviewTasks.length}
              >
                Resolve task
              </button>
            </form>
          </Panel>
        </div>
      )}
      {canResolveReconciliation && (
        <div className="col-lg-6">
          <Panel title="Resolve reconciliation exception">
            <FunctionHelp>
              <p>
                Select an open exception, compare the expected and provider
                values, then record how the mismatch was resolved. The item
                remains an auditable human resolution.
              </p>
            </FunctionHelp>
            <form
              onSubmit={(event) => {
                const form = new FormData(event.currentTarget);
                void run(
                  event,
                  `/reconciliation-items/${String(form.get("reconciliation_item"))}/resolve/`,
                  (submittedForm) => ({
                    resolution_note: String(
                      submittedForm.get("resolution_note"),
                    ),
                  }),
                );
              }}
            >
              <SelectField
                label="Open reconciliation exception"
                name="reconciliation_item"
                options={unresolvedReconciliationItems.map((item) => ({
                  value: item.id,
                  label: `${valueOf(item, "issue_type")} — ${valueOf(item, "program_name") || "Program"}`,
                }))}
                required
              />
              <Textarea
                label="Resolution note"
                name="resolution_note"
                required
              />
              <button
                className="btn btn-outline-primary"
                disabled={!unresolvedReconciliationItems.length}
              >
                Resolve reconciliation item
              </button>
            </form>
          </Panel>
        </div>
      )}
      {canDeduplicate && (
        <div className="col-lg-6">
          <Panel title="Human review queue">
            <Table
              headings={[
                "Type",
                "Affected record",
                "Program",
                "Priority",
                "Status",
                "Assigned to",
              ]}
              rows={reviewTasks.map((task) => [
                readableCode(valueOf(task, "task_type")),
                valueOf(task, "entity_label") || "Tenant record",
                valueOf(task, "program_name") || "Tenant",
                <Badge value={valueOf(task, "priority") || "MEDIUM"} />,
                <Badge value={task.status ?? "OPEN"} />,
                valueOf(task, "assigned_to_name") || "Unassigned",
              ])}
            />
          </Panel>
        </div>
      )}
      {canDeduplicate && (
        <div className="col-12">
          <Panel title="Advisory AI signals">
            <Table
              headings={["Type", "Affected record", "Program", "Confidence", "Status", "Reason", "Created"]}
              rows={signals.map((signal) => [
                readableCode(valueOf(signal, "signal_type")),
                valueOf(signal, "entity_label") || "Tenant record",
                valueOf(signal, "program_name") || "Tenant",
                `${(Number(signal.confidence ?? 0) * 100).toFixed(0)}%`,
                <Badge value={signal.status ?? "OPEN"} />,
                valueOf(signal, "reason"),
                formatDateTime(signal.created_at),
              ])}
            />
          </Panel>
        </div>
      )}
      {canReconcile && (
        <div className="col-12">
          <Panel title="Reconciliation exceptions">
            <Table
              headings={[
                "Program",
                "Issue",
                "Expected",
                "Actual",
                "Status",
                "Resolution",
              ]}
              rows={reconciliationItems.map((item) => [
                valueOf(item, "program_name") || "Program",
                readableCode(valueOf(item, "issue_type")),
                valueOf(item, "expected_amount") || "—",
                valueOf(item, "actual_amount") || "—",
                <Badge value={item.status ?? "OPEN"} />,
                valueOf(item, "resolution_note") || "Pending human review",
              ])}
            />
          </Panel>
        </div>
      )}
      {result && (
        <div className="col-12">
          <Panel title="Controlled service result">
            <p className="form-text">
              This is the outcome of the action you just ran. Review the values
              below and use any new signal, task, or reconciliation exception in
              the relevant queue.
            </p>
            <Table headings={["Result", "Value"]} rows={resultRows} />
          </Panel>
        </div>
      )}
      <div className="col-12">
        <Message status={status} error={error} />
      </div>
    </div>
  );
}
