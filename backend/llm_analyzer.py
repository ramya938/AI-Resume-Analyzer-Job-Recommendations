"""
llm_analyzer.py — LangChain + Gemini LLM Analysis Engine
===========================================================
File: backend/llm_analyzer.py

Provides a full-featured LLM analysis layer for resume processing using
Google Gemini via LangChain. Designed as the single source of truth for
all AI interactions in the project.

Public Functions:
    load_llm()              - Initialize and return configured Gemini LLM
    test_connection()       - Verify API key and model connectivity
    analyze_resume()        - Run complete structured resume analysis
    extract_json_response() - Parse and validate JSON from LLM output
    track_token_usage()     - Estimate and log token usage + cost
    retry_failed_requests() - Decorator / wrapper for retry logic

Prompt Templates:
    strengths_prompt    - Extract resume strengths
    weaknesses_prompt   - Identify weaknesses and gaps
    skills_prompt       - Identify + recommend skills
    suggestions_prompt  - Career and improvement suggestions

Features:
    - API key loaded from .env via python-dotenv
    - Connection testing with friendly error messages
    - Exponential backoff retry (3 attempts, configurable)
    - Quota/rate-limit errors are NOT retried (fail fast)
    - Per-request timeout handling
    - Structured JSON output with schema validation
    - Full logging at DEBUG / INFO / WARNING / ERROR levels
    - Token usage estimation and cumulative cost tracking
"""

import os
import json
import re
import time
import logging
import functools
from datetime import datetime
from dataclasses import dataclass, field
from typing import Optional, Callable

from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

load_dotenv()

logger = logging.getLogger(__name__)


# ===========================================================================
# Configuration Constants
# ===========================================================================

DEFAULT_MODEL       = "gemini-3.6-flash"    # Gemini 3.6 Flash — current recommended model
DEFAULT_TEMPERATURE = 0.2
MAX_OUTPUT_TOKENS   = 4096
MAX_INPUT_CHARS     = 12_000
REQUEST_TIMEOUT     = 60

# Model fallback chain — tried in order when one is unavailable/quota-exhausted
# Updated Sep 2026: gemini-2.0-flash removed by Google, replaced with 3.x models
FALLBACK_MODELS = [
    "gemini-3.6-flash",        # Primary — current recommended model
    "gemini-3.8-flash",        # Fallback 1 — newest available
    "gemini-3.5-flash",        # Fallback 2
    "gemini-3.1-flash-lite",   # Fallback 3 — lighter, faster
    "gemini-flash-latest",     # Fallback 4 — alias, always points to latest
    "gemini-flash-lite-latest",# Fallback 5
]


RETRY_MAX_ATTEMPTS  = 3
RETRY_BASE_DELAY    = 2.0
RETRY_MAX_DELAY     = 16.0

PRICE_INPUT_PER_1M  = 0.075
PRICE_OUTPUT_PER_1M = 0.30
CHARS_PER_TOKEN     = 4.0


# ===========================================================================
# Token Usage Tracker
# ===========================================================================

@dataclass
class TokenUsage:
    total_requests:       int   = 0
    total_input_tokens:   int   = 0
    total_output_tokens:  int   = 0
    failed_requests:      int   = 0
    total_retries:        int   = 0
    session_start:        str   = field(default_factory=lambda: datetime.now().isoformat())

    @property
    def total_tokens(self) -> int:
        return self.total_input_tokens + self.total_output_tokens

    @property
    def estimated_cost_usd(self) -> float:
        input_cost  = (self.total_input_tokens  / 1_000_000) * PRICE_INPUT_PER_1M
        output_cost = (self.total_output_tokens / 1_000_000) * PRICE_OUTPUT_PER_1M
        return round(input_cost + output_cost, 6)

    def to_dict(self) -> dict:
        return {
            "total_requests":      self.total_requests,
            "total_input_tokens":  self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_tokens":        self.total_tokens,
            "failed_requests":     self.failed_requests,
            "total_retries":       self.total_retries,
            "estimated_cost_usd":  self.estimated_cost_usd,
            "session_start":       self.session_start,
        }

    def log_summary(self) -> None:
        logger.info(
            "Token Usage | requests=%d | in=%d | out=%d | total=%d | "
            "cost=$%.6f | retries=%d | failures=%d",
            self.total_requests,
            self.total_input_tokens,
            self.total_output_tokens,
            self.total_tokens,
            self.estimated_cost_usd,
            self.total_retries,
            self.failed_requests,
        )


