"""Small, inspectable datasets used by the scientific validation suite."""

from __future__ import annotations

import numpy as np
import numpy.typing as npt

UINT8_BANDS: dict[str, npt.NDArray[np.uint8]] = {
    "RED": np.array([[10, 20, 30], [40, 50, 60]], dtype=np.uint8),
    "NIR": np.array([[30, 20, 10], [80, 100, 120]], dtype=np.uint8),
    "GREEN": np.array([[20, 30, 40], [50, 60, 70]], dtype=np.uint8),
    "BLUE": np.array([[5, 10, 15], [20, 25, 30]], dtype=np.uint8),
}

UINT16_BANDS: dict[str, npt.NDArray[np.uint16]] = {
    "RED": np.array([[100, 200, 300], [400, 500, 600]], dtype=np.uint16),
    "NIR": np.array([[300, 200, 100], [800, 1000, 1200]], dtype=np.uint16),
    "GREEN": np.array([[200, 300, 400], [500, 600, 700]], dtype=np.uint16),
    "BLUE": np.array([[50, 100, 150], [200, 250, 300]], dtype=np.uint16),
}

FLOAT32_BANDS: dict[str, npt.NDArray[np.float32]] = {
    name: values.astype(np.float32) / np.float32(10) for name, values in UINT8_BANDS.items()
}

# The first pixel is valid. The next three exercise NoData, NaN, and Inf. On
# the second row, the first pixel has a zero denominator, the second is a valid
# negative-input calculation, and the last two also have zero denominators.
INVALIDITY_RED = np.array(
    [[1.0, -9999.0, np.nan, np.inf], [-1.0, -2.0, 2.0, 3.0]],
    dtype=np.float32,
)
INVALIDITY_NIR = np.array(
    [[3.0, 5.0, 7.0, 9.0], [1.0, -4.0, -2.0, -3.0]],
    dtype=np.float32,
)

RECTANGULAR_ROI = np.array(
    [[True, True, False], [False, True, False]],
    dtype=np.bool_,
)

TRIANGULAR_ROI_PIXELS = np.array(
    [
        [True, True, True, True],
        [True, True, True, False],
        [True, True, False, False],
        [True, False, False, False],
    ],
    dtype=np.bool_,
)
