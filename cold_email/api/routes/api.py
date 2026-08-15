from fastapi import APIRouter

from cold_email.api.routes import auth, companies, dlq, outreach, pipeline, system

router = APIRouter(prefix="/api")

# Include all sub-routers
router.include_router(auth.router)
router.include_router(system.router)
router.include_router(outreach.router)
router.include_router(companies.router)
router.include_router(pipeline.router)
router.include_router(dlq.router)
