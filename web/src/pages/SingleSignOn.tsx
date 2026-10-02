import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../api/client";
import { PageHeader, dateTime } from "../components/ui";

const NEWCOMER_ROLES = [
  { value: "", label: "Nobody: only people already added here" },
  { value: "bay_operator", label: "Bay operator" },
  { value: "site_manager", label: "Site manager" },
  { value: "auditor", label: "Auditor" },
];

function CopyLine({ label, value }: { label: string; value: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div>
      <div className="label">{label}</div>
      <div className="flex items-center gap-2">
        <code className="num min-w-0 flex-1 break-all rounded-md bg-line/40 px-2 py-1 text-xs text-ink">{value}</code>
        <button
          type="button"
          className="btn-ghost btn-sm"
          onClick={() => navigator.clipboard?.writeText(value).then(() => setCopied(true), () => setCopied(false))}
        >
          <Copy size={13} /> {copied ? "Copied" : "Copy"}
        </button>
      </div>
    </div>
  );
}

/**
 * The tenant's own identity provider (Azure AD, Google, any OpenID Connect provider):
 * its people sign in there. The provider proves who they are; the email domains below
 * decide who counts as one of yours.
 */
export default function SingleSignOn() {
  const qc = useQueryClient();
  const sso = useQuery({ queryKey: ["sso"], queryFn: api.sso });
  const [issuer, setIssuer] = useState("");
  const [clientId, setClientId] = useState("");
  const [secret, setSecret] = useState("");
  const [domains, setDomains] = useState("");
  const [role, setRole] = useState("");
  const [required, setRequired] = useState(false);
  useEffect(() => {
    const d = sso.data;
    if (!d?.configured) return;
    setIssuer(d.issuer ?? "");
    setClientId(d.client_id ?? "");
    setDomains(d.domains.join(", "));
    setRole(d.default_role ?? "");
    setRequired(d.required);
  }, [sso.data]);
  const done = () => {
    setSecret("");
    qc.invalidateQueries({ queryKey: ["sso"] });
  };
  const save = useMutation({
    mutationFn: () =>
      api.saveSso({
        issuer: issuer.trim(),
        client_id: clientId.trim(),
        client_secret: secret,
        domains: domains.split(/[\s,]+/).filter(Boolean),
        default_role: role || null,
        required,
      }),
    onSuccess: done,
  });
  const remove = useMutation({ mutationFn: api.removeSso, onSuccess: done });
  if (sso.isPending) return <div className="card px-4 py-6 text-sm text-muted">Loading…</div>;
  if (sso.error) return <div className="card px-4 py-6 text-sm text-bad">{(sso.error as Error).message}</div>;
  const d = sso.data!;
  return (
    <>
      <PageHeader
        title="Single sign-on"
        subtitle="Your people sign in with your organisation's accounts. Outsiders the provider knows are still kept out."
      />
      <div className="grid gap-4 lg:grid-cols-[1fr_22rem]">
        <form
          className="card space-y-3 p-4"
          onSubmit={(e) => {
            e.preventDefault();
            save.mutate();
          }}
        >
          <p className={`text-sm ${d.configured ? "text-good" : "text-muted"}`}>
            {d.configured
              ? `On${d.updated_by ? `, set by ${d.updated_by}` : ""}${d.updated_at ? ` ${dateTime(d.updated_at)}` : ""}.`
              : "Off: everyone signs in with a password."}
          </p>
          <div>
            <label htmlFor="sso-issuer" className="label">
              Issuer
            </label>
            <input
              id="sso-issuer"
              className="input"
              placeholder="https://login.microsoftonline.com/<directory id>/v2.0"
              value={issuer}
              onChange={(e) => setIssuer(e.target.value)}
              required
            />
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            <div>
              <label htmlFor="sso-client" className="label">
                Application (client) id
              </label>
              <input id="sso-client" className="input" value={clientId} onChange={(e) => setClientId(e.target.value)} required />
            </div>
            <div>
              <label htmlFor="sso-secret" className="label">
                Client secret
              </label>
              <input
                id="sso-secret"
                type="password"
                className="input"
                autoComplete="off"
                placeholder={d.secret_configured ? "Stored: leave empty to keep it" : ""}
                value={secret}
                onChange={(e) => setSecret(e.target.value)}
                required={!d.secret_configured}
              />
            </div>
          </div>
          <div>
            <label htmlFor="sso-domains" className="label">
              Your email domains
            </label>
            <input
              id="sso-domains"
              className="input"
              placeholder="bakersinn.co.zw"
              value={domains}
              onChange={(e) => setDomains(e.target.value)}
              required
            />
            <p className="mt-1 text-xs text-muted">Anyone else the provider signs in, guests included, is turned away.</p>
          </div>
          <div>
            <label htmlFor="sso-role" className="label">
              Someone from your domains with no account here
            </label>
            <select id="sso-role" className="input" value={role} onChange={(e) => setRole(e.target.value)}>
              {NEWCOMER_ROLES.map((r) => (
                <option key={r.value} value={r.value}>
                  {r.value ? `Gets in as ${r.label.toLowerCase()}` : r.label}
                </option>
              ))}
            </select>
          </div>
          <label className="flex items-start gap-2 text-sm text-ink">
            <input type="checkbox" className="mt-1" checked={required} onChange={(e) => setRequired(e.target.checked)} />
            <span>
              Require it: passwords stop working for everyone but the account's owners, who keep one in case the
              provider is ever down.
            </span>
          </label>
          <div className="flex flex-wrap items-center gap-2">
            <button className="btn-accent" disabled={save.isPending}>
              {d.configured ? "Save" : "Turn on"}
            </button>
            {d.configured && (
              <button type="button" className="btn-ghost text-bad" disabled={remove.isPending} onClick={() => remove.mutate()}>
                Turn off
              </button>
            )}
            {(save.error ?? remove.error) && (
              <span role="alert" className="text-xs text-bad">
                {((save.error ?? remove.error) as Error).message}
              </span>
            )}
          </div>
        </form>
        <aside className="card space-y-3 p-4">
          <h2 className="text-sm font-bold text-ink">At your provider</h2>
          <p className="text-xs text-muted">
            Register IVaaS as a web application with this redirect URI, then give your people the sign-in link.
          </p>
          <CopyLine label="Redirect URI" value={d.redirect_uri} />
          <CopyLine label="Sign-in link" value={d.sign_in_url} />
        </aside>
      </div>
    </>
  );
}
