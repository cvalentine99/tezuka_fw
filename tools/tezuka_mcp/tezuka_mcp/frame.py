"""The maia-httpd /waterfall frame contract (vendored copy of
tools/classifier_templates/waterfall_frame.py so this package installs on
its own; tests/test_frame.py asserts the two agree bit for bit).

  * One binary WebSocket message = float32[fft_size], little-endian,
    fftshifted (index 0 = -fs/2 edge, DC at fft_size/2).
  * Values are LINEAR POWER (integrated |X|^2 scaled by 4e6/(nint*fs) in
    Average mode, peak |X|^2 scaled by 4e6/fs in Peak-detect mode): an
    arbitrary receiver level, NOT dBm. dB here is 10*log10 of that level.
  * The producer overwrites float index 0 with the fastlock profile /
    sweep step word, so f[1:] are the fft_size-1 usable bins and the grid
    is still span / fft_size (nothing is prepended, one bin is hidden).
  * Usable-bins index j (into f[1:]) is centred on
        f_c + (j + 1 - fft_size/2) * span / fft_size,
    DC at j = fft_size/2 - 1.
"""
import math
import struct
from dataclasses import dataclass
from typing import List, Optional, Sequence


@dataclass
class Frame:
    fft_size: int
    step_word: float
    bins: List[float]
    span_hz: Optional[float]
    center_hz: Optional[float]

    @property
    def hz_per_bin(self) -> Optional[float]:
        return None if self.span_hz is None else self.span_hz / self.fft_size

    @property
    def dc_index(self) -> int:
        return self.fft_size // 2 - 1

    def bin_center_hz(self, j: float) -> Optional[float]:
        if self.span_hz is None or self.center_hz is None:
            return None
        return self.center_hz + (j + 1 - self.fft_size / 2) * self.span_hz / self.fft_size


def decode_frame(payload: bytes, span_hz: Optional[float] = None, center_hz: Optional[float] = None) -> Frame:
    if len(payload) % 4 != 0:
        raise ValueError(f"payload is {len(payload)} bytes, not a multiple of 4")
    n = len(payload) // 4
    if n < 2:
        raise ValueError(f"payload has {n} floats, need at least 2")
    vals = struct.unpack(f"<{n}f", payload)
    return Frame(fft_size=n, step_word=vals[0], bins=list(vals[1:]), span_hz=span_hz, center_hz=center_hz)


def hz_per_bin(span_hz: float, fft_size: int) -> float:
    return span_hz / fft_size


def bins_index_to_hz(j: float, fft_size: int, center_hz: float, span_hz: float) -> float:
    return center_hz + (j + 1 - fft_size / 2) * span_hz / fft_size


def hz_to_bins_index(f_hz: float, fft_size: int, center_hz: float, span_hz: float) -> float:
    return (f_hz - center_hz) * fft_size / span_hz + fft_size / 2 - 1


def power_to_db(p: float, floor: float = 1e-10) -> float:
    return 10.0 * math.log10(p if p > floor else floor)


def mean_power(frames: Sequence[Frame]) -> List[float]:
    """Average frames bin by bin in the POWER domain (never across bins
    or sweep steps). All frames must share fft_size."""
    if not frames:
        raise ValueError("no frames")
    n = frames[0].fft_size
    for fr in frames:
        if fr.fft_size != n:
            raise ValueError("frames have different fft_size")
    acc = [0.0] * (n - 1)
    for fr in frames:
        for j, v in enumerate(fr.bins):
            acc[j] += v
    k = len(frames)
    return [a / k for a in acc]
