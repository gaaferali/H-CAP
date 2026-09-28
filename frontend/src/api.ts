const apiBase = "/api";
export type PlatformUser = {
  id: string;
  email: string;
  full_name: string;
  role: string;
  is_active: boolean;
  is_superuser: boolean;
  tenant?: { id: string; name: string };
};

type ApiError = { error?: { message?: string }; detail?: string };

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

async function request<T>(path: string, init: RequestInit = {}, retry = true): Promise<T> {
  const token = localStorage.getItem("hcap_token");
  let response: Response;
  try {
    response = await fetch(`${apiBase}${path}`, {
      ...init,
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
        response = await fetch(`${apiBase}${path}`, { ...init, headers: { "Content-Type": "application/json", Authorization: `Bearer ${payload.access}`, ...init.headers } });
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

export type ApiList<T> = T[] | { results: T[] };
export const listItems = <T,>(result: ApiList<T>) => Array.isArray(result) ? result : result.results;

export const api = {
  async login(email: string, password: string) {
    const result = await request<{ user: PlatformUser; access: string; refresh: string }>("/auth/login/", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    });
    localStorage.setItem("hcap_token", result.access);
    localStorage.setItem("hcap_refresh_token", result.refresh);
    return result.user;
  },
  me: () => request<PlatformUser>("/me/"),
  updateProfile: (payload: { full_name: string; email: string; current_password: string }) =>
    request<PlatformUser>("/profile/details/", { method: "POST", body: JSON.stringify(payload) }),
  changePassword: (payload: { current_password: string; new_password: string; confirm_password: string }) =>
    request<{ status: string }>("/profile/password/", { method: "POST", body: JSON.stringify(payload) }),
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, payload: Record<string, unknown>) => request<T>(path, { method: "POST", body: JSON.stringify(payload) }),
  users: () => request<PlatformUser[] | { results: PlatformUser[] }>("/users/"),
  createUser: (payload: Pick<PlatformUser, "email" | "full_name" | "role"> & { password: string; tenant_id?: string }) =>
    request<PlatformUser>("/users/", { method: "POST", body: JSON.stringify(payload) }),
  logout: () => request<{ status: string }>("/auth/logout/", {
    method: "POST",
    body: JSON.stringify({ refresh: localStorage.getItem("hcap_refresh_token") }),
  }),
  list: <T>(resource: string) => request<ApiList<T>>(`/${resource}/`),
  create: <T>(resource: string, payload: Record<string, unknown>) =>
    request<T>(`/${resource}/`, { method: "POST", body: JSON.stringify(payload) }),
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
    request<T>(`/${resource}/${id}/`, { method: "PATCH", body: JSON.stringify(payload) }),
  remove: <T>(resource: string, id: string) =>
    request<T>(`/${resource}/${id}/`, { method: "DELETE" }),
  programChannel: <T>(programId: string, payload: Record<string, unknown>) =>
    request<T>(`/programs/${programId}/channels/`, { method: "POST", body: JSON.stringify(payload) }),
  simulatePayment: <T>(instructionId: string, outcome: "submit" | "success" | "failure" | "retry" | "reversal") =>
    request<T>(`/payment-instructions/${instructionId}/simulate/`, { method: "POST", body: JSON.stringify({ outcome }) }),
  clearToken: () => { localStorage.removeItem("hcap_token"); localStorage.removeItem("hcap_refresh_token"); },
};
