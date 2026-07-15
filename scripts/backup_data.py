"""Create a timestamped, integrity-manifested backup of mutable solver data."""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
import time
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKUP_SOURCES = (ROOT / "puzzles", ROOT / "data" / "image_imports", ROOT / "data" / "image_jobs")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "backups")
    parser.add_argument("--retain", type=int, default=14, help="Number of newest backups to retain")
    args = parser.parse_args()
    if args.retain < 1:
        raise SystemExit("--retain must be at least 1")
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    destination = output_dir / f"flow-solver-{stamp}.zip"
    manifest: dict[str, object] = {"created_at": time.time(), "files": {}}

    with tempfile.NamedTemporaryFile(dir=output_dir, suffix=".zip.tmp", delete=False) as temporary:
        temporary_path = Path(temporary.name)
    try:
        with zipfile.ZipFile(temporary_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for source in BACKUP_SOURCES:
                if not source.exists():
                    continue
                for path in sorted(value for value in source.rglob("*") if value.is_file()):
                    relative = path.relative_to(ROOT).as_posix()
                    data = path.read_bytes()
                    archive.writestr(relative, data)
                    manifest["files"][relative] = {  # type: ignore[index]
                        "bytes": len(data),
                        "sha256": hashlib.sha256(data).hexdigest(),
                    }
            archive.writestr("BACKUP_MANIFEST.json", json.dumps(manifest, indent=2, sort_keys=True))
        temporary_path.replace(destination)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise

    backups = sorted(output_dir.glob("flow-solver-*.zip"), key=lambda path: path.stat().st_mtime, reverse=True)
    for expired in backups[args.retain :]:
        expired.unlink()
    print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
