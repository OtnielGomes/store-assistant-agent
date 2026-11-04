from typing import Literal

import psycopg
from fastapi import FastAPI
from pydantic import BaseModel

from lia_api.settings import load_settings


class HealthResponse(BaseModel):
    status: Literal["healthy", "unhealthy"]
    database: Literal["connected", "unavailable"]


def create_app() -> FastAPI:
    app = FastAPI(title="Lia")

    @app.get("/health")
    def health() -> HealthResponse:
        settings = load_settings()
        try:
            with psycopg.connect(settings.database_url, connect_timeout=5) as connection:
                connection.execute("SELECT 1")
        except psycopg.Error:
            return HealthResponse(status="unhealthy", database="unavailable")
        return HealthResponse(status="healthy", database="connected")

    return app


app = create_app()
