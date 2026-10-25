import numpy as np
import pytest

from imgframe.ops import OPS, get_op
from imgframe.pipeline import run_sequential, run_tiled
from imgframe.tiling import decode_image, encode_png, plan_tiles, reconstruct, split_image


def test_tiles_cover_image_exactly_once():
    specs = plan_tiles(1000, 1300, 256, halo=16)
    covered = np.zeros((1000, 1300), int)
    for s in specs:
        covered[s.y0:s.y1, s.x0:s.x1] += 1
        assert s.hy0 <= s.y0 and s.hy1 >= s.y1 and s.hx0 <= s.x0 and s.hx1 >= s.x1
    assert (covered == 1).all()
    assert len(specs) == 4 * 6


def test_split_and_reconstruct_roundtrip(image):
    h, w = image.shape[:2]
    assert np.array_equal(reconstruct(h, w, split_image(image, 128, 10)), image)


@pytest.mark.parametrize("op", ["gaussian_blur", "sobel", "sharpen", "grayscale", "bilateral"])
def test_local_filters_match_whole_image_exactly(image, op):
    assert np.array_equal(run_tiled(image, op, 128, workers=1), run_sequential(image, op))


def test_canny_matches_whole_image(image):
    tiled, full = run_tiled(image, "canny", 128, workers=1), run_sequential(image, "canny")
    agreement = (tiled == full).mean()
    assert agreement > 0.999, agreement


def test_parallel_equals_sequential(image):
    assert np.array_equal(run_tiled(image, "gaussian_blur", 128, workers=4),
                          run_sequential(image, "gaussian_blur"))


def test_png_codec_roundtrip(image):
    assert np.array_equal(decode_image(encode_png(image)), image)


def test_unknown_op():
    with pytest.raises(ValueError):
        get_op("nope")
    assert "canny" in OPS


@pytest.mark.parametrize("shape", [(37, 51), (37, 51, 3)])
def test_raw_tile_codec_roundtrip(shape):
    from imgframe.tiling import decode_tile, encode_tile
    arr = np.random.default_rng(1).integers(0, 255, shape, dtype=np.uint8)
    assert np.array_equal(decode_tile(encode_tile(arr)), arr)
    assert np.array_equal(decode_tile(encode_png(arr)), arr)   # PNG tiles still accepted
