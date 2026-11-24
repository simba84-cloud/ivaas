"""RF-DETR export: the sidecar keeps logit positions, and the baked-in normalisation
matches what the processor feeds the model during training."""

import numpy as np
import pytest

from ivaas_ml.export import sidecar_labels


def test_sidecar_labels_keep_gaps_so_argmax_maps_to_the_right_name():
    # COCO-style: id 0 is "N/A", person is 1, and id 3 is missing entirely.
    id2label = {"0": "N/A", "1": "person", "2": "bicycle", "4": "motorcycle"}
    labels = sidecar_labels(id2label, num_labels=6)
    assert labels == ["N/A", "person", "bicycle", "N/A", "motorcycle", "N/A"]
    assert labels.index("person") == 1


def test_sidecar_labels_for_a_dense_head_are_unchanged():
    assert sidecar_labels({0: "stack"}, num_labels=1) == ["stack"]


def test_wrapper_normalisation_matches_the_processor():
    torch = pytest.importorskip("torch")
    transformers = pytest.importorskip("transformers")
    from ivaas_ml.export import _wrap, processor_normalisation
    from ivaas_ml.train import processor_overrides

    if not hasattr(transformers, "RfDetrImageProcessor"):
        pytest.skip("transformers has no RF-DETR")
    from PIL import Image

    size = processor_overrides("rfdetr")["size"]
    processor = transformers.RfDetrImageProcessor(size=size)
    raw = transformers.RfDetrImageProcessor(size=size, do_normalize=False)
    rng = np.random.default_rng(0)
    image = Image.fromarray(rng.integers(0, 256, (300, 420, 3), dtype=np.uint8))

    expected = processor(images=[image], return_tensors="pt")["pixel_values"]
    unnormalised = raw(images=[image], return_tensors="pt")["pixel_values"]
    assert unnormalised.shape == (1, 3, 560, 560)
    assert 0.0 <= float(unnormalised.min()) and float(unnormalised.max()) <= 1.0

    seen = {}

    class Recorder(torch.nn.Module):
        def forward(self, pixel_values):
            seen["pixel_values"] = pixel_values
            out = type("Out", (), {})()
            out.logits, out.pred_boxes = pixel_values[:, :1], pixel_values[:, :1]
            return out

    normalisation = processor_normalisation(processor)
    assert normalisation is not None
    _wrap(Recorder(), normalisation)(unnormalised)
    assert torch.allclose(seen["pixel_values"], expected, atol=1e-5)


def test_rtdetr_processor_is_not_normalised_so_its_export_is_unchanged():
    transformers = pytest.importorskip("transformers")
    pytest.importorskip("torch")
    from ivaas_ml.export import processor_normalisation

    assert processor_normalisation(transformers.RTDetrImageProcessor()) is None


def test_frozen_position_embeddings_are_the_resized_table_and_refuse_other_sizes():
    torch = pytest.importorskip("torch")
    from ivaas_ml.export import freeze_position_embeddings

    class Embeddings(torch.nn.Module):
        patch_size = 14

        def __init__(self):
            super().__init__()
            self.position_embeddings = torch.nn.Parameter(torch.randn(1, 5, 8))

        def interpolate_pos_encoding(self, embeddings, height, width):
            n = embeddings.shape[1]
            return self.position_embeddings[:, :1].expand(1, n, 8) * (height + width)

    module = Embeddings()
    expected = module.interpolate_pos_encoding(torch.zeros(1, 17, 8), 56, 56)
    assert freeze_position_embeddings(torch.nn.Sequential(module), 56, 56) == 1
    assert torch.equal(module.interpolate_pos_encoding(torch.zeros(1, 17, 8), 56, 56), expected)
    with pytest.raises(ValueError):
        module.interpolate_pos_encoding(torch.zeros(1, 17, 8), 70, 70)
