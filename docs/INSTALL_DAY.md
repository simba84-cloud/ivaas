# Install day at Bakers Inn (proposal M9, days 1–2)

On these two days, the Partner Installer enrols the edge node and registers the 17 devices
(16 volumetric cameras and 1 LPR camera). The day ends with evidence for two tests:
**T2.1**, the node is enrolled to the right tenant and site, and **T2.5**, every stream is
healthy and an unplugged camera is noticed within 60 s.

`deploy/install/rehearse.py` runs the same day against a stack with simulated cameras. Run
it on the edge node before going on site (see "Before the day"). Every step below has a
rehearsed result next to it.

## Before the day

Nothing here can be fixed quickly in a loading bay.

| | Check | Why |
|---|---|---|
| ☐ | **Run the rehearsal on the edge node itself, with the full array.** Use `--cameras 16` and the default `--min-fps 2`, and keep the report. | It is the only run that proves this hardware decodes 17 streams. A laptop cannot (see the rehearsal record below). |
| ☐ | **Run the T2.3 load report on the node** (`deploy/simulator/README.md`). | It decides between one node and two. The chokepoints need about 10 processed frames a second, because the counter is proven at that rate. |
| ☐ | **Build the images and fetch the models on the node** (`docker compose --profile edge build pipeline`, `deploy/fetch-models.sh`). | The first build took over 2 minutes in rehearsal, and the bay may have poor internet. |
| ☐ | **Set the node's clock by NTP.** The rehearsal's preflight warns above 5 s of drift. | Tally sheets are matched to loads by time, and evidence clips are cut by time. |
| ☐ | **Get the camera list**: IP, ONVIF login, **RTSP URL** and mounting position for each of the 17, and the VLAN they are on. | Registration takes seconds; finding a camera's password on site takes hours. The portal's "Find on network" (ONVIF) only sees cameras on the API's own network segment. With the platform hosted away from the site, add cameras by RTSP URL. |
| ☐ | **Check the network**: the node reaches every camera over RTSP (554) and ONVIF (80/8000), and reaches the platform over HTTPS. | A camera the node cannot reach shows "offline" and nothing more. |
| ☐ | **Check the platform**: tenant `bakers-inn`, site Bakery Industrial Site (time zone Africa/Harare) and bay Loading Bay exist. | The token is issued for that bay. |
| ☐ | **Set up accounts.** The installer signs in as Partner Installer for the tenant. Someone with Site Manager access is present for the counting check, because the installer role cannot read counts. **Change every seeded password.** | The Users screen shows seeded passwords in red. |
| ☐ | **Check the LPR camera's position** against the scope drawing: it must face the plates of trucks at the bay. | A test on dock footage read one plate in 81 frames. The angle is the cause, not the reader. |

## On the day

Times in brackets are from the rehearsal on 2026-10-01 (a laptop, simulated cameras).

1. **Preflight.** On the node, run `curl -s https://<platform>/api/v1/auth/config` and expect
   an answer. Check the clock with `timedatectl`.
2. **Enrol the node (T2.1).**
   1. In the portal, go to Configure → Edge Nodes → **Add node**, name it, and copy the
      command. The token is shown once and lasts 24 h.
   2. Run the command on the node, then `docker compose --profile edge up -d pipeline`.
   3. Expect the node to appear on the Edge Nodes page under Bakery Industrial Site. Running
      the same command again must be refused. [1–2 s; refused on reuse]
3. **Register the 17 devices.**
   1. Go to Configure → Cameras → **Add camera**, once per camera, with its RTSP URL.
      Where the API shares the cameras' network, **Find on network** (ONVIF) fills the URL in.
   2. Give each camera the role of its position: 4 overhead, 4 side high, 4 side middle,
      2 side low, 2 chokepoint, and 1 LPR.
   3. Expect 17 cameras listed. [17 registered in about 1 s]
4. **Configure the node.** Go to Configure → Edge Nodes → **Configure** on the node.
   1. Tick the cameras it counts with.
   2. Choose how each counts: chokepoints across **a line**, other positions within
      **a zone**. The LPR camera reads plates.
   3. Click **Draw** and, on the frame from the camera, click two points across the path
      the stacks take, for a line, or two opposite corners, for a zone. The coordinates
      are the camera's own pixels and can also be typed in. A camera that is not
      streaming has no frame, so its coordinates must be typed.
   4. Keep **every frame (stride 1) on both chokepoints**. A higher stride saves decoding
      but loses crossings: at 1 processed frame a second, nothing was counted. The editor
      warns when a chokepoint is set above 1. Other cameras may read fewer frames if T2.3
      showed the node is short.
   5. **Save configuration.**
   6. Expect the node to show "configuration applied" within a minute. It restarts its
      pipelines onto the new configuration and keeps the old one for **Roll back**.
      [Rehearsed: applied in 56 s, every stream reconnected.]

   The same configuration can be sent with `PUT /api/v1/edge/nodes/{node id}/config`, for
   example to script a second node.
