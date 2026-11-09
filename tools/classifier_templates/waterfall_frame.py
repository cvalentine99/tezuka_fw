"""waterfall_frame.py — the maia-httpd /waterfall frame contract, in one place.

Every Python consumer of the stream (the template tools, the replay
tools, the MCP server's tests) decodes frames through this module so the
grid and units are stated once. The contract was verified against the
producer source at the pinned maia-sdr commit
(maia-httpd/src/spectrometer.rs, buffer_u64fp_to_f32) and against
recorded frames; see docs/classifier_telemetry.md.

  * One binary WebSocket message = float32[fft_size], little-endian.
  * The FFT is fftshifted: index 0 is the -fs/2 edge, DC is at index
    fft_size / 2, index fft_size - 1 is just below +fs/2.
  * Values are LINEAR POWER (integrated |X|^2 scaled by 4e6/(nint*fs) in
    Average mode, peak |X|^2 scaled by 4e6/fs in Peak-detect mode). They
    are an arbitrary receiver level, not dBm; convert with 10*log10 for
    dB and never label the result dBm.
  * The producer OVERWRITES float index 0 with the fastlock profile /
    sweep step word (an integer stored as float). So f[1:] are the
    fft_size-1 usable bins; the frame still has fft_size bins of width
    span / fft_size. Nothing is prepended.

Grid: bin k of the full frame (k = 0..fft_size-1) is centred on
    f_c + (k - fft_size/2) * span / fft_size
and the usable bins array b = f[1:] has b[j] = f[j+1], so b[j] is at
    f_c + (j + 1 - fft_size/2) * span / fft_size,
with DC at j = fft_size/2 - 1 (2047 for 4096). `span` is the frame's
analysis span: the AD9361 sampling rate (state/rx/sampling /
state/rx/span) when the spectrometer input is the ADC, or the DDC
output rate when the input is the DDC; in sweep mode each frame is one
step and the step word in f[0] says which.
"""
import math
import struct
from dataclasses import dataclass
from typing import List, Optional, Sequence


@dataclass
class Frame:
    fft_size: int
    step_word: float          # f[0] as carried (integer-valued in practice)
    bins: List[float]         # f[1:], linear power, fftshifted
    span_hz: Optional[float]  # analysis span if known, else None
    center_hz: Optional[float]

    @property
    def hz_per_bin(self) -> Optional[float]:
        return None if self.span_hz is None else self.span_hz / self.fft_size

    @property
    def dc_index(self) -> int:
        """Index into `bins` of the DC bin."""
        return self.fft_size // 2 - 1

    def bin_center_hz(self, j: int) -> Optional[float]:
        """Centre frequency of bins[j]; None without span/centre."""
        if self.span_hz is None or self.center_hz is None:
            return None
        return self.center_hz + (j + 1 - self.fft_size / 2) * self.span_hz / self.fft_size

    def bin_edges_hz(self, j: int):
        c = self.bin_center_hz(j)
        if c is None:
            return None
        h = self.hz_per_bin / 2
        return (c - h, c + h)


def decode_frame(payload: bytes, span_hz: Optional[float] = None, center_hz: Optional[float] = None) -> Frame:
    """Decode one WebSocket binary message. Raises ValueError on a payload
    that is not a whole number of float32s or has fewer than 2 floats."""
    if len(payload) % 4 != 0:
        raise ValueError(f"payload is {len(payload)} bytes, not a multiple of 4")
    n = len(payload) // 4
    if n < 2:
        raise ValueError(f"payload has {n} floats, need at least 2")
    vals = struct.unpack(f"<{n}f", payload)
    return Frame(fft_size=n, step_word=vals[0], bins=list(vals[1:]), span_hz=span_hz, center_hz=center_hz)


def hz_per_bin(span_hz: float, fft_size: int) -> float:
    """span / fft_size. NOT span / (fft_size - 1): the step word hides a
    bin, it does not shrink the FFT."""
    return span_hz / fft_size


def bins_index_to_hz(j: int, fft_size: int, center_hz: float, span_hz: float) -> float:
    """Centre frequency of usable-bins index j (0-based into f[1:])."""
    return center_hz + (j + 1 - fft_size / 2) * span_hz / fft_size


def hz_to_bins_index(f_hz: float, fft_size: int, center_hz: float, span_hz: float) -> float:
    """Fractional usable-bins index of frequency f_hz (inverse of the above)."""
    return (f_hz - center_hz) * fft_size / span_hz + fft_size / 2 - 1


def power_to_db(p: float, floor: float = 1e-10) -> float:
    """10*log10 of a linear power value (a level in dB relative to the
    producer's unit scale, not dBm)."""
    return 10.0 * math.log10(p if p > floor else floor)


def mean_power_db(frames: Sequence[Frame]) -> List[float]:
    """Average several frames in the POWER domain, then convert to dB per
    bin. Averaging in dB would bias the estimate low; averaging across
    bins or across sweep steps is never done here."""
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
    return [power_to_db(a / k) for a in acc]
