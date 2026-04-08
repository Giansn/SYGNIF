"""
Optional OpenVINO GenAI on Intel NPU for trade_overseer.

Enabled only when llm_client routes here (SYGNIF_LLM_BACKEND=npu).
Does not import openvino_genai until the pipeline is first used.
"""
from __future__ import annotations

import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout

logger = logging.getLogger("overseer.npu")

_pipe = None
_pipe_lock = threading.Lock()
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="npu_genai")

_MODEL_CANDIDATES = [
    os.environ.get("NPU_OV_MODEL"),
    os.path.expandvars(r"%USERPROFILE%\npu_models\qwen2.5-1.5b-instruct-int4-ov"),
    os.path.expandvars(r"%USERPROFILE%\npu_models\TinyLlama-1.1B-Chat-v1.0_ov"),
    os.path.expandvars(r"%USERPROFILE%\npu_models\deepseek-r1-qwen-7b-int4-ov"),
]


def _resolve_model_dir() -> str:
    for raw in _MODEL_CANDIDATES:
        if not raw:
            continue
        path = os.path.abspath(os.path.expanduser(raw))
        if os.path.isdir(path):
            return path
    raise FileNotFoundError(
        "NPU model not found. Set NPU_OV_MODEL or install a model under %USERPROFILE%\\npu_models\\"
    )


def _build_pipeline_config() -> dict:
    cache = os.environ.get(
        "NPU_CACHE_DIR",
        os.path.join(os.path.expanduser("~"), ".npucache_genai"),
    )
    os.makedirs(cache, exist_ok=True)
    return {
        "CACHE_DIR": cache,
        "GENERATE_HINT": os.environ.get("NPU_GENERATE_HINT", "BEST_PERF"),
        "PREFILL_HINT": os.environ.get("NPU_PREFILL_HINT", "DYNAMIC"),
        "MAX_PROMPT_LEN": int(os.environ.get("NPU_MAX_PROMPT_LEN", "512")),
        "MIN_RESPONSE_LEN": int(os.environ.get("NPU_MIN_RESPONSE_LEN", "64")),
    }


def _get_pipeline():
    global _pipe
    with _pipe_lock:
        if _pipe is not None:
            return _pipe
        os.environ.setdefault("OV_CPU_THREADS_NUM", os.environ.get("OV_CPU_THREADS_NUM", "2"))
        os.environ.setdefault("OMP_NUM_THREADS", os.environ.get("OMP_NUM_THREADS", "2"))
        import openvino_genai as ov_genai

        model_path = _resolve_model_dir()
        logger.info("Loading OpenVINO GenAI LLMPipeline on NPU from %s", model_path)
        _pipe = ov_genai.LLMPipeline(model_path, "NPU", _build_pipeline_config())
        return _pipe


def is_available() -> bool:
    try:
        _get_pipeline()
        return True
    except Exception as e:
        logger.warning("NPU GenAI not available: %s", e)
        return False


def evaluate_combined_prompt(full_prompt: str, timeout: int = 120) -> str | None:
    """
    Run one NPU generation. full_prompt should already include any system instructions.
    """
    max_new = int(os.environ.get("NPU_MAX_NEW_TOKENS", "256"))

    def _run():
        pipe = _get_pipeline()
        return pipe.generate(full_prompt, max_new_tokens=max_new)

    fut = _executor.submit(_run)
    try:
        text = fut.result(timeout=timeout)
        if text is None:
            return None
        out = str(text).strip()
        return out or None
    except FuturesTimeout:
        logger.warning(
            "NPU generation exceeded timeout=%ss (call may still run in background)",
            timeout,
        )
        return None
