# Stream simulator and edge drills (proposal M2: T2.3, T2.4, T2.5)

The simulator stands in for a loading bay's cameras before they are installed. It
replays recorded footage as the POC camera array: 16 volumetric cameras plus 1 LPR
camera. Each stream is pushed to MediaMTX as a push camera, registered through the
API like a real one.

On the GPU edge node this is how T2.3 is run: can one node sustain 16 × 4K + LPR at
the target analytics rate? On a laptop, run it at a smaller scale to check the rig.

## 1. Prepare the footage (once)

```bash
./deploy/simulator/prepare.sh services/pipeline/tests/golden/door-load.mp4 3840x2160 15
```

This re-encodes the clip to the camera spec: 4K at 15 fps, H.264, a keyframe every
second. The result is `deploy/simulator/media/sim.mp4`.

The publisher then copies that file into every stream without re-encoding it. The
simulator therefore costs almost nothing, and the load test measures the edge node,
not the machine faking the cameras.

## 2. Register the cameras and point the node at them

Enrol the node first (see the main README), then:

```bash
docker compose --profile edge run --rm --no-deps \
  -e IVAAS_ADMIN_USER=<tenant admin> -e IVAAS_ADMIN_PASSWORD=<password> \
  pipeline python -m ivaas_pipeline.sim setup --api http://api:8000 \
    --bay <bay id> --node <node id> --stride 1 --out /sim/streams.txt
```

Setup registers 17 push cameras named "Sim ..." and writes their stream paths for the
publisher. It also configures the node to process every one of them. The node picks up
the new configuration within a minute.

## 3. Run it

```bash
docker compose --profile sim up -d simulator
docker compose --profile edge up -d pipeline
```

## Drills

**T2.3, the load report.** Run it for an hour on the real node; 24 hours is the
proposal's soak:

```bash
docker compose --profile edge exec pipeline python -m ivaas_pipeline.loadreport \
  --minutes 60 --target-fps 10 --expect 17 --out /sim/report.md
```

The run passes when every camera meets all of these:
- its stream stayed open with no reconnects;
- it held the target frame rate;
- its lag stayed under 2 s.

GPU and NVDEC use are included when `nvidia-smi` is present. Use the result to choose
between one node and two.

**T2.4, the WAN outage.**

```bash
./deploy/simulator/outage.sh 120     # minutes; 2 is enough to check the rig
```

The drill pauses the API, which looks like a black-holed WAN to the node, and keeps
it paused for the given time. It then unpauses the API and waits for the node's spool
to drain. It checks two things:

- **The pipeline's own spool.** Whatever the footage counted during the outage must be
  delivered afterwards, with nothing dropped.
- **Synthetic crossings.** `python -m ivaas_pipeline.sim inject` sends crossings as the
  node across the outage, through the same spool and sender. The crates the API counts
  must equal the crates sent, exactly.

The synthetic crossings mean the drill always has events at risk, even when the footage
produces few counts. A drill in which nothing was counted is reported inconclusive,
never passed.

A black-holed link does produce duplicates. Requests in flight when the link dies time
out on the node and are sent again, but the API applies the originals when it wakes.
The API's ingest ledger skips the repeats. Its `ivaas_ingest_replays_total` metric shows
how many there were.

**T2.5, a camera unplugged.**

```bash
IVAAS_ADMIN_USER=... IVAAS_ADMIN_PASSWORD=... \
  ./deploy/simulator/unplug.sh <bay id> <stream path from streams.txt>
```

The drill holds one stream down and times how long the API takes to report the camera
offline; the target is under 60 s. It then plugs the stream back in.

## Clean up

```bash
docker compose --profile sim stop simulator
docker compose --profile edge run --rm --no-deps -e IVAAS_ADMIN_USER=... -e IVAAS_ADMIN_PASSWORD=... \
  pipeline python -m ivaas_pipeline.sim teardown --api http://api:8000 --bay <bay id>
```
