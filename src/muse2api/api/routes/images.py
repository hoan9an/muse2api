from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import time
from dataclasses import replace

from fastapi import APIRouter, Depends, Request
from pydantic import ValidationError
from starlette.datastructures import UploadFile

from ...core.media import (
    MAX_REFERENCE_BYTES,
    MAX_REFERENCE_ITEMS,
    MAX_REFERENCE_TOTAL_BYTES,
    load_media_ref,
)
from ...core.models import resolve_model
from ...drivers.base import ImageRequest, InputImage, MediaResult
from ...errors import InvalidRequest, NotFound
from ...services.container import Services
from ...services.tasks import Task
from ..deps import get_services, public_base, require_api_key
from ..schemas import ImageGenerationRequest

router = APIRouter(tags=["images"], dependencies=[Depends(require_api_key)])

# Backwards-compatible name for callers importing it; reference media now shares one cap.
MAX_REFERENCE_IMAGES = MAX_REFERENCE_ITEMS

# Asking muse.ai for a "transparent background" makes it reply with text only, so
# ask for an easy-to-cut plain background and remove it locally instead.
_CUTOUT_HINT = "Isolated subject on a plain, uncluttered studio background."

_KIND_FIELDS = {
    "image": ("image", "images"),
    "video": ("video", "videos"),
    "audio": ("audio", "audios"),
    "file": ("file", "files"),
}
_UPLOAD_FIELDS = ("image", "image[]", "video", "video[]", "audio", "audio[]", "file", "file[]")


def _image_item(r: MediaResult, fmt: str, svc: Services, base: str) -> dict:
    item: dict = {"revised_prompt": r.revised_prompt}
    if fmt == "b64_json":
        item["b64_json"] = base64.b64encode(r.data).decode()
    else:
        name = svc.media.save(r.data, r.mime, prefix="img")
        item["url"] = f"{base}/v1/media/{name}"
    return item


def _task_view(task: Task) -> dict:
    return {
        "id": task.id,
        "object": "image.task",
        "status": task.status.value,
        "progress": task.progress,
        "created_at": int(task.created_at),
        "model": task.request.get("model"),
        "result": task.result,
        "error": task.error,
    }


def _values(value) -> list[str]:
    if value is None:
        return []
    return [str(item) for item in value] if isinstance(value, list) else [str(value)]


def _check_refs(refs: list[InputImage]) -> None:
    if len(refs) > MAX_REFERENCE_ITEMS:
        raise InvalidRequest(f"at most {MAX_REFERENCE_ITEMS} reference media items are supported")
    sized = sum(len(ref.data) for ref in refs)
    if sized > MAX_REFERENCE_TOTAL_BYTES:
        raise InvalidRequest(
            f"reference media total is limited to {MAX_REFERENCE_TOTAL_BYTES // (1024 * 1024)} MiB"
        )


async def _load_refs(body: ImageGenerationRequest) -> list[InputImage]:
    refs: list[InputImage] = []
    for kind, fields in _KIND_FIELDS.items():
        for field in fields:
            for ref in _values(getattr(body, field, None)):
                data, mime = await load_media_ref(ref, kind=kind)
                if kind == "image" and not mime.startswith("image/"):
                    raise InvalidRequest("image references must have an image/* MIME type")
                refs.append(InputImage(data=data, mime=mime, kind=kind))
    _check_refs(refs)
    return refs


def _request_hash(body: ImageGenerationRequest, refs: list[InputImage]) -> str:
    payload = body.model_dump(exclude={"async_"}, mode="json")
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode())
    for ref in refs:
        digest.update(ref.kind.encode())
        digest.update(ref.mime.encode())
        digest.update(len(ref.data).to_bytes(8, "big"))
        digest.update(ref.data)
    return digest.hexdigest()


async def _generate(body: ImageGenerationRequest, refs: list[InputImage],
                    request: Request, svc: Services) -> dict:
    request.state.model = body.model or resolve_model(body.model, "image").id
    _check_refs(refs)
    if body.async_:
        key = (request.headers.get("Idempotency-Key") or "").strip() or None
        if key and len(key) > 255:
            raise InvalidRequest("Idempotency-Key is limited to 255 characters")
        return _submit(body, refs, request, svc, key)
    return await _run(body, refs, public_base(request), svc)


