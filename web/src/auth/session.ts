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

/** Proposal §4.2 permissions, plus the additions the API names in domain/rbac.py. */
export type Permission =
  | "tenant.create"
  | "user.invite"
  | "user.manage"
  | "device.register"
  | "device.calibrate"
  | "video.live.view"
  | "count.read"
  | "count.override"
  | "groundtruth.enter"
  | "reconciliation.resolve"
  | "report.export"
  | "apikey.manage"
  | "invoice.read"
  | "subscription.manage"
  | "audit.read"
  | "assistant.query"
  | "topology.read"
  | "site.manage"
  | "session.operate"
  | "settings.manage"
  | "security.manage"
  | "support.request"
  | "support.approve"
  | "data.export"
  | "tenant.suspend";

export interface Me {
  subject: string;
  name: string;
  roles: string[];
  /** What the portal may offer. The API checks every call regardless. */
  permissions?: string[];
  /** Null for platform and partner staff, who belong to no tenant. */
  tenant?: { id: string; slug: string; name: string; status: string } | null;
  must_change_password?: boolean;
  /** Set while platform support works inside a tenant on an approved grant: read-only. */
  break_glass?: { grant_id: string; expires_at: string } | null;
}

const KEY = "ivaas.token";
const GLASS = "ivaas.break-glass";

/**
 * The break-glass grant support is working under, for this tab only. Every request
 * carries it; the API decides whether it still counts.
 */
export function getBreakGlass(): string | null {
  try {
    return sessionStorage.getItem(GLASS);
  } catch {
    return null;
  }
}

/**
 * Enter or leave a tenant on a grant, then load `then` afresh so nothing cached in one
 * tenant is shown in another. Without `then`, only the header stops being sent.
 */
export function switchBreakGlass(grantId: string | null, then?: string) {
  try {
    if (grantId) sessionStorage.setItem(GLASS, grantId);
    else sessionStorage.removeItem(GLASS);
  } catch {
    /* storage blocked: break-glass cannot be carried across requests */
  }
  if (then) window.location.assign(then);
}
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

/**
 * Whether to offer an action. Permissions come from the account's role bindings,
 * which may be scoped to one site or bay; the API is the judge of each call.
 */
export function can(me: Me | undefined, permission: Permission): boolean {
  return !!me?.permissions?.includes(permission);
}
