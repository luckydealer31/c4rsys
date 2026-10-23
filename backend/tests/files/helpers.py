from typing import Any

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def upload(client: Client, purpose: str, content: bytes = PNG, key: str = "f-1") -> Any:
    return client.post(
        f"/api/files?purpose={purpose}",
        data={"file": SimpleUploadedFile("image.png", content)},
        headers={"Idempotency-Key": key},
    )
