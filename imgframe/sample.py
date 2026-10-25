"""Deterministic synthetic test images (no external downloads needed)."""
import cv2
import numpy as np


def synthetic_image(h: int = 700, w: int = 900, seed: int = 0) -> np.ndarray:
    """Shapes, gradients and noise: gives every op real structure to work on."""
    rng = np.random.default_rng(seed)
    img = np.zeros((h, w, 3), np.uint8)
    img[:] = np.linspace(0, 120, w, dtype=np.uint8)[None, :, None]
    for _ in range(max(40, h * w // 15000)):  # keep shape density constant for big images
        color = tuple(int(c) for c in rng.integers(0, 255, 3))
        x, y = int(rng.integers(0, w)), int(rng.integers(0, h))
        if rng.random() < 0.5:
            cv2.circle(img, (x, y), int(rng.integers(10, 120)), color, -1)
        else:
            cv2.rectangle(img, (x, y), (x + int(rng.integers(20, 200)), y + int(rng.integers(20, 200))), color, -1)
    noise = rng.normal(0, 8, img.shape)
    return np.clip(img + noise, 0, 255).astype(np.uint8)
