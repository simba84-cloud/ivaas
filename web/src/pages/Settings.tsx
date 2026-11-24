import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  Check,
  Info,
  Mail,
  Send,
  Server,
  ShieldCheck,
  SlidersHorizontal,
} from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { AlertEmail, ConfigFact, EditableSetting } from "../api/types";
import { dateTime } from "../components/ui";
import { SkeletonList } from "../motion";

/** One rule an admin can change, shown with the units it is actually measured in. */
function Rule({ setting }: { setting: EditableSetting }) {
  const qc = useQueryClient();
  const asShown =
    setting.kind === "percent" ? String(Number(setting.value) * 100) : String(setting.value);
  const [draft, setDraft] = useState(asShown);
  const dirty = draft !== asShown;

  const save = useMutation({
    mutationFn: () => {
      const n = Number(draft);
      const numeric = ["minutes", "days", "crates", "hour"].includes(setting.kind);
      const value = setting.kind === "percent" ? n / 100 : numeric ? n : draft;
      return api.setSetting(setting.key, value);
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["settings"] }),
  });

  return (
    <div className="flex flex-wrap items-start justify-between gap-4 px-4 py-3.5">
      <div className="min-w-0 max-w-xl">
        <div className="flex items-center gap-2">
          <span className="text-sm font-semibold text-ink">{setting.label}</span>
          {setting.overridden && (
            <span className="chip bg-brand-tint text-brand">Changed here</span>
          )}
        </div>
        <p className="mt-0.5 text-xs leading-relaxed text-muted">{setting.help}</p>
        {save.isError && (
          <p role="alert" className="mt-1 text-xs text-bad">
            {(save.error as Error).message}
          </p>
        )}
      </div>

      <form
        className="flex flex-none items-center gap-1.5"
        onSubmit={(e) => {
          e.preventDefault();
          save.mutate();
        }}
      >
        {setting.kind === "text" ? (
          <input
            id={setting.key}
            aria-label={setting.label}
            className="input h-8 w-72 text-sm"
            maxLength={setting.maximum ?? 500}
            value={draft}
            placeholder="Not recorded"
            onChange={(e) => setDraft(e.target.value)}
          />
        ) : setting.kind === "choice" ? (
          <select
            id={setting.key}
            aria-label={setting.label}
            className="input h-8 w-40 py-0 text-sm"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
          >
            {setting.choices.map((c) => (
              <option key={c || "none"} value={c}>
                {c === "" ? "Nothing (disabled)" : c}
              </option>
            ))}
          </select>
        ) : (
          <>
            <input
              id={setting.key}
              aria-label={setting.label}
              inputMode="decimal"
              className="input num h-8 w-24 text-right text-sm"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
            />
            <span className="w-14 text-xs text-muted">
              {setting.kind === "percent" ? "%" : setting.kind === "hour" ? ":00" : setting.kind}
            </span>
          </>
        )}
        <button className="btn-primary btn-sm" disabled={!dirty || save.isPending}>
          <Check size={13} /> {save.isPending ? "Saving…" : "Save"}
        </button>
      </form>
    </div>
  );
}

const KIND_LABEL: Record<AlertEmail["kind"], string> = {
  discrepancy: "Disputed load",
  sign_off: "Signed off",
  camera_offline: "Camera offline",
  analysis_failed: "Analysis failed",
  daily_summary: "Daily summary",
  test: "Test",
};

function DeliveryChip({ email }: { email: AlertEmail }) {
  if (email.status === "sent") {
    return <span className="chip bg-good/10 text-good">Sent</span>;
  }
  if (email.status === "failed") {
    return <span className="chip bg-bad/10 text-bad">Failed</span>;
  }
  return (
    <span className="chip bg-warn/10 text-warn">
      {email.attempts ? `Retrying (${email.attempts} tried)` : "Waiting to send"}
    </span>
  );
}

