import { motion, useReducedMotion } from "framer-motion";
import { LogIn, ShieldCheck } from "lucide-react";
import { useState } from "react";
import { type AuthConfig, loginLocal, loginOidc } from "../auth/session";
import { BRAND_GRADIENT, CrateMotif, LOGIN_STACKS } from "../components/brand";

const FACTS = [
  { value: "95%", label: "Counting accuracy target" },
  { value: "24/7", label: "Continuous crate counting" },
  { value: "ONVIF", label: "Any camera, any make" },
];

export default function Login({ config }: { config: AuthConfig }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const still = useReducedMotion();

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
    <div className="min-h-screen bg-ground lg:grid lg:grid-cols-[1.05fr_1fr]">
      {/* brand panel */}
      <aside
        className="relative hidden overflow-hidden text-white lg:flex lg:flex-col lg:justify-between lg:p-12"
        style={{ background: BRAND_GRADIENT }}
      >
        <CrateMotif stacks={LOGIN_STACKS} />
        {/* light blooms for depth */}
        <div
          aria-hidden
          className="pointer-events-none absolute -right-32 -top-40 h-[32rem] w-[32rem] rounded-full bg-white/15 blur-3xl"
        />
        <div
          aria-hidden
          className="pointer-events-none absolute -bottom-40 -left-32 h-[30rem] w-[30rem] rounded-full bg-[#c8187d]/40 blur-3xl"
        />
        {/* keeps text legible wherever the gradient lands */}
        <div
          aria-hidden
          className="pointer-events-none absolute inset-0 bg-gradient-to-r from-[#141f47]/90 via-[#141f47]/45 to-transparent"
        />

        <img
          src="/logo-liquid.png"
          alt="Liquid Intelligent Technologies"
          className="relative h-10 w-auto self-start brightness-0 invert"
        />

        <motion.div
          className="relative"
          initial={still ? false : { opacity: 0, y: 14 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.5, ease: [0.2, 0.7, 0.2, 1] }}
        >
          <div className="eyebrow text-white/70">Intelligent Video as a Service</div>
          <h1 className="mt-3 max-w-lg text-[2.6rem] font-extrabold leading-[1.08] tracking-tight">
            Every crate counted. Every truck accounted for.
          </h1>
          <p className="mt-4 max-w-md text-sm leading-relaxed text-white/85">
            Crates are counted off the cameras already at the bay, matched to the truck's number
            plate, and reconciled against the manual sheet.
          </p>

          <div className="mt-9 flex flex-wrap gap-3">
            {FACTS.map((f, i) => (
              <motion.div
                key={f.value}
                initial={still ? false : { opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.4, delay: 0.15 + i * 0.08 }}
                className="rounded-xl border border-white/20 bg-white/10 px-4 py-3 backdrop-blur-md"
              >
                <div className="num text-2xl font-bold leading-none">{f.value}</div>
                <div className="mt-1.5 text-[11px] font-medium text-white/80">{f.label}</div>
              </motion.div>
            ))}
          </div>
        </motion.div>

        <div className="relative num text-[11px] text-white/60">
          © 2026 Liquid Intelligent Technologies · Self-hosted · Role-based access
        </div>
      </aside>

      {/* mobile brand band, so the identity survives on a phone */}
      <div
        className="relative flex h-32 items-end overflow-hidden px-5 pb-4 lg:hidden"
        style={{ background: BRAND_GRADIENT }}
      >
        <CrateMotif stacks={LOGIN_STACKS} />
        <img
          src="/logo-liquid.png"
          alt="Liquid Intelligent Technologies"
          className="relative h-8 w-auto brightness-0 invert"
        />
      </div>

      {/* sign-in */}
      <main className="flex items-center justify-center px-4 py-10 lg:py-12">
        <div className="w-full max-w-sm">
          <motion.div
            initial={still ? false : { opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.4, delay: 0.1 }}
            className="card-lift p-7"
          >
            <div className="flex items-center gap-1.5 text-accent">
              <ShieldCheck size={14} strokeWidth={2.6} />
              <span className="eyebrow text-accent">Secure access</span>
            </div>
            <h2 className="mt-3 text-2xl font-extrabold tracking-tight text-ink">
              Sign in to your account
            </h2>
            <p className="mt-1 text-sm text-muted">
              {config.mode === "oidc"
                ? "You will be redirected to your organisation's sign-in."
                : "Enter your credentials to reach the operations portal."}
            </p>

            <div className="mt-6">
              {config.mode === "oidc" ? (
                <button className="btn-primary w-full" onClick={() => loginOidc(config)}>
                  <LogIn size={16} /> Continue
                </button>
              ) : (
                <form onSubmit={submit} className="space-y-4">
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
                </form>
              )}
            </div>

            <p className="mt-6 border-t border-line pt-4 text-center text-xs leading-relaxed text-muted">
              Accounts are issued by your system administrator. Forgotten your password? Ask an
              administrator to reset it.
            </p>
          </motion.div>

          <p className="mt-5 text-center text-xs text-faint">
            {config.mode === "oidc" ? "Single sign-on" : "Development mode · local accounts"} ·
            Intelligent Video as a Service
          </p>
        </div>
      </main>
    </div>
  );
}
