"""Which detector backs uploaded-video analysis is decided in the container alone."""

from ivaas.config.container import analysis_detector
from ivaas.config.settings import Settings


def test_a_detector_url_sends_analysis_to_the_remote_gpu():
    from ivaas_pipeline.adapters.http_detector import HttpDetector

    make = analysis_detector(
        Settings(detector_url="http://gpu/detect", detector_api_key="k", detector_threshold=0.3)
    )
    detector = make()
    try:
        assert isinstance(detector, HttpDetector)
        assert detector._threshold == 0.3
        assert detector._client.headers["authorization"] == "Bearer k"
    finally:
        detector.close()


def test_without_a_url_analysis_runs_the_local_onnx_model():
    make = analysis_detector(Settings(detector_url=""))
    assert "onnx_detector" in make.__qualname__
