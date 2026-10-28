/**
 * Client for the Postgres-backed login gate added in app_web.py (`simple_auth.py`).
 *
 * Separate from `authClient.ts` (Huawei Account Kit / free-model-quota login):
 * this gate controls access to the app itself, not model quota. Its routes
 * (`/login`, `/auth/login`, `/auth/logout`) live on the same origin as the
 * SPA, served directly by the static file server — no `getApiBase()` needed.
 */

export async function logout(): Promise<void> {
  await fetch('/auth/logout', {
    method: 'POST',
    credentials: 'include',
  }).catch(() => undefined);
  window.location.href = '/login';
}
