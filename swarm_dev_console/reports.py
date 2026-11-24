"""Read-only report access and offline evidence library."""

import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import tarfile
import tempfile

REPORT_LIMIT = 160 * 1024 * 1024
ARCHIVE_LIMIT = 256 * 1024 * 1024
ID = re.compile(r"[0-9a-f]{64}")


class ReportUnavailable(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def atomic_bytes(path, content):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        tmp = Path(stream.name)
        try:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)


def valid_key(key):
    return (
        isinstance(key, str) and 0 < len(key.encode()) <= 1024 and key.endswith(".html")
        and all(part not in {"", ".", ".."} for part in key.split("/"))
        and all(ord(c) >= 32 and ord(c) != 127 for c in key)
    )


class ReportReader:
    def __init__(self, cache, endpoint=None, bucket=None, prefix="", *, client=None):
        self.cache, self.endpoint, self.bucket = Path(cache), endpoint, bucket
        self.prefix = prefix.strip("/")
        self.client = client

    def read(self, key):
        if not valid_key(key):
            raise ReportUnavailable(400, "报告 key 无效")
        if self.prefix and not key.startswith(self.prefix + "/"):
            raise ReportUnavailable(403, "报告 key 超出只读配置范围")
        if not self.endpoint or not self.bucket:
            raise ReportUnavailable(503, "请配置调试台的 MinIO 只读连接")
        try:
            return self._read(key)
        except ReportUnavailable:
            raise
        except Exception as error:
            code = getattr(error, "response", {}).get("Error", {}).get("Code")
            if code in {"404", "NotFound", "NoSuchKey"}:
                raise ReportUnavailable(404, "报告对象尚未出现，可稍后刷新") from None
            if code in {"403", "AccessDenied", "InvalidAccessKeyId", "ExpiredToken", "SignatureDoesNotMatch"}:
                raise ReportUnavailable(403, "MinIO 读取权限或凭据有误") from None
            raise ReportUnavailable(502, "MinIO 报告读取失败") from None

    def _read(self, key):
        if self.client is None:
            import boto3
            from botocore.config import Config

            user = os.environ.get("SWARM_REPORT_ACCESS_KEY") or ""
            secret = os.environ.get("SWARM_REPORT_SECRET_KEY") or ""
            if not user or not secret:
                raise ReportUnavailable(503, "请设置 MinIO 只读账号与密钥")
            self.client = boto3.client(
                "s3", endpoint_url=self.endpoint, aws_access_key_id=user,
                aws_secret_access_key=secret,
                aws_session_token=os.environ.get("SWARM_REPORT_SESSION_TOKEN") or None,
                config=Config(connect_timeout=3, read_timeout=10,
                              retries={"total_max_attempts": 1}, s3={"addressing_style": "path"}),
            )
        obj = self.client.head_object(Bucket=self.bucket, Key=key)
        if obj["ContentLength"] > REPORT_LIMIT:
            raise ReportUnavailable(413, "报告超过查看大小上限")
        etag = obj.get("ETag")
        if not etag:
            raise ReportUnavailable(502, "报告对象缺少版本标识")
        version = hashlib.sha256(
            "\0".join((self.endpoint, self.bucket, key, etag)).encode()
        ).hexdigest()
        path = self.cache / (version + ".html")
        if path.is_file() and path.stat().st_size == obj["ContentLength"]:
            return path.read_bytes()
        result = self.client.get_object(Bucket=self.bucket, Key=key, IfMatch=etag)
        body = result["Body"]
        try:
            content = body.read(REPORT_LIMIT + 1)
        finally:
            body.close()
        if len(content) != obj["ContentLength"]:
            raise ReportUnavailable(502, "报告长度与对象版本不匹配，请刷新")
        atomic_bytes(path, content)
        return content


class EvidenceLibrary:
    """Keep imported data outside the live MQTT state and never unpack arbitrary paths."""

    def __init__(self, root):
        self.root = Path(root)

    def save(self, name, content):
        identity = hashlib.sha256(content).hexdigest()
        folder = self.root / identity
        if name.lower().endswith(".html"):
            atomic_bytes(folder / "report.html", content)
            index = {"id": identity, "name": name, "reports": ["report.html"], "round_epoch": None}
        else:
            index = self._ingest_archive(folder, name, identity, content)
        atomic_bytes(folder / "index.json", json.dumps(index, ensure_ascii=False).encode())
        return index

    def _ingest_archive(self, folder, name, identity, content):
        try:
            with tarfile.open(fileobj=io.BytesIO(content), mode="r:*") as tar:
                items = {}
                total = 0
                for member in tar:
                    path = PurePosixPath(member.name)
                    if (not member.isfile() or path.is_absolute()
                            or ".." in path.parts or member.name in items):
                        raise ValueError("证据包包含不支持的成员")
                    total += member.size
                    if total > ARCHIVE_LIMIT or len(items) >= 4096:
                        raise ValueError("证据包超过导入上限")
                    items[member.name] = member
                manifest = json.load(tar.extractfile(items["manifest.json"]))
                if manifest.get("format") != 1 or type(manifest.get("round_epoch")) is not int:
                    raise ValueError("证据包缺少可回放的轮次")
                files = manifest["files"]
                if (len(files) != len(items) - 1
                        or {row["path"] for row in files} != items.keys() - {"manifest.json"}):
                    raise ValueError("证据内容与清单不符")
                events, report_names = [], []
                for row in files:
                    filename = row["path"]
                    raw = tar.extractfile(items[filename]).read()
                    if len(raw) != row["bytes"] or hashlib.sha256(raw).hexdigest() != row["sha256"]:
                        raise ValueError("证据文件摘要不匹配")
                    if filename.endswith(".jsonl"):
                        for line in raw.splitlines():
                            try:
                                value = json.loads(line)
                                if (isinstance(value, dict) and
                                        value.get("round_epoch", manifest["round_epoch"]) in {0, manifest["round_epoch"]}):
                                    events.append(value)
                            except ValueError:
                                continue
                    if filename.startswith("reports/") and filename.endswith(".html"):
                        path = f"report-{len(report_names)}.html"
                        atomic_bytes(folder / path, raw)
                        report_names.append(path)
                events.sort(key=lambda event: event["time"] if isinstance(event.get("time"), (int, float)) else 0)
                atomic_bytes(folder / "source.tar.gz", content)
                atomic_bytes(folder / "timeline.json", json.dumps(events, ensure_ascii=False).encode())
                return {"id": identity, "name": name, "reports": report_names,
                        "robot_id": manifest.get("robot_id"), "round_epoch": manifest["round_epoch"],
                        "events": len(events), "missing": manifest.get("missing", [])}
        except (tarfile.TarError, OSError, ValueError, TypeError, KeyError, AttributeError):
            raise ReportUnavailable(400, "无法导入证据包，请使用板端按轮次导出的压缩包") from None

    def index(self):
        return [json.loads(path.read_text()) for path in sorted(self.root.glob("*/index.json"))]

    def file(self, identity, name):
        if not ID.fullmatch(identity) or not re.fullmatch(r"(?:report(?:-\d+)?\.html|timeline\.json)", name):
            raise ReportUnavailable(404, "文件不存在")
        path = self.root / identity / name
        if not (path.parent / "index.json").exists() or not path.is_file():
            raise ReportUnavailable(404, "文件不存在")
        return path.read_bytes()
