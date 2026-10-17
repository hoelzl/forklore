"""Media store: content-addressed image bytes for generated nodes.

The event log only ever holds the *reference* (node meta key "image" ->
"<sha256>.<ext>"); bytes live here. Disk-backed when rooted at a
directory (`<log>.media/` for a persisted log), in-memory otherwise
(tests, ephemeral runs). The name regex makes path traversal impossible
by construction.
"""
from __future__ import annotations

import hashlib
import os
import re

_NAME_RE = re.compile(r"^[a-f0-9]{64}\.(svg|png)$")
_CONTENT_TYPES = {"svg": "image/svg+xml", "png": "image/png"}


def _ext_of(data: bytes) -> str:
    if data.startswith(b"\x89PNG"):
        return "png"
    if data[:512].lstrip().startswith((b"<svg", b"<?xml")):
        return "svg"
    raise ValueError("unsupported image payload (expected PNG or SVG)")


class MediaStore:
    def __init__(self, root: str | None = None):
        self.root = root
        self._mem: dict[str, bytes] = {}

    def save(self, data: bytes) -> str:
        """Store bytes, return their content-addressed name."""
        name = f"{hashlib.sha256(data).hexdigest()}.{_ext_of(data)}"
        if self.root is None:
            self._mem[name] = data
        else:
            os.makedirs(self.root, exist_ok=True)
            path = os.path.join(self.root, name)
            if not os.path.exists(path):
                with open(path, "wb") as f:
                    f.write(data)
        return name

    def read(self, name: str) -> tuple[bytes, str] | None:
        """(bytes, content_type), or None for unknown/invalid names."""
        m = _NAME_RE.match(name)
        if not m:
            return None
        content_type = _CONTENT_TYPES[m.group(1)]
        if self.root is None:
            data = self._mem.get(name)
            return (data, content_type) if data is not None else None
        path = os.path.join(self.root, name)
        if not os.path.isfile(path):
            return None
        with open(path, "rb") as f:
            return f.read(), content_type
