from fastapi import APIRouter

from app.api.routers import me

api_v1 = APIRouter(prefix="/api/v1")
api_v1.include_router(me.router)
