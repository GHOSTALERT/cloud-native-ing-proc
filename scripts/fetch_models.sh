#!/usr/bin/env bash
# Download the neural-operation weights into models/ and convert HED to ONNX.
#   bash scripts/fetch_models.sh          (needs: curl, the project .venv with onnx installed)
# HED: official BSDS-pretrained Caffe model (Xie & Tu, ICCV 2015). OpenCV 5 has no Caffe
# importer, so scripts/convert_hed.py rebuilds it as models/hed.onnx.
# Super-resolution: Real-ESRGAN realesr-general-x4v3 (SRVGGNetCompact, 1.2M params), ONNX export.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="${PYTHON:-$ROOT/.venv/bin/python}"
mkdir -p "$ROOT/models"
cd "$ROOT/models"

get() {  # url file sha256
    if [ -f "$2" ] && echo "$3  $2" | sha256sum -c --status -; then echo "ok   $2"; return; fi
    echo "get  $2"
    curl -fL --retry 3 -o "$2" "$1"
    echo "$3  $2" | sha256sum -c -
}

get https://raw.githubusercontent.com/s9xie/hed/master/examples/hed/deploy.prototxt \
    hed_deploy.prototxt 378a9246383da889cf8e0290c47554d75dcf9c5b6bbabd8ab6c481c34aa12b8a
get https://vcl.ucsd.edu/hed/hed_pretrained_bsds.caffemodel \
    hed_pretrained_bsds.caffemodel 4b6937684bce9be1ef5163c78ec812dff9a23653bfbb451925210a64ecfaaac7
get https://huggingface.co/jonathanst29/tinier-upscale-models/resolve/main/realesr-general-x4v3.onnx \
    realesr-general-x4v3.onnx 924ebad6532777303582d4ce7811849b88869231a3ae7093e0f21200249df8d5

[ -f hed.onnx ] || "$PY" "$ROOT/scripts/convert_hed.py"
ls -lh "$ROOT/models"
