import { FormEvent, ReactNode, useEffect, useMemo, useState } from "react";
import { api, listItems } from "../api/api";
import type { PlatformUser } from "../api/api";
import type { Entity, Role } from "../types/types";
import { Badge, Field, FunctionHelp, Message, Panel, SelectField, Table, Textarea } from "../components";
import { TranslateTree, useTranslation } from "../i18n";
import { beneficiaryLabel, distributionEventLabel, enrollmentLabel, householdLabel, nfiBeneficiaryLabel, nfiEntitlementLabel, programLabel, readableCode, valueOf } from "../utils";
export function ProfilePage({
  user,
  onUpdated,
}: {
  user: PlatformUser;
  onUpdated: (user: PlatformUser) => void;
}) {
  const [detailsStatus, setDetailsStatus] = useState("");
  const [passwordStatus, setPasswordStatus] = useState("");
  const [detailsError, setDetailsError] = useState("");
  const [passwordError, setPasswordError] = useState("");
  const updateDetails = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setDetailsStatus("saving");
    setDetailsError("");
    try {
      const updated = await api.updateProfile({
        full_name: String(form.get("profile_name")),
        email: String(form.get("profile_email")),
        current_password: String(form.get("details_password")),
      });
      onUpdated(updated);
      setDetailsStatus("saved");
    } catch (reason) {
      setDetailsStatus("failed");
      setDetailsError(
        reason instanceof Error
          ? reason.message
          : "Could not update your profile details.",
      );
    }
  };
  const updatePassword = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    setPasswordStatus("saving");
    setPasswordError("");
    try {
      await api.changePassword({
        current_password: String(form.get("old_password")),
        new_password: String(form.get("new_password")),
        confirm_password: String(form.get("confirm_password")),
      });
      formElement.reset();
      setPasswordStatus("saved");
    } catch (reason) {
      setPasswordStatus("failed");
      setPasswordError(
        reason instanceof Error
          ? reason.message
          : "Could not change your password.",
      );
    }
  };
  return (
    <div className="profile-grid">
      <Panel title="Update personal details">
        <p className="section-lead">
          Change the name and email shown on your H-CAP account.
        </p>
        <form onSubmit={updateDetails}>
          <Field
            label="Full name"
            name="profile_name"
            defaultValue={user.full_name}
            required
          />
          <Field
            label="Work email"
            name="profile_email"
            type="email"
            defaultValue={user.email}
            required
          />
          <Field
            label="Current password"
            name="details_password"
            type="password"
            required
          />
          <button className="btn btn-primary">Save details</button>
          <Message status={detailsStatus} error={detailsError} />
        </form>
      </Panel>
      <Panel title="Change password">
        <p className="section-lead">
          Use your current password to set a new password for this account.
        </p>
        <form onSubmit={updatePassword}>
          <Field
            label="Current password"
            name="old_password"
            type="password"
            required
          />
          <Field
            label="New password"
            name="new_password"
            type="password"
            required
            help="Use at least 8 characters."
          />
          <Field
            label="Confirm new password"
            name="confirm_password"
            type="password"
            required
          />
          <button className="btn btn-primary">Update password</button>
          <Message status={passwordStatus} error={passwordError} />
        </form>
      </Panel>
    </div>
  );
}
