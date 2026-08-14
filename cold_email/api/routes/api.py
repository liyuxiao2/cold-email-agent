from fastapi import APIRouter

from cold_email.api.routes import dlq, leads, pipeline, system

router = APIRouter(prefix="/api")

# Include all sub-routers
router.include_router(system.router)
router.include_router(leads.router)
router.include_router(pipeline.router)
router.include_router(dlq.router)
