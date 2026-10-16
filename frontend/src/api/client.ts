import createClient, { type Middleware } from "openapi-fetch";
import type { components, paths } from "./schema";

export type Schemas = components["schemas"];

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

/** Called on any 401 so the app can drop back to the login screen. */
let onUnauthorized: () => void = () => {};
export function setUnauthorizedHandler(fn: () => void) {
  onUnauthorized = fn;
}

const csrf: Middleware = {
  onRequest({ request }) {
    request.headers.set("X-Requested-With", "creator-lab");
    return request;
  },
  onResponse({ response, request }) {
    if (response.status === 401 && !request.url.endsWith("/api/auth/me") && !request.url.endsWith("/api/auth/login")) {
      onUnauthorized();
    }
    return response;
  },
};

export const api = createClient<paths>({
  baseUrl: typeof window !== "undefined" ? window.location.origin : "",
  credentials: "same-origin",
  // Look fetch up per call (not once at import) so tests can stub it.
  fetch: (req) => globalThis.fetch(req),
});
api.use(csrf);

/** Unwrap an openapi-fetch result: return data or throw ApiError with the server's message. */
export async function unwrap<T>(
  promise: Promise<{ data?: T; error?: unknown; response: Response }>,
): Promise<T> {
  const { data, error, response } = await promise;
  if (error !== undefined || !response.ok) {
    const msg =
      error && typeof error === "object" && "error" in error ? String((error as { error: unknown }).error) : response.statusText;
    throw new ApiError(msg || `HTTP ${response.status}`, response.status);
  }
  return data as T;
}

/** Plain fetch for endpoints that are not JSON (binary streams, file downloads). */
export async function rawFetch(url: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers);
  headers.set("X-Requested-With", "creator-lab");
  const res = await fetch(url, { ...init, headers, credentials: "same-origin" });
  if (res.status === 401) onUnauthorized();
  if (!res.ok) {
    let msg = res.statusText;
    try {
      msg = (await res.clone().json()).error ?? msg;
    } catch {
      /* not JSON */
    }
    throw new ApiError(msg, res.status);
  }
  return res;
}
