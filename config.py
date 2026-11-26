import json
import os
import re
import sys
from dotenv import load_dotenv
from openai import OpenAI
from kaggle_bridge import get_kaggle_status, start_kaggle_worker, stop_kaggle_worker
import httpx

BASE_DIR = (
    os.path.dirname(os.path.abspath(sys.executable))
    if getattr(sys, "frozen", False)
    else os.path.dirname(os.path.abspath(__file__))
)
load_dotenv(os.path.join(BASE_DIR, ".env"))

MEMORY_FILE = os.path.join(os.path.expanduser("~"), ".ciel_memory.json")
MAX_OUTPUT_CHARS = 4000

MODELS = {
    "groq": {
        "provider": "groq",
        "model": "openai/gpt-oss-20b",
        "base_url": "https://api.groq.com/openai/v1",
        "api_key_env": "GROQ_API_KEY",
    },
    "qwen27b": {
        "provider": "groq",
        "model": "qwen/qwen3.8-27b",
        "base_url": "https://api.groq.com/openai/v1",
        "api_key_env": "GROQ_API_KEY",
    },
    "gpt120b": {
        "provider": "groq",
        "model": "openai/gpt-oss-120b",
        "base_url": "https://api.groq.com/openai/v1",
        "api_key_env": "GROQ_API_KEY",
    },
    "flash": {
        "provider": "gemini",
        "model": "gemini-3.5-flash-lite",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "api_key_env": "GEMINI_API_KEY",
    },
    "pro": {
        "provider": "gemini",
        "model": "gemini-3.8-flash",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "api_key_env": "GEMINI_API_KEY",
    },
    "hydrogen": {
        "provider": "gemini",
        "model": "gemini-4-hydrogen",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "api_key_env": "GEMINI_API_KEY",
    },
    "north-code": {
        "provider": "openrouter",
        "model": "cohere/north-mini-code:free",
        "base_url": "https://openrouter.ai/api/v1",
        "api_key_env": "OPEN_ROUTER_API_KEY",
    },
    "flint": {
        "provider": "openrouter",
        "model": "recursive/flint-v1.0:free",
        "base_url": "https://openrouter.ai/api/v1",
        "api_key_env": "OPEN_ROUTER_API_KEY",
    },
    "free": {
        "provider": "openrouter",
        "model": "openrouter/free",
        "base_url": "https://openrouter.ai/api/v1",
        "api_key_env": "OPEN_ROUTER_API_KEY",
    },
}

LOCAL_MODELS_PATH = os.path.join(BASE_DIR, "local_models.json")
if os.path.exists(LOCAL_MODELS_PATH):
    try:
        with open(LOCAL_MODELS_PATH, "r", encoding="utf-8") as f:
            local_data = json.load(f)
            if isinstance(local_data, dict):
                MODELS.update(local_data)
    except Exception as e:
        print(f"Warning: Could not load local_models.json: {e}")

current_alias = "auto"
current_effort = "low"


def resolve_api_key(cfg: dict) -> str:
    if cfg.get("api_key"):
        return cfg["api_key"]
    env_name = cfg.get("api_key_env")
    return os.getenv(env_name, "") if env_name else ""


def resolve_base_url(cfg: dict) -> str:
    if cfg.get("base_url"):
        return cfg["base_url"]
    env_name = cfg.get("base_url_env")
    return os.getenv(env_name, "") if env_name else ""


def alias_available(alias: str) -> bool:
    cfg = MODELS.get(alias)
    if not cfg or not resolve_base_url(cfg):
        return False
    if cfg.get("provider") in ("ollama", "openai-compatible") or alias in ("local", "kaggle"):
        return True
    return bool(resolve_api_key(cfg))


def has_any_api_key() -> bool:
    return any(alias_available(k) for k in MODELS)


def resolve_default_smart_alias() -> str:
    for candidate in ("qwen27b", "gpt120b", "flash", "north-code", "free"):
        if alias_available(candidate):
            return candidate
    return next(iter(MODELS))

_CLIENT_CACHE: dict[tuple[str, str], OpenAI] = {}



_CLIENT_CACHE: dict[tuple[str, str], OpenAI] = {}
_HTTP_CLIENT = httpx.Client(
    transport=httpx.HTTPTransport(local_address="0.0.0.0"),
    limits=httpx.Limits(max_keepalive_connections=10, max_connections=20, keepalive_expiry=60.0),
    timeout=httpx.Timeout(30.0, connect=5.0),
)


def get_client_and_model(alias: str):
    if alias == "kaggle":
        status, _ = get_kaggle_status()
        if status != "running":
            alias = resolve_default_smart_alias()
    elif alias == "auto":
        alias = resolve_default_smart_alias()
    cfg = MODELS[alias]
    base_url = resolve_base_url(cfg)
    api_key = resolve_api_key(cfg) or "not-set"
    cache_key = (base_url, api_key)
    client = _CLIENT_CACHE.get(cache_key)
    if client is None:
        client = OpenAI(
            base_url=base_url,
            api_key=api_key,
            http_client=_HTTP_CLIENT,
            max_retries=0,
        )
        _CLIENT_CACHE[cache_key] = client
    return client, cfg["model"]

def switch_model(new_alias: str) -> str:
    global current_alias
    new_alias = new_alias.strip().lower()
    if new_alias != "auto" and new_alias not in MODELS:
        return f"Unknown model alias '{new_alias}'."

    current_alias = new_alias

    if new_alias == "kaggle":
        return f"[Switched to kaggle -> {start_kaggle_worker()}]"

    stop_msg = stop_kaggle_worker()
    if stop_msg:
        print(f"[{stop_msg}]")

    _, active_model = get_client_and_model(current_alias)
    return f"[Switched to {current_alias} -> {active_model}]"

def choose_routed_alias(alias: str, user_text: str, step: int = 1) -> str:
    if alias != "auto":
        return alias
    u = (user_text or "").lower().strip()
    is_complex = (
        step > 1
        or len(u) > 120
        or bool(
            re.search(
                r"\b(read|edit|write|create|patch|fix|debug|run|execute|install|search|browse|"
                r"look up|screen|screenshot|play|stop|skip|music|remember|save memory|forget|find|"
                r"grep|git|check|inspect|files|folder|project|repo|audit|desktop|ciel|codebase|"
                r"launch|open|photo|picture|gallery|settings|ls|cat|mkdir|rm|cp|mv)\b"
                r"|https?://|\.py\b|\.json\b|\.sh\b",
                u,
            )
        )
    )
    if is_complex:
        return resolve_default_smart_alias()
    for fast_candidate in ("qwen27b", "flash", "groq", "free"):
        if alias_available(fast_candidate):
            return fast_candidate
    return resolve_default_smart_alias()
