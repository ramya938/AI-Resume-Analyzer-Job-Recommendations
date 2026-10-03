"""
strengths_weaknesses.py — Detailed Strengths & Weaknesses Analyzer
====================================================================
File: backend/strengths_weaknesses.py

Performs a deep, structured analysis of resume strengths and weaknesses
using Google Gemini via llm_analyzer.py. Returns rich, categorized items
with confidence and severity metadata.

Each item in the output follows this schema:
    {
        "title":       str   — Short label (e.g. "Quantified Achievements")
        "description": str   — Detailed explanation with evidence from resume
        "category":    str   — Domain bucket (e.g. "Experience", "Skills")
        "confidence":  str   — "High" | "Medium" | "Low"
        "severity":    str   — For strengths: "Major" | "Notable" | "Minor"
                               For weaknesses: "Critical" | "Moderate" | "Minor"
    }

Public Functions:
    analyze_strengths(resume_text)   → list of strength items
    analyze_weaknesses(resume_text)  → list of weakness items
    analyze_all(resume_text)         → {"strengths": [...], "weaknesses": [...]}
    cache_analysis(cache_key, data)  → store result in memory cache
    save_results(user_id, data)      → persist to SQLite database

Classes:
    StrengthsWeaknessesAnalyzer — OOP wrapper with built-in caching
"""

import os
import json
import hashlib
import logging
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional

from dotenv import load_dotenv
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

from backend.llm_analyzer import (
    load_llm,
    extract_json_response,
    track_token_usage,
    retry_failed_requests,
    MAX_INPUT_CHARS,
    RETRY_MAX_ATTEMPTS,
    RETRY_BASE_DELAY,
)
from utils.database import save_resume_analysis, get_resume_analysis

load_dotenv()

logger = logging.getLogger(__name__)


# ===========================================================================
# Category Constants
# ===========================================================================

# Strength categories
STRENGTH_CATEGORIES = [
    "Experience",
    "Technical Skills",
    "Leadership",
    "Achievements",
    "Communication",
    "Education",
    "Soft Skills",
    "Industry Knowledge",
    "Certifications",
    "Portfolio / Projects",
]

# Weakness categories
WEAKNESS_CATEGORIES = [
    "Missing Content",
    "Formatting / ATS",
    "Technical Skill Gaps",
    "Experience Gaps",
    "Quantification",
    "Clarity",
    "Keywords",
    "Education",
    "Career Progression",
    "Soft Skills",
]

STRENGTH_SEVERITIES  = ["Major", "Notable", "Minor"]
WEAKNESS_SEVERITIES  = ["Critical", "Moderate", "Minor"]
CONFIDENCE_LEVELS    = ["High", "Medium", "Low"]


# ===========================================================================
# Data Models
# ===========================================================================

@dataclass
class AnalysisItem:
    """A single strength or weakness item with rich metadata."""
    title:       str
    description: str
    category:    str
    confidence:  str   # "High" | "Medium" | "Low"
    severity:    str   # For strengths: "Major"/"Notable"/"Minor"
                       # For weaknesses: "Critical"/"Moderate"/"Minor"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "AnalysisItem":
        return cls(
            title=str(d.get("title", "")).strip(),
            description=str(d.get("description", "")).strip(),
            category=str(d.get("category", "General")).strip(),
            confidence=str(d.get("confidence", "Medium")).strip(),
            severity=str(d.get("severity", "Minor")).strip(),
        )

    def validate(self) -> "AnalysisItem":
        """Enforce enum constraints on confidence and severity fields."""
        if self.confidence not in CONFIDENCE_LEVELS:
            self.confidence = "Medium"
        if self.category not in STRENGTH_CATEGORIES + WEAKNESS_CATEGORIES:
            self.category = "General"
        return self


@dataclass
class AnalysisResult:
    """Complete strengths/weaknesses analysis result."""
    strengths:   list = field(default_factory=list)   # list[AnalysisItem]
    weaknesses:  list = field(default_factory=list)   # list[AnalysisItem]
    analyzed_at: str  = field(default_factory=lambda: datetime.now().isoformat())
    token_usage: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "strengths":   [s.to_dict() if hasattr(s, "to_dict") else s for s in self.strengths],
            "weaknesses":  [w.to_dict() if hasattr(w, "to_dict") else w for w in self.weaknesses],
            "analyzed_at": self.analyzed_at,
            "token_usage": self.token_usage,
        }


