from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI

from integrated_agent.bootstrap.matrix_service import build_matrix_service
from integrated_agent.bootstrap.question_service import build_question_service
from integrated_agent.transports.http import create_http_app


ROOT = Path(__file__).parents[2]



def create_production_app() -> FastAPI:
    return create_http_app(
        question_service=build_question_service(),
        matrix_service=build_matrix_service(),
        static_root=ROOT / "static",
        artifacts_root=ROOT / "workspace/artifacts",
        matrix_data_root=ROOT / "data" / "matrix",
    )
