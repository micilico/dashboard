from __future__ import annotations

import tarfile
from datetime import UTC, datetime
from pathlib import Path


SOURCE_ROOT = Path("/source")
BACKUP_ROOT = Path("/backup")


def main() -> None:
    BACKUP_ROOT.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    destination = BACKUP_ROOT / f"dashboard-data-{timestamp}.tar.gz"
    with tarfile.open(destination, "w:gz") as archive:
        for name in ("torrent-panel", "cloud-panel"):
            source = SOURCE_ROOT / name
            if source.exists():
                archive.add(source, arcname=name, recursive=True)
    print(destination)


if __name__ == "__main__":
    main()
