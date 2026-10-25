"""Partitioning a large image into tiles and stitching processed tiles back.

Every tile has a *core* region (the pixels it owns in the final image) and a
*halo* region (core plus `halo` px of surrounding context, clipped to the image
bounds). Workers process the halo region; reconstruction keeps only the core.
"""
from dataclasses import asdict, dataclass

import cv2
import numpy as np


@dataclass(frozen=True)
class TileSpec:
    index: int
    y0: int  # core region [y0:y1, x0:x1]
    y1: int
    x0: int
    x1: int
    hy0: int  # halo region [hy0:hy1, hx0:hx1]
    hy1: int
    hx0: int
    hx1: int

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "TileSpec":
        return cls(**{k: int(v) for k, v in d.items()})

    def crop_core(self, processed: np.ndarray) -> np.ndarray:
        oy, ox = self.y0 - self.hy0, self.x0 - self.hx0
        return processed[oy : oy + (self.y1 - self.y0), ox : ox + (self.x1 - self.x0)]


def plan_tiles(height: int, width: int, tile_size: int, halo: int) -> list[TileSpec]:
    if tile_size <= 0:
        raise ValueError("tile_size must be positive")
    specs = []
    for y0 in range(0, height, tile_size):
        for x0 in range(0, width, tile_size):
            y1, x1 = min(y0 + tile_size, height), min(x0 + tile_size, width)
            specs.append(
                TileSpec(
                    index=len(specs),
                    y0=y0, y1=y1, x0=x0, x1=x1,
                    hy0=max(0, y0 - halo), hy1=min(height, y1 + halo),
                    hx0=max(0, x0 - halo), hx1=min(width, x1 + halo),
                )
            )
    return specs


def split_image(img: np.ndarray, tile_size: int, halo: int) -> list[tuple[TileSpec, np.ndarray]]:
    h, w = img.shape[:2]
    return [(s, img[s.hy0 : s.hy1, s.hx0 : s.hx1]) for s in plan_tiles(h, w, tile_size, halo)]


def reconstruct(height: int, width: int, parts: list[tuple[TileSpec, np.ndarray]]) -> np.ndarray:
    """Stitch processed halo tiles into a full image of size height x width."""
    if not parts:
        raise ValueError("no tiles to reconstruct")
    sample = parts[0][1]
    out = np.zeros((height, width) + sample.shape[2:], dtype=sample.dtype)
    for spec, processed in parts:
        out[spec.y0 : spec.y1, spec.x0 : spec.x1] = spec.crop_core(processed)
    return out


_RAW_MAGIC = b"IMGT"


def encode_tile(img: np.ndarray) -> bytes:
    """Fast internal tile format: 16-byte header (magic, h, w, channels) + raw uint8 pixels.
    Tiles only travel between API, Redis and workers, so PNG compression is wasted CPU."""
    if img.dtype != np.uint8:
        raise ValueError("tiles must be uint8")
    h, w = img.shape[:2]
    c = 1 if img.ndim == 2 else img.shape[2]
    return _RAW_MAGIC + np.array([h, w, c], "<u4").tobytes() + np.ascontiguousarray(img).tobytes()


def decode_tile(data: bytes) -> np.ndarray:
    if data[:4] != _RAW_MAGIC:
        return decode_image(data)  # also accept PNG/JPEG tiles
    h, w, c = np.frombuffer(data, "<u4", 3, 4)
    arr = np.frombuffer(data, np.uint8, offset=16).reshape((h, w) if c == 1 else (h, w, c))
    return arr.copy()


def encode_png(img: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".png", img, [cv2.IMWRITE_PNG_COMPRESSION, 1])
    if not ok:
        raise ValueError("PNG encoding failed")
    return buf.tobytes()


def decode_image(data: bytes) -> np.ndarray:
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_UNCHANGED)
    if img is None:
        raise ValueError("could not decode image")
    if img.ndim == 3 and img.shape[2] == 4:
        img = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
    return img
