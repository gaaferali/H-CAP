import { FormEvent, useState } from "react";
import { api } from "../api/api";
import type { PlatformUser } from "../api/api";
import { Field } from "../components";
import { TranslateTree, useTranslation } from "../i18n";
// Shows the login form and returns the authenticated user to the application.
export function Login({ onLogin }: { onLogin: (user: PlatformUser) => void }) {
  const translate = useTranslation();
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    setSaving(true);
    setError("");
    try {
      onLogin(
        await api.login(
          String(form.get("email")),
          String(form.get("password")),
        ),
      );
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Email or password is incorrect.",
      );
    } finally {
      setSaving(false);
    }
  };
  return (
    <TranslateTree>
      <main className="login-page">
        <form className="login-card" onSubmit={submit}>
          <div className="agency-mark" aria-hidden="true">
            +
          </div>
          <p className="eyebrow">Humanitarian operations</p>
          <h1>H-CAP sign in</h1>
          <p>
            Sign in with your staff account to see only your permitted services.
          </p>
          <Field label="Work email" name="email" type="email" required />
          <Field label="Password" name="password" type="password" required />
          <button className="btn btn-primary w-100" disabled={saving}>
            {saving ? translate("Signing in…") : translate("Sign in")}
          </button>
          {error && (
            <div className="alert alert-danger mt-3" role="alert">
              {translate(error)}
            </div>
          )}
        </form>
      </main>
    </TranslateTree>
  );
}