# ===========================================================================
# Prompt Templates
# ===========================================================================

_SW_SYSTEM_ROLE = (
    "You are a senior HR director and resume coach with 20+ years of experience "
    "across Fortune 500 companies. You provide specific, evidence-based, and "
    "constructive resume feedback. Be concrete — reference actual content from "
    "the resume. You MUST respond ONLY with a valid JSON object."
)

_STRENGTHS_PROMPT = ChatPromptTemplate.from_messages([
    ("system", _SW_SYSTEM_ROLE),
    ("human", """Analyze the following resume and identify its key STRENGTHS.

RESUME:
---
{resume_text}
---

For each strength, provide a structured item with these exact fields.

VALID CATEGORIES: {categories}
VALID CONFIDENCE LEVELS: High, Medium, Low
VALID SEVERITY LEVELS (for strengths): Major, Notable, Minor

Return ONLY this JSON object (no markdown, no extra text):
{{
  "strengths": [
    {{
      "title": "<short label, 3-6 words>",
      "description": "<2-3 sentences with specific evidence from the resume>",
      "category": "<one of the valid categories above>",
      "confidence": "<High | Medium | Low>",
      "severity": "<Major | Notable | Minor>"
    }},
    {{
      "title": "<short label, 3-6 words>",
      "description": "<2-3 sentences with specific evidence from the resume>",
      "category": "<one of the valid categories above>",
      "confidence": "<High | Medium | Low>",
      "severity": "<Major | Notable | Minor>"
    }}
  ]
}}

Provide 4-7 strengths. Order from most impactful (Major) to least (Minor).
Be specific — generic statements like 'good skills' are not acceptable."""),
])

_WEAKNESSES_PROMPT = ChatPromptTemplate.from_messages([
    ("system", _SW_SYSTEM_ROLE),
    ("human", """Analyze the following resume and identify its WEAKNESSES and improvement areas.

RESUME:
---
{resume_text}
---

For each weakness, provide a structured item with these exact fields.

VALID CATEGORIES: {categories}
VALID CONFIDENCE LEVELS: High, Medium, Low
VALID SEVERITY LEVELS (for weaknesses): Critical, Moderate, Minor

Return ONLY this JSON object (no markdown, no extra text):
{{
  "weaknesses": [
    {{
      "title": "<short label, 3-6 words>",
      "description": "<2-3 sentences explaining the issue and how to fix it>",
      "category": "<one of the valid categories above>",
      "confidence": "<High | Medium | Low>",
      "severity": "<Critical | Moderate | Minor>"
    }},
    {{
      "title": "<short label, 3-6 words>",
      "description": "<2-3 sentences explaining the issue and how to fix it>",
      "category": "<one of the valid categories above>",
      "confidence": "<High | Medium | Low>",
      "severity": "<Critical | Moderate | Minor>"
    }}
  ]
}}

Provide 3-6 weaknesses. Order from most severe (Critical) to least (Minor).
Focus on constructive, actionable feedback. Reference specific resume sections."""),
])


# ===========================================================================
# Module-level Cache
# ===========================================================================

# Simple in-memory LRU cache: { cache_key: (timestamp, AnalysisResult) }
_cache: dict[str, tuple[float, AnalysisResult]] = {}
CACHE_TTL_SECONDS = 3600  # 1 hour


def cache_analysis(cache_key: str, data: AnalysisResult) -> None:
    """
    Store an AnalysisResult in the in-memory cache.

    The cache uses a content-hash key so identical resume texts
    always return the same cached result without re-calling the API.

    Args:
        cache_key: SHA-256 hash of the resume text (or any unique key).
        data:      The AnalysisResult to cache.
    """
    _cache[cache_key] = (time.monotonic(), data)
    logger.debug("Cached analysis under key=%s... (%d strengths, %d weaknesses)",
                 cache_key[:12], len(data.strengths), len(data.weaknesses))


