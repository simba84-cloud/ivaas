import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Copy, KeyRound, Plus, ShieldCheck, UserPlus, X } from "lucide-react";
import { useState } from "react";
import { api } from "../api/client";
import type { TemporaryPassword, User, UserRole } from "../api/types";
import { EmptyState, dateTime } from "../components/ui";

const ROLES: { value: UserRole; label: string; what: string }[] = [
  { value: "viewer", label: "Viewer", what: "Reads dashboards and reports" },
  { value: "operator", label: "Operator", what: "Runs the bay and verifies counts" },
  { value: "admin", label: "Administrator", what: "Configures the platform and signs off disputes" },
];

/**
 * A temporary password, shown once.
 *
 * The platform stores only a hash, so this is the single moment it can be read.
 * The dialog says so plainly rather than letting an administrator assume they can
 * come back for it.
 */
function OneTimePassword({ result, onClose }: { result: TemporaryPassword; onClose: () => void }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="card-lift mb-4 border-l-4 border-l-accent p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <KeyRound size={15} className="text-accent" />
            <h2 className="text-sm font-bold text-ink">
              Temporary password for {result.user.username}
            </h2>
          </div>
          <p className="mt-1 text-xs text-muted">
            Give this to them now. It is not stored and cannot be shown again — if it is
            lost, reset the password to get a new one. They must set their own password
            before they can do anything.
          </p>
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <code className="num rounded-lg border border-line bg-ground px-3 py-2 text-base font-bold tracking-wider text-ink">
              {result.temporary_password}
            </code>
            <button
              className="btn-ghost btn-sm"
              onClick={() => {
                navigator.clipboard?.writeText(result.temporary_password).then(
                  () => setCopied(true),
                  () => setCopied(false),
                );
              }}
            >
              <Copy size={13} /> {copied ? "Copied" : "Copy"}
            </button>
          </div>
        </div>
        <button aria-label="Dismiss" className="btn-ghost btn-sm" onClick={onClose}>
          <X size={14} />
        </button>
      </div>
    </div>
  );
}

function NewUser({ onDone }: { onDone: (r: TemporaryPassword) => void }) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [username, setUsername] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [role, setRole] = useState<UserRole>("operator");

  const create = useMutation({
    mutationFn: () => api.createUser(username.trim(), displayName.trim(), [role]),
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ["users"] });
      setOpen(false);
      setUsername("");
      setDisplayName("");
      onDone(r);
    },
  });

  if (!open) {
    return (
      <button className="btn-accent" onClick={() => setOpen(true)}>
        <Plus size={16} /> Add user
      </button>
    );
  }

  return (
    <form
      className="card flex flex-wrap items-end gap-2 p-3"
      onSubmit={(e) => {
        e.preventDefault();
        create.mutate();
      }}
    >
      <div>
        <label htmlFor="new-username" className="label">
          Username
        </label>
        <input
          id="new-username"
          className="input w-40"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          required
        />
      </div>
      <div>
        <label htmlFor="new-display" className="label">
          Full name
        </label>
        <input
          id="new-display"
          className="input w-48"
          value={displayName}
          onChange={(e) => setDisplayName(e.target.value)}
        />
      </div>
      <div>
        <label htmlFor="new-role" className="label">
          Role
        </label>
        <select
          id="new-role"
          className="input w-40"
          value={role}
          onChange={(e) => setRole(e.target.value as UserRole)}
        >
          {ROLES.map((r) => (
            <option key={r.value} value={r.value}>
              {r.label}
            </option>
          ))}
        </select>
      </div>
      <button className="btn-primary" disabled={!username.trim() || create.isPending}>
        <UserPlus size={15} /> {create.isPending ? "Creating…" : "Create"}
      </button>
      <button type="button" className="btn-ghost" onClick={() => setOpen(false)}>
        Cancel
      </button>
      {create.isError && (
        <p role="alert" className="w-full text-sm text-bad">
          {(create.error as Error).message}
        </p>
      )}
    </form>
  );
}

