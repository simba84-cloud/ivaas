"""VideoAnalyser adapter: runs ivaas_pipeline.analyse in a worker thread.

The pipeline package is a dependency of the API image only for this purpose; in
production the same work can move to the GPU edge node behind the same port.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

import cv2

from ivaas.domain.analysis import AnalysisJob, DetectedLoad, TimelineEvent

log = logging.getLogger(__name__)


class PipelineVideoAnalyser:
    def __init__(self, model_path: str, layers_model: str | None, lpr: bool = True) -> None:
        self._model_path, self._layers_model, self._lpr = model_path, layers_model, lpr
        self._loaded = None
        # None until the first analysis has tried to load the reader
        self.plate_reading: bool | None = None

    def _load(self):
        if self._loaded is None:
            from ivaas_pipeline.adapters.onnx_layers import OnnxLayerCounter
            from ivaas_pipeline.adapters.onnx_rtdetr import OnnxRtDetrDetector
            from ivaas_pipeline.stages.layers import PeriodicityLayerCounter

            reader = None
            if self._lpr:
                try:
                    from ivaas_pipeline.adapters.fast_alpr_reader import FastAlprPlateReader

                    reader = FastAlprPlateReader()
                except Exception:
                    # Analysis still counts crates, but every load will report no plate,
                    # which is indistinguishable from "the camera could not read one".
                    # Say so loudly: the report carries the same warning.
                    log.warning(
                        "plate reading is unavailable, so loads will have no number plate. "
                        "Install the pipeline's [lpr] extra (fast-alpr) to enable it.",
                        exc_info=True,
                    )
                    reader = None
            self.plate_reading = reader is not None
            self._loaded = (
                OnnxRtDetrDetector(self._model_path, threshold=0.2),
                OnnxLayerCounter(self._layers_model)
                if self._layers_model
                else PeriodicityLayerCounter(),
                reader,
            )
        return self._loaded

    async def analyse(
        self,
        job: AnalysisJob,
        local_path: str,
        save_frame: Callable[[str, bytes], Awaitable[str]],
        on_progress: Callable[[float], Awaitable[None]],
    ) -> tuple[list[DetectedLoad], list[TimelineEvent], float]:
        from ivaas_pipeline.analyse import analyse_video

        loop = asyncio.get_running_loop()
        detector, layers, reader = await asyncio.to_thread(self._load)

        def save_sync(name: str, image) -> str:
            ok, jpeg = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 85])
            fut = asyncio.run_coroutine_threadsafe(save_frame(name, jpeg.tobytes()), loop)
            return fut.result(timeout=60)

        def progress_sync(p: float) -> None:
            asyncio.run_coroutine_threadsafe(on_progress(p), loop)

        result = await asyncio.to_thread(
            analyse_video,
            local_path,
            detector=detector,
            layers=layers,
            plate_reader=reader,
            save_frame=save_sync,
            progress=progress_sync,
        )
        loads = [
            DetectedLoad(ld.start_s, ld.end_s, ld.stacks, ld.crates, ld.low_confidence, ld.plate)
            for ld in result.loads
        ]
        timeline = [TimelineEvent(e.at_s, e.kind, e.detail, e.frame_key) for e in result.timeline]
        return loads, timeline, result.duration_s
