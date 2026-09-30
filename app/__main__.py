import os

from app.main import app
from app.config import settings
import uvicorn

if __name__ == "__main__":
    # reload=True often stalls on Windows VPS (file watcher). Opt in with SNAPPY_RELOAD=1.
    reload = (os.environ.get("SNAPPY_RELOAD") or "").strip().lower() in {"1", "true", "yes"}
    uvicorn.run(
        "app.main:app",
        host=settings.snappymake_host,
        port=settings.snappymake_port,
        reload=reload,
    )
