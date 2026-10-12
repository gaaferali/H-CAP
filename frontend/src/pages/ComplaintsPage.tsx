import { FormEvent, useEffect, useState } from "react";
import { api, listItems } from "../api/api";
import type { PlatformUser } from "../api/api";
import type { Entity, Role } from "../types/types";
import { Badge, Message, Panel, SelectField, Table, Textarea } from "../components";
import { beneficiaryLabel, readableCode, valueOf } from "../utils";

const formatDateTime = (value: unknown) => {
  if (!value) return "Not recorded";
  const date = new Date(String(value));
  return Number.isNaN(date.getTime())
    ? String(value)
    : date.toLocaleString(document.documentElement.lang || undefined);
};

function ComplaintEvidence({ value }: { value: unknown }) {
  const evidence = value && typeof value === "object"
    ? (value as Record<string, unknown>)
    : {};
  const keywordMatches = evidence.keyword_matches && typeof evidence.keyword_matches === "object"
    ? (evidence.keyword_matches as Record<string, unknown>)
    : {};
  const payment = evidence.payment_context && typeof evidence.payment_context === "object"
    ? (evidence.payment_context as Record<string, unknown>)
    : {};
  const recentEvents = Array.isArray(payment.recent_events)
    ? (payment.recent_events as Record<string, unknown>[])
    : [];

  return (
    <div className="ai-evidence-readable">
      <Table
        headings={["Evidence", "Finding"]}
        rows={[
          ["Complaint category supplied", readableCode(evidence.complaint_category_input || "Not recorded")],
          [
            "Keyword rule matches",
            Object.keys(keywordMatches).length
              ? Object.entries(keywordMatches)
                  .map(([category, count]) => `${readableCode(category)}: ${String(count)}`)
                  .join("; ")
              : "No keyword rule matched",
          ],
        ]}
      />
      {payment.available === false || !payment.payment_status ? (
        <div className="alert alert-secondary mb-0">
          {String(payment.message || "No linked payment evidence is available for this complaint.")}
        </div>
      ) : (
        <>
          <Table
            headings={["Payment status", "Amount", "Currency", "Provider reference"]}
            rows={[[
              <Badge value={String(payment.payment_status || "UNKNOWN")} />,
              String(payment.payment_amount || "Not recorded"),
              String(payment.currency || "Not recorded"),
              String(payment.provider_reference || "Not recorded"),
            ]]}
          />
          <h4>Recent payment events</h4>
          <Table
            headings={["Event", "Status change", "Provider status", "Provider transaction reference", "Time"]}
            rows={recentEvents.map((event) => [
              readableCode(event.event_type),
              `${readableCode(event.from_status || "Not recorded")} → ${readableCode(event.to_status || "Not recorded")}`,
              <Badge value={String(event.provider_status || "UNKNOWN")} />,
              String(event.provider_transaction_id || "Not recorded"),
              formatDateTime(event.created_at),
            ])}
          />
        </>
      )}
    </div>
  );
}

