"""GET /health — used by Docker Compose healthchecks and uptime monitors later."""
from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
async def health() -> dict:
    return {"status": "ok"}