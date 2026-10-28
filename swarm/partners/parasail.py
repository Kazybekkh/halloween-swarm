"""Parasail: the decision-making brain (scouts, grounding agent, commander) and memory embeddings."""
import threading
import time

from crewai import LLM
from crewai.hooks import register_after_llm_call_hook, register_before_llm_call_hook

from swarm.config import CONFIG
from swarm.env import need
from swarm.log import log
from swarm.ratelimit import PARASAIL_LIMITER


def parasail_llm(response_format=None, temperature: float = 0.2) -> LLM:
    return LLM(
        model=CONFIG.parasail_model,
        provider="openai",
        base_url=CONFIG.parasail_base,
        api_key=need("PARASAIL_API_KEY"),
        temperature=temperature,
        max_tokens=CONFIG.parasail_max_tokens,  # reasoning models (GLM-5.3) need room before any content appears
        max_retries=6,  # the OpenAI SDK honours Retry-After on 429
        timeout=180,
        response_format=response_format,
    )


def parasail_embedder() -> dict:
    """CrewAI embedder config pointing the OpenAI-compatible client at Parasail /v1/embeddings."""
    return {
        "provider": "openai",
        "config": {
            "api_key": need("PARASAIL_API_KEY"),
            "api_base": CONFIG.parasail_base,
            "model_name": CONFIG.parasail_embed_model,
        },
    }


_call = threading.local()


def _is_parasail(context) -> bool:
    return getattr(context.llm, "base_url", None) == CONFIG.parasail_base


def _throttle(context) -> None:
    if not _is_parasail(context):
        return
    queued = time.monotonic()
    PARASAIL_LIMITER.acquire()
    _call.wait_ms = round((time.monotonic() - queued) * 1000)
    _call.started = time.monotonic()


def _timed(context) -> None:
    if not _is_parasail(context) or not hasattr(_call, "started"):
        return
    log.info("llm_call", role=getattr(context.agent, "role", None) or "direct", iteration=context.iterations,
             wait_ms=_call.wait_ms, call_ms=round((time.monotonic() - _call.started) * 1000),
             response_chars=len(context.response or ""))
    del _call.started


register_before_llm_call_hook(_throttle)
register_after_llm_call_hook(_timed)
