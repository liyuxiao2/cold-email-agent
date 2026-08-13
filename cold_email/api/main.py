from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from cold_email.api.routes import api
from cold_email.config import settings

app = FastAPI(
    title="Cold Email Agent API",
    description="REST API for the Cold Email Pipeline",
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

app.include_router(api.router)
