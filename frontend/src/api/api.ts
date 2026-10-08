const apiBase = "/api";
import type { ApiError, ApiList, PlatformUser } from "../types/types";
export type { PlatformUser } from "../types/types";

async function refreshAccessToken() {
  const refresh = localStorage.getItem("hcap_refresh_token");
  if (!refresh) return false;
  const response = await fetch(`${apiBase}/auth/refresh/`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh }),
  });
  if (!response.ok) return false;
  const result = (await response.json()) as { access: string };
  localStorage.setItem("hcap_token", result.access);
  return true;
}

export async function apiRequest<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = localStorage.getItem("hcap_token");
  let response: Response;
  try {
    response = await fetch(`${apiBase}${path}`, {
      ...init,
      cache: "no-store",
      headers: {
        "Content-Type": "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...init.headers,
      },
    });
  } catch {
    throw new Error("Unable to reach H-CAP. Check that the backend is running and try again.");
  }
  if (response.status === 401 && !path.includes("/auth/refresh/")) {
    const refresh = localStorage.getItem("hcap_refresh_token");
    if (refresh) {
      const refreshed = await fetch(`${apiBase}/auth/refresh/`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ refresh }) });
      if (refreshed.ok) {
        const payload = (await refreshed.json()) as { access: string };
        localStorage.setItem("hcap_token", payload.access);
        response = await fetch(`${apiBase}${path}`, { ...init, cache: "no-store", headers: { "Content-Type": "application/json", Authorization: `Bearer ${payload.access}`, ...init.headers } });
      }
    }
  }
  if (!response.ok) {
    const payload = (await response.json().catch(() => ({}))) as ApiError;
    throw new Error(payload.error?.message ?? payload.detail ?? "Request failed");
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

const pendingCreates = new Map<string, Promise<unknown>>();
const stablePayload = (value: unknown): string => {
  if (Array.isArray(value)) return `[${value.map(stablePayload).join(",")}]`;
  if (value && typeof value === "object") {
    const object = value as Record<string, unknown>;
    return `{${Object.keys(object).sort().map((key) => `${JSON.stringify(key)}:${stablePayload(object[key])}`).join(",")}}`;
  }
  return JSON.stringify(value);
};
const operationKey = () => `hcap-${typeof crypto !== "undefined" && crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2)}`}`;
const createWithIdempotency = <T,>(resource: string, payload: Record<string, unknown>) => {
  const key = `${resource}:${stablePayload(payload)}`;
  const existing = pendingCreates.get(key);
  if (existing) return existing as Promise<T>;
  const operation = apiRequest<T>(`/${resource}/`, { method: "POST", headers: { "Idempotency-Key": operationKey() }, body: JSON.stringify(payload) })
    .finally(() => pendingCreates.delete(key));
  pendingCreates.set(key, operation);
  return operation;
};

export const listItems = <T,>(result: ApiList<T>) => Array.isArray(result) ? result : result.results;

export const api = {
  async login(email: string, password: string) {
    const result = await apiRequest<{ user: PlatformUser; access: string; refresh: string }>("/auth/login/", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    });
    localStorage.setItem("hcap_token", result.access);
    localStorage.setItem("hcap_refresh_token", result.refresh);
    return result.user;
  },
  me: () => apiRequest<PlatformUser>("/me/"),
  updateProfile: (payload: { full_name: string; email: string; current_password: string }) =>
    apiRequest<PlatformUser>("/profile/details/", { method: "POST", body: JSON.stringify(payload) }),
  changePassword: (payload: { current_password: string; new_password: string; confirm_password: string }) =>
    apiRequest<{ status: string }>("/profile/password/", { method: "POST", body: JSON.stringify(payload) }),
  get: <T>(path: string) => apiRequest<T>(path),
  post: <T>(path: string, payload: Record<string, unknown>) => apiRequest<T>(path, { method: "POST", body: JSON.stringify(payload) }),
  users: () => apiRequest<PlatformUser[] | { results: PlatformUser[] }>("/users/"),
  createUser: (payload: Pick<PlatformUser, "email" | "full_name" | "role"> & { password: string; tenant_id?: string }) =>
    createWithIdempotency<PlatformUser>("users", payload),
  logout: () => apiRequest<{ status: string }>("/auth/logout/", {
    method: "POST",
    body: JSON.stringify({ refresh: localStorage.getItem("hcap_refresh_token") }),
  }),
  list: <T>(resource: string) => apiRequest<ApiList<T>>(`/${resource}/`),
  create: <T>(resource: string, payload: Record<string, unknown>) => createWithIdempotency<T>(resource, payload),
  importFile: async <T,>(file: File, programId: string, confirmationToken?: string) => {
    const token = localStorage.getItem("hcap_token");
    const form = new FormData();
    form.append("file", file);
    form.append("program_id", programId);
    if (confirmationToken) form.append("confirmation_token", confirmationToken);
    let response = await fetch(`${apiBase}/imports/`, { method: "POST", headers: token ? { Authorization: `Bearer ${token}` } : {}, body: form });
    if (response.status === 401) {
      const refresh = localStorage.getItem("hcap_refresh_token");
      if (refresh) {
        const refreshed = await fetch(`${apiBase}/auth/refresh/`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ refresh }) });
        if (refreshed.ok) {
          const payload = (await refreshed.json()) as { access: string };
          localStorage.setItem("hcap_token", payload.access);
          response = await fetch(`${apiBase}/imports/`, { method: "POST", headers: { Authorization: `Bearer ${payload.access}` }, body: form });
        }
      }
    }
    if (!response.ok) {
      const payload = (await response.json().catch(() => ({}))) as ApiError;
      throw new Error(payload.error?.message ?? payload.detail ?? "Import failed");
    }
    return response.json() as Promise<T>;
  },
  update: <T>(resource: string, id: string, payload: Record<string, unknown>) =>
    apiRequest<T>(`/${resource}/${id}/`, { method: "PATCH", body: JSON.stringify(payload) }),
  remove: <T>(resource: string, id: string) =>
    apiRequest<T>(`/${resource}/${id}/`, { method: "DELETE" }),
  programChannel: <T>(programId: string, payload: Record<string, unknown>) =>
    apiRequest<T>(`/programs/${programId}/channels/`, { method: "POST", headers: { "Idempotency-Key": operationKey() }, body: JSON.stringify(payload) }),
  simulatePayment: <T>(instructionId: string, outcome: "submit" | "success" | "failure" | "retry" | "reversal") =>
    apiRequest<T>(`/payment-instructions/${instructionId}/simulate/`, { method: "POST", body: JSON.stringify({ outcome }) }),
  analyzeComplaint: <T>(complaintId: string) => apiRequest<T>(`/ai/complaints/${complaintId}/analyze/`, { method: "POST" }),
  complaintAnalysis: <T>(complaintId: string) => apiRequest<T>(`/ai/complaints/${complaintId}/analysis/`),
  reviewComplaintAnalysis: <T>(complaintId: string, payload: { decision: "ACCEPT" | "MODIFY" | "DISMISS"; reviewer_note: string }) =>
    apiRequest<T>(`/ai/complaints/${complaintId}/review/`, { method: "POST", body: JSON.stringify(payload) }),
  analyzeOpenComplaints: <T>() => apiRequest<T>("/ai/complaints/analyze-bulk/", { method: "POST" }),
  clearToken: () => { localStorage.removeItem("hcap_token"); localStorage.removeItem("hcap_refresh_token"); },
};