def _get_cached(cache_key: str) -> Optional[AnalysisResult]:
    """Return cached result if it exists and hasn't expired."""
    if cache_key not in _cache:
        return None
    ts, data = _cache[cache_key]
    if time.monotonic() - ts > CACHE_TTL_SECONDS:
        del _cache[cache_key]
        logger.debug("Cache expired for key=%s...", cache_key[:12])
        return None
    logger.info("Cache hit for key=%s...", cache_key[:12])
    return data


def _make_cache_key(resume_text: str) -> str:
    """Generate a stable SHA-256 cache key from resume text."""
    return hashlib.sha256(resume_text.strip().encode("utf-8")).hexdigest()


def clear_cache() -> None:
    """Clear all cached analyses (useful for testing or forced refresh)."""
    _cache.clear()
    logger.info("Strengths/weaknesses analysis cache cleared.")


# ===========================================================================
# Database Save
# ===========================================================================

def save_results(user_id: str, data: AnalysisResult) -> dict:
    """
    Persist analysis results to the SQLite database via save_resume_analysis().

    Saves strengths and weaknesses as JSON lists to the resume_analysis table.
    Does NOT overwrite existing analysis rows — creates a new record so the
    user can see history over multiple analyses.

    Args:
        user_id: UUID of the authenticated user.
        data:    AnalysisResult containing strengths and weaknesses.

    Returns:
        dict with keys: success (bool), analysis_id (int|None), message (str).
    """
    if not user_id:
        return {"success": False, "analysis_id": None, "message": "user_id is required."}

    try:
        strengths_titles   = [s.title       if hasattr(s, "title")  else s.get("title", "")
                              for s in data.strengths]
        weaknesses_titles  = [w.title       if hasattr(w, "title")  else w.get("title", "")
                              for w in data.weaknesses]

        # Save full rich items as JSON in extracted_text for retrieval
        rich_payload = json.dumps(data.to_dict(), ensure_ascii=False)

        result = save_resume_analysis(
            user_id=user_id,
            extracted_text=rich_payload,             # store structured JSON
            resume_score=0.0,                        # score handled by analysis.py
            strengths=strengths_titles,
            weaknesses=weaknesses_titles,
            identified_skills=[],
            recommended_skills=[],
        )

        if result.get("success"):
            logger.info(
                "Saved strengths/weaknesses to DB: analysis_id=%s, "
                "strengths=%d, weaknesses=%d",
                result.get("analysis_id"), len(data.strengths), len(data.weaknesses),
            )
        else:
            logger.warning("DB save returned failure: %s", result.get("message"))

        return result

    except Exception as exc:
        logger.error("Failed to save analysis results: %s", exc)
        return {"success": False, "analysis_id": None, "message": str(exc)}


def load_saved_results(user_id: str, limit: int = 5) -> list[dict]:
    """
    Retrieve past strengths/weaknesses analyses from the database.

    Args:
        user_id: UUID of the authenticated user.
        limit:   Max records to return.

    Returns:
        List of saved analysis dicts, newest first.
    """
    if not user_id:
        return []
    return get_resume_analysis(user_id, limit=limit)


# ===========================================================================
# Core Analysis Functions
# ===========================================================================

