/**
 * Auth session for the portal. Two modes, chosen by the API:
 *  - local: username/password -> HS256 token from the API (development)
 *  - oidc:  redirect to the identity provider (Keycloak etc.), code + PKCE
 * Either way the result is a bearer token attached to every request and the WebSocket.
 */
import { UserManager, WebStorageStateStore } from "oidc-client-ts";

export interface AuthConfig {
  mode: "local" | "oidc";
  oidc_issuer: string | null;
  oidc_client_id: string | null;
}

export interface Me {
  subject: string;
  name: string;
  roles: string[];
  must_change_password?: boolean;
}

const KEY = "ivaas.token";
let userManager: UserManager | undefined;
const listeners = new Set<() => void>();

export function getToken(): string | null {
  try {
    return sessionStorage.getItem(KEY);
  } catch {
    return null;
  }
}

function setToken(token: string | null) {
  try {
    if (token) sessionStorage.setItem(KEY, token);
    else sessionStorage.removeItem(KEY);
  } catch {
    /* storage blocked: the token lives for this page only */
  }
  listeners.forEach((l) => l());
}

export function onAuthChange(l: () => void): () => void {
  listeners.add(l);
  return () => {
    listeners.delete(l);
  };
}

export async function fetchAuthConfig(): Promise<AuthConfig> {
  return (await fetch("/api/v1/auth/config")).json();
}

function manager(cfg: AuthConfig): UserManager {
  if (!userManager) {
    userManager = new UserManager({
      authority: cfg.oidc_issuer!,
      client_id: cfg.oidc_client_id!,
      redirect_uri: `${location.origin}/auth/callback`,
      post_logout_redirect_uri: location.origin,
      scope: "openid profile",
      userStore: new WebStorageStateStore({ store: sessionStorage }),
      // Keycloak access tokens last ~5 min. Renew with the refresh token a minute
      // before expiry so an operator is not bounced to the login page mid-shift.
      automaticSilentRenew: true,
      accessTokenExpiringNotificationTimeInSeconds: 60,
    });
    userManager.events.addUserLoaded((user) => setToken(user.access_token));
    userManager.events.addSilentRenewError(() => setToken(null));
    userManager.events.addUserSignedOut(() => setToken(null));
    // a reload mid-session: pick the stored user back up
    userManager.getUser().then((user) => {
      if (user && !user.expired) setToken(user.access_token);
    });
  }
  return userManager;
}

/** Start the renewal machinery for an existing OIDC session (call once at app start). */
export function resumeOidc(cfg: AuthConfig): void {
  if (cfg.mode === "oidc") manager(cfg);
}

/** Returns true when the account holds a temporary password and must set a new one. */
export async function loginLocal(username: string, password: string): Promise<boolean> {
  const r = await fetch("/api/v1/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail ?? "Login failed");
  const body = await r.json();
  setToken(body.access_token);
  return Boolean(body.must_change_password);
}

/** Replace the stored token, after a password change hands back a fresh one. */
export function adoptToken(token: string): void {
  setToken(token);
}

export function loginOidc(cfg: AuthConfig): Promise<void> {
  return manager(cfg).signinRedirect({ state: location.pathname });
}

/** Called on /auth/callback. Returns the path to continue to. */
export async function completeOidc(cfg: AuthConfig): Promise<string> {
  const user = await manager(cfg).signinRedirectCallback();
  setToken(user.access_token);
  return (user.state as string) || "/";
}

export async function logout(cfg: AuthConfig | undefined): Promise<void> {
  setToken(null);
  if (cfg?.mode === "oidc") await manager(cfg).signoutRedirect();
}

export function hasRole(me: Me | undefined, role: "viewer" | "operator" | "admin"): boolean {
  const rank = { viewer: 1, operator: 2, admin: 3 };
  const have = Math.max(0, ...(me?.roles ?? []).map((r) => rank[r as keyof typeof rank] ?? 0));
  return have >= rank[role];
}
