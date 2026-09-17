"""Railway entrypoint."""
import uvicorn

from app.config import get_settings
from app.web import app

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=get_settings().port)
