import pathlib

from fastapi import FastAPI
from fastapi.templating import Jinja2Templates

from cold_email.api.routes import dashboard

TEMPLATES_DIR = pathlib.Path(__file__).parent.parent / "templates"

app = FastAPI(title="Cold Email Dashboard")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

app.include_router(dashboard.router)
