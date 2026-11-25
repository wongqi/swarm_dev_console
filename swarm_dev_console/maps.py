"""Read display maps from the PC only; board map paths are never rewritten."""

import io
import math
from pathlib import Path, PurePosixPath

from PIL import Image, UnidentifiedImageError
import yaml


class MapLibrary:
    def __init__(self, root):
        self.root = Path(root).resolve()

    def bundle(self, package):
        if not isinstance(package, str) or not package.startswith("/") or "\x00" in package:
            raise ValueError("map_package 需要设备上的地图绝对路径")
        parts = PurePosixPath(package)
        if ".." in parts.parts or not parts.name:
            raise ValueError("地图路径无效")
        directory = (self.root / parts.name).resolve()
        if not directory.is_relative_to(self.root) or directory == self.root:
            raise ValueError("地图目录超出本机 maps 范围")
        if not directory.is_dir():
            raise ValueError(f"本机 maps 中没有 {parts.name}，请放入同名地图目录")
        return directory

    def metadata(self, package):
        directory = self.bundle(package)
        maps = []
        for path in sorted([*directory.glob("*.yaml"), *directory.glob("*.yml")]):
            info, _image = self.read(directory, path.name)
            maps.append(info)
        if not maps:
            raise ValueError("地图目录中没有 ROS 地图 YAML")
        return {"package": directory.name, "maps": maps}

    @staticmethod
    def read(directory, filename):
        path = (directory / filename).resolve()
        if (path.parent != directory or path.suffix not in {".yaml", ".yml"}
                or not path.is_file()):
            raise ValueError("地图 YAML 不存在或路径无效")
        try:
            data = yaml.safe_load(path.read_text())
            resolution, origin = data["resolution"], data["origin"]
            if (type(resolution) not in (int, float) or not math.isfinite(resolution)
                    or resolution <= 0 or not isinstance(origin, list) or len(origin) != 3
                    or any(type(n) not in (int, float) or not math.isfinite(n) for n in origin)):
                raise ValueError("分辨率或原点无效")
            image = (directory / data["image"]).resolve()
            if not image.is_relative_to(directory):
                raise ValueError("底图文件超出地图目录")
            with Image.open(image) as raster:
                width, height = raster.size
            return {"frame_id": path.stem, "file": filename, "resolution": resolution,
                    "origin": origin, "width": width, "height": height}, image
        except (KeyError, TypeError, OSError, yaml.YAMLError, UnidentifiedImageError,
                Image.DecompressionBombError) as error:
            raise ValueError(f"地图 {filename} 无法读取：{type(error).__name__}") from error

    def image(self, package, filename):
        _info, path = self.read(self.bundle(package), filename)
        try:
            with Image.open(path) as raster:
                raster.thumbnail((2048, 2048))
                buffer = io.BytesIO()
                raster.convert("RGB").save(buffer, format="PNG")
                return buffer.getvalue()
        except (OSError, Image.DecompressionBombError) as error:
            raise ValueError("底图无法解码") from error