function Row({ user, me, onReset }: { user: User; me: string; onReset: (r: TemporaryPassword) => void }) {
  const qc = useQueryClient();
  const refresh = () => qc.invalidateQueries({ queryKey: ["users"] });
  const isSelf = user.username === me;

  const roles = useMutation({
    mutationFn: (role: UserRole) => api.assignRoles(user.username, [role]),
    onSuccess: refresh,
  });
  const enabled = useMutation({
    mutationFn: (on: boolean) => api.setUserEnabled(user.username, on),
    onSuccess: refresh,
  });
  const reset = useMutation({
    mutationFn: () => api.resetPassword(user.username),
    onSuccess: (r) => {
      refresh();
      onReset(r);
    },
  });

  const error = roles.error ?? enabled.error ?? reset.error;

  return (
    <tr className={`border-t border-line ${user.disabled ? "opacity-60" : ""}`}>
      <td className="td">
        <div className="font-semibold text-ink">{user.display_name}</div>
        <div className="num text-xs text-muted">
          {user.username}
          {isSelf && " · you"}
        </div>
        {error && (
          <div role="alert" className="mt-1 max-w-xs text-xs text-bad">
            {(error as Error).message}
          </div>
        )}
      </td>
      <td className="td">
        <select
          aria-label={`Role for ${user.username}`}
          className="input h-8 w-40 py-0 text-xs"
          value={user.roles[user.roles.length - 1] ?? "viewer"}
          onChange={(e) => roles.mutate(e.target.value as UserRole)}
          disabled={roles.isPending}
        >
          {ROLES.map((r) => (
            <option key={r.value} value={r.value}>
              {r.label}
            </option>
          ))}
        </select>
      </td>
      <td className="td">
        {user.disabled ? (
          <span className="chip bg-ground text-muted">
            <span className="dot bg-faint" /> Disabled
          </span>
        ) : user.must_change_password ? (
          <span className="chip bg-warn/10 text-warn">
            <span className="dot bg-warn" /> Must set password
          </span>
        ) : user.password_is_default ? (
          <span className="chip bg-bad/10 text-bad" title="Still signing in with the seeded password">
            <AlertTriangle size={11} /> Default password
          </span>
        ) : (
          <span className="chip bg-good/10 text-good">
            <span className="dot bg-good" /> Active
          </span>
        )}
      </td>
      <td className="td text-muted">
        {user.last_login_at ? dateTime(user.last_login_at) : "Never"}
      </td>
      <td className="td">
        <div className="flex justify-end gap-1.5">
          <button
            className="btn-ghost btn-sm"
            onClick={() => reset.mutate()}
            disabled={reset.isPending}
          >
            <KeyRound size={13} /> Reset password
          </button>
          <button
            className="btn-ghost btn-sm"
            onClick={() => enabled.mutate(user.disabled)}
            disabled={enabled.isPending || isSelf}
            title={isSelf ? "You cannot disable your own account" : undefined}
          >
            {user.disabled ? "Enable" : "Disable"}
          </button>
        </div>
      </td>
    </tr>
  );
}

export default function Users({ me }: { me: string }) {
  const [shown, setShown] = useState<TemporaryPassword | null>(null);
  const users = useQuery({ queryKey: ["users"], queryFn: api.users });
  const rows = users.data ?? [];
  const defaults = rows.filter((u) => u.password_is_default && !u.disabled);

  return (
    <>
      <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-extrabold tracking-tight text-ink">Users</h1>
          <p className="mt-0.5 text-sm text-muted">
            Who can sign in, what they may do, and resetting a forgotten password.
          </p>
        </div>
        <NewUser onDone={setShown} />
      </div>

      {shown && <OneTimePassword result={shown} onClose={() => setShown(null)} />}

      {defaults.length > 0 && (
        <div className="card mb-4 flex items-start gap-2.5 border-l-4 border-l-bad p-3.5">
          <AlertTriangle size={16} className="mt-0.5 flex-none text-bad" />
          <div className="text-sm">
            <span className="font-semibold text-ink">
              {defaults.length} account{defaults.length > 1 ? "s are" : " is"} still using the
              password it was created with.
            </span>
            <span className="ml-1 text-muted">
              Reset {defaults.length > 1 ? "them" : "it"}, or have the owner set a new password.
            </span>
          </div>
        </div>
      )}

      <div className="card overflow-hidden">
        {rows.length ? (
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr className="border-b border-line">
                  <th className="th">User</th>
                  <th className="th">Role</th>
                  <th className="th">Status</th>
                  <th className="th">Last signed in</th>
                  <th className="th" />
                </tr>
              </thead>
              <tbody>
                {rows.map((u) => (
                  <Row key={u.username} user={u} me={me} onReset={setShown} />
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState title="No accounts" body="Add a user to let someone sign in." />
        )}
      </div>

      <p className="mt-3 flex items-start gap-2 text-xs text-muted">
        <ShieldCheck size={13} className="mt-0.5 flex-none" />
        Passwords are stored as Argon2id hashes and can never be read back, by anyone. A
        reset produces a new temporary password shown once. Resetting a password or
        disabling an account ends any session it already has.
      </p>
    </>
  );
}
