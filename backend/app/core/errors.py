"""RFC 9457 problem responses.

Domain code raises ``ProblemError`` with a stable machine-readable ``code``.
Clients switch on ``code``; ``detail`` is safe for display and never contains
internals, prompts or secrets.
"""

import logging
from collections.abc import Mapping
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)

PROBLEM_CONTENT_TYPE = "application/problem+json"


class ProblemError(Exception):
    def __init__(
        self,
        status: int,
        code: str,
        title: str,
        detail: str | None = None,
        *,
        headers: Mapping[str, str] | None = None,
        extra: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(f"{status} {code}: {title}")
        self.status = status
        self.code = code
        self.title = title
        self.detail = detail
        self.headers = dict(headers or {})
        self.extra = dict(extra or {})


def _problem(
    request: Request,
    status: int,
    code: str,
    title: str,
    detail: str | None = None,
    *,
    headers: Mapping[str, str] | None = None,
    extra: Mapping[str, Any] | None = None,
) -> JSONResponse:
    body: dict[str, Any] = {
        "type": f"https://wallora.ai/problems/{code}",
        "title": title,
        "status": status,
        "code": code,
    }
    if detail:
        body["detail"] = detail
    request_id = getattr(request.state, "request_id", None)
    if request_id:
        body["request_id"] = request_id
    if extra:
        body.update(extra)
    return JSONResponse(
        body, status_code=status, headers=dict(headers or {}), media_type=PROBLEM_CONTENT_TYPE
    )


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ProblemError)
    async def handle_problem(request: Request, exc: ProblemError) -> JSONResponse:
        return _problem(
            request,
            exc.status,
            exc.code,
            exc.title,
            exc.detail,
            headers=exc.headers,
            extra=exc.extra,
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [
            {"loc": list(err.get("loc", ())), "msg": err.get("msg", ""), "type": err.get("type")}
            for err in exc.errors()
        ]
        return _problem(
            request,
            422,
            "validation_error",
            "The request is invalid.",
            extra={"errors": errors},
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "http_error")
        return _problem(request, exc.status_code, code, str(exc.detail))

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled_error", extra={"path": request.url.path})
        return _problem(request, 500, "internal_error", "Something went wrong.")
