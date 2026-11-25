"""PC-only map matching, raster conversion, and path boundaries."""

import io
import math

from PIL import Image
import pytest
import yaml

from swarm_dev_console.maps import MapLibrary


def raster(root, frame="map_000", **fields):
    root.mkdir(parents=True, exist_ok=True)
    Image.new("L", (60, 40), 127).save(root / (frame + ".pgm"))
    (root / (frame + ".yaml")).write_text(yaml.safe_dump({
        "image": frame + ".pgm", "resolution": 0.1, "origin": [1, -2, math.pi / 2], **fields,
    }))


def test_map_package_matches_local_basename_with_all_submaps(tmp_path):
    raster(tmp_path / "outdoor")
    raster(tmp_path / "outdoor", "map_001")
    library = MapLibrary(tmp_path)
    metadata = library.metadata("/home/cat/Workspace/Maps/outdoor/")
    assert [item["frame_id"] for item in metadata["maps"]] == ["map_000", "map_001"]
    assert metadata["maps"][0]["origin"] == [1, -2, math.pi / 2]
    assert metadata["maps"][0]["width"] == 60
    with Image.open(io.BytesIO(library.image("/board/outdoor", "map_000.yaml"))) as image:
        assert image.format == "PNG" and image.size == (60, 40)


def test_display_thumbnail_keeps_original_coordinate_dimensions(tmp_path):
    raster(tmp_path / "large")
    Image.new("L", (3000, 1000), 255).save(tmp_path / "large" / "map_000.pgm")
    library = MapLibrary(tmp_path)
    assert library.metadata("/board/large")["maps"][0]["width"] == 3000
    with Image.open(io.BytesIO(library.image("/board/large", "map_000.yaml"))) as image:
        assert image.width == 2048 and image.height < 1000


@pytest.mark.parametrize("path", ["outdoor", "/board/../outdoor", "/", "/missing"])
def test_invalid_or_unavailable_local_package(tmp_path, path):
    with pytest.raises(ValueError):
        MapLibrary(tmp_path).metadata(path)


def test_map_never_reads_outside_local_library(tmp_path):
    root = tmp_path / "maps"
    raster(root / "good")
    raster(tmp_path / "outside")
    (root / "escape").symlink_to(tmp_path / "outside", target_is_directory=True)
    library = MapLibrary(root)
    with pytest.raises(ValueError):
        library.metadata("/board/escape")
    with pytest.raises(ValueError):
        library.image("/board/good", "../../outside/map_000.yaml")
    raster(root / "good", image="../../outside/map_000.pgm")
    with pytest.raises(ValueError, match="底图文件超出"):
        library.metadata("/board/good")


@pytest.mark.parametrize("fields", [{"resolution": 0}, {"origin": [0, 0, float("nan")]},
                                   {"origin": [0, 0]}, {"image": "missing.png"}])
def test_invalid_map_metadata(tmp_path, fields):
    raster(tmp_path / "map", **fields)
    with pytest.raises(ValueError):
        MapLibrary(tmp_path).metadata("/board/map")