export function ComplaintsPage({ role }: { role: Role }) {
  const [beneficiaries, setBeneficiaries] = useState<Entity[]>([]);
  const [complaints, setComplaints] = useState<Entity[]>([]);
  const [users, setUsers] = useState<PlatformUser[]>([]);
  const [selectedComplaint, setSelectedComplaint] = useState<Entity | null>(
    null,
  );
  const [analysis, setAnalysis] = useState<Entity | null>(null);
  const [analysisLoading, setAnalysisLoading] = useState(false);
  const [reviewNote, setReviewNote] = useState("");
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const canAssign = role === "ADMIN" || role === "MANAGER";
  const canUseAI =
    role === "ADMIN" || role === "MANAGER" || role === "REVIEWER" || role === "SUPPORT";

  const load = async () => {
    try {
      const [beneficiaryResponse, complaintResponse] = await Promise.all([
        api.list<Entity>("beneficiaries"),
        api.list<Entity>("complaints"),
      ]);
      setBeneficiaries(listItems(beneficiaryResponse));
      setComplaints(listItems(complaintResponse));
      if (canAssign) setUsers(listItems(await api.users()));
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Could not load complaint records.",
      );
    }
  };

  useEffect(() => {
    void load();
  }, [canAssign]);

  const selectComplaint = async (complaint: Entity) => {
    setSelectedComplaint(complaint);
    setAnalysis(null);
    setError("");
    try {
      setAnalysis(await api.complaintAnalysis<Entity>(complaint.id));
    } catch (reason) {
      if (!(reason instanceof Error) || !reason.message.includes("not found"))
        setError(
          reason instanceof Error
            ? reason.message
            : "Could not load AI analysis.",
        );
    }
  };
  const runAnalysis = async () => {
    if (!selectedComplaint) return;
    setAnalysisLoading(true);
    setError("");
    setStatus("Analyzing complaint…");
    try {
      setAnalysis(await api.analyzeComplaint<Entity>(selectedComplaint.id));
      await load();
      setStatus("AI analysis complete.");
    } catch (reason) {
      setStatus("failed");
      setError(
        reason instanceof Error
          ? reason.message
          : "Could not analyze this complaint.",
      );
    } finally {
      setAnalysisLoading(false);
    }
  };
  const reviewAnalysis = async (decision: "ACCEPT" | "MODIFY" | "DISMISS") => {
    if (!selectedComplaint) return;
    setAnalysisLoading(true);
    setError("");
    try {
      setAnalysis(
        await api.reviewComplaintAnalysis<Entity>(selectedComplaint.id, {
          decision,
          reviewer_note: reviewNote,
        }),
      );
      setStatus("Human review saved.");
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Could not save the human review.",
      );
    } finally {
      setAnalysisLoading(false);
    }
  };
  const bulkAnalyze = async () => {
    setAnalysisLoading(true);
    setError("");
    try {
      const result = await api.analyzeOpenComplaints<{
        analyzed_count: number;
      }>();
      await load();
      setStatus(`${result.analyzed_count} open complaint(s) analyzed.`);
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Could not analyze open complaints.",
      );
    } finally {
      setAnalysisLoading(false);
    }
  };

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    setStatus("saving");
    setError("");
    try {
      await api.create("complaints", {
        beneficiary: form.get("beneficiary"),
        category: form.get("category"),
        description: form.get("description"),
        severity: form.get("severity"),
        status: "OPEN",
      });
      formElement.reset();
      await load();
      setStatus("saved");
    } catch (reason) {
      setStatus("failed");
      setError(
        reason instanceof Error
          ? reason.message
          : "Could not register the complaint.",
      );
    }
  };

  const updateComplaint = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    const complaintId = String(form.get("complaint"));
    setStatus("saving");
    setError("");
    try {
      const payload: Record<string, unknown> = {
        status: form.get("complaint_status"),
        resolution_notes: form.get("resolution_notes"),
      };
      if (canAssign && form.get("assigned_to"))
        payload.assigned_to = form.get("assigned_to");
      await api.update("complaints", complaintId, payload);
      formElement.reset();
      await load();
      setStatus("saved");
    } catch (reason) {
      setStatus("failed");
      setError(
        reason instanceof Error
          ? reason.message
          : "Could not update the complaint.",
      );
    }
  };

  const openComplaints = complaints.filter(
    (complaint) => !["RESOLVED", "CLOSED"].includes(String(complaint.status)),
  );
  return (
    <div className="row g-3">
      <div className="col-lg-5">
        <Panel title="Register complaint">
          <form onSubmit={submit}>
            <SelectField
              label="Beneficiary / household"
              name="beneficiary"
              options={beneficiaries.map((item) => ({
                value: item.id,
                label: `${beneficiaryLabel(item)} — ${valueOf(item, "program_name") || "Program"}`,
              }))}
              required
            />
            <SelectField
              label="Category"
              name="category"
              options={[
                { value: "PAYMENT", label: "Payment" },
                { value: "ACCESS", label: "Access" },
                { value: "ELIGIBILITY", label: "Eligibility" },
              ]}
              required
            />
            <Textarea label="Description" name="description" required />
            <SelectField
              label="Severity"
              name="severity"
              options={["LOW", "MEDIUM", "HIGH", "CRITICAL"].map((value) => ({
                value,
                label: value,
              }))}
              required
            />
            <button className="btn btn-primary">Register complaint</button>
          </form>
        </Panel>
        <Panel title="Assign, investigate, or resolve">
          <form onSubmit={updateComplaint}>
            <SelectField
              label="Open complaint"
              name="complaint"
              options={openComplaints.map((item) => ({
                value: item.id,
                label: `${valueOf(item, "category")} — ${valueOf(item, "beneficiary_name") || "Beneficiary"}`,
              }))}
              required
            />
            {canAssign && (
              <SelectField
                label="Assign to tenant user"
                name="assigned_to"
                options={users.map((person) => ({
                  value: person.id,
                  label: `${person.full_name} (${person.role})`,
                }))}
              />
            )}
            <SelectField
              label="Case status"
              name="complaint_status"
              options={["IN_PROGRESS", "RESOLVED", "CLOSED"].map((value) => ({
                value,
                label: value,
              }))}
              required
            />
            <Textarea
              label="Investigation or resolution note"
              name="resolution_notes"
              required
            />
            <button
              className="btn btn-outline-primary"
              disabled={!openComplaints.length}
            >
              Save complaint update
            </button>
          </form>
          <Message status={status} error={error} />
        </Panel>
      </div>
      <div className="col-lg-7">
        <Panel title="Complaints">
          <div className="d-flex justify-content-between align-items-center mb-3">
            {canUseAI && (
              <button
                className="btn btn-outline-primary"
                onClick={() => void bulkAnalyze()}
                disabled={analysisLoading}
              >
                Analyze Open Complaints
              </button>
            )}
            <span className="form-text">
              Select a complaint to view details and AI analysis.
            </span>
          </div>
          <Table
            headings={[
              "Beneficiary",
              "Program",
              "Category",
              "Severity",
              "AI",
              "Status",
              "View",
            ]}
            rows={complaints.map((item) => [
              valueOf(item, "beneficiary_name") || "Beneficiary",
              valueOf(item, "program_name") || "Program",
              readableCode(valueOf(item, "category")),
              <Badge value={valueOf(item, "severity") || "MEDIUM"} />,
              <Badge
                value={item.ai_analysis_available ? "ANALYZED" : "NOT ANALYZED"}
              />,
              <Badge value={item.status ?? "OPEN"} />,
              <button
                className="btn btn-sm btn-outline-secondary"
                onClick={() => void selectComplaint(item)}
              >
                View
              </button>,
            ])}
          />
        </Panel>
        {selectedComplaint && (
          <Panel title="Complaint details">
            <dl className="complaint-details">
              <dt>Beneficiary</dt>
              <dd>{valueOf(selectedComplaint, "beneficiary_name")}</dd>
              <dt>Program / household</dt>
              <dd>
                {valueOf(selectedComplaint, "program_name")} /{" "}
                {valueOf(selectedComplaint, "household_reference")}
              </dd>
              <dt>Category</dt>
              <dd>{readableCode(valueOf(selectedComplaint, "category"))}</dd>
              <dt>Description</dt>
              <dd>{valueOf(selectedComplaint, "description")}</dd>
              <dt>Severity / status</dt>
              <dd>
                {readableCode(valueOf(selectedComplaint, "severity"))} /{" "}
                {readableCode(valueOf(selectedComplaint, "status"))}
              </dd>
              <dt>Assigned user</dt>
              <dd>
                {valueOf(selectedComplaint, "assigned_to_name") || "Unassigned"}
              </dd>
              <dt>Resolution notes</dt>
              <dd>{valueOf(selectedComplaint, "resolution_notes") || "—"}</dd>
            </dl>
            <Panel title="AI Analysis">
              {analysisLoading && (
                <p className="form-text">Analyzing complaint…</p>
              )}
              {!analysis && (
                <div>
                  <p className="form-text">
                    No AI analysis is available for this complaint.
                  </p>
                  {canUseAI && (
                    <button
                      className="btn btn-primary"
                      onClick={() => void runAnalysis()}
                      disabled={analysisLoading}
                    >
                      Run AI Analysis
                    </button>
                  )}
                </div>
              )}
              {analysis && (
                <div className="ai-analysis">
                  <div className="d-flex gap-2 flex-wrap mb-3">
                    <Badge value={valueOf(analysis, "severity")} />
                    {analysis.human_review_required && (
                      <span className="alert alert-warning py-1 px-2 mb-0">
                        Human Review Required
                      </span>
                    )}
                  </div>
                  <p>
                    <strong>Category:</strong> {readableCode(valueOf(analysis, "category"))}
                  </p>
                  <p>
                    <strong>Summary:</strong> {valueOf(analysis, "summary")}
                  </p>
                  <p>
                    <strong>Overall confidence:</strong>{" "}
                    {(Number(analysis.confidence ?? 0) * 100).toFixed(0)}%
                  </p>
                  <p>
                    <strong>Requires escalation:</strong>{" "}
                    {analysis.requires_escalation ? "Yes" : "No"}
                  </p>
                  <p>
                    <strong>Model version:</strong>{" "}
                    {valueOf(analysis, "model_version")}
                  </p>
                  <h3>Possible causes</h3>
                  <ul>
                    {Array.isArray(analysis.possible_causes) &&
                      (
                        analysis.possible_causes as Record<string, unknown>[]
                      ).map((cause, index) => (
                        <li key={index}>
                          {String(cause.cause)} (
                          {(Number(cause.confidence ?? 0) * 100).toFixed(0)}%)
                        </li>
                      ))}
                  </ul>
                  <h3>Recommended actions</h3>
                  <ul>
                    {Array.isArray(analysis.recommended_actions) &&
                      (analysis.recommended_actions as string[]).map(
                        (action, index) => <li key={index}>{action}</li>,
                      )}
                  </ul>
                  <h3>Evidence and payment context</h3>
                  <ComplaintEvidence value={analysis.evidence} />
                  {analysis.review_decision && (
                    <p>
                       <strong>Human decision:</strong>{" "}
                       {readableCode(valueOf(analysis, "review_decision"))} —{" "}
                      {valueOf(analysis, "reviewer_note")}
                    </p>
                  )}
                  {canUseAI && (
                    <>
                      <Textarea
                        label="Reviewer note"
                        name="ai-review-note"
                        value={reviewNote}
                        onChange={setReviewNote}
                      />
                      <div className="d-flex gap-2 flex-wrap">
                        <button
                          className="btn btn-success"
                          onClick={() => void reviewAnalysis("ACCEPT")}
                          disabled={analysisLoading}
                        >
                          Accept
                        </button>
                        <button
                          className="btn btn-outline-primary"
                          onClick={() => void reviewAnalysis("MODIFY")}
                          disabled={analysisLoading}
                        >
                          Modify
                        </button>
                        <button
                          className="btn btn-outline-danger"
                          onClick={() => void reviewAnalysis("DISMISS")}
                          disabled={analysisLoading}
                        >
                          Dismiss
                        </button>
                        <button
                          className="btn btn-outline-secondary"
                          onClick={() =>
                            void selectComplaint(selectedComplaint)
                          }
                          disabled={analysisLoading}
                        >
                          Refresh AI Analysis
                        </button>
                      </div>
                    </>
                  )}
                </div>
              )}
            </Panel>
          </Panel>
        )}
      </div>
    </div>
  );
}
