"""Partitioning a large image into tiles and stitching processed tiles back.

Every tile has a *core* region (the pixels it owns in the final image) and a
*halo* region (core plus `halo` px of surrounding context, clipped to the image
bounds). Workers process the halo region; reconstruction keeps only the core.

Two extensions for neural operations:
  * scale: an operation may change the output size (4x super-resolution), so
    cropping and stitching multiply every offset by the op's scale factor.
  * align: a CNN with pooling stride 16 only gives the same output for the same
    pixel if the tile starts on the same pooling grid, so halo origins are
    snapped down to a multiple of `align`.
"""
from dataclasses import asdict, dataclass
from typing import Iterable, Iterator

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

    @property
    def halo_pixels(self) -> int:
        return (self.hy1 - self.hy0) * (self.hx1 - self.hx0)

    def crop_core(self, processed: np.ndarray, scale: int = 1) -> np.ndarray:
        oy, ox = (self.y0 - self.hy0) * scale, (self.x0 - self.hx0) * scale
        return processed[oy : oy + (self.y1 - self.y0) * scale, ox : ox + (self.x1 - self.x0) * scale]


def plan_tiles(height: int, width: int, tile_size: int, halo: int, align: int = 1) -> list[TileSpec]:
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
                    hy0=max(0, (y0 - halo) // align * align), hy1=min(height, y1 + halo),
                    hx0=max(0, (x0 - halo) // align * align), hx1=min(width, x1 + halo),
                )
            )
    return specs


def split_image(img: np.ndarray, tile_size: int, halo: int,
                align: int = 1) -> list[tuple[TileSpec, np.ndarray]]:
    h, w = img.shape[:2]
    return [(s, img[s.hy0 : s.hy1, s.hx0 : s.hx1]) for s in plan_tiles(h, w, tile_size, halo, align)]


def reconstruct(height: int, width: int, parts: list[tuple[TileSpec, np.ndarray]],
                scale: int = 1) -> np.ndarray:
    """Stitch processed halo tiles into the full output (height*scale x width*scale)."""
    if not parts:
        raise ValueError("no tiles to reconstruct")
    sample = parts[0][1]
    out = np.zeros((height * scale, width * scale) + sample.shape[2:], dtype=sample.dtype)
    for spec, processed in parts:
        out[spec.y0 * scale : spec.y1 * scale, spec.x0 * scale : spec.x1 * scale] = \
            spec.crop_core(processed, scale)
    return out


def stream_tiled_tiff(path: str, height: int, width: int, tile_size: int, scale: int,
                      parts: Iterable[tuple[TileSpec, np.ndarray]], preview_side: int = 1200
                      ) -> np.ndarray:
    """Write the stitched output as a tiled BigTIFF without ever holding it whole.

    `parts` must arrive in tile-index (row-major) order; each core becomes exactly
    one TIFF tile of (tile_size*scale)^2 px, edge tiles padded. Peak memory is one
    tile. Returns a downscaled preview built on the way."""
    import tifffile

    t = tile_size * scale
    if t % 16:
        raise ValueError("tile_size * scale must be a multiple of 16 for tiled TIFF")
    H, W = height * scale, width * scale
    f = min(1.0, preview_side / max(H, W))
    preview = None

    def tiles() -> Iterator[np.ndarray]:
        nonlocal preview
        for spec, processed in parts:
            core = spec.crop_core(processed, scale)
            if preview is None:
                preview = np.zeros((max(1, round(H * f)), max(1, round(W * f))) + core.shape[2:], core.dtype)
            py0, px0 = round(spec.y0 * scale * f), round(spec.x0 * scale * f)
            py1, px1 = round(spec.y1 * scale * f), round(spec.x1 * scale * f)
            if py1 > py0 and px1 > px0:
                preview[py0:py1, px0:px1] = cv2.resize(core, (px1 - px0, py1 - py0),
                                                       interpolation=cv2.INTER_AREA)
            if core.shape[:2] != (t, t):
                pad = np.zeros((t, t) + core.shape[2:], core.dtype)
                pad[: core.shape[0], : core.shape[1]] = core
                core = pad
            yield core[..., ::-1] if core.ndim == 3 else core  # TIFF stores RGB, OpenCV uses BGR

    it = tiles()
    head = next(it)

    def chain():
        yield head
        yield from it

    shape = (H, W) + head.shape[2:]
    tifffile.imwrite(path, chain(), shape=shape, dtype=head.dtype, tile=(t, t), bigtiff=True,
                     photometric="rgb" if head.ndim == 3 else "minisblack", compression=None)
    return preview


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
    if img is not None and img.dtype != np.uint8:  # 16-bit PNG/TIFF -> 8-bit
        img = (img / 257).astype(np.uint8) if img.dtype == np.uint16 else img.astype(np.uint8)
    if img is None:
        raise ValueError("could not decode image")
    if img.ndim == 3 and img.shape[2] == 4:
        img = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
    return img
