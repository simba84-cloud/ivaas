"""One still frame from an RTSP stream, for drawing zones on (OpenCV, FFmpeg)."""

from __future__ import annotations

import asyncio

#: a stream that has not given a frame by then is treated as giving none
TIMEOUT_S = 10


class OpenCvFrameGrabber:
    async def grab(self, url: str) -> bytes | None:
        def grab() -> bytes | None:
            import cv2

            cap = cv2.VideoCapture(
                url,
                cv2.CAP_FFMPEG,
                [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 4000, cv2.CAP_PROP_READ_TIMEOUT_MSEC, 4000],
            )
            try:
                ok, image = cap.read() if cap.isOpened() else (False, None)
                if not ok:
                    return None
                ok, jpeg = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 80])
                return jpeg.tobytes() if ok else None
            finally:
                cap.release()

        try:
            return await asyncio.wait_for(asyncio.to_thread(grab), timeout=TIMEOUT_S)
        except TimeoutError:
            return None
