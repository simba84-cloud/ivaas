import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Info, Server, ShieldCheck, SlidersHorizontal } from "lucide-react";
import { useState } from "react";
import { api } from "../api/client";
import type { ConfigFact, EditableSetting } from "../api/types";
import { EmptyState } from "../components/ui";

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
      const value = setting.kind === "percent" ? n / 100 : setting.kind === "minutes" ? n : draft;
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
        {setting.kind === "choice" ? (
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
              {setting.kind === "percent" ? "%" : "minutes"}
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
          <EmptyState title="Loading settings" body="Reading the platform configuration." />
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
