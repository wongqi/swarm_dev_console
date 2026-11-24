"""Console-side JSON validation and MQTT addressing."""

import json
import math
import re

PREFIX = "fh/swarm/dispatch"
HEARTBEAT = "fh/swarm/device"
SUBSCRIPTIONS = (
    f"{HEARTBEAT}/+/heartbeat",
    *(f"{PREFIX}/{channel}" for channel in ("state", "events", "services_reply", "services")),
)
MAX_BYTES = 8 * 1024 * 1024
WIRE_BYTES = 16 * 1024 * 1024
MAX_EPOCH = 2**53 - 1
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
PRIVATE = {"storage", "stscredentials", "accesskey", "secretkey", "sessiontoken",
           "password", "mqtt_password", "authorization"}
TERMINAL = {"COMPLETED", "FAILED", "CANCELLED"}


def sanitize(value):
    if isinstance(value, dict):
        return {k: sanitize(v) for k, v in value.items() if k.lower() not in PRIVATE}
    if isinstance(value, list):
        return [sanitize(v) for v in value]
    return value


def parse(raw):
    def pairs(items):
        out = {}
        for key, value in items:
            if key in out:
                raise ValueError("JSON 字段重复")
            out[key] = value
        return out

    def invalid(_value):
        raise ValueError("JSON 数值必须有限")

    value = json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid)
    if not isinstance(value, dict):
        raise ValueError("JSON 顶层必须是对象")
    # Also rejects values such as 1e999 that json converts to infinity.
    json.dumps(value, allow_nan=False)
    return value


def epoch_of(value):
    epoch = value.get("round_epoch")
    return epoch if type(epoch) is int and 0 < epoch <= MAX_EPOCH else 0


def validate_task_data(data):
    robots, tasks, path = data.get("robot_ids"), data.get("tasks"), data.get("map_package")
    if (not isinstance(robots, list) or not 1 <= len(robots) <= 32
            or any(not isinstance(robot, str) or not IDENTIFIER.fullmatch(robot) for robot in robots)
            or len(set(robots)) != len(robots)):
        raise ValueError("robot_ids 需要 1 至 32 个不同的设备 ID")
    if not isinstance(path, str) or "\x00" in path or not path.startswith("/"):
        raise ValueError("map_package 需要设备上的地图绝对路径")
    if not isinstance(tasks, list):
        raise ValueError("tasks 需要数组")
    ids = set()
    for point in tasks:
        if not isinstance(point, dict) or not isinstance(point.get("id"), str):
            raise ValueError("每个任务点需要 id")
        if not IDENTIFIER.fullmatch(point["id"]) or point["id"] in ids:
            raise ValueError("任务点 ID 无效或重复")
        ids.add(point["id"])
        location = point.get("location")
        if not isinstance(location, dict) or not location.get("frame_id"):
            raise ValueError("每个任务点需要 location.frame_id")
        pos = location.get("pos")
        if (not isinstance(pos, list) or len(pos) != 3
                or any(type(v) not in (int, float) or not math.isfinite(v) for v in pos)):
            raise ValueError("location.pos 需要三个有限坐标")
        if not isinstance(point.get("tasks"), list):
            raise ValueError("每个点的 tasks 需要数组")
