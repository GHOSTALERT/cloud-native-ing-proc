"""Convert the official HED Caffe weights (Xie & Tu, ICCV 2015) to ONNX.

OpenCV 5 removed its Caffe importer, so the pretrained caffemodel is decoded
here directly from the protobuf wire format (no Caffe or caffe.proto needed)
and the network is rebuilt as an ONNX graph:

  VGG16 conv1..conv5 -> five 1x1 side outputs -> bilinear upsample to the
  input size -> concat -> 1x1 fusion -> sigmoid

As in the widely used pytorch-hed port, conv1_1 uses pad 1 and the side
outputs are resized bilinearly instead of Caffe's pad-35 + deconvolution +
crop trick. The graph stays fully convolutional, which is what tiling needs.

    python scripts/convert_hed.py            # models/hed_pretrained_bsds.caffemodel -> models/hed.onnx
"""
import argparse
import struct
from pathlib import Path

import numpy as np
import onnx
from onnx import TensorProto, helper, numpy_helper

ROOT = Path(__file__).resolve().parent.parent


# ------------------------------------------------------- protobuf decoding
def _varint(buf: bytes, i: int) -> tuple[int, int]:
    shift = result = 0
    while True:
        b = buf[i]
        i += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, i
        shift += 7


def _fields(buf: bytes):
    """Yield (field_number, wire_type, value) for one protobuf message."""
    i = 0
    while i < len(buf):
        key, i = _varint(buf, i)
        field, wt = key >> 3, key & 7
        if wt == 0:
            v, i = _varint(buf, i)
        elif wt == 1:
            v, i = buf[i:i + 8], i + 8
        elif wt == 2:
            n, i = _varint(buf, i)
            v, i = buf[i:i + n], i + n
        elif wt == 5:
            v, i = buf[i:i + 4], i + 4
        else:
            raise ValueError(f"unsupported wire type {wt}")
        yield field, wt, v


def _blob(buf: bytes) -> np.ndarray:
    legacy = {1: 1, 2: 1, 3: 1, 4: 1}
    data: list[float] = []
    shape: list[int] = []
    for f, wt, v in _fields(buf):
        if f in legacy and wt == 0:
            legacy[f] = v
        elif f == 5:  # data: packed or repeated float
            data.extend(struct.unpack(f"<{len(v) // 4}f", v) if wt == 2 else struct.unpack("<f", v))
        elif f == 7:  # BlobShape
            for sf, swt, sv in _fields(v):
                if sf == 1 and swt == 2:
                    j = 0
                    while j < len(sv):
                        d, j = _varint(sv, j)
                        shape.append(d)
                elif sf == 1:
                    shape.append(sv)
    shape = shape or [legacy[1], legacy[2], legacy[3], legacy[4]]
    return np.asarray(data, np.float32).reshape(shape)


def read_caffemodel(path: Path) -> dict[str, list[np.ndarray]]:
    """Layer name -> list of parameter blobs (weights, bias)."""
    layers = {}
    for f, _, v in _fields(path.read_bytes()):
        if f not in (100, 2):  # `layer` (current) or `layers` (V1)
            continue
        name_f, blob_f = (1, 7) if f == 100 else (4, 6)
        name, blobs = None, []
        for lf, _, lv in _fields(v):
            if lf == name_f:
                name = lv.decode()
            elif lf == blob_f:
                blobs.append(_blob(lv))
        if name and blobs:
            layers[name] = blobs
    return layers


# --------------------------------------------------------------- ONNX graph
VGG = [["conv1_1", "conv1_2"], ["conv2_1", "conv2_2"], ["conv3_1", "conv3_2", "conv3_3"],
       ["conv4_1", "conv4_2", "conv4_3"], ["conv5_1", "conv5_2", "conv5_3"]]


def build(layers: dict[str, list[np.ndarray]]) -> onnx.ModelProto:
    nodes, inits = [], []

    def conv(name: str, x: str, pad: int) -> str:
        w, b = layers[name]
        inits.extend([numpy_helper.from_array(w, f"{name}.w"), numpy_helper.from_array(b.reshape(-1), f"{name}.b")])
        k = w.shape[-1]
        nodes.append(helper.make_node("Conv", [x, f"{name}.w", f"{name}.b"], [name],
                                      kernel_shape=[k, k], pads=[pad] * 4))
        return name

    shape = "in_shape"
    nodes.append(helper.make_node("Shape", ["input"], [shape]))
    inits += [numpy_helper.from_array(np.array([2], np.int64), "s2"),
              numpy_helper.from_array(np.array([4], np.int64), "s4"),
              numpy_helper.from_array(np.array([1, 1], np.int64), "nc")]
    nodes.append(helper.make_node("Slice", [shape, "s2", "s4"], ["hw"]))
    nodes.append(helper.make_node("Concat", ["nc", "hw"], ["out_size"], axis=0))

    x, sides = "input", []
    for stage, convs in enumerate(VGG, 1):
        if stage > 1:
            nodes.append(helper.make_node("MaxPool", [x], [f"pool{stage - 1}"], kernel_shape=[2, 2],
                                          strides=[2, 2], ceil_mode=1))
            x = f"pool{stage - 1}"
        for c in convs:
            y = conv(c, x, 1)
            nodes.append(helper.make_node("Relu", [y], [f"{c}_r"]))
            x = f"{c}_r"
        side = conv(f"score-dsn{stage}", x, 0)
        if stage > 1:
            nodes.append(helper.make_node("Resize", [side, "", "", "out_size"], [f"{side}-up"],
                                          mode="linear", coordinate_transformation_mode="half_pixel"))
            side = f"{side}-up"
        sides.append(side)
    nodes.append(helper.make_node("Concat", sides, ["concat"], axis=1))
    fuse = conv("new-score-weighting", "concat", 0)
    nodes.append(helper.make_node("Sigmoid", [fuse], ["output"]))

    graph = helper.make_graph(
        nodes, "hed",
        [helper.make_tensor_value_info("input", TensorProto.FLOAT, [1, 3, "h", "w"])],
        [helper.make_tensor_value_info("output", TensorProto.FLOAT, [1, 1, "h", "w"])],
        inits)
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 17)],
                              producer_name="imgframe-convert-hed", ir_version=8)
    model.doc_string = ("HED (Xie & Tu, ICCV 2015), BSDS-pretrained weights. Input: BGR float32 NCHW "
                        "minus mean (104.007, 116.669, 122.679). Output: edge probability [0, 1].")
    onnx.checker.check_model(model)
    return model


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--caffemodel", default=ROOT / "models" / "hed_pretrained_bsds.caffemodel", type=Path)
    ap.add_argument("--out", default=ROOT / "models" / "hed.onnx", type=Path)
    args = ap.parse_args()
    layers = read_caffemodel(args.caffemodel)
    print(f"read {len(layers)} parameter layers from {args.caffemodel.name}")
    onnx.save(build(layers), args.out)
    print(f"wrote {args.out} ({args.out.stat().st_size / 2**20:.1f} MB)")


if __name__ == "__main__":
    main()
