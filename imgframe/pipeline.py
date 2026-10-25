"""Pipeline stages shared by the API, the workers, the tests and the benchmark:
partition -> process tile -> reconstruct."""
import multiprocessing
import time
from concurrent.futures import ProcessPoolExecutor

import numpy as np

from .ops import get_op
from .tiling import TileSpec, decode_tile, encode_tile, reconstruct, split_image

MP_CONTEXT = multiprocessing.get_context("forkserver")


def partition(img: np.ndarray, op: str, tile_size: int) -> list[tuple[TileSpec, bytes]]:
    """Split an image into encoded halo tiles sized for `op`."""
    halo = get_op(op).halo
    return [(spec, encode_tile(tile))
            for spec, tile in split_image(img, tile_size, halo)]


def process_tile(op: str, tile_data: bytes) -> tuple[bytes, float]:
    """Worker stage: decode, run the OpenCV op, re-encode. Returns (tile bytes, compute seconds)."""
    tile = decode_tile(tile_data)
    t0 = time.perf_counter()
    out = get_op(op).fn(tile)
    compute_s = time.perf_counter() - t0
    return encode_tile(out), compute_s


def assemble(height: int, width: int, parts: list[tuple[TileSpec, bytes]]) -> np.ndarray:
    """Aggregation stage: decode tile results and stitch them into the final image."""
    return reconstruct(height, width, [(spec, decode_tile(data)) for spec, data in parts])


# --- in-memory variants used by the benchmark (no Redis / PNG overhead) -----

def _apply(args: tuple[str, np.ndarray]) -> np.ndarray:
    op, tile = args
    return get_op(op).fn(tile)


def run_sequential(img: np.ndarray, op: str) -> np.ndarray:
    return get_op(op).fn(img)


def run_tiled(img: np.ndarray, op: str, tile_size: int, workers: int,
              pool: ProcessPoolExecutor | None = None) -> np.ndarray:
    """Split into tiles, process them on `workers` processes, stitch back."""
    tiles = split_image(img, tile_size, get_op(op).halo)
    jobs = [(op, np.ascontiguousarray(t)) for _, t in tiles]
    if workers <= 1 and pool is None:
        outs = [_apply(j) for j in jobs]
    elif pool is not None:
        outs = list(pool.map(_apply, jobs, chunksize=max(1, len(jobs) // (workers * 4))))
    else:
        with ProcessPoolExecutor(workers, mp_context=MP_CONTEXT) as p:
            outs = list(p.map(_apply, jobs, chunksize=max(1, len(jobs) // (workers * 4))))
    h, w = img.shape[:2]
    return reconstruct(h, w, [(spec, out) for (spec, _), out in zip(tiles, outs)])
