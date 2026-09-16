from app.main import app
from app.config import settings
import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=settings.snappymake_host,
        port=settings.snappymake_port,
        reload=True,
    )