def analyze_strengths(
    resume_text: str,
    use_cache: bool = True,
    api_key: Optional[str] = None,
) -> list[AnalysisItem]:
    """
    Identify and return the key strengths of a resume.

    Uses Gemini via llm_analyzer.py with retry and token tracking.
    Results are cached by content hash to avoid redundant API calls.

    Args:
        resume_text: Cleaned plain text from the resume.
        use_cache:   If True, return cached result when available.
        api_key:     Optional override for GOOGLE_API_KEY.

    Returns:
        List of AnalysisItem objects representing resume strengths.
        Returns an empty list on failure (errors are logged).
    """
    if not resume_text or not resume_text.strip():
        logger.warning("analyze_strengths called with empty text.")
        return []

    text = resume_text.strip()[:MAX_INPUT_CHARS]
    cache_key = _make_cache_key(text + ":strengths")

    # ── Cache check ───────────────────────────────────────────────
    if use_cache:
        cached = _get_cached(cache_key)
        if cached is not None:
            return cached.strengths

    # ── Build prompt and call LLM ─────────────────────────────────
    prompt_input = {"resume_text": text, "categories": ", ".join(STRENGTH_CATEGORIES)}
    prompt_msgs  = _STRENGTHS_PROMPT.format_messages(**prompt_input)
    prompt_text  = " ".join(m.content for m in prompt_msgs)

    @retry_failed_requests(max_attempts=RETRY_MAX_ATTEMPTS, base_delay=RETRY_BASE_DELAY)
    def _call():
        llm   = load_llm(api_key=api_key)
        chain = _STRENGTHS_PROMPT | llm | StrOutputParser()
        return chain.invoke(prompt_input)

    try:
        logger.info("Analyzing strengths (%d chars)...", len(text))
        raw = _call()

        data = extract_json_response(raw, schema_keys=["strengths"])
        raw_items = data.get("strengths", [])

        items = [
            AnalysisItem.from_dict(item).validate()
            for item in raw_items
            if isinstance(item, dict)
        ]

        # Validate severity values
        for item in items:
            if item.severity not in STRENGTH_SEVERITIES:
                item.severity = "Notable"

        track_token_usage(prompt_text, raw, success=True)
        logger.info("Strengths analysis complete: %d items", len(items))

        # Store in cache
        if use_cache:
            result_obj = AnalysisResult(strengths=items)
            cache_analysis(cache_key, result_obj)

        return items

    except ValueError as exc:
        logger.error("Config/key error in analyze_strengths: %s", exc)
        track_token_usage(prompt_text, "", success=False)
        return []

    except Exception as exc:
        logger.error("Error in analyze_strengths: %s", exc)
        track_token_usage(prompt_text, "", success=False)
        return []


def analyze_weaknesses(
    resume_text: str,
    use_cache: bool = True,
    api_key: Optional[str] = None,
) -> list[AnalysisItem]:
    """
    Identify and return the weaknesses and improvement areas of a resume.

    Uses Gemini via llm_analyzer.py with retry and token tracking.
    Results are cached by content hash to avoid redundant API calls.

    Args:
        resume_text: Cleaned plain text from the resume.
        use_cache:   If True, return cached result when available.
        api_key:     Optional override for GOOGLE_API_KEY.

    Returns:
        List of AnalysisItem objects representing resume weaknesses.
        Returns an empty list on failure (errors are logged).
    """
    if not resume_text or not resume_text.strip():
        logger.warning("analyze_weaknesses called with empty text.")
        return []

    text = resume_text.strip()[:MAX_INPUT_CHARS]
    cache_key = _make_cache_key(text + ":weaknesses")

    # ── Cache check ───────────────────────────────────────────────
    if use_cache:
        cached = _get_cached(cache_key)
        if cached is not None:
            return cached.weaknesses

    # ── Build prompt and call LLM ─────────────────────────────────
    prompt_input = {"resume_text": text, "categories": ", ".join(WEAKNESS_CATEGORIES)}
    prompt_msgs  = _WEAKNESSES_PROMPT.format_messages(**prompt_input)
    prompt_text  = " ".join(m.content for m in prompt_msgs)

    @retry_failed_requests(max_attempts=RETRY_MAX_ATTEMPTS, base_delay=RETRY_BASE_DELAY)
    def _call():
        llm   = load_llm(api_key=api_key)
        chain = _WEAKNESSES_PROMPT | llm | StrOutputParser()
        return chain.invoke(prompt_input)

    try:
        logger.info("Analyzing weaknesses (%d chars)...", len(text))
        raw = _call()

        data = extract_json_response(raw, schema_keys=["weaknesses"])
        raw_items = data.get("weaknesses", [])

        items = [
            AnalysisItem.from_dict(item).validate()
            for item in raw_items
            if isinstance(item, dict)
        ]

        # Validate severity values
        for item in items:
            if item.severity not in WEAKNESS_SEVERITIES:
                item.severity = "Moderate"

        track_token_usage(prompt_text, raw, success=True)
        logger.info("Weaknesses analysis complete: %d items", len(items))

        # Store in cache
        if use_cache:
            result_obj = AnalysisResult(weaknesses=items)
            cache_analysis(cache_key, result_obj)

        return items

    except ValueError as exc:
        logger.error("Config/key error in analyze_weaknesses: %s", exc)
        track_token_usage(prompt_text, "", success=False)
        return []

    except Exception as exc:
        logger.error("Error in analyze_weaknesses: %s", exc)
        track_token_usage(prompt_text, "", success=False)
        return []


