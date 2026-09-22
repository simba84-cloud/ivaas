import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Radar, X } from "lucide-react";
import { useState } from "react";
import { api } from "../api/client";
import type { CameraRole, DiscoveredDevice, DiscoveredStream } from "../api/types";
import { roleLabel } from "./ui";

const ROLES: CameraRole[] = ["chokepoint", "overhead", "side_high", "side_mid", "side_low", "lpr"];
const input =
  "w-full rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none focus:border-brand-navy focus:ring-2 focus:ring-brand-navy/20";
const label = "mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500";

/** Register a camera of any make: paste a stream URL, or find it over ONVIF. */
export function AddCamera({ bayId, onClose }: { bayId: string; onClose: () => void }) {
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [role, setRole] = useState<CameraRole>("chokepoint");
  const [url, setUrl] = useState("");
  const [push, setPush] = useState(false);

  const [device, setDevice] = useState<DiscoveredDevice | null>(null);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");

  const add = useMutation({
    mutationFn: () => api.addCamera(bayId, { name, role, source_url: push ? null : url }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["cameras"] });
      qc.invalidateQueries({ queryKey: ["summary"] });
      onClose();
    },
  });
  const discover = useMutation({ mutationFn: api.discover });
  const streams = useMutation({
    mutationFn: () => api.discoverStreams(device!.address, username, password),
  });

  const pick = (s: DiscoveredStream) => {
    setUrl(s.url);
    setPush(false);
    if (!name && device) setName(device.name ?? device.host);
  };

  return (
    <div className="card mb-6 overflow-hidden">
      <div className="flex items-center justify-between bg-brand-navy px-5 py-3 text-white">
        <h2 className="font-semibold">Add camera</h2>
        <button onClick={onClose} aria-label="Close" className="rounded p-1 hover:bg-white/10">
          <X size={18} />
        </button>
      </div>

      <div className="grid gap-6 p-5 lg:grid-cols-2">
        <form
          onSubmit={(e) => {
            e.preventDefault();
            add.mutate();
          }}
        >
          <div className="grid gap-4 sm:grid-cols-2">
            <div>
              <label className={label}>Name</label>
              <input
                className={input}
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Dock door east"
                required
              />
            </div>
            <div>
              <label className={label}>Position</label>
              <select
                className={input}
                value={role}
                onChange={(e) => setRole(e.target.value as CameraRole)}
              >
                {ROLES.map((r) => (
                  <option key={r} value={r}>
                    {roleLabel(r)}
                  </option>
                ))}
              </select>
            </div>
          </div>

          <div className="mt-4">
            <label className={label}>Stream URL</label>
            <input
              className={`${input} font-mono text-xs disabled:bg-slate-100`}
              value={push ? "" : url}
              onChange={(e) => setUrl(e.target.value)}
              disabled={push}
              required={!push}
              placeholder="rtsp://user:pass@192.168.1.64:554/Streaming/Channels/101"
              autoComplete="off"
              spellCheck={false}
            />
            <p className="mt-1.5 text-xs text-slate-500">
              RTSP, RTMP, SRT, HLS/MJPEG over HTTP, MPEG-TS over UDP, or WebRTC (WHEP): any vendor.
              The password is stored server-side and never shown again.
            </p>
            <label className="mt-3 flex items-center gap-2 text-sm text-slate-600">
              <input type="checkbox" checked={push} onChange={(e) => setPush(e.target.checked)} />
              This device pushes its stream to the platform instead
            </label>
          </div>

          {add.isError && (
            <p className="mt-3 text-sm text-red-600">{(add.error as Error).message}</p>
          )}
          <div className="mt-5 flex gap-3">
            <button className="btn-primary" disabled={add.isPending}>
              {add.isPending ? "Adding…" : "Add camera"}
            </button>
            <button type="button" className="btn-ghost" onClick={onClose}>
              Cancel
            </button>
          </div>
        </form>

        <div className="rounded-lg border border-slate-200 bg-brand-surface/60 p-4">
          <div className="flex items-center justify-between">
            <div>
              <div className="text-sm font-semibold text-brand-navy">Find on network</div>
              <div className="text-xs text-slate-500">ONVIF discovery: works across vendors</div>
            </div>
            <button
              className="btn-ghost"
              onClick={() => discover.mutate()}
              disabled={discover.isPending}
            >
              <Radar size={16} />
              {discover.isPending ? "Scanning…" : "Scan"}
            </button>
          </div>

          {discover.isSuccess && discover.data.length === 0 && (
            <p className="mt-3 text-xs text-slate-500">
              No ONVIF devices answered. The API server must be on the same network segment as the
              cameras (multicast does not cross routers or Docker's default bridge).
            </p>
          )}

          <ul className="mt-3 space-y-1">
            {discover.data?.map((d) => (
              <li key={d.address}>
                <button
                  onClick={() => {
                    setDevice(d);
                    streams.reset();
                  }}
                  className={`w-full rounded-lg px-3 py-2 text-left text-sm ${
                    device?.address === d.address
                      ? "bg-brand-navy text-white"
                      : "bg-white hover:bg-brand-navy-tint"
                  }`}
                >
                  <span className="font-semibold">{d.name ?? d.host}</span>
                  <span className="ml-2 font-mono text-xs opacity-70">
                    {d.host} {d.hardware ? `· ${d.hardware}` : ""}
                  </span>
                </button>
              </li>
            ))}
          </ul>

          {device && (
            <form
              className="mt-3 grid grid-cols-[1fr_1fr_auto] gap-2"
              onSubmit={(e) => {
                e.preventDefault();
                streams.mutate();
              }}
            >
              <input
                className={input}
                placeholder="Camera username"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                autoComplete="off"
              />
              <input
                className={input}
                type="password"
                placeholder="Camera password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="new-password"
              />
              <button className="btn-primary" disabled={streams.isPending}>
                Get streams
              </button>
            </form>
          )}
          {streams.isError && (
            <p className="mt-2 text-xs text-red-600">{(streams.error as Error).message}</p>
          )}
          <ul className="mt-2 space-y-1">
            {streams.data?.map((s) => (
              <li key={s.url}>
                <button
                  onClick={() => pick(s)}
                  className="flex w-full items-center justify-between rounded-lg bg-white px-3 py-2 text-sm hover:bg-brand-magenta-tint"
                >
                  <span className="font-semibold text-brand-navy">{s.profile}</span>
                  <span className="text-xs text-slate-500">
                    {s.resolution ? `${s.resolution[0]}×${s.resolution[1]}` : ""} {s.encoding ?? ""}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </div>
  );
}
