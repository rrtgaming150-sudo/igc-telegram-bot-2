"""
ai_solver.py — AI-powered answer engine for India Genius Challenge.

Supported providers (all free tier):
  - groq     : fastest (~100–200 ms), uses llama-3.1-8b-instant
  - gemini   : generous free quota, uses gemini-1.5-flash
  - openrouter: many free models, uses mistralai/mistral-7b-instruct:free

Configuration is stored in answers_cache.json under the "ai_config" key:
  {
    "provider": "groq",
    "api_key":  "gsk_..."
  }

Usage:
  from ai_solver import AIConfig, ask_ai

  cfg = AIConfig.load()
  answer = await ask_ai(cfg, question_text, options)
  # returns the exact option string, or None on failure
"""

import asyncio
import json
import os
import re

import aiohttp

# ─── Config ──────────────────────────────────────────────────────────────────

CACHE_FILE = "answers_cache.json"
AI_CONFIG_KEY = "ai_config"

PROVIDER_ENDPOINTS = {
    "groq": "https://api.groq.com/openai/v1/chat/completions",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent",
    "openrouter": "https://openrouter.ai/api/v1/chat/completions",
}

PROVIDER_MODELS = {
    "groq": "llama-3.1-8b-instant",
    "gemini": "gemini-1.5-flash",
    "openrouter": "mistralai/mistral-7b-instruct:free",
}

PROVIDER_TIMEOUT = {
    "groq": 5,
    "gemini": 8,
    "openrouter": 10,
}


class AIConfig:
    """Holds provider + key. Load from / save to cache file."""

    def __init__(self, provider: str = "", api_key: str = ""):
        self.provider = provider.lower().strip()
        self.api_key = api_key.strip()

    @property
    def is_configured(self) -> bool:
        return bool(self.provider and self.api_key)

    # ── persistence ──────────────────────────────────────────────────────────

    @classmethod
    def load(cls) -> "AIConfig":
        if not os.path.exists(CACHE_FILE):
            return cls()
        try:
            with open(CACHE_FILE, "r") as f:
                data = json.load(f)
            cfg = data.get(AI_CONFIG_KEY, {})
            return cls(cfg.get("provider", ""), cfg.get("api_key", ""))
        except Exception:
            return cls()

    def save(self) -> None:
        data: dict = {}
        if os.path.exists(CACHE_FILE):
            try:
                with open(CACHE_FILE, "r") as f:
                    data = json.load(f)
            except Exception:
                data = {}
        data[AI_CONFIG_KEY] = {"provider": self.provider, "api_key": self.api_key}
        with open(CACHE_FILE, "w") as f:
            json.dump(data, f, indent=2)

    def __repr__(self) -> str:
        masked = (self.api_key[:6] + "…" + self.api_key[-4:]) if len(self.api_key) > 12 else "***"
        return f"AIConfig(provider={self.provider!r}, key={masked})"


# ─── Prompt builder ──────────────────────────────────────────────────────────

def _build_prompt(question_text: str, options: list[str]) -> str:
    """
    Minimal, unambiguous prompt.  The model must reply with ONLY the exact
    option text — no explanation, no prefix, no punctuation outside the answer.
    """
    opts_block = "\n".join(f"{chr(65 + i)}) {opt}" for i, opt in enumerate(options))
    return (
        "You are answering a multiple-choice quiz question. "
        "Reply with ONLY the exact text of the correct option — nothing else, "
        "no letter, no explanation.\n\n"
        f"Question: {question_text}\n\n"
        f"Options:\n{opts_block}"
    )


# ─── Provider-specific callers ────────────────────────────────────────────────

async def _call_groq(session: aiohttp.ClientSession, cfg: AIConfig,
                     prompt: str) -> str | None:
    headers = {
        "Authorization": f"Bearer {cfg.api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": PROVIDER_MODELS["groq"],
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 60,
        "temperature": 0,
    }
    timeout = aiohttp.ClientTimeout(total=PROVIDER_TIMEOUT["groq"])
    async with session.post(PROVIDER_ENDPOINTS["groq"],
                            headers=headers, json=payload,
                            timeout=timeout) as resp:
        if resp.status != 200:
            return None
        data = await resp.json(content_type=None)
    return data["choices"][0]["message"]["content"].strip()


async def _call_gemini(session: aiohttp.ClientSession, cfg: AIConfig,
                       prompt: str) -> str | None:
    url = f"{PROVIDER_ENDPOINTS['gemini']}?key={cfg.api_key}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": 60, "temperature": 0},
    }
    timeout = aiohttp.ClientTimeout(total=PROVIDER_TIMEOUT["gemini"])
    async with session.post(url, json=payload, timeout=timeout) as resp:
        if resp.status != 200:
            return None
        data = await resp.json(content_type=None)
    return data["candidates"][0]["content"]["parts"][0]["text"].strip()


