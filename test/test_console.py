"""HTTP/MQTT round trip using an opt-in local broker."""

from contextlib import contextmanager
import hashlib
import io
import json
import os
import shutil
import socket
import subprocess
import tarfile
from threading import Thread
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from paho.mqtt import client as mqtt

from swarm_dev_console.console import Console
from swarm_dev_console.contracts import PREFIX
from swarm_dev_console.maps import MapLibrary
from swarm_dev_console.reports import EvidenceLibrary, ReportReader
from swarm_dev_console.server import Handler, ThreadingHTTPServer


def round_archive():
    events = "runtime/agent/audit/round-101/r001/events.jsonl"
    reports = "reports/round-101/demo.html"
    payloads = {
        events: b'{"time":2,"robot_id":"R1","round_epoch":101}\n'
                b'{"time":1,"robot_id":"R1","round_epoch":101}\n',
        reports: b"<html>offline report</html>",
    }
    payloads["manifest.json"] = json.dumps({
        "format": 1, "round_epoch": 101, "robot_id": "R1", "files": [
            {"path": name, "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}
            for name, raw in payloads.items()
        ],
    }).encode()
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, raw in payloads.items():
            row = tarfile.TarInfo(name)
            row.size = len(raw)
            archive.addfile(row, io.BytesIO(raw))
    return buffer.getvalue()


def until(predicate):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(0.025)
    raise AssertionError("message not received")


@contextmanager
def local_broker(tmp_path):
    broker = os.environ.get("SWARM_BROKER") or shutil.which("mosquitto")
    if not broker:
        pytest.skip("set SWARM_BROKER to a local Mosquitto executable")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    config = tmp_path / "broker.conf"
    config.write_text(f"listener {port} 127.0.0.1\nallow_anonymous true\npersistence false\n")
    with subprocess.Popen([broker, "-c", str(config)], stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL) as process:
        def listening():
            try:
                with socket.create_connection(("127.0.0.1", port), 0.2):
                    return True
            except OSError:
                return False

        try:
            until(listening)
            yield port
        finally:
            process.terminate()
            process.wait(timeout=5)


def test_console_http_and_mqtt(tmp_path, monkeypatch):
    generated_ms = 1795513654008
    monkeypatch.setattr("swarm_dev_console.console.time.time_ns", lambda: generated_ms * 1_000_000)
    with local_broker(tmp_path) as port:
        peer = mqtt.Client(client_id="console-test-peer")
        messages, ready = [], []
        peer.on_message = lambda c, u, msg: messages.append(json.loads(msg.payload))
        peer.on_subscribe = lambda *_args: ready.append(True)
        peer.connect("127.0.0.1", port)
        peer.subscribe(PREFIX + "/services", qos=1)
        peer.loop_start()
        with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
            console = server.console = Console(tmp_path / "data", "127.0.0.1", port)
            published = []
            publish_request = console.client.publish

            def track_publish(topic, payload, **options):
                published.append((topic, json.loads(payload)))
                return publish_request(topic, payload, **options)

            monkeypatch.setattr(console.client, "publish", track_publish)
            server.library = EvidenceLibrary(tmp_path / "data" / "archives")
            server.reports = ReportReader(tmp_path / "data" / "reports")
            server.maps = MapLibrary(tmp_path / "maps")
            thread = Thread(target=server.serve_forever)
            thread.start()
            address = "http://127.0.0.1:" + str(server.server_port)

            def api(path):
                with urlopen(address + path, timeout=2) as response:
                    return json.load(response)

            try:
                until(lambda: ready and api("/api/state")["subscriptions"].get(PREFIX + "/services") == 1)
                with urlopen(address, timeout=2) as response:
                    page = response.read().decode()
                    assert "多机协同调试台" in page
                    assert 'id="new-task"' in page and 'id="file"' in page
                    assert 'id="bid"' not in page and 'id="new-epoch"' not in page
                    assert 'class="side-nav"' not in page
                    assert "company2" not in page
                with pytest.raises(HTTPError, match="404"):
                    api("/examples/company2_test3.json")
                token = api("/api/session")["token"]
                upload = Request(address + "/api/archive?name=round-101.tar.gz",
                                 headers={"X-Swarm-Session": token}, data=round_archive())
                with urlopen(upload, timeout=2) as response:
                    assert response.status == 201
                    archived = json.load(response)
                identity = archived["id"]
                assert archived["events"] == 2 and api("/api/archive") == [archived]
                timeline = api(f"/api/archive/file?id={identity}&name=timeline.json")
                assert [event["time"] for event in timeline] == [1, 2]
                with urlopen(address + f"/api/archive/file?id={identity}&name=report-0.html", timeout=2) as response:
                    assert response.read() == b"<html>offline report</html>"
                    assert "sandbox" in response.headers["Content-Security-Policy"]
                with pytest.raises(HTTPError, match="503"):
                    api("/api/report?key=tmp/test/demo.html")
                data = {"robot_ids": ["R1"], "map_package": "/maps/test", "tasks": [],
                        "round_epoch": 101, "metadata": {"label": "preserve-input"},
                        "storage": {"secretKey": "request-secret"}}

                def send(method, body, session=token):
                    request = Request(address + "/api/request", headers={
                        "X-Swarm-Session": session, "Content-Type": "application/json",
                    }, data=json.dumps({"method": method, "bid": "demo", "data": body}).encode())
                    with urlopen(request, timeout=2) as response:
                        return json.load(response)

                with pytest.raises(HTTPError, match="403"):
                    send("task.execute", data, "invalid")
                with pytest.raises(HTTPError, match="400"):
                    send("task.execute", {**data, "map_package": "company2"})
                with pytest.raises(HTTPError, match="400"):
                    send("task.execute", {key: value for key, value in data.items() if key != "round_epoch"})
                epoch_request = Request(address + "/api/epoch", headers={"X-Swarm-Session": token},
                                        data=b"{}")
                with urlopen(epoch_request, timeout=2) as response:
                    generated = json.load(response)["round_epoch"]
                assert generated == generated_ms
                assert not messages
                sent = send("task.execute", data)
                epoch = sent["round_epoch"]
                until(lambda: messages)
                assert epoch == 101 and messages[0]["data"] == data
                assert not (tmp_path / "data" / "epoch").exists()

                for i, method in enumerate(("task.add", "task.stop", "task.reset"), start=2):
                    send(method, {**data, "round_epoch": epoch})
                    until(lambda: len(messages) >= i)
                    assert messages[i - 1]["method"] == method
                    assert messages[i - 1]["data"] == data

                def publish(topic, payload):
                    peer.publish(topic, json.dumps(payload), qos=1).wait_for_publish(2)

                publish(PREFIX + "/services_reply", {"tid": sent["tid"], "_result": 0})
                publish("fh/swarm/device/R1/heartbeat", {
                    "robot_id": "R1", "round_epoch": epoch, "sequence": 2,
                    "consensus_role": "leader", "authority": True, "coordinator_boot": "fresh",
                    "pose": {"frame_id": "map_000", "pos": [1.2, 2.3, 0.0]},
                    "capabilities": ["device", "gimbal.vis"],
                    "planner_metrics": {"wall_seconds": 1.1, "peak_rss_kib": 4096},
                })
                # A replay of an older plan carries the current commit index.
                plan_event = {"stage": "for_platform_task_plan", "round_epoch": epoch}
                routes = {"R1": {"robot_id": "R1", "task_ids": ["p2"], "dependencies": [],
                                 "checkpoints": [{"type": "task"}]}}
                publish(PREFIX + "/events", {"data": {**plan_event, "plan_revision": 2,
                    "commit_index": 7, "event_id": "plan-2", "routes": routes}})
                publish(PREFIX + "/events", {"data": {**plan_event, "plan_revision": 1,
                    "commit_index": 8, "event_id": "plan-1", "routes": {}}})
                payload = {"stage": "for_platform_mission_finished", "state": "COMPLETED",
                           "round_epoch": epoch, "commit_index": 9, "event_id": "same",
                           "storage": {"secretKey": "must-not-leak"}}
                for _ in range(2):
                    publish(PREFIX + "/events", {"data": payload, "bid": "demo"})
                publish(PREFIX + "/state", {"round_epoch": epoch, "commit_index": 8, "state": "ACTIVE"})
                snapshot = until(lambda: (state if (state := api("/api/state"))["mission"] else None))
                assert snapshot["mission"]["state"] == "COMPLETED"
                assert snapshot["devices"][0]["authority"]
                assert snapshot["requests"][0]["status"] == "已受理"
                assert sum(r["stage"] == "for_platform_mission_finished" for r in snapshot["events"]) == 1
                assert "must-not-leak" not in json.dumps(api("/api/evidence"))
                compact = api("/api/state?compact=1")
                assert compact["history_version"] == 2
                assert compact["rounds"][0]["revisions"] == 2
                assert [p["plan_revision"] for p in api("/api/plans?epoch=101")] == [1, 2]
                assert api("/api/plans?epoch=101&revision=2")["routes"] == routes
                with pytest.raises(HTTPError, match="404"):
                    api("/api/plans?epoch=101&revision=3")
                with pytest.raises(HTTPError, match="400"):
                    api("/api/plans?epoch=../outside")
                assert compact["devices"][0]["pose"]["frame_id"] == "map_000"
                assert compact["plan"]["plan_revision"] == 2
                assert compact["plan"]["routes"][0]["checkpoint_count"] == 1
                assert compact["topics"][PREFIX + "/services"]["count"] == 4
                # Local map reads and a new epoch after another entry point's
                # newer heartbeat must neither publish control nor advance the clock.
                from PIL import Image
                local_map = tmp_path / "maps" / "test"
                local_map.mkdir(parents=True)
                Image.new("L", (20, 10), 255).save(local_map / "map_000.png")
                (local_map / "map_000.yaml").write_text(
                    "image: map_000.png\nresolution: 0.5\norigin: [1, 2, 0]\n")
                maps = api("/api/maps?package=/board/maps/test")
                assert maps["maps"][0]["width"] == 20
                with urlopen(address + "/api/maps/image?package=/board/maps/test&file=map_000.yaml", timeout=2) as response:
                    assert response.headers["Content-Type"] == "image/png"
                    assert response.read().startswith(b"\x89PNG")
                publish("fh/swarm/device/R2/heartbeat", {
                    "robot_id": "R2", "round_epoch": generated + 1000, "sequence": 1,
                })
                until(lambda: any(d["robot_id"] == "R2" and d["round_epoch"] == generated + 1000
                                  for d in api("/api/state")["devices"]))
                with urlopen(epoch_request, timeout=2) as response:
                    assert json.load(response)["round_epoch"] == generated_ms
                plan_message = api(f"/api/messages?id={compact['plan']['message_id']}")
                assert plan_message["payload"]["data"]["routes"]["R1"]["checkpoints"] == routes["R1"]["checkpoints"]
                wire = api("/api/messages")
                assert sum(row["stage"] == "for_platform_mission_finished" for row in wire) == 2
                assert all("payload" not in row for row in wire)
                assert {row["topic"] for row in wire} == {
                    PREFIX + "/services", PREFIX + "/services_reply", PREFIX + "/events",
                    PREFIX + "/state", "fh/swarm/device/R1/heartbeat", "fh/swarm/device/R2/heartbeat",
                }
                for _ in range(3):
                    api("/api/state?compact=1")
                    api("/api/messages")
                    api("/api/archive")
                assert len(published) == 4
                assert all(topic == PREFIX + "/services" and request["data"] == data
                           for topic, request in published)
            finally:
                server.shutdown()
                thread.join(timeout=3)
                console.close()
                peer.disconnect()
                peer.loop_stop()
            journal = (tmp_path / "data" / "mqtt")
            records = "".join(path.read_text() for path in journal.glob("*.jsonl"))
            assert '"direction":"in"' in records and '"direction":"out"' in records
            assert "must-not-leak" not in records and "request-secret" not in records
