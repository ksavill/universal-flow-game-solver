from __future__ import annotations

import os
from pathlib import Path

import uvicorn

if __name__ == "__main__":
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8000"))
    reload = os.environ.get("RELOAD", "1").lower() in {"1", "true", "yes", "y", "on"}
    root = Path(__file__).resolve().parent
    reload_dirs = [
        str(path)
        for path in (root / "backend", root / "flow_solver")
        if path.is_dir()
    ]
    uvicorn.run(
        "backend.app:app",
        host=host,
        port=port,
        reload=reload,
        reload_dirs=reload_dirs if reload else None,
    )