_usage = TokenUsage()


def track_token_usage(
    input_text: str = "",
    output_text: str = "",
    success: bool = True,
    retries_used: int = 0,
) -> dict:
    input_tokens  = max(1, int(len(input_text)  / CHARS_PER_TOKEN))
    output_tokens = max(1, int(len(output_text) / CHARS_PER_TOKEN))

    _usage.total_requests      += 1
    _usage.total_input_tokens  += input_tokens
    _usage.total_output_tokens += output_tokens
    _usage.total_retries       += retries_used
    if not success:
        _usage.failed_requests += 1

    per_request_cost = round(
        (input_tokens  / 1_000_000) * PRICE_INPUT_PER_1M +
        (output_tokens / 1_000_000) * PRICE_OUTPUT_PER_1M,
        6,
    )

    stats = {
        "request_input_tokens":  input_tokens,
        "request_output_tokens": output_tokens,
        "request_total_tokens":  input_tokens + output_tokens,
        "request_cost_usd":      per_request_cost,
        "retries_used":          retries_used,
        "success":               success,
        "session_totals":        _usage.to_dict(),
    }

    logger.info(
        "Token usage | in=%d | out=%d | cost=$%.6f | retries=%d",
        input_tokens, output_tokens, per_request_cost, retries_used,
    )
    return stats


def get_usage_summary() -> dict:
    return _usage.to_dict()


def reset_usage() -> None:
    global _usage
    _usage = TokenUsage()
    logger.debug("Token usage tracker reset.")


# ===========================================================================
# Retry Decorator
# ===========================================================================

def retry_failed_requests(
    max_attempts: int = RETRY_MAX_ATTEMPTS,
    base_delay:   float = RETRY_BASE_DELAY,
    max_delay:    float = RETRY_MAX_DELAY,
    retryable_exceptions: tuple = (Exception,),
):
    """
    Decorator with exponential backoff retry.

    NON-retryable (raises immediately):
        - Auth / API key errors
        - Quota / rate-limit errors (retrying makes these WORSE)

    Retryable: transient network errors, timeouts, 5xx errors.
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exc = None
            retries_used = 0

            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)

                except retryable_exceptions as exc:
                    last_exc = exc
                    err_msg = str(exc).lower()

                    # Non-retryable: auth errors
                    if any(k in err_msg for k in (
                        "api_key", "api key", "invalid_api",
                        "unauthenticated", "permission",
                    )):
                        logger.error("Non-retryable auth error: %s", exc)
                        raise

                    # Non-retryable: quota / rate-limit
                    # Retrying immediately makes quota exhaustion WORSE.
                    if any(k in err_msg for k in (
                        "quota", "resource_exhausted", "429",
                        "rate limit", "rate_limit", "too many requests",
                    )):
                        logger.warning("Quota/rate-limit hit - not retrying: %s", exc)
                        raise

                    if attempt == max_attempts:
                        logger.error(
                            "All %d attempts failed. Last error: %s",
                            max_attempts, exc,
                        )
                        break

                    delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
                    retries_used += 1
                    _usage.total_retries += 1

                    logger.warning(
                        "Attempt %d/%d failed: %s - retrying in %.1fs",
                        attempt, max_attempts, exc, delay,
                    )
                    time.sleep(delay)

            raise last_exc

        return wrapper
    return decorator


# ===========================================================================
# Prompt Templates
# ===========================================================================

_SYSTEM_ROLE = (
    "You are a senior HR professional and resume coach with 15+ years of experience "
    "evaluating resumes across technology, finance, healthcare, and business sectors. "
    "You provide specific, actionable, and honest feedback. "
    "You MUST respond ONLY with a valid JSON object - no markdown, no explanation text."
)

strengths_prompt = ChatPromptTemplate.from_messages([
    ("system", _SYSTEM_ROLE),
    ("human", """Analyze the following resume and identify its TOP STRENGTHS.

