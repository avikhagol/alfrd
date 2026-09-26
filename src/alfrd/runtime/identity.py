from __future__ import annotations

import socket
from pathlib import Path


def project_identifier(
    root_path: str | Path, project_name: str, hostname: str | None = None
) -> str:
    """Return the host, directory, and manifest-name project identifier."""
    host = hostname or socket.gethostname()
    absolute = Path(root_path).expanduser().resolve().as_posix()
    dotted_path = absolute.lstrip("/").replace("/", ".")
    location = f"{host}.{dotted_path}" if dotted_path else host
    return f"{location}.{project_name}"


__all__ = ["project_identifier"]