/** Where alert emails go, who gets them, what went out, and a test to yourself. */
function EmailAlerts() {
  const qc = useQueryClient();
  const status = useQuery({ queryKey: ["alert-email"], queryFn: api.alertEmail });
  const test = useMutation({
    mutationFn: api.sendTestEmail,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["alert-email"] }),
  });
  const s = status.data;

  return (
    <section className="card overflow-hidden">
      <div className="panel-head">
        <div className="flex items-center gap-2">
          <Mail size={15} className="text-accent" />
          <h2 className="panel-title">Email alerts</h2>
        </div>
        {s && (
          <span className="text-xs text-muted">
            {s.enabled ? "On" : "Off"} · switched in Operating rules above
          </span>
        )}
      </div>
      {!s ? (
        <SkeletonList rows={3} />
      ) : (
        <>
          <ul className="divide-y divide-line">
            <li className="flex flex-wrap items-baseline gap-x-3 px-4 py-2.5">
              <span className="w-52 flex-none text-sm font-semibold text-ink">Relay</span>
              {s.configured ? (
                <>
                  <span className="num text-sm text-ink">{s.relay}</span>
                  <span className="text-xs text-muted">
                    From {s.sender} · STARTTLS {s.starttls}
                  </span>
                </>
              ) : (
                <span className="text-sm text-bad">
                  Not configured: IVAAS_SMTP_HOST is empty, so no alert emails are sent
                </span>
              )}
            </li>
            <li className="flex flex-wrap items-baseline gap-x-3 px-4 py-2.5">
              <span className="w-52 flex-none text-sm font-semibold text-ink">Goes to</span>
              <span className="text-sm text-ink">
                {s.recipients.length
                  ? s.recipients.join(", ")
                  : "Nobody yet: no one in the group has an email address"}
              </span>
              <span className="w-full text-xs text-muted sm:w-auto">{s.group}</span>
            </li>
          </ul>

          {s.missing_email.length > 0 && (
            <p className="flex items-start gap-2 border-t border-line px-4 py-2.5 text-xs text-warn">
              <AlertTriangle size={13} className="mt-0.5 flex-none" />
              <span>
                {s.missing_email.length === 1 ? "1 person" : `${s.missing_email.length} people`}{" "}
                who should get alerts {s.missing_email.length === 1 ? "has" : "have"} no email
                address: {s.missing_email.join(", ")}. Add one under{" "}
                <Link to="/users" className="underline">
                  Users
                </Link>
                .
              </span>
            </p>
          )}

          <div className="flex flex-wrap items-center gap-3 border-t border-line px-4 py-3">
            <button
              className="btn-primary btn-sm"
              onClick={() => test.mutate()}
              disabled={test.isPending}
            >
              <Send size={13} /> {test.isPending ? "Sending…" : "Send a test to me"}
            </button>
            {test.data &&
              (test.data.status === "sent" ? (
                <span role="status" className="text-xs text-good">
                  Sent to {test.data.recipients.join(", ")}. Check that it arrived.
                </span>
              ) : (
                <span role="alert" className="text-xs text-bad">
                  Not sent: {test.data.last_error}
                </span>
              ))}
            {test.isError && (
              <span role="alert" className="text-xs text-bad">
                {(test.error as Error).message}
              </span>
            )}
          </div>

          <div className="border-t border-line">
            <div className="px-4 pb-1 pt-3 text-xs font-semibold uppercase tracking-wide text-muted">
              Recent emails
            </div>
            {s.recent.length ? (
              <ul className="divide-y divide-line">
                {s.recent.map((e) => (
                  <li key={e.id} className="flex flex-wrap items-baseline gap-x-3 px-4 py-2">
                    <span className="w-32 flex-none text-xs text-muted">{KIND_LABEL[e.kind]}</span>
                    <span className="min-w-0 flex-1 truncate text-sm text-ink">{e.subject}</span>
                    <DeliveryChip email={e} />
                    <span className="num text-xs text-muted">{dateTime(e.created_at)}</span>
                    {e.last_error && (
                      <span className="w-full text-xs text-bad">{e.last_error}</span>
                    )}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="px-4 pb-3 text-sm text-muted">No alert emails yet.</p>
            )}
          </div>
        </>
      )}
    </section>
  );
}

function Facts({ facts }: { facts: ConfigFact[] }) {
  return (
    <ul className="divide-y divide-line">
      {facts.map((f) => (
        <li key={f.label} className="flex flex-wrap items-baseline gap-x-3 px-4 py-2.5">
          <span className="w-52 flex-none text-sm font-semibold text-ink">{f.label}</span>
          <span className="num text-sm text-ink">{f.value}</span>
          {f.detail && <span className="w-full text-xs text-muted sm:w-auto">{f.detail}</span>}
        </li>
      ))}
    </ul>
  );
}

export default function Settings() {
  const settings = useQuery({ queryKey: ["settings"], queryFn: api.settings });
  const s = settings.data;

  return (
    <>
      <div className="mb-4">
        <h1 className="text-2xl font-extrabold tracking-tight text-ink">System settings</h1>
        <p className="mt-0.5 text-sm text-muted">
          Operating rules you can change here; deployment configuration you cannot.
        </p>
      </div>

      {!s ? (
        <div className="card">
          <SkeletonList rows={4} />
        </div>
      ) : (
        <div className="space-y-4">
          <section className="card overflow-hidden">
            <div className="panel-head">
              <div className="flex items-center gap-2">
                <SlidersHorizontal size={15} className="text-accent" />
                <h2 className="panel-title">Operating rules</h2>
              </div>
              <span className="text-xs text-muted">Applies immediately · recorded in the audit log</span>
            </div>
            <div className="divide-y divide-line">
              {s.editable.map((setting) => (
                <Rule key={setting.key} setting={setting} />
              ))}
            </div>
          </section>

          <EmailAlerts />

          <section className="card overflow-hidden">
            <div className="panel-head">
              <div className="flex items-center gap-2">
                <ShieldCheck size={15} className="text-good" />
                <h2 className="panel-title">Account security</h2>
              </div>
            </div>
            <Facts facts={s.security} />
            <p className="flex items-start gap-2 border-t border-line px-4 py-2.5 text-xs text-muted">
              <Info size={13} className="mt-0.5 flex-none" />
              Secrets are never shown here, only whether they are configured. Passwords and
              accounts are managed where they live: in configuration for local sign-in, or in
              your identity provider for OIDC.
            </p>
          </section>

          <section className="card overflow-hidden">
            <div className="panel-head">
              <div className="flex items-center gap-2">
                <Server size={15} className="text-brand" />
                <h2 className="panel-title">Deployment</h2>
              </div>
              <span className="text-xs text-muted">Read-only · set by environment, needs a restart</span>
            </div>
            <Facts facts={s.platform} />
          </section>
        </div>
      )}
    </>
  );
}
