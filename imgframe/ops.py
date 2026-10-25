"""OpenCV operations the workers can run on a tile.

Each operation declares a *halo*: how many pixels of neighbouring context it
needs around a tile so the stitched result matches processing the whole image
at once. Filters with a k x k kernel need a halo of k // 2; Canny's hysteresis
step can follow edges further, so it gets a generous margin.
"""
import os
from dataclasses import dataclass
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


@dataclass(frozen=True)
class Op:
    name: str
    fn: Callable[[np.ndarray], np.ndarray]
    halo: int
    description: str


OPS: dict[str, Op] = {
    op.name: op
    for op in (
        Op("canny", canny, 16, "Gaussian smoothing + Canny edge detection"),
        Op("gaussian_blur", gaussian_blur, 7, "15x15 Gaussian blur"),
        Op("sobel", sobel, 1, "Sobel gradient magnitude"),
        Op("sharpen", sharpen, 1, "3x3 sharpening kernel"),
        Op("grayscale", grayscale, 0, "BGR to grayscale"),
        Op("bilateral", bilateral, 4, "Bilateral filter (CPU heavy)"),
    )
}


def get_op(name: str) -> Op:
    try:
        return OPS[name]
    except KeyError:
        raise ValueError(f"unknown operation {name!r}; choose from {sorted(OPS)}") from None
