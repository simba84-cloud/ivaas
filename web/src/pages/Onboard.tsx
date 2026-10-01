import { useMutation, useQuery } from "@tanstack/react-query";
import { Copy, KeyRound } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { Provisioned } from "../api/types";
import { type Me } from "../auth/session";
import { OnboardingProgress } from "../components/OnboardingProgress";
import { PageHeader } from "../components/ui";
import { isPlatform } from "./ConsoleTenant";

export const slugOf = (name: string) =>
  name
    .toLowerCase()
    .normalize("NFKD")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 64);

/** The owner's temporary password, shown once: only a hash is kept. */
function OwnerPassword({ made }: { made: Provisioned }) {
  const [copied, setCopied] = useState(false);
  if (!made.temporary_password) {
    return (
      <p role="status" className="card mb-4 px-4 py-3 text-sm text-muted">
        This request had already been made, so {made.tenant.name} already existed. Its owner's password was shown the
        first time and cannot be shown again; reset it from Users if it was lost.
      </p>
    );
  }
  return (
    <div className="card-lift mb-4 border-l-4 border-l-accent p-4">
      <div className="flex items-center gap-2">
        <KeyRound size={15} className="text-accent" />
        <h2 className="text-sm font-bold text-ink">Temporary password for {made.owner_username}</h2>
      </div>
      <p className="mt-1 text-xs text-muted">
        Give this to the customer's owner now. It is not stored and cannot be shown again. They set their own password
        the first time they sign in.
      </p>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <code className="num rounded-lg border border-line bg-ground px-3 py-2 text-base font-bold tracking-wider text-ink">
          {made.temporary_password}
        </code>
        <button
          className="btn-ghost btn-sm"
          onClick={() =>
            navigator.clipboard?.writeText(made.temporary_password!).then(
              () => setCopied(true),
              () => setCopied(false),
            )
          }
        >
          <Copy size={13} /> {copied ? "Copied" : "Copy"}
        </button>
      </div>
    </div>
  );
}

/**
 * T8.1's wizard: the customer and its owner, then the plan, the first site and bay,
 * and an enrollment token, ending when the node reports in. The steps after the first
 * are the tenant's onboarding panel, so leaving and coming back loses nothing.
 */
export default function Onboard({ me }: { me: Me | undefined }) {
  const platform = isPlatform(me);
  const partners = useQuery({ queryKey: ["console", "partners"], queryFn: api.partners });
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [owner, setOwner] = useState("");
  const [ownerName, setOwnerName] = useState("");
  const [partner, setPartner] = useState("");
  // one key per attempt: a retried click is the same request, never a second tenant
  const [key] = useState(() => `console-${crypto.randomUUID()}`);
  const effectiveSlug = slug || slugOf(name);
  const create = useMutation({
    mutationFn: () =>
      api.provisionTenant(
        {
          slug: effectiveSlug,
          name,
          owner_username: owner || `${effectiveSlug}.owner`,
          owner_display_name: ownerName,
          partner_id: platform ? partner || null : undefined,
        },
        key,
      ),
  });

  if (create.data) {
    const made = create.data;
    return (
      <>
        <PageHeader
          title={`Onboarding ${made.tenant.name}`}
          subtitle="Set the plan, create the first site and bay, and enrol the edge node. This page follows the node in."
          actions={
            <Link to={`/console/tenants/${made.tenant.id}`} className="btn-ghost">
              Open tenant
            </Link>
          }
        />
        <OwnerPassword made={made} />
        <OnboardingProgress tenantId={made.tenant.id} />
      </>
    );
  }

  return (
    <>
      <PageHeader title="Onboard a tenant" subtitle="The customer and the person who will own its account." />
      <form
        className="card max-w-xl space-y-3 p-4"
        onSubmit={(e) => {
          e.preventDefault();
          create.mutate();
        }}
      >
        <div>
          <label htmlFor="tenant-name" className="label">
            Customer name
          </label>
          <input id="tenant-name" className="input" value={name} onChange={(e) => setName(e.target.value)} required />
        </div>
        <div>
          <label htmlFor="tenant-slug" className="label">
            Short name
          </label>
          <input
            id="tenant-slug"
            className="input"
            value={effectiveSlug}
            onChange={(e) => setSlug(e.target.value)}
            pattern="[a-z0-9][a-z0-9-]*"
            required
          />
          <p className="mt-1 text-xs text-muted">Lower case, digits and hyphens. It cannot be changed later.</p>
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          <div>
            <label htmlFor="owner-username" className="label">
              Owner's username
            </label>
            <input
              id="owner-username"
              className="input"
              value={owner}
              placeholder={effectiveSlug ? `${effectiveSlug}.owner` : ""}
              onChange={(e) => setOwner(e.target.value)}
            />
          </div>
          <div>
            <label htmlFor="owner-name" className="label">
              Owner's name
            </label>
            <input id="owner-name" className="input" value={ownerName} onChange={(e) => setOwnerName(e.target.value)} />
          </div>
        </div>
        {platform && (
          <div>
            <label htmlFor="tenant-partner" className="label">
              Billed through
            </label>
            <select id="tenant-partner" className="input" value={partner} onChange={(e) => setPartner(e.target.value)}>
              <option value="">Cassava directly</option>
              {partners.data?.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
          </div>
        )}
        <div className="flex items-center gap-3">
          <button className="btn-accent" disabled={!name || !effectiveSlug || create.isPending}>
            Create tenant
          </button>
          {create.error && (
            <span role="alert" className="text-xs text-bad">
              {(create.error as Error).message}
            </span>
          )}
        </div>
      </form>
    </>
  );
}
