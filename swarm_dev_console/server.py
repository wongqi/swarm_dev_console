"""Loopback HTTP console using only the public swarm MQTT channels."""

import argparse
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
from urllib.parse import parse_qs, urlsplit

from .console import Console
from .contracts import MAX_BYTES, parse
from .reports import ARCHIVE_LIMIT, EvidenceLibrary, ReportReader, ReportUnavailable


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def response(self, code, raw, content_type, *, attachment=False, report=False):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        policy = "default-src 'self'; frame-ancestors 'none'; object-src 'none'"
        if report:
            policy = "sandbox; default-src 'none'; img-src data:; style-src 'unsafe-inline'; frame-ancestors 'self'"
        self.send_header("Content-Security-Policy", policy)
        if attachment:
            name = "swarm-report.html" if report else "swarm-evidence.json"
            self.send_header("Content-Disposition", f'attachment; filename="{name}"')
        self.end_headers()
        self.wfile.write(raw)

    def json(self, code, data, **options):
        self.response(code, json.dumps(data, ensure_ascii=False).encode(),
                      "application/json; charset=utf-8", **options)

    def local_host(self):
        port = self.server.server_port
        return self.headers.get("Host") in {f"127.0.0.1:{port}", f"localhost:{port}"}

    def do_GET(self):
        if not self.local_host():
            return self.json(403, {"error": "仅允许本地调试台地址"})
        url = urlsplit(self.path)
        path = url.path
        params = parse_qs(url.query)
        if path == "/api/state":
            return self.json(200, self.server.console.snapshot(compact=params.get("compact") == ["1"]))
        if path == "/api/messages":
            message = self.server.console.messages(params.get("id", [None])[0])
            if message is None:
                return self.json(404, {"error": "该消息已移出最近记录"})
            return self.json(200, message)
        if path == "/api/session":
            return self.json(200, {"token": self.server.console.token})
        if path == "/api/evidence":
            return self.json(200, self.server.console.snapshot(wire=True), attachment=True)
        if path in {"/api/report", "/api/archive", "/api/archive/file"}:
            try:
                if path == "/api/archive":
                    return self.json(200, self.server.library.index())
                if path == "/api/report":
                    content = self.server.reports.read(params.get("key", [""])[0])
                else:
                    name = params.get("name", [""])[0]
                    content = self.server.library.file(params.get("id", [""])[0], name)
                    if name == "timeline.json":
                        return self.response(200, content, "application/json; charset=utf-8")
                return self.response(200, content, "text/html; charset=utf-8",
                                     report=True, attachment=params.get("download") == ["1"])
            except ReportUnavailable as error:
                if params.get("view") == ["1"]:
                    raw = ('<!doctype html><meta charset="utf-8"><title>报告暂不可用</title>'
                           "<p>" + escape(str(error)) + "</p>").encode()
                    return self.response(error.status, raw, "text/html; charset=utf-8", report=True)
                return self.json(error.status, {"error": str(error)})
        assets = {"/": ("index.html", "text/html"), "/style.css": ("style.css", "text/css"),
                  "/console.js": ("console.js", "text/javascript")}
        if path not in assets:
            return self.json(404, {"error": "页面不存在"})
        name, content_type = assets[path]
        raw = (Path(__file__).parent / "static" / name).read_bytes()
        self.response(200, raw, content_type + "; charset=utf-8")

    def do_POST(self):
        console = self.server.console
        if (not self.local_host() or self.headers.get("Origin") not in {
                None, "http://" + self.headers.get("Host", "")}
                or not secrets.compare_digest(self.headers.get("X-Swarm-Session", ""), console.token)):
            return self.json(403, {"error": "本地会话无效"})
        path = urlsplit(self.path).path
        if path not in {"/api/request", "/api/archive", "/api/epoch"}:
            return self.json(404, {"error": "接口不存在"})
        try:
            n = int(self.headers.get("Content-Length", 0))
            if n < 1 or n > (ARCHIVE_LIMIT if path == "/api/archive" else MAX_BYTES):
                raise ValueError("请求大小无效")
            if path == "/api/archive":
                name = parse_qs(urlsplit(self.path).query).get("name", ["archive.tar.gz"])[0]
                return self.json(201, self.server.library.save(Path(name).name[:256], self.rfile.read(n)))
            if path == "/api/epoch":
                parse(self.rfile.read(n))
                return self.json(200, {"round_epoch": console.new_epoch()})
            self.json(202, console.send(parse(self.rfile.read(n))))
        except ReportUnavailable as error:
            self.json(error.status, {"error": str(error)})
        except (ValueError, TypeError, OverflowError, RecursionError) as error:
            self.json(400, {"error": str(error)})
        except OSError:
            self.json(503, {"error": "本地轮次记录不可写"})


def default_root():
    source = Path(__file__).resolve().parents[1]
    return source / "runtime" if (source / "pyproject.toml").is_file() else Path.home() / ".swarm-dev-console"

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mqtt-host", default="121.196.36.117")
    parser.add_argument("--mqtt-port", type=int, default=20402)
    parser.add_argument("--mqtt-tls", action="store_true")
    parser.add_argument("--mqtt-ca")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data-root", type=Path, default=default_root())
    parser.add_argument("--report-endpoint", default=os.environ.get("SWARM_REPORT_ENDPOINT"))
    parser.add_argument("--report-bucket", default=os.environ.get("SWARM_REPORT_BUCKET"))
    parser.add_argument("--report-prefix", default=os.environ.get("SWARM_REPORT_PREFIX", ""))
    args = parser.parse_args()
    legacy_epoch = Path.home() / ".swarm-dev-console/epoch"
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    try:
        server.console = Console(args.data_root, args.mqtt_host, args.mqtt_port,
                                 username=os.environ.get("SWARM_MQTT_USER"),
                                 password=os.environ.get("SWARM_MQTT_PASSWORD"),
                                 tls=args.mqtt_tls, ca=args.mqtt_ca,
                                 legacy_epoch=legacy_epoch if args.data_root == default_root() else None)
        server.reports = ReportReader(args.data_root / "reports", args.report_endpoint,
                                      args.report_bucket, args.report_prefix)
        server.library = EvidenceLibrary(args.data_root / "archives")
        print(f"Swarm 调试台 http://127.0.0.1:{server.server_port}", flush=True)
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        if hasattr(server, "console"):
            server.console.close()


if __name__ == "__main__":
    main()
