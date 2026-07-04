"""FastAPI entrypoint. Routers are added here as new app/api/*.py files are built
on their locked days (reviews.py on Day 17-18, etc.) — this file's structure
does not change, only the include_router list grows.
"""
import logging

from fastapi import FastAPI

from app.api import health, webhook

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="CodeLens CI", version="0.1.0")

app.include_router(health.router)
app.include_router(webhook.router)

@app.get("/")
async def root():
    return {"service": "CodeLens CI", "status": "running"}