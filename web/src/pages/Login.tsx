import { motion, useReducedMotion } from "framer-motion";
import { LogIn, ShieldCheck } from "lucide-react";
import { useState } from "react";
import { type AuthConfig, loginLocal, loginOidc } from "../auth/session";

/**
 * The brand panel is deliberately single-theme: it is a lit surface in its own
 * right, like the video wells, so it reads the same in light and dark. Its colours
 * are literals rather than tokens for that reason.
 */
const GRADIENT =
  "linear-gradient(150deg, #16234f 0%, #273c87 32%, #5b2a8c 62%, #a3187f 84%, #c8187d 100%)";

/**
 * Crate stacks: the thing this platform counts, drawn as the panel's own motif.
 * Few and large rather than many and small, so it reads as a deliberate graphic
 * and not as a grid of loading skeletons.
 */
const STACKS = [
  { x: 296, base: 992, w: 300, h: 86, n: 6, o: 0.09 },
  { x: 92, base: 1004, w: 250, h: 74, n: 4, o: 0.06 },
  { x: 470, base: 560, w: 210, h: 62, n: 3, o: 0.05 },
];

/** One crate: an overhanging lip it stacks on, and a cut-out handle at each end. */
function Crate({ x, y, w, h, o }: { x: number; y: number; w: number; h: number; o: number }) {
  const lip = w * 0.035;
  const slotW = w * 0.16;
  return (
    <g>
      {/* body */}
      <rect
        x={x}
        y={y}
        width={w}
        height={h}
        rx={6}
        fill="#fff"
        fillOpacity={o}
        stroke="#fff"
        strokeOpacity={o * 2.6}
        strokeWidth={1.5}
      />
      {/* the lip overhangs the body: what makes a stack read as a stack */}
      <rect
        x={x - lip}
        y={y - h * 0.06}
        width={w + lip * 2}
        height={h * 0.17}
        rx={4}
        fill="#fff"
        fillOpacity={o * 1.7}
        stroke="#fff"
        strokeOpacity={o * 2.2}
        strokeWidth={1}
      />
      {/* handles, cut back towards the panel behind */}
      {[x + w * 0.11, x + w * 0.73].map((sx) => (
        <rect
          key={sx}
          x={sx}
          y={y + h * 0.45}
          width={slotW}
          height={h * 0.22}
          rx={3}
          fill="#0d1636"
          fillOpacity={0.35}
        />
      ))}
    </g>
  );
}

function CrateMotif() {
  return (
    <svg
      aria-hidden
      className="absolute inset-0 h-full w-full"
      viewBox="0 0 640 960"
      preserveAspectRatio="xMidYMax slice"
      fill="none"
    >
      {STACKS.map((s) => (
        <g key={`${s.x}-${s.base}`}>
          {Array.from({ length: s.n }, (_, i) => (
            <Crate
              key={i}
              // hand-loaded stacks lean a little
              x={s.x + Math.round(Math.sin(i * 0.8) * (s.w * 0.012))}
              y={s.base - (i + 1) * (s.h + 2)}
              w={s.w}
              h={s.h}
              o={s.o}
            />
          ))}
        </g>
      ))}
    </svg>
  );
}

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
        style={{ background: GRADIENT }}
      >
        <CrateMotif />
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
        style={{ background: GRADIENT }}
      >
        <CrateMotif />
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
