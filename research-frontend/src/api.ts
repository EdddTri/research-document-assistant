import type { User } from "./types";

export const API = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";
let token = "";
let identity = "";
let generation = 0;
let controller = new AbortController();
let refreshing: Promise<User> | null = null;
const channel = typeof BroadcastChannel !== "undefined" ? new BroadcastChannel("research-identity") : null;

export function clearIdentity(broadcast = true) {
  generation++;
  controller.abort();
  controller = new AbortController();
  token = "";
  identity = "";
  refreshing = null;
  window.dispatchEvent(new Event("research-identity-cleared"));
  if (broadcast) channel?.postMessage("changed");
}
channel?.addEventListener("message", () => clearIdentity(false));

function csrf() {
  return document.cookie.split("; ").find(c => c.startsWith("research_csrf="))?.split("=")[1] || "";
}

async function locked<T>(work: () => Promise<T>): Promise<T> {
  return navigator.locks ? navigator.locks.request("research-auth", work) : work();
}

async function checked(response: Response) {
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(typeof data.detail === "string" ? data.detail : `Request failed (${response.status})`);
  }
  return response;
}

export async function refresh(): Promise<User> {
  if (refreshing) return refreshing;
  const epoch = generation;
  const pending = locked(async () => {
    if (epoch !== generation) throw new DOMException("Account changed", "AbortError");
    const response = await checked(await fetch(`${API}/auth/refresh`, {
      method: "POST", credentials: "include", signal: controller.signal, headers: { "X-CSRF-Token": csrf() },
    }));
    const data = await response.json();
    if (epoch !== generation) throw new DOMException("Account changed", "AbortError");
    if (identity && identity !== data.user.id) {
      clearIdentity();
      throw new Error("Account changed. Sign in again.");
    }
    token = data.access_token;
    identity = data.user.id;
    return data.user as User;
  });
  refreshing = pending;
  try { return await pending; } finally { if (refreshing === pending) refreshing = null; }
}

export async function signIn(email: string, password: string, register: boolean): Promise<User> {
  clearIdentity();
  const epoch = generation;
  return locked(async () => {
    const response = await checked(await fetch(`${API}/auth/${register ? "register" : "login"}`, {
      method: "POST", credentials: "include", signal: controller.signal,
      headers: { "Content-Type": "application/json" }, body: JSON.stringify({ email, password }),
    }));
    const data = await response.json();
    if (epoch !== generation) throw new DOMException("Account changed", "AbortError");
    token = data.access_token;
    identity = data.user.id;
    return data.user;
  });
}

export async function request(path: string, options: RequestInit = {}, retry = true): Promise<Response> {
  const epoch = generation;
  const response = await fetch(API + path, {
    ...options, credentials: "include", signal: controller.signal,
    headers: { ...(options.body && !(options.body instanceof FormData) ? { "Content-Type": "application/json" } : {}),
      Authorization: `Bearer ${token}`, "X-CSRF-Token": csrf(), ...options.headers },
  });
  if (epoch !== generation) throw new DOMException("Account changed", "AbortError");
  if (response.status === 401 && retry) {
    try { await refresh(); } catch (error) { clearIdentity(); throw error; }
    return request(path, options, false);
  }
  return checked(response);
}

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await request(path, options);
  return response.status === 204 ? undefined as T : response.json();
}

export async function signOut() {
  try { await locked(() => request("/auth/logout", { method: "POST" }, false)); }
  finally { clearIdentity(); }
}
