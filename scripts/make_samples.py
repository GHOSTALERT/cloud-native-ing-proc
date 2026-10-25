"""Create samples/synthetic_8192.png (67 megapixels, ~140 MB, too big for git).

    python scripts/make_samples.py

The image is deterministic (fixed seed), so it is identical to the one used in the report.
"""
import sys
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from imgframe.sample import synthetic_image  # noqa: E402

if __name__ == "__main__":
    out = ROOT / "samples" / "synthetic_8192.png"
    out.parent.mkdir(exist_ok=True)
    if out.exists():
        print(f"{out.relative_to(ROOT)} already exists")
    else:
        cv2.imwrite(str(out), synthetic_image(8192, 8192), [cv2.IMWRITE_PNG_COMPRESSION, 1])
        print(f"wrote {out.relative_to(ROOT)}")
