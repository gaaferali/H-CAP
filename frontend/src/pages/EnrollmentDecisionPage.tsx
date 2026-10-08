import { FormEvent, ReactNode, useEffect, useMemo, useState } from "react";
import { api, listItems } from "../api/api";
import type { PlatformUser } from "../api/api";
import type { Entity, Role } from "../types/types";
import { Badge, Field, FunctionHelp, Message, Panel, SelectField, Table, Textarea } from "../components";
import { TranslateTree, useTranslation } from "../i18n";
import { beneficiaryLabel, distributionEventLabel, enrollmentLabel, householdLabel, nfiBeneficiaryLabel, nfiEntitlementLabel, programLabel, readableCode, valueOf } from "../utils";
export function EnrollmentDecisionPage() {
  const [enrollments, setEnrollments] = useState<Entity[]>([]);
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const load = async () => {
    try {
      setEnrollments(listItems(await api.list<Entity>("enrollments")));
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Could not load enrollment decisions.",
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
      await api.update("enrollments", String(form.get("enrollment")), {
        status: form.get("status"),
      });
      formElement.reset();
      await load();
      setStatus("saved");
    } catch (reason) {
      setStatus("failed");
      setError(
        reason instanceof Error
          ? reason.message
          : "Could not record the enrollment decision.",
      );
    }
  };
  return (
    <div className="row g-3">
      <div className="col-lg-5">
        <Panel title="Enrollment decision">
          <form onSubmit={submit}>
            <SelectField
              label="Enrollment"
              name="enrollment"
              options={enrollments.map((item) => ({
                value: item.id,
                label: enrollmentLabel(item),
              }))}
              required
            />
            <SelectField
              label="Decision"
              name="status"
              options={["APPROVED", "REJECTED"].map((value) => ({
                value,
                label: value,
              }))}
              required
            />
            <button className="btn btn-primary">Record human decision</button>
          </form>
          <Message status={status} error={error} />
        </Panel>
      </div>
      <div className="col-lg-7">
        <Panel title="Enrollment history">
          <Table
            headings={["Beneficiary", "Program", "Eligibility", "Decision"]}
            rows={enrollments.map((item) => [
              enrollmentLabel(item),
              valueOf(item, "program_name"),
              <Badge
                value={valueOf(item, "eligibility_status") || "PENDING"}
              />,
              <Badge value={item.status ?? "ENROLLED"} />,
            ])}
          />
        </Panel>
      </div>
    </div>
  );
}
