import { LogIn } from "lucide-react";
import { useState } from "react";
import { type AuthConfig, loginLocal, loginOidc } from "../auth/session";

export default function Login({ config }: { config: AuthConfig }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const input =
    "w-full rounded-lg border border-slate-300 px-3.5 py-2.5 text-sm outline-none focus:border-brand-navy focus:ring-2 focus:ring-brand-navy/20";

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await loginLocal(username, password);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-brand-surface px-4">
      <div className="w-full max-w-sm">
        <div className="mb-8 text-center">
          <img src="/logo-liquid.png" alt="Liquid Intelligent Technologies" className="mx-auto h-12" />
          <div className="mt-4 text-xs font-semibold uppercase tracking-widest text-slate-400">
            Intelligent Video as a Service
          </div>
        </div>
        <div className="card p-6">
          {config.mode === "oidc" ? (
            <>
              <p className="text-sm text-slate-600">
                Sign in with your organisation account to access the IVaaS portal.
              </p>
              <button className="btn-primary mt-5 w-full" onClick={() => loginOidc(config)}>
                <LogIn size={16} /> Sign in
              </button>
            </>
          ) : (
            <form onSubmit={submit} className="space-y-4">
              <div>
                <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500">
                  Username
                </label>
                <input
                  className={input}
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  autoComplete="username"
                  autoFocus
                  required
                />
              </div>
              <div>
                <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500">
                  Password
                </label>
                <input
                  className={input}
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  autoComplete="current-password"
                  required
                />
              </div>
              {error && <p className="text-sm text-red-600">{error}</p>}
              <button className="btn-primary w-full" disabled={busy}>
                <LogIn size={16} /> {busy ? "Signing in…" : "Sign in"}
              </button>
              <p className="text-center text-xs text-slate-400">
                Development mode · local accounts
              </p>
            </form>
          )}
        </div>
      </div>
    </div>
  );
}
