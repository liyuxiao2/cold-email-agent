import pathlib

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.templating import Jinja2Templates

from cold_email.api.routes import api, dashboard
from cold_email.config import settings

TEMPLATES_DIR = pathlib.Path(__file__).parent.parent / "templates"

app = FastAPI(
    title="Cold Email Agent API",
    description="REST API and Review Dashboard for the Cold Email Pipeline",
    version="1.0.0",
)

# Enable CORS for frontend clients (e.g. Vercel deployment, localhost)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

app.include_router(dashboard.router)
app.include_router(api.router)

