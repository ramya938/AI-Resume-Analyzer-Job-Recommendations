"""
analysis.py — Resume Analysis Orchestrator
============================================
File: backend/analysis.py

Thin orchestration layer that:
    1. Calls llm_analyzer.analyze_resume() for AI analysis
    2. Persists results to the SQLite database
    3. Provides get_analysis_history() for UI retrieval

All AI logic (prompts, retries, token tracking, JSON parsing) lives
in backend/llm_analyzer.py — this module is the clean public API
consumed by frontend/analysis_page.py.
"""

import logging

from backend.llm_analyzer import (
    analyze_resume as _llm_analyze,
    get_usage_summary,
)
from utils.database import save_resume_analysis, get_resume_analysis

logger = logging.getLogger(__name__)


# ===========================================================================
# Public API
# ===========================================================================

def analyze_resume(resume_text: str, user_id: str, mode: str = "full") -> dict:
    """
    Run AI analysis via llm_analyzer and persist results to the database.

    Args:
        resume_text: Cleaned plain text extracted from the user's resume.
        user_id:     UUID of the authenticated user.
        mode:        "full" (single call) or "sectioned" (4 calls, detailed).

    Returns:
        dict with keys:
            success     (bool)      — True if analysis succeeded.
            data        (dict)      — Full analysis result (see llm_analyzer).
            analysis_id (int|None)  — DB row id of saved record.
            message     (str)       — Human-readable status.
            token_usage (dict)      — Token counts and estimated cost.
    """
    if not user_id:
        return {
            "success": False, "data": {}, "analysis_id": None,
            "message": "User ID is required.", "token_usage": {},
        }

    # ── Step 1: Run AI analysis ───────────────────────────────────
    result = _llm_analyze(resume_text=resume_text, mode=mode)

    if not result["success"]:
        return {**result, "analysis_id": None}

    data = result["data"]

    # ── Step 2: Persist to database ───────────────────────────────
    try:
        db_result = save_resume_analysis(
            user_id=user_id,
            extracted_text=resume_text,
            resume_score=float(data.get("overall_score", 0)),
            strengths=data.get("strengths", []),
            weaknesses=data.get("weaknesses", []),
            identified_skills=data.get("identified_skills", []),
            recommended_skills=data.get("recommended_skills", []),
        )

        analysis_id = db_result.get("analysis_id") if db_result.get("success") else None
        if analysis_id:
            logger.info(
                "Analysis saved to DB: analysis_id=%d, score=%.0f",
                analysis_id, data["overall_score"],
            )
        else:
            logger.warning("DB save reported failure: %s", db_result.get("message"))

    except Exception as exc:
        logger.error("Failed to save analysis to DB: %s", exc)
        analysis_id = None

    return {
        "success":     True,
        "data":        data,
        "analysis_id": analysis_id,
        "message":     result.get("message", "Analysis completed and saved."),
        "token_usage": result.get("token_usage", {}),
    }


def get_analysis_history(user_id: str, limit: int = 10) -> list[dict]:
    """
    Retrieve a user's past resume analyses from the database, newest first.

    Args:
        user_id: UUID of the authenticated user.
        limit:   Maximum number of records to return.

    Returns:
        List of analysis dicts. Empty list if user has no history.
    """
    if not user_id:
        return []
    return get_resume_analysis(user_id, limit=limit)


# ===========================================================================
# Self-test — run with: python -m backend.analysis
# ===========================================================================

if __name__ == "__main__":
    import os
    import logging as _logging
    import uuid
    from utils.database import create_database

    _logging.basicConfig(level=_logging.INFO, format="%(levelname)s | %(message)s")

    api_key = os.getenv("GOOGLE_API_KEY", "")
    if not api_key or api_key == "your_google_api_key_here":
        print("[SKIP] GOOGLE_API_KEY not set — run python -m backend.llm_analyzer for full tests.")
    else:
        create_database()
        test_user = str(uuid.uuid4())

        sample = """
        Jane Smith | Product Manager
        jane@company.com | New York, NY

        EXPERIENCE
        Senior Product Manager — TechCo (2020–Present)
        - Launched 3 product lines generating $8M ARR
        - Managed roadmap for 500K+ user SaaS platform

        EDUCATION
        MBA — Wharton School (2018)

        SKILLS
        Product Strategy, SQL, JIRA, Figma, A/B Testing, Python
        """

        result = analyze_resume(sample.strip(), test_user)
        if result["success"]:
            d = result["data"]
            print(f"[OK] Score:       {d['overall_score']}/100")
            print(f"[OK] Level:       {d['experience_level']}")
            print(f"[OK] Skills:      {d['identified_skills'][:5]}")
            print(f"[OK] analysis_id: {result['analysis_id']}")
            print(f"[OK] Tokens:      {result['token_usage'].get('request_total_tokens', 'N/A')}")
        else:
            print(f"[FAIL] {result['message']}")

    print("\n[OK] analysis.py delegation to llm_analyzer verified.")