RESUME:
---
{resume_text}
---

Return ONLY this JSON object:
{{
  "strengths": [
    "<specific strength 1 with evidence from resume>",
    "<specific strength 2 with evidence from resume>",
    "<specific strength 3 with evidence from resume>",
    "<specific strength 4 with evidence from resume>",
    "<specific strength 5 with evidence from resume>"
  ]
}}"""),
])

weaknesses_prompt = ChatPromptTemplate.from_messages([
    ("system", _SYSTEM_ROLE),
    ("human", """Analyze the following resume and identify its WEAKNESSES and gaps.

RESUME:
---
{resume_text}
---

Return ONLY this JSON object:
{{
  "weaknesses": [
    "<specific weakness 1 - be constructive>",
    "<specific weakness 2 - be constructive>",
    "<specific weakness 3 - be constructive>"
  ],
  "missing_sections": [
    "<section that is absent or underdeveloped>"
  ],
  "ats_issues": [
    "<ATS or formatting concern>"
  ]
}}"""),
])

skills_prompt = ChatPromptTemplate.from_messages([
    ("system", _SYSTEM_ROLE),
    ("human", """Analyze the following resume for skills.

RESUME:
---
{resume_text}
---

Return ONLY this JSON object:
{{
  "identified_skills": [
    "<skill found in resume 1>",
    "<skill found in resume 2>",
    "<skill found in resume 3>",
    "<skill found in resume 4>",
    "<skill found in resume 5>",
    "<skill found in resume 6>",
    "<skill found in resume 7>",
    "<skill found in resume 8>"
  ],
  "recommended_skills": [
    "<high-value skill to add 1>",
    "<high-value skill to add 2>",
    "<high-value skill to add 3>",
    "<high-value skill to add 4>",
    "<high-value skill to add 5>"
  ],
  "skill_gaps": [
    "<critical gap relative to the target industry>"
  ]
}}"""),
])

suggestions_prompt = ChatPromptTemplate.from_messages([
    ("system", _SYSTEM_ROLE),
    ("human", """Based on the following resume, provide career and improvement suggestions.

RESUME:
---
{resume_text}
---

Return ONLY this JSON object:
{{
  "overall_score": <integer 0-100>,
  "score_explanation": "<1-2 sentences explaining the score>",
  "experience_level": "<Entry-Level | Junior | Mid-Level | Senior | Executive>",
  "industry": "<primary industry or domain>",
  "professional_summary": "<2-3 sentence summary of this candidate>",
  "career_suggestions": [
    "<job title that fits this resume>",
    "<job title that fits this resume>",
    "<job title that fits this resume>"
  ],
  "improvement_tips": [
    "<specific actionable tip 1>",
    "<specific actionable tip 2>",
    "<specific actionable tip 3>"
  ]
}}

Scoring rubric:
  90-100: Exceptional
  75-89:  Good
  60-74:  Average
  40-59:  Below average
  0-39:   Poor"""),
])

_full_analysis_prompt = ChatPromptTemplate.from_messages([
    ("system", _SYSTEM_ROLE),
    ("human", """Perform a comprehensive resume analysis and return ALL of the following
in a single JSON object.

RESUME TEXT:
---
{resume_text}
---

