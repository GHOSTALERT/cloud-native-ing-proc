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
avoid small tiles.

Refinements found when validating the model against measurements (the plain
formula above is kept as model="simple" for comparison):
  * workers do not scale linearly on real hardware (shared cores, SMT, turbo,
    memory bandwidth), and how badly depends on the operation (CNN inference
    is memory-bound and saturates early), so each op has its own measured
    capacity curve S_op(n) (throughput of n busy workers relative to one).
    A wave of n concurrent tiles runs at S_op(n)/n of single-worker speed:
        compute = ceil(K / n) * c_op (s + 2h)^2 * n / S_op(n)
  * where tile transfer is done by a single dispatcher (the in-memory engine:
    the parent process pickles every tile), the overhead does not parallelise:
        T_dist = T_split + max(compute, K * o(s)) + T_stitch
    In the cluster, transfers happen in the workers (S3 GET/PUT), so o(s) is
    paid per wave like compute. A job is distributed only if the best T_dist beats T_local
by PLANNER_MARGIN; a local plan becomes a single-tile job on the same path.

Backends: "engine" (in-memory process pool, the benchmark), "cluster" (S3
mode) and "cluster_redis" (all-in-Redis mode) differ only in their overheads.

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
    "capacity": {"1": 1.0},
}
SERIAL_DISPATCH = {"engine"}   # backends whose per-tile transfer runs in one process


@dataclass(frozen=True)
class Overheads:
    o0: float          # seconds per tile
    o_px: float        # seconds per pixel moved (input halo tile + output tile)
    split_px: float    # seconds per input pixel, serial
    stitch_px: float   # seconds per output pixel, serial
    serial: bool = False                              # transfers done by one dispatcher
    capacity: tuple[tuple[int, float], ...] = ((1, 1.0),)  # measured (n, S(n)) points

    def speedup(self, n: int) -> float:
        """S(n): throughput of n busy workers relative to one, interpolated."""
        pts = sorted(self.capacity)
        if n <= pts[0][0]:
            return n * pts[0][1] / pts[0][0]
        for (n0, s0), (n1, s1) in zip(pts, pts[1:]):
            if n <= n1:
                return s0 + (s1 - s0) * (n - n0) / (n1 - n0)
        return pts[-1][1] if len(pts) > 1 else float(n)  # flat beyond the measured range


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


def overheads(backend: str = "cluster", profiles: dict | None = None, op: str | None = None) -> Overheads:
    prof = profiles or load_profiles()
    p = prof.get(backend) or _FALLBACK.get(backend, _FALLBACK["cluster"])
    curves = prof.get("capacity", _FALLBACK["capacity"])
    if curves and isinstance(next(iter(curves.values())), dict):  # per-op curves
        curves = curves.get(op) or curves.get("_default") or {"1": 1.0}
    cap = tuple((int(k), float(v)) for k, v in curves.items())
    return Overheads(p["o0"], p["o_px"], p["split_px"], p["stitch_px"], backend in SERIAL_DISPATCH, cap)


def c_op(op: str, profiles: dict | None = None) -> float:
    p = profiles or load_profiles()
    return float(p["ops"].get(op, _FALLBACK["ops"].get(op, 1e-8)))


def tile_overhead(op: str, halo_pixels: float, ov: Overheads) -> float:
    """Fixed + transfer overhead of one tile (input halo tile in, scaled output out)."""
    scale = get_op(op).scale
    return ov.o0 + ov.o_px * halo_pixels * (1 + scale * scale)


def tile_cost(op: str, halo_pixels: float, c: float, ov: Overheads) -> float:
    """Predicted seconds for one tile: compute + fixed and transfer overhead."""
    return c * halo_pixels + tile_overhead(op, halo_pixels, ov)


def predict(op: str, height: int, width: int, tile_size: int, workers: int, c: float,
            ov: Overheads, model: str = "refined") -> tuple[float, int]:
    """(T_dist seconds, number of tiles) for one configuration."""
    o = get_op(op)
    specs = plan_tiles(height, width, tile_size, o.halo, o.align)
    k = len(specs)
    mean_px = sum(s.halo_pixels for s in specs) / k
    waves = math.ceil(k / workers)
    serial = ov.split_px * height * width + ov.stitch_px * height * width * o.scale ** 2
    if model == "simple":
        return serial + waves * tile_cost(op, mean_px, c, ov), k
    n = min(workers, k)
    compute = waves * c * mean_px * n / ov.speedup(n)   # each wave: n tiles at contended speed
    over = tile_overhead(op, mean_px, ov)
    if ov.serial:
        return serial + max(compute + over, k * over), k
    return serial + compute + waves * over, k


def predict_local(op: str, height: int, width: int, c: float, ov: Overheads, on_cluster: bool) -> float:
    """In-process (engine): just the compute. On the cluster a local plan is a single-tile
    job, so it still pays the serial upload/stitch steps and moves the whole image once."""
    if not on_cluster:
        return c * height * width
    o = get_op(op)
    return (ov.split_px * height * width + tile_cost(op, height * width, c, ov)
            + ov.stitch_px * height * width * o.scale ** 2)


def plan(op: str, height: int, width: int, workers_available: int, *, backend: str = "cluster",
         c: float | None = None, profiles: dict | None = None, margin: float = 0.2,
         tile_sizes: tuple[int, ...] = TILE_SIZES, deadline_s: float | None = None) -> Plan:
    """Choose local vs distributed, tile size and worker count.

    Latency mode (default): the fastest plan, using the fewest workers that reach it.
    Cost mode (deadline_s): among plans meeting the deadline, fewest worker-seconds."""
    profiles = profiles or load_profiles()
    c = c if c is not None else c_op(op, profiles)
    ov = overheads(backend, profiles, op)
    n_max = max(1, workers_available)
    t_local = predict_local(op, height, width, c, ov, on_cluster=backend.startswith("cluster"))

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
