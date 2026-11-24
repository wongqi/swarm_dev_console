"""Segmented PC-side MQTT records for manual archive."""

import json
import logging
import os
from pathlib import Path
from queue import Full, Queue
from threading import Thread
from time import time, time_ns


class Journal:
    def __init__(self, root, segment_bytes=16 * 1024 * 1024):
        self.root, self.segment_bytes = Path(root), segment_bytes
        self.queue = Queue(maxsize=128)
        self.dropped = 0
        self.log = logging.getLogger(__name__)
        self.last_warning = 0
        self.thread = Thread(target=self._write, daemon=True, name="swarm-mqtt-records")
        self.thread.start()

    def record(self, direction, row):
        try:
            self.queue.put_nowait({"direction": direction, "time": time(), **row})
        except Full:
            self._drop()

    def _drop(self):
        self.dropped += 1
        if time() - self.last_warning >= 10:
            self.log.warning("MQTT 运行记录写入失败或队列已满，累计丢失 %d 条", self.dropped)
            self.last_warning = time()

    def _write(self):
        segment = None
        size = 0
        while (row := self.queue.get()) is not None:
            try:
                raw = (json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
                if segment is None or size + len(raw) > self.segment_bytes:
                    self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
                    segment = self.root / f"mqtt-{time_ns()}.jsonl"
                    size = 0
                with open(segment, "ab", opener=lambda path, flags: os.open(path, flags, 0o600)) as stream:
                    stream.write(raw)
                size += len(raw)
            except (OSError, ValueError, TypeError):
                self._drop()
        try:
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
            (self.root / "status.json").write_text(json.dumps({"dropped": self.dropped}))
        except OSError:
            self._drop()

    def close(self):
        try:
            self.queue.put(None, timeout=1)
        except Full:
            self._drop()
            return
        self.thread.join(timeout=3)
