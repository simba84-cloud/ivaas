"""Faces with OpenCV's own models: YuNet finds them (MIT), SFace embeds them (Apache-2.0).

One module for both ends: the API uses it to enrol a person from a photo, the edge
node to recognise them, so the embedding and the match threshold cannot drift apart.
Models come from the OpenCV Zoo (see deploy/fetch-models.sh).
"""

from __future__ import annotations

import cv2
import numpy as np

#: SFace's published cosine threshold for "same person" on LFW
SAME_PERSON = 0.363
#: a face smaller than this (pixels, shorter side) is too small to trust either way
MIN_FACE_PX = 40


class OpenCvFaces:
    def __init__(self, detector_path: str, recognizer_path: str, *, score: float = 0.8) -> None:
        self._detector = cv2.FaceDetectorYN.create(detector_path, "", (320, 320), score)
        self._recognizer = cv2.FaceRecognizerSF.create(recognizer_path, "")

    def faces(self, image: np.ndarray) -> list[np.ndarray]:
        """Detected faces large enough to judge: rows of box, landmarks and score."""
        h, w = image.shape[:2]
        self._detector.setInputSize((w, h))
        _, found = self._detector.detect(image)
        if found is None:
            return []
        return [f for f in found if min(f[2], f[3]) >= MIN_FACE_PX]

    def embedding(self, image: np.ndarray, face: np.ndarray) -> np.ndarray:
        aligned = self._recognizer.alignCrop(image, face)
        return self._recognizer.feature(aligned).flatten().astype(np.float32)

    def embed_photo(self, image_bytes: bytes) -> tuple[float, ...]:
        """Enrolment: exactly one clear face, or a ValueError saying what is wrong."""
        image = cv2.imdecode(np.frombuffer(image_bytes, np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError("that file is not an image this server can read")
        found = self.faces(image)
        if not found:
            raise ValueError("no clear face found; use a well-lit, front-on photo")
        if len(found) > 1:
            raise ValueError("more than one face in the photo; use a photo of one person")
        return tuple(float(v) for v in self.embedding(image, found[0]))

    #: the API's FaceEncoder port
    embed = embed_photo

    def match(
        self, embedding: np.ndarray, gallery: list[tuple[str, np.ndarray]]
    ) -> tuple[str | None, float]:
        """(name, similarity) of the closest enrolled person, or (None, best) if nobody
        clears the threshold."""
        best_name, best = None, -1.0
        for name, known in gallery:
            score = float(
                self._recognizer.match(
                    embedding.reshape(1, -1), known.reshape(1, -1), cv2.FaceRecognizerSF_FR_COSINE
                )
            )
            if score > best:
                best_name, best = name, score
        return (best_name if best >= SAME_PERSON else None), best