5. **All streams healthy (T2.5).**
   - Expect every camera "online" on the Cameras page.
   - Expect the node "online" on the Edge Nodes page, with every stream connected at a
     working frame rate and the spool at 0.
   - [All 17 online in 4–12 s. The node reported every stream connected in 33–74 s.]
   - "Connected" alone is not healthy: a stream the node cannot keep up with is connected
     and counts nothing.
6. **Unplug drill (T2.5).**
   1. Pull the network cable of one camera. Expect it "offline", with an alert, **within
      60 s**. [4–10 s]
   2. Plug it back in. Expect it online again. [about 9 s]
   3. Note both times.
7. **Counting sanity check.** The Site Manager watches one real load through the door on the
   Operations page. Expect the live bay count to rise as stacks cross, and to be roughly
   what a person counts. This is a sanity check; accuracy is measured on days 8–10 against
   tally sheets.

## When a step fails

| What you see | Likely cause | What to do |
|---|---|---|
| Enrolment: "cannot write /var/lib/ivaas/node.json" | The volume belongs to another user | Fix ownership, then enrol again. The token is not spent when this happens. |
| Enrolment refused | The token was already used, has expired, or was mistyped | Create a new token. Tokens work once. |
| A camera stays offline | Wrong RTSP URL or password, wrong VLAN, or a firewall | Open the stream from the node with `ffprobe rtsp://…`, then check `docker compose logs mediamtx`. |
| Connected, but below a working frame rate | The node is short of decode for this many streams | Raise the stride on overhead and side cameras, never on chokepoints. If it is still short, T2.3 calls for a second node. |
| Node "stale" or "offline" | The node cannot reach the platform | Events wait in the node's spool and arrive in order after reconnection (T2.4), so nothing counted is lost. Fix the uplink. |
| Nothing counted while stacks cross | The chokepoint line is in the wrong place, the stride is above 1, or the chokepoint frame rate is too low | Move the line onto the crossing, set stride 1, and check the chokepoint's frame rate on the Edge Nodes page. |
| Loads without plates | The LPR camera's angle | Re-aim it. Loads still count and can be identified by hand (Sessions → identify). |
| Clock warning | No NTP on the node | Set NTP before any tally sheet is entered. |

## After the day

From the first heartbeat, the platform keeps each node's and camera's availability. That
is the history the POC report's reliability figure is measured from. On days 13–14, open
Reports → **Proof-of-concept report** for the POC's dates:
- Give the loading cycle time Bakers Inn measured before the system, so speed can be
  judged.
- Give a crate value, so the crates not yet back are priced.

Accuracy and plate reading come from the tally sheets entered during the POC. Without
them, those two criteria stay "not measured" and the report stays incomplete.

## Sign-off

| Test | Evidence | Result | Time |
|---|---|---|---|
| T2.1 | Node listed under Bakery Industrial Site only; token refused on reuse | | |
| T2.5 | All 17 cameras online; the node reports every stream at a working frame rate | | |
| T2.5 | Unplugged camera offline within 60 s; back online after re-plugging | | |
| Sanity | One load counted on the live bay | | |

Signed (Partner Installer): ____________ Witnessed (Bakers Inn): ____________ Date: ________

## Rehearsal record

2026-10-01, on the development stack (MacBook, Colima with 4 CPU cores), in the test
tenant's Test Bay:

- **The full array (16 + 1) passed every platform step.**
  - 17 devices registered.
  - A 17-camera configuration was applied.
  - All 17 cameras were online in 12 s.
  - The node connected every stream within 74 s.
  - The unplug drill took 10 s to show offline and 10 s to recover.
- **It failed the health check, correctly.** All 17 streams were below 2 fps, because the
  laptop's CPU cannot decode 17 streams. That is the GPU node's job, measured by T2.3.
- **A reduced rig** (2 chokepoints and the LPR camera) passed enrolment, registration,
  health and the unplug drill (4 s offline, 9 s back).
- **Counting was inconclusive on the laptop.** The chokepoints were processed at 0.3 fps,
  against the 10 fps the counter is proven at; the golden-footage test in CI counts this
  footage correctly at that rate. Counting can only be rehearsed on the GPU node.
- **The rehearsal found that a reduced array had no chokepoints**, because the scope lists
  them last. It now uses chokepoints for a reduced rig, so counting is tested.
- **It also found that "connected" was being taken as "healthy".** Healthy now means a
  working frame rate on every stream.
- **The node configuration had no editor**, so the chokepoint line had to be sent through
  the API, which is hard to get right in a loading bay. The Edge Nodes page now has one.
  - A line drawn on a real 1280×720 frame from a streaming camera was applied by the
    rehearsal node within 56 s.
  - That node started both chokepoints, one counting at the line and one in a zone, and
    the LPR camera, with no errors.
- **The rehearsal node first never restarted** after a configuration change. A node takes
  up a new configuration by exiting and being restarted onto it, and the rehearsal had
  turned restarts off. It restarts like the real node now.
- **Correction:** an earlier version of this record said ONVIF discovery had no portal
  screen. It has one (Cameras → Add camera → Find on network). Its real limit is that it
  only reaches cameras on the API's network segment.
