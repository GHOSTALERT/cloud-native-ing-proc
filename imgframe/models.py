"""Neural models for the learned operations, loaded once per process.

Weights are fetched by scripts/fetch_models.sh (HED is converted from the
official Caffe model by scripts/convert_hed.py). ONNX Runtime runs with
intra_op_num_threads = OPENCV_THREADS (1 by default) for the same reason
OpenCV does: parallelism comes from tiles x workers, not from inside an op.
"""
import os
import threading
from pathlib import Path

from .config import settings

MODELS = {
    "hed": "hed.onnx",
    "superres": "realesr-general-x4v3.onnx",
}

_sessions: dict = {}
_lock = threading.Lock()


def model_path(name: str) -> Path:
    return Path(settings.model_dir) / MODELS[name]


def available(name: str) -> bool:
    return model_path(name).exists()


def session(name: str):
    """Cached onnxruntime.InferenceSession for `name` (thread-safe to run)."""
    sess = _sessions.get(name)
    if sess is not None:
        return sess
    with _lock:
        if name not in _sessions:
            import onnxruntime as ort

            path = model_path(name)
            if not path.exists():
                raise FileNotFoundError(f"model {path} missing; run scripts/fetch_models.sh")
            opts = ort.SessionOptions()
            opts.intra_op_num_threads = int(os.environ.get("OPENCV_THREADS", "1"))
            opts.inter_op_num_threads = 1
            opts.log_severity_level = 3
            _sessions[name] = ort.InferenceSession(str(path), opts, providers=["CPUExecutionProvider"])
        return _sessions[name]
