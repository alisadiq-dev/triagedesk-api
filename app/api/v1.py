from fastapi import APIRouter

from app.api.routers import config, me, tickets, users

api_v1 = APIRouter(prefix="/api/v1")
api_v1.include_router(me.router)
api_v1.include_router(users.router)
api_v1.include_router(config.categories)
api_v1.include_router(config.sla_policies)
api_v1.include_router(tickets.router)
