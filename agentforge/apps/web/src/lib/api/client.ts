import createClient, { type Middleware } from "openapi-fetch";

import type { paths } from "./schema";

/** The current session's CSRF token, set after sign-in or a session check. */
let csrfToken: string | null = null;

export function setCsrfToken(token: string | null): void {
  csrfToken = token;
}

const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);

const csrf: Middleware = {
  onRequest({ request }) {
    if (!SAFE_METHODS.has(request.method) && csrfToken) {
      request.headers.set("X-CSRF-Token", csrfToken);
    }
    return request;
  },
};

/** Typed client generated from the API's OpenAPI schema. Same origin: /api is proxied. */
export const api = createClient<paths>({ baseUrl: "/api", credentials: "same-origin" });
api.use(csrf);

export interface Problem {
  title: string;
  status: number;
  detail?: string;
  trace_id?: string;
}

export function problemTitle(error: unknown, fallback = "Something went wrong"): string {
  if (error && typeof error === "object" && "title" in error && typeof error.title === "string") {
    return error.title;
  }
  return fallback;
}
