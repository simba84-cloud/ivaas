"""Count the crates in one stack from the periodicity of its image.

Crates in a stack are identical and repeat at a fixed pitch, so the vertical
edge-energy profile of a stack crop is strongly periodic: one period per crate.
The autocorrelation of that profile peaks at the pitch, and

    crates = stack height / pitch

One thing keeps it honest on real footage (found by running it on site video):

  - **Pitch prior.** Crates have internal lattice and handle-slot texture that is also
    periodic, at a much finer pitch. A crate's height is a fixed fraction of its width,
    so only pitches within `pitch_to_width` of the stack's width are considered. The
    range is a per-camera calibration value (it depends on viewing angle).
What it can NOT do is tell a stack from anything else periodic: paving bricks and
ribbed truck panels score as confidently as crates. (A left/right coherence gate was
tried and removed: perspective puts a crate's rim at different heights on each side, so
it rejected real stacks and accepted paving.) It must only ever be called on boxes that
a stack *detector* produced.

This needs no training data. It is a *per-frame estimate*; StackCrossingCounter
takes the median over every frame in which the stack was tracked, which is what
makes it robust to a single bad frame (motion blur, a worker walking in front).
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from ivaas_pipeline.types import Box


@dataclass(frozen=True)
class LayerEstimate:
    layers: float  # fractional on purpose: rounding happens once, after the median
    pitch_px: int
    confidence: float  # autocorrelation peak height, 0..1


class PeriodicityLayerCounter:
    def __init__(
        self,
        *,
        min_layers: int = 2,
        max_layers: int = 30,
        min_confidence: float = 0.2,
        centre_fraction: float = 0.6,
        pitch_to_width: tuple[float, float] = (0.12, 0.45),
    ) -> None:
        self._min, self._max = min_layers, max_layers
        self._min_conf = min_confidence
        self._centre = centre_fraction
        self._ratio = pitch_to_width

    def estimate(self, image: np.ndarray, box: Box) -> LayerEstimate | None:
        h_img, w_img = image.shape[:2]
        x1, y1 = max(0, int(box.x1)), max(0, int(box.y1))
        x2, y2 = min(w_img, int(box.x2)), min(h_img, int(box.y2))
        width, height = x2 - x1, y2 - y1
        if height < 4 * self._min or width < 8:
            return None

        # the middle of the stack: avoids neighbouring stacks and background at the edges
        margin = int(width * (1 - self._centre) / 2)
        crop = image[y1:y2, x1 + margin : x2 - margin]
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
        profile = np.abs(cv2.Sobel(gray.astype(np.float64), cv2.CV_64F, 0, 1, ksize=3)).mean(axis=1)
        profile -= profile.mean()
        if profile.std() < 1e-6:
            return None  # featureless crop: a wall, a truck side

        ac = np.correlate(profile, profile, "full")[len(profile) - 1 :]
        ac /= ac[0]
        lo = max(3, height // self._max, int(width * self._ratio[0]))
        hi = min(len(ac) - 1, height // self._min, int(width * self._ratio[1]))
        if hi <= lo:
            return None

        # First strong local maximum, not the global one: a periodic signal also peaks at
        # 2x, 3x the pitch, and picking one of those would halve or third the count.
        window = ac[lo : hi + 1]
        best = float(window.max())
        if best < self._min_conf:
            return None
        pitch = None
        for i in range(1, len(window) - 1):
            if (
                window[i] >= 0.8 * best
                and window[i] >= window[i - 1]
                and window[i] >= window[i + 1]
            ):
                pitch = lo + i
                break
        if pitch is None:
            pitch = lo + int(window.argmax())
        return LayerEstimate(height / pitch, pitch, float(ac[pitch]))
