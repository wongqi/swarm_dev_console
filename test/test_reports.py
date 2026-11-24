"""Local report cache and imported evidence validation."""

import io
import tarfile

import pytest
from swarm_dev_console.reports import EvidenceLibrary, ReportReader, ReportUnavailable


def test_archive_validates_member_paths_before_import(tmp_path):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        archive.addfile(tarfile.TarInfo("../reports/demo.html"), io.BytesIO())
    library = EvidenceLibrary(tmp_path / "archives")
    with pytest.raises(ReportUnavailable, match="无法导入"):
        library.save("invalid.tar.gz", buffer.getvalue())
    assert not library.index()


def test_reader_refreshes_cache_when_minio_etag_changes(tmp_path):
    class Storage:
        version, gets = "one", 0

        def head_object(self, Bucket, Key):
            assert (Bucket, Key) == ("test", "tmp/test/demo.html")
            return {"ContentLength": len(self.version), "ETag": self.version}

        def get_object(self, *, Bucket, Key, IfMatch):
            assert IfMatch == self.version
            self.gets += 1
            return {"Body": io.BytesIO(self.version.encode())}

    storage = Storage()
    reader = ReportReader(tmp_path, "http://127.0.0.1", "test", "tmp/test", client=storage)
    assert reader.read("tmp/test/demo.html") == reader.read("tmp/test/demo.html") == b"one"
    assert storage.gets == 1
    storage.version = "two"
    assert reader.read("tmp/test/demo.html") == b"two" and storage.gets == 2
    with pytest.raises(ReportUnavailable) as error:
        reader.read("outside/demo.html")
    assert error.value.status == 403
