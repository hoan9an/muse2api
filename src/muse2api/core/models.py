"""Model registry: public model ids exposed on /v1/models and alias resolution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ..errors import InvalidRequest

ModelKind = Literal["chat", "image", "video"]


@dataclass(frozen=True)
class ModelSpec:
    id: str
    kind: ModelKind
    description: str = ""
    owned_by: str = "muse"


MODELS: dict[str, ModelSpec] = {
    m.id: m
    for m in (
        ModelSpec("muse-chat", "chat", "Default muse.ai assistant"),
        ModelSpec("muse-image", "image", "Text-to-image / image editing"),
        ModelSpec("muse-video", "video", "Text-to-video / first-frame image-to-video"),
    )
}

# Popular client-side model names are routed to the closest muse model so that
# existing OpenAI tooling works without reconfiguration.
ALIASES: dict[str, str] = {
    "muse-spark": "muse-chat",
    "gpt-4o": "muse-chat",
    "gpt-4o-mini": "muse-chat",
    "gpt-4.1": "muse-chat",
    "gpt-4.1-mini": "muse-chat",
    "gpt-5": "muse-chat",
    "gpt-5-mini": "muse-chat",
    "o3": "muse-chat",
    "o4-mini": "muse-chat",
    "claude-3-5-sonnet": "muse-chat",
    "claude-3-7-sonnet": "muse-chat",
    "claude-sonnet-4": "muse-chat",
    "gemini-2.0-flash": "muse-chat",
    "gemini-2.5-pro": "muse-chat",
    "deepseek-chat": "muse-chat",
    "deepseek-reasoner": "muse-chat",
    "qwen": "muse-chat",
    "kimi": "muse-chat",
    "grok": "muse-chat",
    "dall-e-3": "muse-image",
    "gpt-image-1": "muse-image",
    "gemini-2.5-flash-image": "muse-image",
    "sora": "muse-video",
    "veo-3": "muse-video",
    "kling": "muse-video",
}

DEFAULT_BY_KIND: dict[ModelKind, str] = {
    "chat": "muse-chat",
    "image": "muse-image",
    "video": "muse-video",
}


def resolve_model(name: str | None, kind: ModelKind) -> ModelSpec:
    """Resolve a client-supplied model name to a ``ModelSpec`` of the expected kind.

    Unknown names fall back to the default model of that kind; a known model of the
    wrong kind is rejected so that e.g. ``muse-video`` is never silently used for chat.
    """
    if not name:
        return MODELS[DEFAULT_BY_KIND[kind]]
    key = ALIASES.get(name, name)
    spec = MODELS.get(key)
    if spec is None:
        return MODELS[DEFAULT_BY_KIND[kind]]
    if spec.kind != kind:
        raise InvalidRequest(f"model '{name}' is a {spec.kind} model, expected {kind}")
    return spec
