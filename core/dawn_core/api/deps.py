from __future__ import annotations

from fastapi import Request

from ..context import DawnContext


def get_ctx(request: Request) -> DawnContext:
    return request.app.state.ctx
