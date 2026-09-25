import { useMutation } from "@tanstack/react-query";
import { KeyRound, ShieldCheck } from "lucide-react";
import { useState } from "react";
import { api } from "../api/client";
import { BRAND_GRADIENT, CrateMotif, LOGIN_STACKS } from "../components/brand";

const MIN_LENGTH = 12;

/**
 * Shown instead of the portal when the account holds a temporary password.
 *
 * The API refuses everything else until this is done, so this is not merely a
 * prompt that could be skipped by navigating elsewhere.
 */
export default function ChangePassword({
  forced,
  onDone,
  onCancel,
}: {
  forced: boolean;
  onDone: (token: string) => void;
  onCancel?: () => void;
}) {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [again, setAgain] = useState("");

  const mismatch = again.length > 0 && next !== again;
  const tooShort = next.length > 0 && next.length < MIN_LENGTH;
  const ready = current && next.length >= MIN_LENGTH && next === again;

  const change = useMutation({
    mutationFn: () => api.changePassword(current, next),
    onSuccess: (r) => onDone(r.access_token),
  });

  return (
    <div className="grid min-h-screen bg-ground lg:grid-cols-[1fr_1fr]">
      <aside
        className="relative hidden overflow-hidden text-white lg:flex lg:flex-col lg:justify-between lg:p-12"
        style={{ background: BRAND_GRADIENT }}
      >
        <CrateMotif stacks={LOGIN_STACKS} />
        <div
          aria-hidden
          className="pointer-events-none absolute inset-0 bg-gradient-to-r from-[#141f47]/90 via-[#141f47]/45 to-transparent"
        />
        <img
          src="/logo-liquid.png"
          alt="Liquid Intelligent Technologies"
          className="relative h-10 w-auto self-start brightness-0 invert"
        />
        <div className="relative">
          <div className="eyebrow text-white/70">Account security</div>
          <h1 className="mt-3 max-w-md text-3xl font-extrabold leading-tight tracking-tight">
            {forced ? "Set a password only you know" : "Change your password"}
          </h1>
          <p className="mt-4 max-w-md text-sm leading-relaxed text-white/85">
            Length is what makes a password hard to guess, so a passphrase of a few
            ordinary words beats a short one full of symbols.
          </p>
        </div>
        <div className="relative num text-[11px] text-white/60">
          Stored as an Argon2id hash · never readable by anyone
        </div>
      </aside>

      <main className="flex items-center justify-center px-4 py-10">
        <div className="w-full max-w-sm">
          <div className="card-lift p-7">
            <div className="flex items-center gap-1.5 text-accent">
              <KeyRound size={14} strokeWidth={2.6} />
              <span className="eyebrow text-accent">
                {forced ? "Password change required" : "Your password"}
              </span>
            </div>
            <h2 className="mt-3 text-2xl font-extrabold tracking-tight text-ink">
              Choose a new password
            </h2>
            {forced && (
              <p className="mt-1 text-sm text-muted">
                Your password was reset by an administrator. Set your own before continuing.
              </p>
            )}

            <form
              className="mt-6 space-y-4"
              onSubmit={(e) => {
                e.preventDefault();
                if (ready) change.mutate();
              }}
            >
              <div>
                <label htmlFor="current-password" className="label">
                  {forced ? "Temporary password" : "Current password"}
                </label>
                <input
                  id="current-password"
                  className="input"
                  type="password"
                  autoComplete="current-password"
                  autoFocus
                  value={current}
                  onChange={(e) => setCurrent(e.target.value)}
                  required
                />
              </div>
              <div>
                <label htmlFor="new-password" className="label">
                  New password
                </label>
                <input
                  id="new-password"
                  className="input"
                  type="password"
                  autoComplete="new-password"
                  value={next}
                  onChange={(e) => setNext(e.target.value)}
                  required
                />
                <p className={`mt-1 text-xs ${tooShort ? "text-warn" : "text-faint"}`}>
                  At least {MIN_LENGTH} characters. No capitals or symbols required.
                </p>
              </div>
              <div>
                <label htmlFor="confirm-password" className="label">
                  New password again
                </label>
                <input
                  id="confirm-password"
                  className="input"
                  type="password"
                  autoComplete="new-password"
                  value={again}
                  onChange={(e) => setAgain(e.target.value)}
                  required
                />
                {mismatch && <p className="mt-1 text-xs text-warn">The two do not match.</p>}
              </div>

              {change.isError && (
                <p role="alert" className="rounded-lg bg-bad/10 px-3 py-2 text-sm text-bad">
                  {(change.error as Error).message}
                </p>
              )}

              <button className="btn-primary w-full" disabled={!ready || change.isPending}>
                <ShieldCheck size={16} /> {change.isPending ? "Saving…" : "Set password"}
              </button>
              {!forced && onCancel && (
                <button type="button" className="btn-ghost w-full" onClick={onCancel}>
                  Cancel
                </button>
              )}
            </form>
          </div>
          <p className="mt-4 text-center text-xs text-faint">
            Changing your password signs out your other sessions.
          </p>
        </div>
      </main>
    </div>
  );
}
