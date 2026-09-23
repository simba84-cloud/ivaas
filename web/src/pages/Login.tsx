import { LogIn } from "lucide-react";
import { useState } from "react";
import { type AuthConfig, loginLocal, loginOidc } from "../auth/session";

const POINTS = [
  ["Count", "every crate on every truck, from the cameras already at the bay"],
  ["Link", "each load to its number plate the moment the LPR camera reads it"],
  ["Reconcile", "AI counts against the manual sheet, with the variance in plain sight"],
];

export default function Login({ config }: { config: AuthConfig }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

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
    <div className="grid min-h-screen bg-ground lg:grid-cols-[1.1fr_1fr]">
      {/* brand panel */}
      <aside className="relative hidden overflow-hidden bg-brand-deep text-white lg:flex lg:flex-col lg:justify-between lg:p-12">
        <div
          aria-hidden
          className="pointer-events-none absolute -right-40 -top-40 h-[34rem] w-[34rem] rounded-full bg-accent/25 blur-3xl"
        />
        <div
          aria-hidden
          className="pointer-events-none absolute -bottom-48 -left-24 h-[28rem] w-[28rem] rounded-full bg-white/10 blur-3xl"
        />
        <img src="/logo-liquid.png" alt="Liquid Intelligent Technologies" className="relative h-10 w-auto self-start brightness-0 invert" />
        <div className="relative">
          <div className="eyebrow text-white/60">Intelligent Video as a Service</div>
          <h1 className="mt-3 max-w-md text-4xl font-extrabold leading-tight tracking-tight [text-wrap:balance]">
            Every crate counted. Every truck accounted for.
          </h1>
          <ul className="mt-10 max-w-md space-y-5">
            {POINTS.map(([head, body]) => (
              <li key={head} className="grid grid-cols-[6rem_1fr] gap-4 text-sm">
                <span className="num font-bold uppercase tracking-wider text-accent">{head}</span>
                <span className="text-white/80">{body}</span>
              </li>
            ))}
          </ul>
        </div>
        <div className="relative num text-xs text-white/50">Loading-bay POC · target accuracy 95%</div>
      </aside>

      {/* sign-in */}
      <main className="flex items-center justify-center px-4 py-12">
        <div className="w-full max-w-sm">
          <img src="/logo-liquid.png" alt="Liquid Intelligent Technologies" className="mb-8 h-10 dark:brightness-0 dark:invert lg:hidden" />
          <h2 className="text-2xl font-extrabold tracking-tight text-ink">Sign in</h2>
          <p className="mt-1 text-sm text-muted">to the IVaaS operations portal</p>

          <div className="mt-8">
            {config.mode === "oidc" ? (
              <>
                <p className="text-sm text-muted">Use your organisation account. You will be redirected to sign in.</p>
                <button className="btn-primary mt-5 w-full" onClick={() => loginOidc(config)}>
                  <LogIn size={16} /> Continue
                </button>
              </>
            ) : (
              <form onSubmit={submit} className="space-y-5">
                <div>
                  <label htmlFor="username" className="label">
                    Username
                  </label>
                  <input
                    id="username"
                    className="input"
                    value={username}
                    onChange={(e) => setUsername(e.target.value)}
                    autoComplete="username"
                    autoFocus
                    required
                  />
                </div>
                <div>
                  <label htmlFor="password" className="label">
                    Password
                  </label>
                  <input
                    id="password"
                    className="input"
                    type="password"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    autoComplete="current-password"
                    required
                  />
                </div>
                {error && (
                  <p role="alert" className="rounded-lg bg-bad/10 px-3 py-2 text-sm text-bad">
                    {error}
                  </p>
                )}
                <button className="btn-primary w-full" disabled={busy}>
                  <LogIn size={16} /> {busy ? "Signing in…" : "Sign in"}
                </button>
                <p className="text-center text-xs text-faint">Development mode · local accounts</p>
              </form>
            )}
          </div>
        </div>
      </main>
    </div>
  );
}