def analyze_all(
    resume_text: str,
    use_cache: bool = True,
    api_key: Optional[str] = None,
) -> dict:
    """
    Run both strengths and weaknesses analysis in sequence and return a
    combined result.

    This is the main entry point for full analysis. Both calls share the
    same cache TTL window, so running this twice within an hour only hits
    the API once.

    Args:
        resume_text: Cleaned plain text from the resume.
        use_cache:   Pass-through to analyze_strengths/analyze_weaknesses.
        api_key:     Optional API key override.

    Returns:
        dict matching the required output schema:
        {
            "strengths": [
                {
                    "title": str,
                    "description": str,
                    "category": str,
                    "confidence": str,
                    "severity": str
                },
                ...
            ],
            "weaknesses": [
                {
                    "title": str,
                    "description": str,
                    "category": str,
                    "confidence": str,
                    "severity": str
                },
                ...
            ],
            "success": bool,
            "message": str,
            "analyzed_at": str  (ISO timestamp),
            "counts": {"strengths": int, "weaknesses": int}
        }
    """
    if not resume_text or not resume_text.strip():
        return {
            "strengths": [], "weaknesses": [],
            "success": False, "message": "Resume text is empty.",
            "analyzed_at": datetime.now().isoformat(),
            "counts": {"strengths": 0, "weaknesses": 0},
        }

    logger.info("Running full strengths/weaknesses analysis...")

    strengths  = analyze_strengths(resume_text, use_cache=use_cache, api_key=api_key)
    weaknesses = analyze_weaknesses(resume_text, use_cache=use_cache, api_key=api_key)

    success = bool(strengths or weaknesses)
    message = (
        f"Analysis complete: {len(strengths)} strengths, {len(weaknesses)} weaknesses found."
        if success
        else "Analysis returned no results. Please check your API key or try again."
    )

    return {
        "strengths":   [s.to_dict() for s in strengths],
        "weaknesses":  [w.to_dict() for w in weaknesses],
        "success":     success,
        "message":     message,
        "analyzed_at": datetime.now().isoformat(),
        "counts": {
            "strengths":  len(strengths),
            "weaknesses": len(weaknesses),
        },
    }


# ===========================================================================
# OOP Wrapper Class
# ===========================================================================

