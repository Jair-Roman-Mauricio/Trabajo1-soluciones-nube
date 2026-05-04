from pathlib import Path

import pytest

from app.ocr import decode_image


def test_decode_image_rejects_empty_bytes():
    with pytest.raises(ValueError, match="vacia"):
        decode_image(b"")


def test_decode_image_supports_avif_fixture_if_present():
    fixture = Path("/Users/jair-roman-mauricio/Downloads/1766191244-yape-no-contacto-4.png.avif")
    if not fixture.exists():
        pytest.skip("AVIF fixture is only available on the local developer machine")

    image = decode_image(fixture.read_bytes())

    assert image.shape[0] > 0
    assert image.shape[1] > 0