def _submit(body: ImageGenerationRequest, refs: list[InputImage], request: Request,
            svc: Services, idempotency_key: str | None = None) -> dict:
    """Run the generation as a background task; results are stored as media URLs."""
    spec = resolve_model(body.model, "image")
    body = body.model_copy(update={"response_format": "url"})  # keep base64 out of tasks.json
    base = public_base(request)
    request_hash = _request_hash(body, refs)
    if idempotency_key:
        existing = svc.tasks.find_idempotent(idempotency_key, request_hash)
        if existing is not None:
            request.state.task_id = existing.id
            return _task_view(existing)

    async def runner(progress) -> dict:
        return await _run(body, refs, base, svc)

    meta = {"model": spec.id, "prompt": body.prompt, "size": body.size, "n": body.n,
            "references": len(refs),
            "reference_kinds": sorted({ref.kind for ref in refs})}
    task = svc.tasks.submit("image", meta, runner, idempotency_key=idempotency_key,
                            request_hash=request_hash)
    request.state.task_id = task.id
    return _task_view(task)


async def _run(body: ImageGenerationRequest, refs: list[InputImage], base: str,
               svc: Services) -> dict:
    spec = resolve_model(body.model, "image")
    transparent = body.background == "transparent"
    prompt = f"{body.prompt.rstrip('. ')}. {_CUTOUT_HINT}" if transparent else body.prompt
    req = ImageRequest(prompt=prompt, model=spec.id, size=body.size, n=body.n,
                       reference_images=refs, timeout=svc.settings.image_timeout)
    results = await svc.gateway.generate_image(req)
    if transparent:
        # After the gateway call, so the account is not held during matting.
        results = [replace(r, data=await svc.matting.remove(r.data), mime="image/png")
                   for r in results]
    return {"created": int(time.time()),
            "data": [_image_item(r, body.response_format, svc, base) for r in results]}


@router.post("/v1/images/generations")
async def generate_images(body: ImageGenerationRequest, request: Request,
                          svc: Services = Depends(get_services)) -> dict:
    return await _generate(body, await _load_refs(body), request, svc)


def _upload_kind(field: str) -> str:
    for kind in ("image", "video", "audio", "file"):
        if field == kind or field.startswith(kind):
            return kind
    return "file"


def _upload_mime(kind: str, upload: UploadFile) -> str:
    mime = upload.content_type or ""
    if not mime or mime == "application/octet-stream":
        mime = mimetypes.guess_type(upload.filename or "")[0] or ""
    fallback = {"image": "image/png", "video": "video/mp4", "audio": "audio/mpeg",
                "file": "application/octet-stream"}[kind]
    if kind == "file":
        return mime or fallback
    return mime if mime and mime.split("/")[0] == kind else fallback


@router.post("/v1/images/edits")
async def edit_images(request: Request, svc: Services = Depends(get_services)) -> dict:
    """OpenAI-style multipart edit: image/video/audio/file uploads plus a prompt.

    muse.ai has no inpainting, so the media act as references for a new image
    and ``mask`` is rejected rather than silently ignored.
    """
    form = await request.form()
    if form.get("mask") is not None:
        raise InvalidRequest("mask is not supported; muse.ai cannot inpaint a region")
    uploads = [(key, f) for key in _UPLOAD_FIELDS for f in form.getlist(key)
               if isinstance(f, UploadFile)]
    if not uploads:
        raise InvalidRequest(
            "at least one reference file is required (image/image[], video, audio or file)"
        )
    fields = {k: v for k, v in form.items() if isinstance(v, str)}
    try:
        body = ImageGenerationRequest(**fields)
    except ValidationError as exc:
        raise InvalidRequest(exc.errors()[0].get("msg", "invalid request")) from exc
    refs = await _load_refs(body)
    for field, upload in uploads:
        kind = _upload_kind(field)
        data = await upload.read()
        if len(data) > MAX_REFERENCE_BYTES:
            raise InvalidRequest(f"each reference media item is limited to "
                                 f"{MAX_REFERENCE_BYTES // (1024 * 1024)} MiB")
        mime = _upload_mime(kind, upload)
        if kind == "image" and not mime.startswith("image/"):
            raise InvalidRequest("image references must have an image/* MIME type")
        refs.append(InputImage(data=data, mime=mime, kind=kind,
                               filename=upload.filename or ""))
    _check_refs(refs)
    return await _generate(body, refs, request, svc)


@router.get("/v1/images/generations/{task_id}")
async def get_image_task(task_id: str, request: Request,
                         svc: Services = Depends(get_services)) -> dict:
    task = svc.tasks.get(task_id)
    if task is None or task.kind != "image":
        raise NotFound(f"image task '{task_id}' not found")
    request.state.model = task.request.get("model")
    return _task_view(task)
