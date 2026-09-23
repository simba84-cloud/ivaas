"""Upload -> worker -> report, through the real HTTP API with a scripted analyser."""

import time

from conftest import login, make_client

from ivaas.domain.analysis import DetectedLoad, TimelineEvent


class FakeAnalyser:
    async def analyse(self, job, local_path, save_frame, on_progress):
        await on_progress(0.5)
        key = await save_frame("stack-0001-12s.jpg", b"\xff\xd8fakejpeg")
        return (
            [DetectedLoad(10.0, 40.0, 2, 27, 0, "ABC 1234")],
            [
                TimelineEvent(10.0, "load_started", "Load 1 started"),
                TimelineEvent(12.0, "stack_counted", "Stack of 14 crates", key),
                TimelineEvent(40.0, "load_ended", "Load 1 ended: 2 stacks, 27 crates"),
            ],
            60.0,
        )


def test_upload_then_report(tmp_path):
    with make_client(objects="local", objects_dir=str(tmp_path)) as c:
        c.app.state.container.analyser = FakeAnalyser()  # RunNextJob reads it per call
        c.headers.update(login(c, "operator"))
        bay = c.get("/api/v1/bays").json()[0]["id"]

        r = c.post(
            f"/api/v1/analysis?bay_id={bay}",
            files={"file": ("loading.mp4", b"\x00" * 1000, "video/mp4")},
        )
        assert r.status_code == 202, r.text
        job = r.json()
        assert job["status"] == "queued" and job["created_by"] == "operator"

        for _ in range(50):  # the worker polls every 2 s
            job = c.get(f"/api/v1/analysis/{job['id']}").json()
            if job["status"] in ("done", "failed"):
                break
            time.sleep(0.2)
        assert job["status"] == "done", job
        assert job["total_crates"] == 27 and job["loads"][0]["plate"] == "ABC 1234"
        assert job["duration_s"] == 60.0
        frame = next(e for e in job["timeline"] if e["kind"] == "stack_counted")
        assert frame["frame_url"].startswith("/api/v1/objects/frames/")
        assert c.get(frame["frame_url"]).content.startswith(b"\xff\xd8")
        assert c.get(job["video_url"]).status_code == 200
        assert len(c.get("/api/v1/analysis").json()) == 1


def test_rejects_wrong_type_and_needs_operator(tmp_path):
    with make_client(objects="local", objects_dir=str(tmp_path)) as c:
        c.headers.update(login(c, "viewer"))
        bay = c.get("/api/v1/bays").json()[0]["id"]
        r = c.post(f"/api/v1/analysis?bay_id={bay}", files={"file": ("x.mp4", b"0", "video/mp4")})
        assert r.status_code == 403
        c.headers.update(login(c, "operator"))
        r = c.post(f"/api/v1/analysis?bay_id={bay}", files={"file": ("x.txt", b"0", "text/plain")})
        assert r.status_code == 415


def test_object_keys_cannot_escape_the_store(tmp_path):
    with make_client(objects="local", objects_dir=str(tmp_path)) as c:
        c.headers.update(login(c, "viewer"))
        assert c.get("/api/v1/objects/../../etc/passwd").status_code in (400, 404)


def test_reports_survive_a_restart(tmp_path):
    with make_client(objects="local", objects_dir=str(tmp_path)) as c:
        c.app.state.container.analyser = FakeAnalyser()
        c.headers.update(login(c, "operator"))
        bay = c.get("/api/v1/bays").json()[0]["id"]
        job = c.post(
            f"/api/v1/analysis?bay_id={bay}", files={"file": ("a.mp4", b"0", "video/mp4")}
        ).json()
        for _ in range(50):
            if c.get(f"/api/v1/analysis/{job['id']}").json()["status"] == "done":
                break
            time.sleep(0.2)
    with make_client(objects="local", objects_dir=str(tmp_path)) as c:  # "restart"
        c.headers.update(login(c, "viewer"))
        again = c.get(f"/api/v1/analysis/{job['id']}").json()
        assert again["status"] == "done" and again["total_crates"] == 27
        assert again["loads"][0]["plate"] == "ABC 1234"
