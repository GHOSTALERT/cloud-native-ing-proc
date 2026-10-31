"""Cost-aware planner: decide per job whether and how to distribute an operation.

For an H x W image, tile size s, halo h and n workers (K tiles):

    T_dist(s, n) = T_split + ceil(K / n) * (c_op * (s + 2h)^2 + o(s)) + T_stitch
    T_local      = c_op * H * W  (+ one tile's overhead when it runs on the cluster)

  c_op      compute seconds per input pixel of the operation
  o(s)      fixed per-tile overhead o0 plus a transfer term o_px per pixel moved
            (encode/decode, Redis or S3 round trip, claim latency)
  T_split   serial partitioning, proportional to H * W
  T_stitch  serial reconstruction, proportional to the output pixels

The (s + 2h)^2 term charges for halo recomputation, so big-halo operations
avoid small tiles. A job is distributed only if the best T_dist beats T_local
by PLANNER_MARGIN; a local plan becomes a single-tile job on the same path.

Profiles: imgframe/profiles.json is written offline by scripts/profile_ops.py
(c_op fitted by regression on tile pixels; overheads from empty round trips).
Workers refine c_op online with an exponential moving average stored in Redis
under imgframe:profile:<op>.
"""
import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .ops import get_op
from .tiling import plan_tiles

TILE_SIZES = (256, 512, 1024, 2048)
PROFILE_FILE = Path(__file__).with_name("profiles.json")

# Used only until scripts/profile_ops.py has written profiles.json. Derived from the
# original benchmark (8192^2, 512 px tiles, results.csv): sequential time / pixels.
_FALLBACK = {
    "ops": {"bilateral": 7.9e-8, "gaussian_blur": 4.3e-9, "canny": 3.1e-9, "sobel": 4e-9,
            "sharpen": 3e-9, "grayscale": 1e-9, "hed": 4.6e-6, "superres": 2.1e-5},
    "engine": {"o0": 1e-3, "o_px": 1e-8, "split_px": 3e-9, "stitch_px": 3e-9},
    "cluster": {"o0": 4e-3, "o_px": 2e-8, "split_px": 2.2e-8, "stitch_px": 8e-9},
}


@dataclass(frozen=True)
class Overheads:
    o0: float          # seconds per tile
    o_px: float        # seconds per pixel moved (input halo tile + output tile)
    split_px: float    # seconds per input pixel, serial
    stitch_px: float   # seconds per output pixel, serial


@dataclass
class Plan:
    op: str
    mode: str                  # "local" or "distributed"
    tile_size: int
    workers: int
    tiles: int
    predicted_s: float
    t_local_s: float
    t_dist_s: float            # best distributed prediction
    reason: str = ""
    candidates: list = field(default_factory=list, repr=False)

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("candidates")
        return d


def load_profiles(path: Path = PROFILE_FILE) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return _FALLBACK


def overheads(backend: str = "cluster", profiles: dict | None = None) -> Overheads:
    p = (profiles or load_profiles()).get(backend) or _FALLBACK[backend]
    return Overheads(p["o0"], p["o_px"], p["split_px"], p["stitch_px"])


def c_op(op: str, profiles: dict | None = None) -> float:
    p = profiles or load_profiles()
    return float(p["ops"].get(op, _FALLBACK["ops"].get(op, 1e-8)))


def tile_cost(op: str, halo_pixels: int, c: float, ov: Overheads) -> float:
    """Predicted seconds for one tile: compute + fixed and transfer overhead."""
    scale = get_op(op).scale
    return c * halo_pixels + ov.o0 + ov.o_px * halo_pixels * (1 + scale * scale)


def predict(op: str, height: int, width: int, tile_size: int, workers: int, c: float,
            ov: Overheads) -> tuple[float, int]:
    """(T_dist seconds, number of tiles) for one configuration."""
    o = get_op(op)
    specs = plan_tiles(height, width, tile_size, o.halo, o.align)
    k = len(specs)
    mean_px = sum(s.halo_pixels for s in specs) / k
    waves = math.ceil(k / workers)
    t = (ov.split_px * height * width
         + waves * tile_cost(op, mean_px, c, ov)
         + ov.stitch_px * height * width * o.scale ** 2)
    return t, k


def predict_local(op: str, height: int, width: int, c: float, ov: Overheads, on_cluster: bool) -> float:
    t = c * height * width
    if on_cluster:  # the whole image still travels once as a single tile
        t += tile_cost(op, height * width, c, ov) - c * height * width
    return t


def plan(op: str, height: int, width: int, workers_available: int, *, backend: str = "cluster",
         c: float | None = None, profiles: dict | None = None, margin: float = 0.2,
         tile_sizes: tuple[int, ...] = TILE_SIZES, deadline_s: float | None = None) -> Plan:
    """Choose local vs distributed, tile size and worker count.

    Latency mode (default): the fastest plan, using the fewest workers that reach it.
    Cost mode (deadline_s): among plans meeting the deadline, fewest worker-seconds."""
    profiles = profiles or load_profiles()
    c = c if c is not None else c_op(op, profiles)
    ov = overheads(backend, profiles)
    n_max = max(1, workers_available)
    t_local = predict_local(op, height, width, c, ov, on_cluster=backend == "cluster")

    cands = []
    for s in tile_sizes:
        if s >= max(height, width) and s != tile_sizes[0]:
            continue  # larger tiles than the image are the local plan
        for n in range(1, n_max + 1):
            t, k = predict(op, height, width, s, n, c, ov)
            cands.append((t, n, s, k))
            if n >= k:
                break  # more workers than tiles cannot help
    best_t = min(t for t, *_ in cands)

    if deadline_s is not None:
        ok = [x for x in cands if x[0] <= deadline_s]
        if t_local <= deadline_s:
            return Plan(op, "local", max(height, width), 1, 1, t_local, t_local, best_t,
                        "local meets the deadline with one worker", cands)
        if ok:
            t, n, s, k = min(ok, key=lambda x: (x[0] * x[1], x[0]))
            return Plan(op, "distributed", s, n, k, t, t_local, best_t,
                        "fewest worker-seconds meeting the deadline", cands)

    # fastest plan; among near-ties (within 2%) prefer fewer workers, then bigger tiles
    t, n, s, k = min((x for x in cands if x[0] <= best_t * 1.02), key=lambda x: (x[1], -x[2]))
    if t < (1 - margin) * t_local:
        return Plan(op, "distributed", s, n, k, t, t_local, best_t,
                    f"distributed is {t_local / t:.2f}x faster than local", cands)
    return Plan(op, "local", max(height, width), 1, 1, t_local, t_local, best_t,
                f"best distributed plan ({best_t:.3f}s) is not {margin:.0%} faster than local "
                f"({t_local:.3f}s)", cands)
