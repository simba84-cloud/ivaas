/**
 * Enrolled faces and the badge log.
 *
 * Enrolment is locked until face recognition is switched on, which itself needs its
 * legal basis recorded. Each person records where their consent is kept; the photo is
 * turned into an encrypted embedding on the server and discarded.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Lock, ScanFace, Trash2, UserPlus } from "lucide-react";
import { useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../../api/client";
import { MotionRow, SkeletonRows } from "../../motion";
import { useToast } from "../toast";
import { EmptyState, dateTime } from "../ui";

export function PeopleTab() {
  const qc = useQueryClient();
  const toast = useToast();
  const status = useQuery({ queryKey: ["security-status"], queryFn: api.securityStatus });
  const people = useQuery({ queryKey: ["people"], queryFn: api.people });
  const [name, setName] = useState("");
  const [ref, setRef] = useState("");
  const [consent, setConsent] = useState("");
  const [photo, setPhoto] = useState<File | null>(null);
  const [confirming, setConfirming] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  const on = !!status.data?.face_recognition;
  const ready = on && !!status.data?.face_models_installed;

  const enrol = useMutation({
    mutationFn: () => {
      const form = new FormData();
      form.set("name", name);
      form.set("employee_ref", ref);
      form.set("consent_reference", consent);
      form.set("photo", photo!);
      return api.enrol(form);
    },
    onSuccess: (p) => {
      toast({ severity: "success", title: `Enrolled · ${p.name}`, detail: "The photo was not kept." });
      setName("");
      setRef("");
      setConsent("");
      setPhoto(null);
      if (fileInput.current) fileInput.current.value = "";
      qc.invalidateQueries({ queryKey: ["people"] });
      qc.invalidateQueries({ queryKey: ["security-status"] });
    },
    onError: (e) => toast({ severity: "critical", title: "Not enrolled", detail: (e as Error).message }),
  });
  const remove = useMutation({
    mutationFn: api.removePerson,
    onSuccess: () => {
      setConfirming(null);
      toast({ severity: "success", title: "Removed", detail: "Their face embedding is deleted." });
      qc.invalidateQueries({ queryKey: ["people"] });
      qc.invalidateQueries({ queryKey: ["security-status"] });
    },
  });

  return (
    <div className="grid gap-4 xl:grid-cols-[360px_minmax(0,1fr)]">
      <section className="card self-start">
        <div className="panel-head">
          <h2 className="panel-title flex items-center gap-2">
            <ScanFace size={15} className="text-muted" /> Enrol a person
          </h2>
        </div>
        {!ready ? (
          <div className="flex gap-3 p-4 text-sm">
            <Lock size={16} className="mt-0.5 flex-none text-warn" />
            <div>
              <p className="font-semibold text-ink">
                {!on ? "Face recognition is switched off" : "Face models are not installed on the server"}
              </p>
              <p className="mt-1 text-muted">
                {!on ? (
                  <>
                    It processes biometric data. An admin records its legal basis under{" "}
                    <Link to="/settings" className="font-semibold text-brand hover:underline">
                      Settings
                    </Link>{" "}
                    and then switches it on.
                  </>
                ) : (
                  "Run ./deploy/fetch-models.sh --faces on the server, then restart the API."
                )}
              </p>
            </div>
          </div>
        ) : (
          <form
            className="space-y-3 p-4"
            onSubmit={(e) => {
              e.preventDefault();
              if (name && ref && consent && photo) enrol.mutate();
            }}
          >
            <div>
              <label className="label" htmlFor="person-name">
                Name
              </label>
              <input id="person-name" className="input" value={name} onChange={(e) => setName(e.target.value)} />
            </div>
            <div>
              <label className="label" htmlFor="person-ref">
                Employee reference
              </label>
              <input id="person-ref" className="input" value={ref} onChange={(e) => setRef(e.target.value)} />
            </div>
            <div>
              <label className="label" htmlFor="person-consent">
                Where their consent is recorded
              </label>
              <input
                id="person-consent"
                className="input"
                value={consent}
                onChange={(e) => setConsent(e.target.value)}
                placeholder="e.g. HR file, form reference"
              />
            </div>
            <div>
              <label className="label" htmlFor="person-photo">
                Photo (front-on, one face)
              </label>
              <input
                ref={fileInput}
                id="person-photo"
                type="file"
                accept="image/jpeg,image/png"
                className="block w-full text-xs text-muted file:mr-3 file:rounded-md file:border-0 file:bg-brand-tint file:px-3 file:py-1.5 file:text-xs file:font-semibold file:text-brand"
                onChange={(e) => setPhoto(e.target.files?.[0] ?? null)}
              />
              <p className="mt-1 text-xs text-muted">The photo is discarded once its face embedding is made.</p>
            </div>
            <button className="btn-primary btn-sm" disabled={!name || !ref || !consent || !photo || enrol.isPending}>
              <UserPlus size={13} /> {enrol.isPending ? "Enrolling…" : "Enrol"}
            </button>
          </form>
        )}
      </section>

      <section className="card min-w-0 overflow-hidden">
        <div className="panel-head">
          <h2 className="panel-title">Enrolled people</h2>
          <span className="text-xs text-faint">{people.data?.length ?? 0} people</span>
        </div>
        {people.isPending ? (
          <table className="w-full">
            <SkeletonRows rows={3} cols={4} />
          </table>
        ) : people.data?.length ? (
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr className="border-b border-line">
                  <th className="th">Person</th>
                  <th className="th">Consent recorded at</th>
                  <th className="th">Enrolled</th>
                  <th className="th" />
                </tr>
              </thead>
              <tbody>
                {people.data.map((p, i) => (
                  <MotionRow key={p.id} index={i} className="border-t border-line">
                    <td className="td">
                      <div className="font-semibold text-ink">{p.name}</div>
                      <div className="num text-xs text-muted">{p.employee_ref}</div>
                    </td>
                    <td className="td text-xs text-muted">{p.consent_reference}</td>
                    <td className="td text-xs text-muted">
                      {dateTime(p.enrolled_at)} · {p.enrolled_by}
                    </td>
                    <td className="td text-right">
                      {confirming === p.id ? (
                        <span className="inline-flex items-center gap-2 text-xs">
                          <button className="font-semibold text-bad hover:underline" onClick={() => remove.mutate(p.id)}>
                            Remove
                          </button>
                          <button className="text-muted" onClick={() => setConfirming(null)}>
                            Keep
                          </button>
                        </span>
                      ) : (
                        <button
                          aria-label={`Remove ${p.name}`}
                          className="rounded p-1 text-faint transition hover:bg-bad/10 hover:text-bad"
                          onClick={() => setConfirming(p.id)}
                        >
                          <Trash2 size={15} />
                        </button>
                      )}
                    </td>
                  </MotionRow>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState title="Nobody enrolled" body="'Known faces only' zones treat everyone as unrecognised until people are enrolled." />
        )}
      </section>
    </div>
  );
}

export function BadgesTab() {
  const badges = useQuery({ queryKey: ["badges"], queryFn: () => api.badges(24) });
  return (
    <section className="card overflow-hidden">
      <div className="panel-head">
        <h2 className="panel-title">Badge swipes · last 24 hours</h2>
        <span className="text-xs text-faint">from the access-control system</span>
      </div>
      {badges.isPending ? (
        <table className="w-full">
          <SkeletonRows rows={4} cols={4} />
        </table>
      ) : badges.data?.length ? (
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead>
              <tr className="border-b border-line">
                <th className="th">When</th>
                <th className="th">Door</th>
                <th className="th">Badge</th>
                <th className="th">Result</th>
              </tr>
            </thead>
            <tbody>
              {badges.data.map((b, i) => (
                <MotionRow key={b.id} index={i} className="border-t border-line">
                  <td className="td num text-muted">{dateTime(b.at)}</td>
                  <td className="td font-semibold text-ink">{b.door}</td>
                  <td className="td">
                    <span className="num">{b.badge_id}</span>
                    {b.holder && <span className="ml-2 text-muted">{b.holder}</span>}
                  </td>
                  <td className="td">
                    <span className={`chip py-0 text-[10.5px] ${b.granted ? "bg-good/10 text-good" : "bg-bad/10 text-bad"}`}>
                      {b.granted ? "Granted" : "Refused"}
                    </span>
                  </td>
                </MotionRow>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <EmptyState
          title="No badge swipes received"
          body="The access-control system posts each swipe to /api/v1/ingest/badges with a service key. Until it does, badge-required zones cannot be checked."
        />
      )}
    </section>
  );
}
