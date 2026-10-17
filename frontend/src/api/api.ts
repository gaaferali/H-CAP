const apiBase = "/api";
import type { ApiError, ApiList, PlatformUser } from "../types/types";
export type { PlatformUser } from "../types/types";

// Gets a new access token from the secure refresh cookie when a session expires.
async function refreshAccessToken() {
  const response = await fetch(`${apiBase}/auth/refresh/`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    body: "{}",
  });
  if (!response.ok) return false;
  const result = (await response.json()) as { access: string };
  localStorage.setItem("hcap_token", result.access);
  return true;
}

const errorMessage = (payload: ApiError & Record<string, unknown>, fallback: string) => {
  if (payload.error?.message) return payload.error.message;
  if (payload.detail) return payload.detail;
  const entry = Object.entries(payload).find(([key]) => key !== "error" && key !== "detail");
  if (!entry) return fallback;
  const [field, value] = entry;
  const message = Array.isArray(value) ? value.join(" ") : String(value);
  return `${field.replaceAll("_", " ")}: ${message}`;
};

// Sends one authenticated API request and returns the parsed backend response.
export async function apiRequest<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = localStorage.getItem("hcap_token");
  let response: Response;
  try {
    response = await fetch(`${apiBase}${path}`, {
      ...init,
      cache: "no-store",
      credentials: "same-origin",
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
    if (await refreshAccessToken()) {
      const access = localStorage.getItem("hcap_token");
      response = await fetch(`${apiBase}${path}`, { ...init, cache: "no-store", credentials: "same-origin", headers: { "Content-Type": "application/json", ...(access ? { Authorization: `Bearer ${access}` } : {}), ...init.headers } });
    }
  }
  if (!response.ok) {
    const payload = (await response.json().catch(() => ({}))) as ApiError;
    throw new Error(errorMessage(payload as ApiError & Record<string, unknown>, "Request failed"));
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
// Prevents a double-click from sending the same create request at the same time.
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

// Gets every page for a resource and returns one complete records list.
async function listAll<T>(resource: string): Promise<T[]> {
  let path: string | null = `/${resource}/`;
  const rows: T[] = [];
  const visited = new Set<string>();
  while (path && !visited.has(path)) {
    visited.add(path);
    const page: ApiList<T> = await apiRequest<ApiList<T>>(path);
    if (Array.isArray(page)) return [...rows, ...page];
    rows.push(...page.results);
    if (!page.next) break;
    const next = new URL(page.next, window.location.origin);
    path = `${next.pathname.replace(/^\/api/, "")}${next.search}`;
  }
  return rows;
}

export const api = {
  // Logs in with email and password, then returns the user shown in the platform.
  async login(email: string, password: string) {
    const result = await apiRequest<{ user: PlatformUser; access: string }>("/auth/login/", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    });
    localStorage.setItem("hcap_token", result.access);
    return result.user;
  },
  // Gets the current user to restore an existing frontend session.
  me: () => apiRequest<PlatformUser>("/me/"),
  updateProfile: (payload: { full_name: string; email: string; current_password: string }) =>
    apiRequest<PlatformUser>("/profile/details/", { method: "POST", body: JSON.stringify(payload) }),
  changePassword: (payload: { current_password: string; new_password: string; confirm_password: string }) =>
    apiRequest<{ status: string }>("/profile/password/", { method: "POST", body: JSON.stringify(payload) }),
  // Generic GET and POST helpers used by dashboard and workflow actions.
  get: <T>(path: string) => apiRequest<T>(path),
  post: <T>(path: string, payload: Record<string, unknown>) => apiRequest<T>(path, { method: "POST", body: JSON.stringify(payload) }),
  action: <T>(path: string, payload: Record<string, unknown> = {}) =>
    apiRequest<T>(path, { method: "POST", headers: { "Idempotency-Key": operationKey() }, body: JSON.stringify(payload) }),
  users: () => apiRequest<PlatformUser[] | { results: PlatformUser[] }>("/users/"),
  createUser: (payload: Pick<PlatformUser, "email" | "full_name" | "role"> & { password: string; tenant_id?: string }) =>
    createWithIdempotency<PlatformUser>("users", payload),
  logout: () => apiRequest<{ status: string }>("/auth/logout/", {
    method: "POST",
    body: "{}",
  }),
  // Returns all records for a resource, including paginated results.
  list: <T>(resource: string) => listAll<T>(resource),
  // Creates one record and returns the saved backend data.
  create: <T>(resource: string, payload: Record<string, unknown>) => createWithIdempotency<T>(resource, payload),
  importFile: async <T,>(file: File, programId: string, confirmationToken?: string) => {
    const token = localStorage.getItem("hcap_token");
    const form = new FormData();
    form.append("file", file);
    form.append("program_id", programId);
    if (confirmationToken) form.append("confirmation_token", confirmationToken);
    let response = await fetch(`${apiBase}/imports/`, { method: "POST", credentials: "same-origin", headers: token ? { Authorization: `Bearer ${token}` } : {}, body: form });
    if (response.status === 401) {
      if (await refreshAccessToken()) {
        const access = localStorage.getItem("hcap_token");
        response = await fetch(`${apiBase}/imports/`, { method: "POST", credentials: "same-origin", headers: access ? { Authorization: `Bearer ${access}` } : {}, body: form });
      }
    }
    if (!response.ok) {
      const payload = (await response.json().catch(() => ({}))) as ApiError;
      throw new Error(errorMessage(payload as ApiError & Record<string, unknown>, "Import failed"));
    }
    return response.json() as Promise<T>;
  },
  // Updates one record and returns its latest backend representation.
  update: <T>(resource: string, id: string, payload: Record<string, unknown>) =>
    apiRequest<T>(`/${resource}/${id}/`, { method: "PATCH", body: JSON.stringify(payload) }),
  remove: <T>(resource: string, id: string) =>
    apiRequest<T>(`/${resource}/${id}/`, { method: "DELETE" }),
  removeWithReason: <T>(resource: string, id: string, reason: string) =>
    apiRequest<T>(`/${resource}/${id}/`, { method: "DELETE", body: JSON.stringify({ reason }) }),
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