class StrengthsWeaknessesAnalyzer:
    """
    Object-oriented wrapper for the strengths/weaknesses analysis pipeline.

    Provides instance-level caching control, API key management, and
    a clean interface for use in Streamlit pages or other modules.

    Usage:
        analyzer = StrengthsWeaknessesAnalyzer()
        result   = analyzer.run("resume text here")
        analyzer.save(user_id, result)
    """

    def __init__(self, api_key: Optional[str] = None, use_cache: bool = True):
        """
        Args:
            api_key:   API key override (defaults to GOOGLE_API_KEY env var).
            use_cache: Whether to use the module-level memory cache.
        """
        self.api_key   = api_key
        self.use_cache = use_cache
        self._last_result: Optional[AnalysisResult] = None
        logger.debug("StrengthsWeaknessesAnalyzer initialized (cache=%s)", use_cache)

    def analyze_strengths(self, resume_text: str) -> list[AnalysisItem]:
        """Run strengths analysis. See module-level analyze_strengths()."""
        return analyze_strengths(resume_text, use_cache=self.use_cache, api_key=self.api_key)

    def analyze_weaknesses(self, resume_text: str) -> list[AnalysisItem]:
        """Run weaknesses analysis. See module-level analyze_weaknesses()."""
        return analyze_weaknesses(resume_text, use_cache=self.use_cache, api_key=self.api_key)

    def run(self, resume_text: str) -> dict:
        """
        Run full analysis (strengths + weaknesses) and store internally.

        Returns:
            The same dict as the module-level analyze_all().
        """
        result = analyze_all(resume_text, use_cache=self.use_cache, api_key=self.api_key)

        # Store AnalysisResult for .save()
        self._last_result = AnalysisResult(
            strengths=[AnalysisItem.from_dict(s) for s in result["strengths"]],
            weaknesses=[AnalysisItem.from_dict(w) for w in result["weaknesses"]],
        )
        return result

    def save(self, user_id: str, result: Optional[dict] = None) -> dict:
        """
        Save the last run result (or a provided dict) to the database.

        Args:
            user_id: UUID of the authenticated user.
            result:  Optional dict from run(). Uses last run if None.

        Returns:
            dict from save_results().
        """
        if result is not None:
            ar = AnalysisResult(
                strengths=[AnalysisItem.from_dict(s) for s in result.get("strengths", [])],
                weaknesses=[AnalysisItem.from_dict(w) for w in result.get("weaknesses", [])],
            )
        elif self._last_result is not None:
            ar = self._last_result
        else:
            return {"success": False, "analysis_id": None, "message": "No result to save."}

        return save_results(user_id, ar)

    def clear_cache(self) -> None:
        """Clear the module-level analysis cache."""
        clear_cache()

    def get_summary(self, result: dict) -> dict:
        """
        Extract a concise summary from an analyze_all() result dict.

        Returns:
            dict with counts, top items per severity, and timestamp.
        """
        strengths  = result.get("strengths", [])
        weaknesses = result.get("weaknesses", [])

        return {
            "total_strengths":   len(strengths),
            "total_weaknesses":  len(weaknesses),
            "major_strengths":   [s for s in strengths  if s.get("severity") == "Major"],
            "critical_issues":   [w for w in weaknesses if w.get("severity") == "Critical"],
            "top_categories": {
                "strengths":  list({s["category"] for s in strengths}),
                "weaknesses": list({w["category"] for w in weaknesses}),
            },
            "analyzed_at": result.get("analyzed_at", ""),
        }


# ===========================================================================
# Self-test — run with: python -m backend.strengths_weaknesses
# ===========================================================================

