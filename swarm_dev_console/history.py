"""Local, immutable allocation history reconstructed from public plan events."""

import json
import os
import sqlite3
import threading

from .contracts import epoch_of, sanitize


def routes_of(plan):
    routes = plan.get("routes", [])
    return list(routes.values()) if isinstance(routes, dict) else routes


class PlanHistory:
    def __init__(self, path):
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        os.chmod(path, 0o600)
        with self.db:
            self.db.execute("""CREATE TABLE IF NOT EXISTS plans (
                epoch INTEGER NOT NULL, revision INTEGER NOT NULL,
                summary TEXT NOT NULL, payload TEXT NOT NULL,
                PRIMARY KEY (epoch, revision))""")
        self.version = self.db.execute("SELECT COUNT(*) FROM plans").fetchone()[0]

    def record(self, plan, received_at):
        epoch, revision = epoch_of(plan), plan.get("plan_revision")
        routes = routes_of(plan)
        if (not epoch or type(revision) is not int or revision < 1
                or not isinstance(routes, list) or any(not isinstance(r, dict) for r in routes)):
            return False
        plan = sanitize(plan)
        summary = {
            "round_epoch": epoch, "plan_revision": revision,
            "mission_id": plan.get("mission_id", ""),
            "map_package": plan.get("map_package", ""), "received_at": received_at,
            "robot_count": len(routes),
            "assigned_count": len({task for route in routes for task in route.get("task_ids", [])}),
        }
        with self.lock, self.db:
            # Replayed snapshots contain present-day summary fields. Keep the
            # first observation and the originally committed routes per revision.
            inserted = self.db.execute(
                "INSERT OR IGNORE INTO plans VALUES (?, ?, ?, ?)",
                (epoch, revision, json.dumps(summary, ensure_ascii=False),
                 json.dumps(plan, ensure_ascii=False)),
            ).rowcount
            self.version += inserted
            return bool(inserted)

    def rounds(self):
        with self.lock:
            rows = self.db.execute("""SELECT p.summary, counts.revisions
                FROM plans p JOIN (
                    SELECT epoch, MAX(revision) latest, COUNT(*) revisions FROM plans GROUP BY epoch
                ) counts ON p.epoch=counts.epoch AND p.revision=counts.latest
                ORDER BY p.epoch DESC LIMIT 50""").fetchall()
            return [{**json.loads(summary), "revisions": count} for summary, count in rows]

    def index(self, epoch):
        with self.lock:
            return [json.loads(row[0]) for row in self.db.execute(
                "SELECT summary FROM plans WHERE epoch=? ORDER BY revision", (epoch,)
            )]

    def get(self, epoch, revision):
        with self.lock:
            row = self.db.execute(
                "SELECT payload FROM plans WHERE epoch=? AND revision=?", (epoch, revision)
            ).fetchone()
            return json.loads(row[0]) if row else None

    def latest(self):
        with self.lock:
            row = self.db.execute("SELECT payload FROM plans ORDER BY epoch DESC, revision DESC LIMIT 1").fetchone()
            return json.loads(row[0]) if row else None

    def close(self):
        self.db.close()