Return ONLY this JSON object (no markdown fences, no extra text):
{{
  "overall_score": <integer 0-100>,
  "score_explanation": "<1-2 sentences>",
  "professional_summary": "<2-3 sentence professional summary>",
  "experience_level": "<Entry-Level | Junior | Mid-Level | Senior | Executive>",
  "industry": "<primary industry>",
  "strengths": ["<strength 1>", "<strength 2>", "<strength 3>", "<strength 4>", "<strength 5>"],
  "weaknesses": ["<weakness 1>", "<weakness 2>", "<weakness 3>"],
  "identified_skills": ["<skill 1>", "<skill 2>", "<skill 3>", "<skill 4>", "<skill 5>",
                        "<skill 6>", "<skill 7>", "<skill 8>", "<skill 9>", "<skill 10>"],
  "recommended_skills": ["<rec skill 1>", "<rec skill 2>", "<rec skill 3>",
                         "<rec skill 4>", "<rec skill 5>"],
  "career_suggestions": ["<job title 1>", "<job title 2>", "<job title 3>"],
  "improvement_tips": ["<tip 1>", "<tip 2>", "<tip 3>"],
  "missing_sections": ["<missing section>"],
  "ats_issues": ["<ATS concern>"]
}}"""),
])


# ===========================================================================
# Core Functions
# ===========================================================================

def load_llm(
    api_key:     Optional[str] = None,
    model_name:  Optional[str] = None,
    temperature: float = DEFAULT_TEMPERATURE,
    max_tokens:  int   = MAX_OUTPUT_TOKENS,
) -> ChatGoogleGenerativeAI:
    """
    Initialize and return a configured Gemini LLM instance.

    Auto-fallback: if the requested model returns 404 (not found) or
    429 (quota exhausted), automatically tries the next model in
    FALLBACK_MODELS until one works.
    """
    resolved_key = api_key or os.getenv("GOOGLE_API_KEY", "")

    if not resolved_key or resolved_key == "your_google_api_key_here":
        raise ValueError(
            "GOOGLE_API_KEY is not set. "
            "Please add it to your .env file and restart the app.\n"
            "Get a free key at: https://aistudio.google.com/apikey"
        )

    # Build the models-to-try list: configured model first, then fallbacks
    configured = model_name or os.getenv("GEMINI_MODEL", DEFAULT_MODEL)
    models_to_try = [configured] + [
        m for m in FALLBACK_MODELS if m != configured
    ]

    last_exc = None
    for attempt_model in models_to_try:
        try:
            llm = ChatGoogleGenerativeAI(
                model=attempt_model,
                google_api_key=resolved_key,
                temperature=temperature,
                max_output_tokens=max_tokens,
                timeout=REQUEST_TIMEOUT,
                convert_system_message_to_human=False,
            )
            logger.info("LLM loaded: model=%s", attempt_model)
            return llm

        except Exception as exc:
            err = str(exc).lower()
            # Only fall through on 404 (not found / deprecated)
            if "404" in err or "not_found" in err or "not found" in err:
                logger.warning("Model '%s' not found — trying next fallback", attempt_model)
                last_exc = exc
                continue
            # For any other error (auth, quota, network) raise immediately
            raise

    # All models failed with 404
    raise RuntimeError(
        f"None of the configured models are available: {models_to_try}. "
        "Please check your API key or try again later."
    ) from last_exc


def test_connection(api_key: Optional[str] = None) -> dict:
    try:
        llm = load_llm(api_key=api_key)
        model_name = os.getenv("GEMINI_MODEL", DEFAULT_MODEL)

        start_time = time.time()
        probe_messages = [
            HumanMessage(content='Reply with exactly: {"status": "ok"}'),
        ]

        response = llm.invoke(probe_messages)
        latency_ms = int((time.time() - start_time) * 1000)
        raw = StrOutputParser().invoke(response)

        logger.info("Connection test passed: model=%s | latency=%dms", model_name, latency_ms)

        return {
            "success":    True,
            "model":      model_name,
            "latency_ms": latency_ms,
            "response":   raw[:100],
            "message":    f"Connected to {model_name} successfully ({latency_ms}ms).",
        }

    except ValueError as exc:
        return {"success": False, "model": "", "latency_ms": 0, "message": str(exc)}

    except Exception as exc:
        err = str(exc)
        err_lower = err.lower()
        if "API_KEY" in err.upper() or "api key" in err_lower:
            msg = "Invalid API key. Please check GOOGLE_API_KEY in .env."
        elif any(k in err_lower for k in ("quota", "resource_exhausted", "429", "rate limit")):
            msg = "API quota exceeded. Please wait before retrying."
        elif "network" in err_lower or "connect" in err_lower:
            msg = "Network error. Please check your internet connection."
        else:
            msg = f"Connection failed: {err}"

        logger.error("Connection test failed: %s", exc)
        return {"success": False, "model": "", "latency_ms": 0, "message": msg}


def extract_json_response(raw_text: str, schema_keys: Optional[list] = None) -> dict:
    if not raw_text or not raw_text.strip():
        raise ValueError("LLM returned an empty response.")

    text = raw_text.strip()

    # Strategy 1: Remove markdown fences then parse
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE | re.MULTILINE)
    text = re.sub(r"\s*```\s*$", "", text, flags=re.MULTILINE)
    text = text.strip()

    try:
        data = json.loads(text)
        logger.debug("JSON extracted via strategy 1 (direct parse)")
        return _validate_json_schema(data, schema_keys)
    except json.JSONDecodeError:
        pass

    # Strategy 2: Find { ... } boundaries
    start = text.find("{")
    end   = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidate = text[start : end + 1]
        try:
            data = json.loads(candidate)
            logger.debug("JSON extracted via strategy 2 (boundary detection)")
            return _validate_json_schema(data, schema_keys)
        except json.JSONDecodeError:
            pass

    # Strategy 3: Repair truncated JSON
    repaired = _attempt_json_repair(text)
    if repaired:
        try:
            data = json.loads(repaired)
            logger.debug("JSON extracted via strategy 3 (repair)")
            return _validate_json_schema(data, schema_keys)
        except json.JSONDecodeError:
            pass

    logger.error("All JSON extraction strategies failed. Raw: %s", raw_text[:400])
    raise ValueError(
        "Could not extract valid JSON from LLM response. "
        "The model may have returned unexpected formatting."
    )


def _attempt_json_repair(text: str) -> Optional[str]:
    try:
        start = text.find("{")
        if start == -1:
            return None
        text = text[start:]
        text = re.sub(r",\s*([}\]])", r"\1", text)
        open_braces   = text.count("{") - text.count("}")
        open_brackets = text.count("[") - text.count("]")
        text = text.rstrip(", \n\r\t")
        text += "]" * max(0, open_brackets)
        text += "}" * max(0, open_braces)
        return text
    except Exception:
        return None


def _validate_json_schema(data: dict, schema_keys: Optional[list]) -> dict:
    if schema_keys:
        for key in schema_keys:
            if key not in data:
                logger.warning("Expected JSON key '%s' is missing from LLM response.", key)
    return data


def _extract_quota_retry_delay(err_str: str) -> Optional[int]:
    """Extract the suggested retry delay in seconds from an API quota error string."""
    match = re.search(r"retryDelay[\"']?\s*[:=]\s*[\"']?(\d+)", err_str)
    if match:
        return int(match.group(1))
    # Also try "retry in Xs" pattern
    match2 = re.search(r"retry(?:\s+in)?\s+(\d+)[\.\s]", err_str.lower())
    if match2:
        return int(match2.group(1))
    return None


# ===========================================================================
# Resume Analysis - Core Function
# ===========================================================================

def analyze_resume(
    resume_text: str,
    mode: str = "full",
    api_key: Optional[str] = None,
) -> dict:
    """
    Run AI-powered resume analysis with retry, timeout, and token tracking.

    Error types returned in result["error_type"]:
        "auth"    - Bad API key
        "quota"   - Rate limit or daily quota exhausted
        "timeout" - Request timed out
        "unknown" - Other error

    For quota errors, result["message"] starts with:
        "QUOTA_DAILY:"   - daily quota exhausted
        "QUOTA_MINUTE:N:" - per-minute throttle, N = seconds to wait
    """
    if not resume_text or not resume_text.strip():
        return {
            "success": False, "data": {}, "message": "Resume text is empty.",
            "error_type": "validation", "token_usage": {}, "retries_used": 0,
        }

    text = resume_text.strip()
    if len(text) < 50:
        return {
            "success": False, "data": {},
            "message": "Resume text is too short. Please upload a complete resume.",
            "error_type": "validation", "token_usage": {}, "retries_used": 0,
        }

    if len(text) > MAX_INPUT_CHARS:
        logger.warning("Truncating resume from %d to %d chars", len(text), MAX_INPUT_CHARS)
        text = text[:MAX_INPUT_CHARS]

    retries_used = [0]

    @retry_failed_requests(max_attempts=RETRY_MAX_ATTEMPTS, base_delay=RETRY_BASE_DELAY)
    def _call_llm(prompt_template: ChatPromptTemplate, input_vars: dict) -> str:
        llm = load_llm(api_key=api_key)
        chain = prompt_template | llm | StrOutputParser()
        return chain.invoke(input_vars)

    try:
        if mode == "sectioned":
            return _run_sectioned_analysis(text, _call_llm, retries_used)
        else:
            return _run_full_analysis(text, _call_llm, retries_used)

    except ValueError as exc:
        logger.error("Config/validation error: %s", exc)
        usage = track_token_usage(text, "", success=False, retries_used=retries_used[0])
        return {
            "success": False, "data": {}, "message": str(exc),
            "error_type": "config", "token_usage": usage, "retries_used": retries_used[0],
        }

    except Exception as exc:
        err = str(exc)
        err_lower = err.lower()

        # ── Model deprecated / not found ─────────────────────────
        if any(k in err_lower for k in (
            "no longer available", "not found", "is not available",
            "model not found", "404", "deprecated",
        )):
            msg = (
                f"Model no longer available. Google has retired this model. "
                f"The system will auto-switch to a newer model. Please try again."
            )
            error_type = "model_deprecated"
            logger.warning("Model deprecated, will fallback: %s", err[:200])

        # ── Auth error ────────────────────────────────────────────
        elif "API_KEY" in err.upper() or "api key" in err_lower:
            msg = "Invalid API key. Please check GOOGLE_API_KEY in your .env file."
            error_type = "auth"

        # ── Quota / rate-limit ────────────────────────────────────
        elif any(k in err_lower for k in (
            "quota", "resource_exhausted", "429",
            "rate limit", "rate_limit", "too many requests",
        )):
            retry_secs = _extract_quota_retry_delay(err)
            is_daily = (
                "per_day" in err_lower
                or "perday" in err_lower
                or "free_tier_requests" in err_lower
                or (retry_secs is not None and retry_secs > 300)
            )
            if is_daily:
                msg = (
                    "QUOTA_DAILY: Your free Gemini API daily quota is exhausted. "
                    "It resets at midnight Pacific Time (PT). "
                    "To fix: wait until tomorrow, or add a different API key in Settings."
                )
            elif retry_secs:
                msg = f"QUOTA_MINUTE:{retry_secs}: Rate limit hit. Please wait {retry_secs} seconds and try again."
            else:
                msg = "QUOTA_MINUTE:60: API rate limit hit. Please wait about 60 seconds and try again."
            error_type = "quota"

        # ── Timeout ───────────────────────────────────────────────
        elif "timeout" in err_lower:
            msg = f"Request timed out after {REQUEST_TIMEOUT}s. Please try again."
            error_type = "timeout"

        # ── Unknown ───────────────────────────────────────────────
        else:
            msg = f"Analysis failed: {err[:300]}"
            error_type = "unknown"

        logger.warning("Analysis error [%s]: %s", error_type, err[:200])
        usage = track_token_usage(text, "", success=False, retries_used=retries_used[0])
        return {
            "success":      False,
            "data":         {},
            "message":      msg,
            "error_type":   error_type,
            "token_usage":  usage,
            "retries_used": retries_used[0],
        }


# ===========================================================================
# Internal Analysis Runners
# ===========================================================================

def _run_full_analysis(
    text: str,
    call_llm: Callable,
    retries_used: list,
) -> dict:
    prompt_str  = _full_analysis_prompt.format_messages(resume_text=text)
    prompt_text = " ".join(m.content for m in prompt_str)

    logger.info("Running FULL analysis (%d chars)...", len(text))
    raw = call_llm(_full_analysis_prompt, {"resume_text": text})

    schema_keys = [
        "overall_score", "strengths", "weaknesses",
        "identified_skills", "recommended_skills",
        "career_suggestions", "improvement_tips",
    ]
    data      = extract_json_response(raw, schema_keys=schema_keys)
    validated = _validate_and_normalize(data)

    usage = track_token_usage(prompt_text, raw, success=True, retries_used=retries_used[0])

    logger.info(
        "Full analysis complete - score=%d | skills=%d | level=%s",
        validated["overall_score"],
        len(validated["identified_skills"]),
        validated["experience_level"],
    )

    return {
        "success":      True,
        "data":         validated,
        "message":      "Analysis completed successfully.",
        "error_type":   None,
        "token_usage":  usage,
        "retries_used": retries_used[0],
    }


def _run_sectioned_analysis(
    text: str,
    call_llm: Callable,
    retries_used: list,
) -> dict:
    logger.info("Running SECTIONED analysis (%d chars)...", len(text))
    combined: dict = {}
    total_prompt = ""
    total_raw    = ""

    sections = [
        ("strengths",   strengths_prompt,   ["strengths"]),
        ("weaknesses",  weaknesses_prompt,   ["weaknesses"]),
        ("skills",      skills_prompt,       ["identified_skills", "recommended_skills"]),
        ("suggestions", suggestions_prompt,  ["overall_score", "career_suggestions"]),
    ]

    for section_name, prompt_template, expected_keys in sections:
        logger.info("  Analyzing section: %s", section_name)
        prompt_msgs = prompt_template.format_messages(resume_text=text)
        prompt_text = " ".join(m.content for m in prompt_msgs)

        raw = call_llm(prompt_template, {"resume_text": text})
        section_data = extract_json_response(raw, schema_keys=expected_keys)
        combined.update(section_data)

        total_prompt += prompt_text
        total_raw    += raw
        time.sleep(0.3)

    validated = _validate_and_normalize(combined)
    usage = track_token_usage(total_prompt, total_raw, success=True, retries_used=retries_used[0])

    logger.info(
        "Sectioned analysis complete - score=%d | skills=%d",
        validated["overall_score"],
        len(validated["identified_skills"]),
    )

    return {
        "success":      True,
        "data":         validated,
        "message":      "Sectioned analysis completed successfully.",
        "error_type":   None,
        "token_usage":  usage,
        "retries_used": retries_used[0],
    }


# ===========================================================================
# Validation & Normalization
# ===========================================================================

def _validate_and_normalize(data: dict) -> dict:
    def _to_list(val, default=None, max_items=15) -> list:
        if isinstance(val, list):
            return [str(x).strip() for x in val if str(x).strip()][:max_items]
        if isinstance(val, str) and val.strip():
            return [val.strip()]
        return default or []

    def _clamp_int(val, lo, hi, default) -> int:
        try:
            return max(lo, min(hi, int(val)))
        except (TypeError, ValueError):
            return default

    return {
        "overall_score":        _clamp_int(data.get("overall_score"), 0, 100, 50),
        "score_explanation":    str(data.get("score_explanation", "")).strip(),
        "professional_summary": str(data.get("professional_summary", "")).strip(),
        "experience_level":     str(data.get("experience_level", "Unknown")).strip(),
        "industry":             str(data.get("industry", "General")).strip(),
        "strengths":            _to_list(data.get("strengths"),           [], 10),
        "weaknesses":           _to_list(data.get("weaknesses"),          [], 8),
        "identified_skills":    _to_list(data.get("identified_skills"),   [], 15),
        "recommended_skills":   _to_list(data.get("recommended_skills"),  [], 8),
        "career_suggestions":   _to_list(data.get("career_suggestions"),  [], 5),
        "improvement_tips":     _to_list(data.get("improvement_tips"),    [], 5),
        "missing_sections":     _to_list(data.get("missing_sections"),    [], 5),
        "ats_issues":           _to_list(data.get("ats_issues"),          [], 5),
    }


# ===========================================================================
# Self-Test
# ===========================================================================

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )

    print("=" * 60)
    print("  LLM Analyzer - Self Test")
    print("=" * 60)

    api_key = os.getenv("GOOGLE_API_KEY", "")
    if not api_key or api_key == "your_google_api_key_here":
        print("\n[SKIP] GOOGLE_API_KEY not configured - skipping live API tests.")
        print("[OK]   Module structure and imports are valid.")
        exit(0)

    print("\n[1] Testing load_llm()...")
    llm = load_llm()
    print(f"     [OK] LLM loaded: {llm.model}")

    print("\n[2] Testing test_connection()...")
    conn = test_connection()
    status = "[OK]" if conn["success"] else "[FAIL]"
    print(f"     {status} {conn['message']}")

    if not conn["success"]:
        print("     Aborting - cannot proceed without a valid connection.")
        exit(1)

    print("\n[3] Testing extract_json_response()...")
    test_cases = [
        ('{"key": "value"}', ["key"]),
        ('```json\n{"score": 85}\n```', ["score"]),
        ('Some text before {"a": 1, "b": [1,2]} after', ["a", "b"]),
    ]
    for raw, keys in test_cases:
        result = extract_json_response(raw, schema_keys=keys)
        print(f"     [OK] Parsed: {result}")

    print("\n[4] Testing track_token_usage()...")
    usage = track_token_usage("Hello world " * 100, "Response " * 50, success=True)
    print(f"     [OK] in={usage['request_input_tokens']} | "
          f"out={usage['request_output_tokens']} | "
          f"cost=${usage['request_cost_usd']}")

    print("\n[5] Testing retry_failed_requests()...")
    call_count = [0]

    @retry_failed_requests(max_attempts=3, base_delay=0.1)
    def _flaky():
        call_count[0] += 1
        if call_count[0] < 3:
            raise ConnectionError("Simulated flaky network")
        return "success"

    result = _flaky()
    assert result == "success" and call_count[0] == 3
    print(f"     [OK] Succeeded on attempt {call_count[0]} (2 retries)")

    print("\n[6] Testing quota error NOT retried...")
    quota_count = [0]

    @retry_failed_requests(max_attempts=3, base_delay=0.01)
    def _quota_error():
        quota_count[0] += 1
        raise Exception("429 RESOURCE_EXHAUSTED quota exceeded")

    try:
        _quota_error()
    except Exception:
        pass
    assert quota_count[0] == 1, f"Expected 1 attempt, got {quota_count[0]}"
    print(f"     [OK] Quota error raised immediately after 1 attempt (no wasted retries)")

    print("\n=== All LLM Analyzer tests complete ===")
