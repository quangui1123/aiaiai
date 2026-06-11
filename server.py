import uvicorn
from app.main import create_app
from app.config import settings

app = create_app()

if __name__ == "__main__":
    reload_enabled = os.getenv("RENDER") is None
    uvicorn.run("server:app", host=settings.host, port=settings.port, reload=reload_enabled)