async def _call_openrouter(session: aiohttp.ClientSession, cfg: AIConfig,
                           prompt: str) -> str | None:
    headers = {
        "Authorization": f"Bearer {cfg.api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://www.indiageniuschallenge.com",
    }
    payload = {
        "model": PROVIDER_MODELS["openrouter"],
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 60,
        "temperature": 0,
    }
    timeout = aiohttp.ClientTimeout(total=PROVIDER_TIMEOUT["openrouter"])
    async with session.post(PROVIDER_ENDPOINTS["openrouter"],
                            headers=headers, json=payload,
                            timeout=timeout) as resp:
        if resp.status != 200:
            return None
        data = await resp.json(content_type=None)
    return data["choices"][0]["message"]["content"].strip()


_CALLERS = {
    "groq": _call_groq,
    "gemini": _call_gemini,
    "openrouter": _call_openrouter,
}


# ─── Answer matching ──────────────────────────────────────────────────────────

def _match_to_option(raw: str, options: list[str]) -> str | None:
    """
    Map the model's raw reply to an exact option string.

    Strategy (in order):
    1. Exact match (case-insensitive, stripped)
    2. Option starts with the raw reply
    3. Raw reply starts with the option (model included extra words)
    4. Substring match
    5. If the model returned a letter (A/B/C/D), use the corresponding option
    """
    if not raw:
        return None

    raw_clean = raw.strip().lower()

    # 1. Exact
    for opt in options:
        if opt.strip().lower() == raw_clean:
            return opt

    # 2. Option starts with raw
    for opt in options:
        if opt.strip().lower().startswith(raw_clean):
            return opt

    # 3. Raw starts with option
    for opt in options:
        if raw_clean.startswith(opt.strip().lower()):
            return opt

    # 4. Substring
    for opt in options:
        if opt.strip().lower() in raw_clean or raw_clean in opt.strip().lower():
            return opt

    # 5. Letter prefix  (A / B / C / D)
    letter_match = re.match(r"^([a-dA-D])[).\s]", raw.strip())
    if letter_match:
        idx = ord(letter_match.group(1).upper()) - ord("A")
        if 0 <= idx < len(options):
            return options[idx]

    return None


# ─── Public API ──────────────────────────────────────────────────────────────

async def ask_ai(cfg: AIConfig,
                 question_text: str,
                 options: list[str],
                 session: aiohttp.ClientSession | None = None) -> str | None:
    """
    Ask the configured AI provider for the correct option.

    Returns the exact option string from `options`, or None if the call
    fails or the response cannot be matched to any option.

    Pass an existing `session` to reuse connections; if None a temporary
    session is created for this call only.
    """
    if not cfg.is_configured:
        return None

    caller = _CALLERS.get(cfg.provider)
    if caller is None:
        return None

    prompt = _build_prompt(question_text, options)

    own_session = session is None
    if own_session:
        session = aiohttp.ClientSession()

    try:
        raw = await caller(session, cfg, prompt)
        return _match_to_option(raw, options)
    except asyncio.TimeoutError:
        return None
    except Exception:
        return None
    finally:
        if own_session:
            await session.close()


async def test_ai_connection(cfg: AIConfig,
                             session: aiohttp.ClientSession | None = None
                             ) -> dict:
    """
    Run a known test question through the AI and return a result dict:
      {
        "ok":       bool,
        "provider": str,
        "model":    str,
        "answer":   str | None,   # what the AI returned
        "correct":  bool,         # whether it matched the expected answer
        "latency":  float,        # seconds
        "error":    str | None,
      }
    """
    TEST_QUESTION = "What is the capital of India?"
    TEST_OPTIONS  = ["Mumbai", "New Delhi", "Kolkata", "Chennai"]
    TEST_EXPECTED = "New Delhi"

    import time

    result = {
        "ok": False,
        "provider": cfg.provider,
        "model": PROVIDER_MODELS.get(cfg.provider, "unknown"),
        "answer": None,
        "correct": False,
        "latency": 0.0,
        "error": None,
    }

    if not cfg.is_configured:
        result["error"] = "AI not configured. Use /setai <provider> <api_key>."
        return result

    if cfg.provider not in _CALLERS:
        result["error"] = f"Unknown provider '{cfg.provider}'. Use: groq, gemini, openrouter."
        return result

    own_session = session is None
    if own_session:
        session = aiohttp.ClientSession()

    t0 = time.perf_counter()
    try:
        answer = await ask_ai(cfg, TEST_QUESTION, TEST_OPTIONS, session=session)
        result["latency"] = round(time.perf_counter() - t0, 2)
        result["answer"] = answer
        result["ok"] = answer is not None
        result["correct"] = (answer == TEST_EXPECTED) if answer else False
    except Exception as exc:
        result["latency"] = round(time.perf_counter() - t0, 2)
        result["error"] = str(exc)
    finally:
        if own_session:
            await session.close()

    return result
