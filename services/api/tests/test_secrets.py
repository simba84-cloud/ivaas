import pytest

from ivaas.adapters.persistence.secrets import SecretBox


def test_round_trip_and_ciphertext_hides_password():
    box = SecretBox([SecretBox.generate_key()])
    url = "rtsp://admin:hunter2@10.0.0.9/ch1"
    sealed = box.seal(url)
    assert sealed.startswith("enc:") and "hunter2" not in sealed
    assert box.open(sealed) == url
    assert box.seal(None) is None and box.open(None) is None


def test_legacy_plaintext_rows_still_open():
    box = SecretBox([SecretBox.generate_key()])
    assert box.open("rtsp://10.0.0.1/plain") == "rtsp://10.0.0.1/plain"


def test_key_rotation_reads_old_and_writes_new():
    old, new = SecretBox.generate_key(), SecretBox.generate_key()
    sealed_old = SecretBox([old]).seal("rtsp://u:p@h/x")
    rotated = SecretBox([new, old])
    assert rotated.open(sealed_old) == "rtsp://u:p@h/x"
    resealed = rotated.seal("rtsp://u:p@h/x")
    assert SecretBox([new]).open(resealed) == "rtsp://u:p@h/x"
    with pytest.raises(ValueError):
        SecretBox([new]).open(sealed_old)  # old key dropped: refuse, do not return garbage


def test_requires_a_key():
    with pytest.raises(ValueError):
        SecretBox([])
