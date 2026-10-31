"""OpenCV operations the workers can run on a tile.

Each operation declares a *halo*: how many pixels of neighbouring context it
needs around a tile so the stitched result matches processing the whole image
at once. Filters with a k x k kernel need a halo of k // 2; Canny's hysteresis
step can follow edges further, so it gets a generous margin.

Neural operations (HED edges, 4x super-resolution) have a receptive field far
larger than any practical halo, so their halo is *calibrated*: the smallest
halo whose seam error stays under a threshold, measured by
scripts/calibrate_halo.py and stored in imgframe/calibration.json.
"""
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

# One OpenCV thread per process: parallelism comes from the framework (tiles x
# workers), and it keeps the sequential baseline genuinely sequential.
cv2.setNumThreads(int(os.environ.get("OPENCV_THREADS", "1")))


def _gray(img: np.ndarray) -> np.ndarray:
    return img if img.ndim == 2 else cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


def grayscale(img: np.ndarray) -> np.ndarray:
    return _gray(img)


def gaussian_blur(img: np.ndarray) -> np.ndarray:
    return cv2.GaussianBlur(img, (15, 15), 0)


def sobel(img: np.ndarray) -> np.ndarray:
    g = _gray(img)
    gx = cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3)
    return cv2.convertScaleAbs(cv2.magnitude(gx, gy))


def sharpen(img: np.ndarray) -> np.ndarray:
    kernel = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]], dtype=np.float32)
    return cv2.filter2D(img, -1, kernel)


def canny(img: np.ndarray) -> np.ndarray:
    blurred = cv2.GaussianBlur(_gray(img), (5, 5), 1.4)
    return cv2.Canny(blurred, 50, 150)


def bilateral(img: np.ndarray) -> np.ndarray:
    # Edge-preserving smoothing; deliberately CPU-heavy, useful for benchmarks.
    return cv2.bilateralFilter(img, 9, 75, 75)


_HED_MEAN = np.array([104.00698793, 116.66876762, 122.67891434], np.float32)


def _bgr(img: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR) if img.ndim == 2 else img


def hed(img: np.ndarray) -> np.ndarray:
    """Holistically-Nested Edge Detection (Xie & Tu 2015): learned replacement for Canny.
    Returns the fused edge probability as a single-channel uint8 map."""
    from .models import session

    x = (_bgr(img).astype(np.float32) - _HED_MEAN).transpose(2, 0, 1)[None]
    prob = session("hed").run(None, {"input": np.ascontiguousarray(x)})[0][0, 0]
    return np.clip(prob * 255.0 + 0.5, 0, 255).astype(np.uint8)


def superres(img: np.ndarray) -> np.ndarray:
    """4x super-resolution with Real-ESRGAN general v3 (Wang et al. 2021)."""
    from .models import session

    x = (_bgr(img)[..., ::-1].astype(np.float32) / 255.0).transpose(2, 0, 1)[None]
    y = session("superres").run(None, {"input": np.ascontiguousarray(x)})[0][0]
    return np.clip(y.transpose(1, 2, 0)[..., ::-1] * 255.0 + 0.5, 0, 255).astype(np.uint8)


@dataclass(frozen=True)
class Op:
    name: str
    fn: Callable[[np.ndarray], np.ndarray]
    halo: int
    description: str
    scale: int = 1          # output size / input size (4 for super-resolution)
    align: int = 1          # halo origin snapped to this stride (CNN pooling grid)
    kind: str = "classical"
    theoretical_halo: int = 0  # receptive-field radius (exact but expensive halo)


_CALIBRATION = Path(__file__).with_name("calibration.json")


def _calibrated(name: str, default: int) -> int:
    try:
        return int(json.loads(_CALIBRATION.read_text())[name]["halo"])
    except (OSError, KeyError, ValueError):
        return default


OPS: dict[str, Op] = {
    op.name: op
    for op in (
        Op("canny", canny, 16, "Gaussian smoothing + Canny edge detection"),
        Op("gaussian_blur", gaussian_blur, 7, "15x15 Gaussian blur"),
        Op("sobel", sobel, 1, "Sobel gradient magnitude"),
        Op("sharpen", sharpen, 1, "3x3 sharpening kernel"),
        Op("grayscale", grayscale, 0, "BGR to grayscale"),
        Op("bilateral", bilateral, 4, "Bilateral filter (CPU heavy)"),
        # VGG16 conv5_3 receptive field is 196 px -> exact halo ~98 px; pooling stride 16.
        Op("hed", hed, _calibrated("hed", 32), "HED neural edge detection (VGG16, ONNX)",
           align=16, kind="neural", theoretical_halo=98),
        Op("superres", superres, _calibrated("superres", 10), "Real-ESRGAN 4x super-resolution (ONNX)",
           scale=4, kind="neural", theoretical_halo=34),  # 34 stacked 3x3 convs
    )
}


def get_op(name: str) -> Op:
    try:
        return OPS[name]
    except KeyError:
        raise ValueError(f"unknown operation {name!r}; choose from {sorted(OPS)}") from None
