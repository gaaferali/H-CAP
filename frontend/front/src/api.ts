const apiBase = import.meta.env.VITE_API_BASE_URL ?? "/api";

export type PlatformUser = {
  id: string;
  email: string;
  full_name: string;
  role: string;
  is_active: boolean;
  tenant?: { id: string; name: string };
};

type ApiError = { error?: { message?: string }; detail?: string };

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = localStorage.getItem("hcap_token");
  const response = await fetch(`${apiBase}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Token ${token}` } : {}),
      ...init.headers,
    },
  });
  if (!response.ok) {
    const payload = (await response.json().catch(() => ({}))) as ApiError;
    throw new Error(payload.error?.message ?? payload.detail ?? "Request failed");
  }
  return response.json() as Promise<T>;
}

export type ApiList<T> = T[] | { results: T[] };
export const listItems = <T,>(result: ApiList<T>) => Array.isArray(result) ? result : result.results;

export const api = {
  async login(email: string, password: string) {
    const result = await request<{ user: PlatformUser; token: string }>("/auth/login/", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    });
    localStorage.setItem("hcap_token", result.token);
    return result.user;
  },
  me: () => request<PlatformUser>("/me/"),
  get: <T>(path: string) => request<T>(path),
  users: () => request<PlatformUser[] | { results: PlatformUser[] }>("/users/"),
  createUser: (payload: Pick<PlatformUser, "email" | "full_name" | "role"> & { password: string }) =>
    request<PlatformUser>("/users/", { method: "POST", body: JSON.stringify(payload) }),
  logout: () => request<{ status: string }>("/auth/logout/", { method: "POST" }),
  list: <T>(resource: string) => request<ApiList<T>>(`/${resource}/`),
  create: <T>(resource: string, payload: Record<string, unknown>) =>
    request<T>(`/${resource}/`, { method: "POST", body: JSON.stringify(payload) }),
  update: <T>(resource: string, id: string, payload: Record<string, unknown>) =>
    request<T>(`/${resource}/${id}/`, { method: "PATCH", body: JSON.stringify(payload) }),
  programChannel: <T>(programId: string, payload: Record<string, unknown>) =>
    request<T>(`/programs/${programId}/channels/`, { method: "POST", body: JSON.stringify(payload) }),
  simulatePayment: <T>(instructionId: string, outcome: "submit" | "success" | "failure" | "retry" | "reversal") =>
    request<T>(`/payment-instructions/${instructionId}/simulate/`, { method: "POST", body: JSON.stringify({ outcome }) }),
  clearToken: () => localStorage.removeItem("hcap_token"),
};