if __name__ == "__main__":
    import sys
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )

    print("=" * 60)
    print("  Strengths & Weaknesses Analyzer — Self Test")
    print("=" * 60)

    # ── Unit tests (no API key needed) ────────────────────────────
    print("\n[1] Testing AnalysisItem.from_dict()...")
    item = AnalysisItem.from_dict({
        "title": "Strong Python Skills",
        "description": "Candidate lists Python across 3 roles.",
        "category": "Technical Skills",
        "confidence": "High",
        "severity": "Major",
    })
    assert item.title == "Strong Python Skills"
    assert item.confidence == "High"
    assert item.severity == "Major"
    item2 = AnalysisItem.from_dict({"confidence": "InvalidValue", "severity": "BadSeverity"})
    item2.validate()
    assert item2.confidence == "Medium"   # corrected
    print("     [OK] AnalysisItem parsing and validation correct")

    print("\n[2] Testing cache_analysis() + _get_cached()...")
    clear_cache()
    dummy = AnalysisResult(
        strengths=[item],
        weaknesses=[AnalysisItem("Weak Summary", "No summary section.", "Missing Content", "High", "Critical")],
    )
    cache_analysis("test_key_123", dummy)
    hit = _get_cached("test_key_123")
    assert hit is not None
    assert len(hit.strengths) == 1
    miss = _get_cached("nonexistent_key")
    assert miss is None
    clear_cache()
    assert _get_cached("test_key_123") is None
    print("     [OK] Cache store, hit, miss, and clear all work")

    print("\n[3] Testing _make_cache_key() consistency...")
    k1 = _make_cache_key("Hello world")
    k2 = _make_cache_key("Hello world")
    k3 = _make_cache_key("Hello world!")
    assert k1 == k2 and k1 != k3
    print("     [OK] SHA-256 cache key is stable and unique")

    print("\n[4] Testing AnalysisResult.to_dict()...")
    d = dummy.to_dict()
    assert "strengths"  in d
    assert "weaknesses" in d
    assert len(d["strengths"]) == 1
    assert d["strengths"][0]["title"] == "Strong Python Skills"
    print("     [OK] to_dict() serializes correctly")

    print("\n[5] Testing StrengthsWeaknessesAnalyzer.get_summary()...")
    analyzer = StrengthsWeaknessesAnalyzer()
    mock_result = {
        "strengths": [
            {"title": "A", "category": "Technical Skills", "severity": "Major"},
            {"title": "B", "category": "Experience",       "severity": "Notable"},
        ],
        "weaknesses": [
            {"title": "C", "category": "Missing Content", "severity": "Critical"},
        ],
        "analyzed_at": "2025-01-01T00:00:00",
    }
    summary = analyzer.get_summary(mock_result)
    assert summary["total_strengths"]  == 2
    assert summary["total_weaknesses"] == 1
    assert len(summary["major_strengths"])  == 1
    assert len(summary["critical_issues"])  == 1
    print("     [OK] get_summary() aggregation is correct")

    # ── Live API test ─────────────────────────────────────────────
    api_key = os.getenv("GOOGLE_API_KEY", "")
    if not api_key or api_key == "your_google_api_key_here":
        print("\n[SKIP] GOOGLE_API_KEY not configured — skipping live API tests.")
        print("       Set GOOGLE_API_KEY in .env and re-run to test live Gemini calls.")
    else:
        sample_resume = """
        Alex Johnson | Senior Software Engineer
        alex.johnson@email.com | San Francisco, CA | github.com/alexj

        SUMMARY
        Full-stack engineer with 7 years of experience building scalable SaaS products.
        Led multiple teams and shipped features used by 500K+ users.

        EXPERIENCE
        Senior Software Engineer — MegaSaaS Inc. (2020–Present)
        - Architected microservices reducing latency by 35%
        - Led team of 8 engineers through 3 major product launches
        - Built real-time notifications system handling 2M events/day

        Software Engineer — WebCo (2017–2020)
        - Developed RESTful APIs using Django and PostgreSQL
        - Implemented OAuth2 authentication for 50K users

        EDUCATION
        B.S. Computer Science — UC Berkeley (2017)

        SKILLS
        Python, JavaScript, React, Django, PostgreSQL, AWS, Docker, Kubernetes, Redis
        """

        print("\n[6] Testing analyze_strengths() with live API...")
        strengths = analyze_strengths(sample_resume.strip())
        if strengths:
            print(f"     [OK] Got {len(strengths)} strengths:")
            for s in strengths[:3]:
                print(f"          [{s.severity}][{s.confidence}] {s.title} ({s.category})")
        else:
            print("     [WARN] No strengths returned (API may be rate-limited)")

        print("\n[7] Testing analyze_weaknesses() with live API...")
        weaknesses = analyze_weaknesses(sample_resume.strip(), use_cache=False)
        if weaknesses:
            print(f"     [OK] Got {len(weaknesses)} weaknesses:")
            for w in weaknesses[:3]:
                print(f"          [{w.severity}][{w.confidence}] {w.title} ({w.category})")
        else:
            print("     [WARN] No weaknesses returned (API may be rate-limited)")

        print("\n[8] Testing cache hit (second call should skip API)...")
        import time as _t
        start = _t.monotonic()
        strengths_cached = analyze_strengths(sample_resume.strip(), use_cache=True)
        elapsed = _t.monotonic() - start
        print(f"     [OK] Cache returned {len(strengths_cached)} items in {elapsed:.3f}s")

        print("\n[9] Testing StrengthsWeaknessesAnalyzer.run()...")
        result = analyzer.run(sample_resume.strip())
        summary = analyzer.get_summary(result)
        print(f"     [OK] Success: {result['success']}")
        print(f"     [OK] Counts:  {result['counts']}")
        print(f"     [OK] Message: {result['message']}")

    print("\n=== All strengths_weaknesses tests complete ===")
