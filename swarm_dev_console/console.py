"""MQTT bridge and console state, independent of the HTTP interface."""

from collections import deque
import fcntl
import json
import secrets
import threading
import time
import uuid

from paho.mqtt import client as mqtt

from .contracts import (HEARTBEAT, IDENTIFIER, MAX_BYTES, MAX_EPOCH, PREFIX,
                        SUBSCRIPTIONS, TERMINAL, WIRE_BYTES, epoch_of, parse,
                        sanitize, validate_task_data)
from .journal import Journal
from .reports import atomic_bytes


class Console:
    def __init__(self, root, host, port, *, username=None, password=None, tls=False, ca=None,
                 legacy_epoch=None):
        self.root = root
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.process_lock = (root / "console.lock").open("a")
        try:
            fcntl.flock(self.process_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.process_lock.close()
            raise ValueError("已有调试台使用此数据目录") from None
        self.lock = threading.RLock()
        self.epoch_path = root / "epoch"
        try:
            if not self.epoch_path.exists() and legacy_epoch is not None and legacy_epoch.is_file():
                atomic_bytes(self.epoch_path, legacy_epoch.read_bytes())
            self.epoch = int(self.epoch_path.read_text()) if self.epoch_path.exists() else 0
        except (ValueError, OSError):
            self.process_lock.close()
            raise
        self.journal = Journal(root / "mqtt")
        self.devices, self.mission, self.plan, self.requests = {}, None, None, {}
        self.events = deque(maxlen=250)
        self.raw = deque(maxlen=400)
        self.raw_bytes = 0
        self.message_id = 0
        self.topics = {}
        self.subscriptions = {}
        self.seen = {}
        self.connected = False
        self.connection_result = None
        self.endpoint = f"{host}:{port}"
        self.token = secrets.token_urlsafe(32)
        self.client = mqtt.Client(client_id="swarm-dev-" + uuid.uuid4().hex[:16])
        if username:
            self.client.username_pw_set(username, password)
        if tls or ca:
            self.client.tls_set(ca_certs=ca)
        self.client.on_connect = self.on_connect
        self.client.on_disconnect = self.on_disconnect
        self.client.on_message = self.on_message
        self.client.on_subscribe = self.on_subscribe
        self.client.connect_async(host, port, 15)
        self.client.loop_start()

    def close(self):
        self.client.disconnect()
        self.client.loop_stop()
        self.journal.close()
        self.process_lock.close()

    def on_connect(self, client, _data, _flags, result):
        with self.lock:
            self.connected = result == 0
            self.connection_result = int(result)
            self.subscriptions.clear()
            if self.connected:
                client.subscribe([(topic, 1) for topic in SUBSCRIPTIONS])

    def on_subscribe(self, _client, _data, _mid, granted):
        with self.lock:
            self.subscriptions = dict(zip(SUBSCRIPTIONS, granted))

    def on_disconnect(self, *_args):
        with self.lock:
            self.connected = False
            self.subscriptions.clear()

    def on_message(self, _client, _data, message):
        if message.retain or len(message.payload) > MAX_BYTES:
            return
        try:
            payload = parse(message.payload)
            self.receive(message.topic, sanitize(payload))
        except (ValueError, TypeError, OverflowError, RecursionError):
            return

    def receive(self, topic, value):
        now = time.time()
        with self.lock:
            data = value.get("data", value)
            if not isinstance(data, dict):
                return
            self.message_id += 1
            size = len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode())
            metadata = {
                "id": self.message_id, "topic": topic, "time": now, "bytes": size,
                "bid": value.get("bid"), "tid": value.get("tid"), "method": value.get("method"),
                "stage": data.get("stage"), "round_epoch": data.get("round_epoch"),
                "robot_id": data.get("robot_id"), "plan_revision": data.get("plan_revision"),
            }
            raw = {**metadata, "payload": value}
            self.journal.record("in", raw)
            while self.raw and (len(self.raw) == self.raw.maxlen or self.raw_bytes + size > WIRE_BYTES):
                self.raw_bytes -= self.raw.popleft()["bytes"]
            if size <= WIRE_BYTES:
                self.raw.append(raw)
                self.raw_bytes += size
            if topic not in self.topics and len(self.topics) == 256:
                self.topics.pop(next(iter(self.topics)))
            self.topics.setdefault(topic, {"count": 0})["count"] += 1
            self.topics[topic]["last_received"] = now
            epoch = epoch_of(data)
            self.epoch = max(self.epoch, epoch)
            if topic.startswith(HEARTBEAT + "/"):
                robot = topic.split("/")[-2]
                if robot != value.get("robot_id") or not IDENTIFIER.fullmatch(robot):
                    return
                previous = self.devices.get(robot, {})
                boot = (epoch, value.get("coordinator_boot") or value.get("session_id"))
                old_boot = (epoch_of(previous), previous.get("coordinator_boot") or previous.get("session_id"))
                # Dropping delayed packets also prevents them from refreshing the online timer.
                if previous and (epoch < old_boot[0]
                                 or (boot == old_boot and value.get("sequence", 0)
                                     and value.get("sequence", 0) <= previous.get("sequence", 0))):
                    return
                self.devices[robot] = {**value, "received_at": now}
            elif topic == PREFIX + "/state":
                self.set_state(data, now)
            elif topic == PREFIX + "/events":
                event_id = data.get("event_id")
                if isinstance(event_id, str):
                    if event_id in self.seen:
                        return
                    self.seen[event_id] = True
                    if len(self.seen) > 2048:
                        self.seen.pop(next(iter(self.seen)))
                self.events.append(raw)
                stage = data.get("stage")
                if stage == "for_platform_task_plan":
                    if self.plan is None or self.plan_order(data) >= self.plan_order(self.plan):
                        self.plan = {**data, "message_id": raw["id"]}
                if stage == "for_platform_mission_finished" and data.get("state") in TERMINAL:
                    self.set_state(data, now)
            elif topic == PREFIX + "/services_reply":
                self.events.append(raw)
                tid = value.get("tid")
                if isinstance(tid, str) and tid in self.requests:
                    item = self.requests[tid]
                    if item.get("status") != "已受理":
                        item["status"] = "已受理" if value.get("_result") == 0 else "设备拒绝"
                        item["reply"] = value

    @staticmethod
    def order(data):
        return tuple(data.get(k, 0) if type(data.get(k, 0)) is int else 0
                     for k in ("round_epoch", "commit_index", "term", "plan_revision"))

    @classmethod
    def plan_order(cls, data):
        epoch, index, term, revision = cls.order(data)
        return epoch, revision, index, term

    def set_state(self, data, now):
        if self.mission is None or self.order(data) >= self.order(self.mission):
            self.mission = {**data, "received_at": now}

    def new_epoch(self):
        with self.lock:
            epoch = max(self.epoch + 1, time.time_ns() // 1_000_000)
            if epoch > MAX_EPOCH:
                raise ValueError("轮次号已达到安全整数上限")
            atomic_bytes(self.epoch_path, str(epoch).encode())
            self.epoch = epoch
            return epoch

    def send(self, payload):
        method, bid, data = payload.get("method"), payload.get("bid"), payload.get("data")
        if method not in {"task.execute", "task.add", "task.stop", "task.reset"}:
            raise ValueError("任务操作无效")
        if not isinstance(bid, str) or not IDENTIFIER.fullmatch(bid):
            raise ValueError("任务 ID 无效")
        if not isinstance(data, dict):
            raise ValueError("data 需要对象")
        data = dict(data)
        if method in {"task.execute", "task.add"}:
            validate_task_data(data)
        if not epoch_of(data):
            raise ValueError("请求 JSON 需要有效的 round_epoch")
        with self.lock:
            if not self.connected:
                raise ValueError("MQTT 尚未连接")
            tid = uuid.uuid4().hex
            envelope = {"bid": bid, "tid": tid, "method": method, "data": data,
                        "timestamp": time.time_ns() // 1_000_000}
            body = json.dumps(envelope, ensure_ascii=False, allow_nan=False)
            if len(body.encode()) > MAX_BYTES:
                raise ValueError("请求超出消息大小限制")
            info = self.client.publish(PREFIX + "/services", body, qos=1, retain=False)
            if info.rc != mqtt.MQTT_ERR_SUCCESS:
                raise ValueError("MQTT 发布队列不可用")
            self.epoch = max(self.epoch, data["round_epoch"])
            status = {"bid": bid, "tid": tid, "method": method, "round_epoch": data["round_epoch"],
                      "status": "已交给 MQTT 队列"}
            self.journal.record("out", {"topic": PREFIX + "/services", "payload": sanitize(envelope)})
            self.requests[tid] = status
            if len(self.requests) > 100:
                self.requests.pop(next(iter(self.requests)))
            return dict(status)

    def snapshot(self, *, wire=False, compact=False):
        with self.lock:
            value = {"connected": self.connected, "broker": self.endpoint, "time": time.time(),
                     "devices": list(self.devices.values()), "mission": self.mission, "plan": self.plan,
                     "events": list(self.events), "requests": list(self.requests.values()),
                     "connection_result": self.connection_result, "journal_dropped": self.journal.dropped,
                     "topics": {topic: stats.copy() for topic, stats in self.topics.items()},
                     "subscriptions": self.subscriptions.copy()}
            if compact:
                if self.plan:
                    routes = self.plan.get("routes")
                    if isinstance(routes, dict):
                        routes = list(routes.values())
                    routes = routes if isinstance(routes, list) else []
                    value["plan"] = {
                        **{k: self.plan.get(k) for k in ("round_epoch", "plan_revision", "message_id")},
                        "routes": [
                            {**{k: route.get(k) for k in ("robot_id", "task_ids", "dependencies")},
                             "checkpoint_count": len(route.get("checkpoints") or [])}
                            for route in routes if isinstance(route, dict)
                        ],
                    }
                value["events"] = [
                    {k: v for k, v in row.items() if k != "payload"} | {
                        "message": row["payload"].get("_error") or (
                            row["payload"].get("data") or {}).get("message", ""),
                        "result": row["payload"].get("_result"),
                    }
                    for row in self.events
                ]
            if wire:
                value["wire"] = list(self.raw)
            return value

    def messages(self, message_id=None):
        with self.lock:
            if message_id is None:
                return [{k: v for k, v in row.items() if k != "payload"} for row in reversed(self.raw)]
            for history in (self.raw, self.events):
                for row in history:
                    if str(row["id"]) == message_id:
                        return row
            return None
