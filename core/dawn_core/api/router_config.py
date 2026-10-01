from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import ValidationError

from ..config import DawnConfig
from ..config.loader import dump_config
from ..context import DawnContext
from .deps import get_ctx

router = APIRouter(prefix="/api/config", tags=["config"])


def _errors(e: ValidationError) -> list[dict[str, Any]]:
    """pydantic error dicts contain exception objects in ctx; make them JSON-safe."""
    out = []
    for err in e.errors():
        out.append({"loc": list(err.get("loc", ())), "msg": err.get("msg", ""), "type": err.get("type", "")})
    return out


@router.get("")
def get_config(ctx: DawnContext = Depends(get_ctx)) -> dict[str, Any]:
    return ctx.config.model_dump(mode="json")


@router.get("/schema")
def get_schema() -> dict[str, Any]:
    return DawnConfig.model_json_schema()


@router.get("/yaml")
def get_yaml(ctx: DawnContext = Depends(get_ctx)) -> dict[str, str]:
    return {"path": str(ctx.cfg_mgr.path), "yaml": dump_config(ctx.config)}


@router.get("/defaults")
def get_defaults() -> dict[str, Any]:
    return DawnConfig().model_dump(mode="json")


@router.patch("")
async def patch_config(patch: dict[str, Any], ctx: DawnContext = Depends(get_ctx)) -> dict[str, Any]:
    """Deep-merge a partial document into the config, validate, write and hot-apply."""
    try:
        new = await ctx.cfg_mgr.update(patch)
    except ValidationError as e:
        raise HTTPException(422, detail=_errors(e)) from e
    return new.model_dump(mode="json")


@router.put("")
async def put_config(doc: dict[str, Any], ctx: DawnContext = Depends(get_ctx)) -> dict[str, Any]:
    try:
        new = await ctx.cfg_mgr.replace(doc)
    except ValidationError as e:
        raise HTTPException(422, detail=_errors(e)) from e
    return new.model_dump(mode="json")


@router.post("/reload")
async def reload_config(ctx: DawnContext = Depends(get_ctx)) -> dict[str, Any]:
    ok = await ctx.cfg_mgr.reload()
    return {"ok": ok, "error": ctx.cfg_mgr.last_error}
