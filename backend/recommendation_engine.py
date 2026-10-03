"""
recommendation_engine.py  —  AI-Powered Job Recommendation & Ranking Engine
=============================================================================
File   : backend/recommendation_engine.py
Project: AI Resume Analyzer & Job Recommendation System

Ranks matched jobs using five signals and generates personalised career
content (application tips, cover letter points, interview prep) for each job.

    ┌──────────────────────────────────────────────────────────────────┐
    │  Ranking Signals                 Weight                         │
    │  ──────────────────────────────────────────────────────────────  │
    │  Match Percentage                  45%                          │
    │  Recency (Posted Date)             20%                          │
    │  Salary Attractiveness             15%                          │
    │  Competition (Applicant Count)     10%                          │
    │  Remote Preference Alignment       10%                          │
    └──────────────────────────────────────────────────────────────────┘

    ┌──────────────────────────────────────────────────────────────────┐
    │  Job Grade Tiers                                                 │
    │  ──────────────────────────────────────────────────────────────  │
    │  Excellent   85 – 100                                           │
    │  Good        70 – 84                                            │
    │  Fair        60 – 69                                            │
    │  Below Fair  < 60   (filtered from top picks by default)        │
    └──────────────────────────────────────────────────────────────────┘

Public API:
    rank_jobs(matched_jobs, user_prefs)
    select_top_jobs(ranked_jobs, n, min_grade)
    generate_application_tips(job, resume_data)
    generate_cover_letter_points(job, resume_data)
    generate_interview_tips(job, resume_data)
    track_saved_jobs(user_id, job_id, action)
    track_application_status(user_id, job_id, status, notes)
    get_saved_jobs(user_id)
    get_application_history(user_id)
    get_full_recommendations(matched_jobs, resume_data, user_prefs, user_id)
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import sqlite3
import sys
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Generator, Optional

from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Logger
# ---------------------------------------------------------------------------
logger = logging.getLogger(__name__)
if not logger.handlers:
    _h = logging.StreamHandler(sys.stdout)
    _h.setFormatter(logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    logger.addHandler(_h)
logger.setLevel(getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO))

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
_DB_PATH = Path(os.getenv("DATABASE_PATH", "data/database.db"))

# ── Ranking signal weights (must sum to 1.0) ──────────────────────────────
W_MATCH     = 0.45   # overall match % from job_matcher
W_RECENCY   = 0.20   # how recently posted
W_SALARY    = 0.15   # salary attractiveness
W_COMP      = 0.10   # competition (fewer applicants = higher score)
W_REMOTE    = 0.10   # remote preference alignment

# ── Grade thresholds ──────────────────────────────────────────────────────
GRADE_EXCELLENT = 85
GRADE_GOOD      = 70
GRADE_FAIR      = 60

# ── Application status options ────────────────────────────────────────────
VALID_STATUSES = {
    "saved", "applied", "interviewing", "offer_received",
    "rejected", "withdrawn", "accepted",
}


# ===========================================================================
# Database helpers
# ===========================================================================

_DDL = """
CREATE TABLE IF NOT EXISTS saved_jobs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT    NOT NULL,
    job_id      TEXT    NOT NULL,
    job_title   TEXT,
    company     TEXT,
    job_url     TEXT,
    salary      TEXT,
    location    TEXT,
    source      TEXT,
    match_pct   REAL    DEFAULT 0,
    rank_score  REAL    DEFAULT 0,
    grade       TEXT,
    saved_at    TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE(user_id, job_id)
);

CREATE TABLE IF NOT EXISTS application_tracker (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT    NOT NULL,
    job_id      TEXT    NOT NULL,
    job_title   TEXT,
    company     TEXT,
    status      TEXT    NOT NULL DEFAULT 'saved',
    notes       TEXT,
    applied_at  TEXT,
    updated_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_sj_user   ON saved_jobs(user_id);
CREATE INDEX IF NOT EXISTS idx_at_user   ON application_tracker(user_id);
CREATE INDEX IF NOT EXISTS idx_at_status ON application_tracker(status);
"""


@contextmanager
def _db(write: bool = False) -> Generator[sqlite3.Connection, None, None]:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH), timeout=15, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        if write:
            conn.commit()
    except sqlite3.Error:
        conn.rollback()
        raise
    finally:
        conn.close()


def _ensure_tables() -> None:
    try:
        with _db(write=True) as conn:
            conn.executescript(_DDL)
    except sqlite3.Error as exc:
        logger.error("_ensure_tables: %s", exc)


# ===========================================================================
# Internal helpers
# ===========================================================================

def _parse_salary(salary_str: str) -> float:
    """
    Extract a numeric annual salary estimate from a salary string.
    Returns 0.0 if not parseable.

    Examples:
        "$130,000 - $170,000"  → 150000.0
        "₹12 LPA"              → 1200000.0  (LPA = lakhs per annum)
        "80k-100k"             → 90000.0
        "Remote"               → 0.0
    """
    if not salary_str:
        return 0.0

    text = str(salary_str).lower().replace(",", "")

    # Lakhs per annum (Indian scale)
    lpa = re.findall(r"(\d+(?:\.\d+)?)\s*lpa", text)
    if lpa:
        return float(lpa[0]) * 100_000

    # k suffix
    nums_k = re.findall(r"(\d+(?:\.\d+)?)\s*k", text)
    if nums_k:
        vals = [float(n) * 1000 for n in nums_k]
        return sum(vals) / len(vals)

    # Plain numbers
    nums = re.findall(r"\d+(?:\.\d+)?", text)
    if not nums:
        return 0.0

    vals = [float(n) for n in nums if float(n) > 1000]
    return sum(vals) / len(vals) if vals else 0.0


def _parse_posted_days_ago(posted_date: str) -> int:
    """
    Return number of days since the job was posted.
    Returns 30 (neutral) if unparseable.
    """
    if not posted_date:
        return 30

    text = str(posted_date).lower().strip()

    # "2 hours ago", "1 hour ago"
    if "hour" in text:
        return 0

    # "3 days ago", "1 day ago"
    m = re.search(r"(\d+)\s*day", text)
    if m:
        return int(m.group(1))

    # "2 weeks ago"
    m = re.search(r"(\d+)\s*week", text)
    if m:
        return int(m.group(1)) * 7

    # "1 month ago"
    m = re.search(r"(\d+)\s*month", text)
    if m:
        return int(m.group(1)) * 30

    # ISO date string: "2026-05-20"
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%Y-%m-%dT%H:%M:%S"):
        try:
            dt = datetime.strptime(text[:len(fmt)], fmt)
            return (datetime.now() - dt).days
        except ValueError:
            continue

    return 30  # neutral fallback


def _recency_score(days_ago: int) -> float:
    """
    Convert days-since-posting into a 0–100 recency score.
    Fresh postings score highest; 90-day-old postings score near zero.
    Uses exponential decay: score = 100 * e^(-k * days)
    """
    k = 0.03   # decay constant → half-life ≈ 23 days
    return min(100.0, 100.0 * math.exp(-k * max(0, days_ago)))


def _competition_score(applicant_count: int | str) -> float:
    """
    Fewer applicants → higher score (less competition).
    Score range: 0–100.
        0 applicants  → 100
        200 applicants → ~37
        500+ applicants → ~10
    """
    try:
        count = int(str(applicant_count).split()[0].replace(",", ""))
    except (ValueError, AttributeError, IndexError):
        return 60.0  # neutral if unknown

    if count <= 0:
        return 100.0
    # Inverse exponential: score = 100 * e^(-count/200)
    return min(100.0, max(0.0, 100.0 * math.exp(-count / 200)))


def _salary_score(salary: float, market_midpoint: float = 100_000) -> float:
    """
    Normalise salary to a 0–100 score relative to a market midpoint.
    Salaries above midpoint score near 100; 0 salary scores 50 (unknown).
    """
    if salary <= 0:
        return 50.0  # unknown salary → neutral

    ratio = salary / max(market_midpoint, 1)
    # Sigmoid-like: saturates at ~200% of midpoint
    return min(100.0, max(0.0, (math.log(ratio + 0.5) + 1) * 40))


def _remote_score(job: dict, prefer_remote: bool) -> float:
    """
    Return 100 if the job's remote status aligns with user preference,
    50 if preference is unknown, 20 if it conflicts.
    """
    location = str(job.get("location", "") or "").lower()
    is_remote = any(kw in location for kw in ("remote", "work from home", "wfh", "anywhere"))

    if not prefer_remote and not is_remote:
        return 80.0  # on-site matches on-site preference
    if prefer_remote == is_remote:
        return 100.0
    return 20.0  # mismatch


def _grade(score: float) -> str:
    if score >= GRADE_EXCELLENT:
        return "Excellent"
    if score >= GRADE_GOOD:
        return "Good"
    if score >= GRADE_FAIR:
        return "Fair"
    return "Poor"


# ===========================================================================
# 1.  rank_jobs()
# ===========================================================================

def rank_jobs(
    matched_jobs: list[dict],
    user_prefs:   Optional[dict] = None,
) -> list[dict]:
    """
    Rank a list of match-enriched job dicts using five weighted signals.

    Each job dict should contain at minimum:
        overall_match      int      — from job_matcher.match_resume_to_job()
        job_title          str
        company_name       str
        location           str
        posted_date        str
        salary             str
        applicant_count    int|str
        job_url            str
        source             str

    Args:
        matched_jobs: List of job dicts (job data + match scores merged).
        user_prefs:   Optional preferences dict:
            {
                "prefer_remote":      bool   (default True),
                "preferred_salary":   float  (annual, USD; default 100_000),
                "preferred_location": str,
            }

    Returns:
        Same list sorted by rank_score descending, each dict enriched with:
            rank_score    float   — composite ranking score (0–100)
            rank          int     — 1-based position
            grade         str     — Excellent / Good / Fair / Poor
            signal_scores dict    — breakdown of each signal
    """
    if not matched_jobs:
        return []

    prefs = user_prefs or {}
    prefer_remote    = prefs.get("prefer_remote", True)
    preferred_salary = prefs.get("preferred_salary", 100_000)

    scored: list[dict] = []

    for job in matched_jobs:
        # ── Signal 1: Match percentage ─────────────────────────────────
        match_pct   = float(job.get("overall_match", 0) or 0)
        match_sig   = match_pct  # already 0-100

        # ── Signal 2: Recency ──────────────────────────────────────────
        days_ago    = _parse_posted_days_ago(str(job.get("posted_date", "")))
        recency_sig = _recency_score(days_ago)

        # ── Signal 3: Salary ───────────────────────────────────────────
        salary_val  = _parse_salary(str(job.get("salary", "")))
        salary_sig  = _salary_score(salary_val, preferred_salary)

        # ── Signal 4: Competition ──────────────────────────────────────
        comp_sig    = _competition_score(job.get("applicant_count", ""))

        # ── Signal 5: Remote preference ────────────────────────────────
        remote_sig  = _remote_score(job, prefer_remote)

        # ── Composite rank score ───────────────────────────────────────
        rank_score = (
            match_sig   * W_MATCH
            + recency_sig * W_RECENCY
            + salary_sig  * W_SALARY
            + comp_sig    * W_COMP
            + remote_sig  * W_REMOTE
        )
        rank_score = min(round(rank_score, 2), 100.0)

        job = dict(job)   # shallow copy — don't mutate original
        job["rank_score"]    = rank_score
        job["grade"]         = _grade(match_pct)
        job["days_ago"]      = days_ago
        job["salary_parsed"] = round(salary_val)
        job["signal_scores"] = {
            "match":      round(match_sig, 1),
            "recency":    round(recency_sig, 1),
            "salary":     round(salary_sig, 1),
            "competition": round(comp_sig, 1),
            "remote":     round(remote_sig, 1),
        }
        scored.append(job)

    # Sort by rank_score DESC, then match_pct DESC as tiebreaker
    scored.sort(key=lambda j: (j["rank_score"], j.get("overall_match", 0)), reverse=True)

    for i, job in enumerate(scored, 1):
        job["rank"] = i

    logger.info(
        "rank_jobs: ranked %d jobs — top='%s' score=%.1f",
        len(scored),
        scored[0].get("job_title", "")[:40] if scored else "",
        scored[0]["rank_score"] if scored else 0,
    )
    return scored


# ===========================================================================
# 2.  select_top_jobs()
# ===========================================================================

def select_top_jobs(
    ranked_jobs: list[dict],
    n:           int = 10,
    min_grade:   str = "Fair",
) -> dict:
    """
    Select the top-N jobs from a ranked list, grouped by grade tier.

    Args:
        ranked_jobs: Output of rank_jobs().
        n:           Maximum jobs to return (default 10).
        min_grade:   Minimum grade to include: "Excellent", "Good", or "Fair".

    Returns:
        {
            "total_ranked":   int,
            "total_selected": int,
            "excellent":      list[dict],
            "good":           list[dict],
            "fair":           list[dict],
            "top_jobs":       list[dict],   ← flat top-N across all grades
            "summary":        str,
        }
    """
    grade_order = {"Excellent": 3, "Good": 2, "Fair": 1, "Poor": 0}
    min_order   = grade_order.get(min_grade, 1)

    eligible = [
        j for j in ranked_jobs
        if grade_order.get(j.get("grade", "Poor"), 0) >= min_order
    ]

    top = eligible[:n]

    excellent = [j for j in top if j.get("grade") == "Excellent"]
    good      = [j for j in top if j.get("grade") == "Good"]
    fair      = [j for j in top if j.get("grade") == "Fair"]

    summary = (
        f"Found {len(ranked_jobs)} matched jobs. "
        f"Top {len(top)} selected: "
        f"{len(excellent)} Excellent, {len(good)} Good, {len(fair)} Fair."
    )

    logger.info("select_top_jobs: %s", summary)
    return {
        "total_ranked":   len(ranked_jobs),
        "total_selected": len(top),
        "excellent":      excellent,
        "good":           good,
        "fair":           fair,
        "top_jobs":       top,
        "summary":        summary,
    }


# ===========================================================================
# 3.  generate_application_tips()
# ===========================================================================

def generate_application_tips(
    job:         dict,
    resume_data: dict,
) -> dict:
    """
    Generate personalised application tips for a specific job.

    Tips cover:
        - Resume customisation for this role
        - Skills to highlight / add
        - Keyword optimisation for ATS
        - Timing and application strategy

    Args:
        job:         Ranked job dict (includes match scores).
        resume_data: Parsed resume dict.

    Returns:
        {
            "job_title":        str,
            "company":          str,
            "overall_match":    int,
            "resume_tips":      list[str],
            "ats_keywords":     list[str],
            "timing_advice":    str,
            "priority":         str,   ← "High" / "Medium" / "Low"
        }
    """
    title        = job.get("job_title", "this role")
    company      = job.get("company_name", "the company")
    match_pct    = int(job.get("overall_match", 0))
    missing      = job.get("missing_skills", [])
    matching     = job.get("matching_skills", [])
    grade        = job.get("grade", "Fair")
    days_ago     = int(job.get("days_ago", 30))
    salary       = job.get("salary", "")
    applicants   = job.get("applicant_count", "")
    exp_gap      = float(job.get("experience_gap_years", 0))
    location     = str(job.get("location", "")).lower()
    is_remote    = "remote" in location

    resume_skills = set(
        s.lower() for s in (
            resume_data.get("identified_skills", [])
            or resume_data.get("skills", [])
        )
    )

    tips: list[str] = []

    # ── Resume customisation tips ──────────────────────────────────────────
    if missing:
        top_missing = missing[:3]
        tips.append(
            f"Add a 'Skills' section and explicitly list: "
            f"{', '.join(top_missing)} — these are required by {company}."
        )

    if matching:
        top_match = matching[:4]
        tips.append(
            f"Lead your resume summary with your expertise in "
            f"{', '.join(top_match)} to immediately grab the recruiter's attention."
        )

    if exp_gap > 0:
        tips.append(
            f"You have a ~{exp_gap:.0f}-year experience gap. "
            f"Compensate by quantifying achievements (e.g., '30% faster API', "
            f"'reduced costs by $X') and highlighting relevant projects."
        )

    tips.append(
        f"Tailor your resume headline to include '{title}' and one core skill "
        f"so ATS systems immediately categorise you correctly."
    )

    if match_pct >= GRADE_EXCELLENT:
        tips.append(
            "Your profile is an excellent fit — apply promptly and keep your "
            "LinkedIn profile updated to match the resume you submit."
        )
    elif match_pct >= GRADE_GOOD:
        tips.append(
            "Write a strong professional summary (3–4 sentences) that connects "
            "your specific experience to this role's requirements."
        )
    else:
        tips.append(
            "Bridge the skill gap with a 'Projects' section showcasing "
            "self-built work using the required technologies."
        )

    # ── ATS keywords ──────────────────────────────────────────────────────
    ats_keywords: list[str] = list(matching[:6]) + [
        s for s in missing[:4]
        if s.lower() not in resume_skills
    ]

    # ── Timing advice ─────────────────────────────────────────────────────
    if days_ago <= 2:
        timing = (
            "Apply TODAY — this job was posted very recently. "
            "Early applicants are often prioritised by recruiters."
        )
        priority = "High"
    elif days_ago <= 7:
        timing = (
            "Apply within the next 24-48 hours. "
            "The posting is still fresh and competition is still low."
        )
        priority = "High" if grade == "Excellent" else "Medium"
    elif days_ago <= 21:
        timing = (
            "Apply soon — the posting is 1-3 weeks old. "
            "Customise your resume carefully to stand out from accumulated applicants."
        )
        priority = "Medium"
    else:
        timing = (
            "The posting is older — verify it is still open before investing "
            "significant customisation time. If open, a strong tailored application "
            "can still succeed."
        )
        priority = "Low" if grade != "Excellent" else "Medium"

    logger.debug(
        "generate_application_tips: job='%s' match=%d%% tips=%d",
        title[:30], match_pct, len(tips),
    )
    return {
        "job_title":     title,
        "company":       company,
        "overall_match": match_pct,
        "resume_tips":   tips,
        "ats_keywords":  ats_keywords,
        "timing_advice": timing,
        "priority":      priority,
    }


# ===========================================================================
# 4.  generate_cover_letter_points()
# ===========================================================================

def generate_cover_letter_points(
    job:         dict,
    resume_data: dict,
) -> dict:
    """
    Generate structured cover letter talking points for a specific job.

    Output includes:
        - Opening hook
        - 3 value-proposition bullet points
        - Skill bridge paragraph
        - Closing call-to-action

    Args:
        job:         Ranked job dict.
        resume_data: Parsed resume dict.

    Returns:
        {
            "job_title":          str,
            "company":            str,
            "opening_hook":       str,
            "value_propositions": list[str],
            "skill_bridge":       str,
            "closing_cta":        str,
            "tone_advice":        str,
            "full_template":      str,
        }
    """
    title        = job.get("job_title", "the role")
    company      = job.get("company_name", "your company")
    matching     = job.get("matching_skills", [])
    missing      = job.get("missing_skills", [])
    match_pct    = int(job.get("overall_match", 0))
    exp_years    = float(resume_data.get("years_of_experience", 0))
    edu_raw      = resume_data.get("education", "")
    edu_text     = edu_raw if isinstance(edu_raw, str) else " ".join(str(e) for e in edu_raw)
    resume_name  = resume_data.get("name", "I")

    top_skills   = matching[:3]
    top_missing  = missing[:2]

    # ── Opening hook ──────────────────────────────────────────────────────
    if match_pct >= GRADE_EXCELLENT:
        hook = (
            f"As a professional with {exp_years:.0f}+ years of hands-on experience in "
            f"{', '.join(top_skills[:2]) if top_skills else 'software engineering'}, "
            f"I was immediately drawn to the {title} opportunity at {company}. "
            f"My background aligns closely with your team's needs, and I am excited "
            f"to bring measurable impact from day one."
        )
    elif match_pct >= GRADE_GOOD:
        hook = (
            f"I am writing to express my strong interest in the {title} position at {company}. "
            f"With experience in {', '.join(top_skills[:2]) if top_skills else 'this domain'} "
            f"and a track record of delivering results, I believe I can contribute "
            f"meaningfully to your team's mission."
        )
    else:
        hook = (
            f"I am excited to apply for the {title} role at {company}. "
            f"While I am still building expertise in certain areas you require, "
            f"my strong foundation in {top_skills[0] if top_skills else 'core technologies'} "
            f"and eagerness to grow make me a motivated addition to your team."
        )

    # ── Value propositions ────────────────────────────────────────────────
    vps: list[str] = []

    if top_skills:
        vps.append(
            f"Proven expertise in {', '.join(top_skills)} — directly matching "
            f"your core technical requirements."
        )

    if exp_years >= 2:
        vps.append(
            f"{exp_years:.0f}+ years of professional experience building and "
            f"shipping production-quality software, with a focus on quality and reliability."
        )
    else:
        vps.append(
            "Strong academic foundation and self-driven project portfolio that "
            "demonstrates real-world problem-solving ability."
        )

    if edu_text:
        vps.append(
            f"Educational background ({edu_text[:60].strip()}) that provided "
            f"rigorous technical and analytical training relevant to this role."
        )

    # ── Skill bridge ──────────────────────────────────────────────────────
    if top_missing:
        bridge = (
            f"I recognise that {', '.join(top_missing)} are important for this role. "
            f"I am actively upskilling in these areas through hands-on projects and "
            f"online courses, and I am confident I can reach proficiency quickly "
            f"given my existing technical base."
        )
    else:
        bridge = (
            f"My skill set maps directly to your requirements, and I am well-positioned "
            f"to contribute to {company}'s goals without a significant ramp-up period."
        )

    # ── Closing CTA ───────────────────────────────────────────────────────
    cta = (
        f"I would welcome the opportunity to discuss how my background can contribute "
        f"to {company}'s success. I am available for a call or interview at your "
        f"convenience and can provide a portfolio of relevant work upon request."
    )

    # ── Tone advice ───────────────────────────────────────────────────────
    tone = (
        "Keep the tone professional yet enthusiastic. "
        "Aim for 3 paragraphs, under 350 words. "
        "Use specific numbers wherever possible (e.g., '20% faster', '3 microservices')."
    )

    # ── Full template ─────────────────────────────────────────────────────
    full = "\n\n".join([hook, "\n".join(f"• {v}" for v in vps), bridge, cta])

    logger.debug(
        "generate_cover_letter_points: job='%s' match=%d%%", title[:30], match_pct
    )
    return {
        "job_title":          title,
        "company":            company,
        "opening_hook":       hook,
        "value_propositions": vps,
        "skill_bridge":       bridge,
        "closing_cta":        cta,
        "tone_advice":        tone,
        "full_template":      full,
    }


# ===========================================================================
# 5.  generate_interview_tips()
# ===========================================================================

def generate_interview_tips(
    job:         dict,
    resume_data: dict,
) -> dict:
    """
    Generate role-specific interview preparation tips.

    Covers:
        - Technical topics to study
        - Behavioural questions to prepare
        - Company research checklist
        - Common questions for this role type
        - Salary negotiation advice

    Args:
        job:         Ranked job dict.
        resume_data: Parsed resume dict.

    Returns:
        {
            "job_title":              str,
            "company":                str,
            "technical_topics":       list[str],
            "behavioural_questions":  list[str],
            "company_research":       list[str],
            "common_questions":       list[str],
            "salary_negotiation":     list[str],
            "preparation_timeline":   str,
        }
    """
    title      = job.get("job_title", "this role")
    company    = job.get("company_name", "the company")
    matching   = job.get("matching_skills", [])
    missing    = job.get("missing_skills", [])
    salary_str = job.get("salary", "")
    salary_val = _parse_salary(salary_str)
    match_pct  = int(job.get("overall_match", 0))

    title_lower = title.lower()

    # ── Detect role category ──────────────────────────────────────────────
    is_ml = any(k in title_lower for k in ("machine learning", "ml", "data scientist", "ai"))
    is_be = any(k in title_lower for k in ("backend", "back-end", "api", "server"))
    is_fe = any(k in title_lower for k in ("frontend", "front-end", "react", "ui", "ux"))
    is_fs = any(k in title_lower for k in ("full stack", "fullstack", "full-stack"))
    is_de = any(k in title_lower for k in ("data engineer", "etl", "pipeline", "spark"))
    is_dv = any(k in title_lower for k in ("devops", "sre", "platform", "infrastructure"))
    is_mo = any(k in title_lower for k in ("mobile", "android", "ios", "flutter", "kotlin"))

    # ── Technical topics ──────────────────────────────────────────────────
    tech_base = [f"Deep dive into: {s}" for s in matching[:4]]

    if is_ml:
        tech_extra = [
            "ML fundamentals: bias-variance tradeoff, overfitting, regularisation",
            "Model evaluation metrics: Precision, Recall, F1, AUC-ROC",
            "Feature engineering and selection techniques",
            "Python ML stack: pandas, scikit-learn, TensorFlow/PyTorch",
            "MLOps basics: model versioning, A/B testing, monitoring",
        ]
    elif is_be:
        tech_extra = [
            "System design: scalability, load balancing, database sharding",
            "REST API design principles and best practices",
            "SQL optimisation: indexing, query plans, transactions",
            "Caching strategies: Redis, CDN, in-memory caching",
            "Microservices architecture and service communication patterns",
        ]
    elif is_fe:
        tech_extra = [
            "Browser rendering performance and Core Web Vitals",
            "React/Vue lifecycle, state management (Redux, Zustand)",
            "CSS architecture: BEM, CSS modules, styled-components",
            "Accessibility (WCAG) and semantic HTML",
            "JavaScript event loop and async patterns",
        ]
    elif is_de:
        tech_extra = [
            "Data pipeline architecture: batch vs streaming",
            "Apache Spark transformations and optimisations",
            "Data warehousing: star schema, dimensional modelling",
            "SQL window functions, CTEs, query optimisation",
            "Data quality and governance practices",
        ]
    elif is_dv:
        tech_extra = [
            "CI/CD pipeline design (GitHub Actions, Jenkins, ArgoCD)",
            "Kubernetes: deployments, services, ingress, RBAC",
            "Infrastructure as Code: Terraform, Pulumi",
            "Observability: Prometheus, Grafana, distributed tracing",
            "Linux internals and shell scripting",
        ]
    elif is_mo:
        tech_extra = [
            "Mobile architecture patterns: MVVM, Clean Architecture",
            "State management and lifecycle awareness",
            "App performance profiling and optimisation",
            "Push notifications and background processing",
            "App store submission and release management",
        ]
    else:
        tech_extra = [
            "Data structures & algorithms (practise on LeetCode — medium level)",
            "OOP and design patterns (SOLID, Factory, Observer)",
            "Version control best practices (Git branching, rebasing)",
            "Code review etiquette and clean code principles",
        ]

    if missing:
        tech_extra.append(
            f"Quick study: {missing[0]} — focus on core concepts, not mastery"
        )

    technical_topics = tech_base + tech_extra

    # ── Behavioural questions ─────────────────────────────────────────────
    behavioural = [
        "Tell me about yourself (practise a 90-second structured pitch).",
        "Describe a time you solved a difficult technical problem under pressure.",
        "Give an example of how you handled a conflict with a teammate.",
        "Tell me about a project you led from start to finish — what went wrong?",
        "How do you stay current with industry trends and new technologies?",
        "Describe a time you had to learn a new technology quickly.",
        "How do you prioritise when you have multiple deadlines simultaneously?",
    ]
    if match_pct < GRADE_GOOD:
        behavioural.append(
            "Be ready to explain skill gaps honestly and articulate your "
            "learning plan — interviewers appreciate self-awareness."
        )

    # ── Company research ──────────────────────────────────────────────────
    research = [
        f"Read {company}'s 'About Us' page — note their mission, values, and recent news.",
        f"Check {company}'s LinkedIn for recent posts, headcount growth, and team culture.",
        f"Review {company}'s product(s) — sign up for a trial or watch a demo if available.",
        "Read Glassdoor and Blind reviews — identify common interview themes.",
        "Understand their tech stack (StackShare, job descriptions, engineering blog).",
        "Look up the interviewer on LinkedIn — find common ground topics.",
        "Know their business model and primary revenue drivers.",
    ]

    # ── Common interview questions ────────────────────────────────────────
    common = [
        f"Why do you want to work at {company} specifically?",
        f"What interests you most about the {title} role?",
        "Where do you see yourself in 3–5 years?",
        "What is your greatest technical strength and weakness?",
        "Walk me through your most impactful project.",
        "How do you approach debugging a complex issue in production?",
        "What does your ideal team culture look like?",
    ]

    # ── Salary negotiation ────────────────────────────────────────────────
    if salary_val > 0:
        salary_range_low  = round(salary_val * 0.9 / 1000) * 1000
        salary_range_high = round(salary_val * 1.15 / 1000) * 1000
        salary_advice = [
            f"Posted salary: {salary_str}. Research Glassdoor/Levels.fyi for benchmarks.",
            f"Target range: ${salary_range_low:,} – ${salary_range_high:,} based on posted figure.",
            "Never anchor first — let the employer name a number if possible.",
            "Negotiate total compensation: base, bonus, equity, benefits, remote flexibility.",
            "Always get the offer in writing before accepting or declining.",
        ]
    else:
        salary_advice = [
            "Research market rates on Glassdoor, Levels.fyi, or LinkedIn Salary.",
            "Prepare a target range based on your experience and location.",
            "If asked, give a range (not a single number) and anchor high.",
            "Consider total comp: base + bonus + equity + benefits.",
            "Practise salary negotiation responses — it is always expected.",
        ]

    # ── Preparation timeline ──────────────────────────────────────────────
    timeline = (
        "Day 1-2: Research the company and role. Update resume for ATS. "
        "Day 3-4: Review technical topics and practise LeetCode problems. "
        "Day 5: Mock-interview with a friend or record yourself answering STAR questions. "
        "Day 6: Prepare smart questions to ask the interviewer. "
        "Day 7: Rest, review notes briefly, and arrive/log in early."
    )

    logger.debug(
        "generate_interview_tips: job='%s' tech=%d tips behavioural=%d",
        title[:30], len(technical_topics), len(behavioural),
    )
    return {
        "job_title":             title,
        "company":               company,
        "technical_topics":      technical_topics,
        "behavioural_questions": behavioural,
        "company_research":      research,
        "common_questions":      common,
        "salary_negotiation":    salary_advice,
        "preparation_timeline":  timeline,
    }


# ===========================================================================
# 6.  track_saved_jobs()
# ===========================================================================

def track_saved_jobs(
    user_id: str,
    job_id:  str,
    action:  str = "save",
    job_data: Optional[dict] = None,
) -> dict:
    """
    Save or unsave a job for a user.

    Args:
        user_id:  Authenticated user UUID.
        job_id:   Unique job identifier.
        action:   "save" or "unsave".
        job_data: Optional job dict to store metadata on save.

    Returns:
        {success, action, message}
    """
    _ensure_tables()

    action = action.lower().strip()
    if action not in ("save", "unsave"):
        return {"success": False, "action": action, "message": "Invalid action — use 'save' or 'unsave'."}

    try:
        with _db(write=True) as conn:
            if action == "unsave":
                conn.execute(
                    "DELETE FROM saved_jobs WHERE user_id=? AND job_id=?",
                    (user_id, str(job_id)),
                )
                logger.info("track_saved_jobs: unsaved job_id='%s' user='%s'", job_id[:20], user_id[:8])
                return {"success": True, "action": "unsave", "message": "Job removed from saved list."}

            # Save
            jd = job_data or {}
            conn.execute(
                """
                INSERT INTO saved_jobs
                    (user_id, job_id, job_title, company, job_url, salary,
                     location, source, match_pct, rank_score, grade)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(user_id, job_id) DO UPDATE SET
                    job_title  = excluded.job_title,
                    match_pct  = excluded.match_pct,
                    rank_score = excluded.rank_score,
                    grade      = excluded.grade,
                    saved_at   = datetime('now')
                """,
                (
                    user_id,
                    str(job_id),
                    jd.get("job_title", ""),
                    jd.get("company_name", ""),
                    jd.get("job_url", ""),
                    jd.get("salary", ""),
                    jd.get("location", ""),
                    jd.get("source", ""),
                    float(jd.get("overall_match", 0)),
                    float(jd.get("rank_score", 0)),
                    jd.get("grade", ""),
                ),
            )
        logger.info("track_saved_jobs: saved job_id='%s' user='%s'", job_id[:20], user_id[:8])
        return {"success": True, "action": "save", "message": "Job saved to your list."}

    except sqlite3.Error as exc:
        logger.error("track_saved_jobs: %s", exc)
        return {"success": False, "action": action, "message": str(exc)}


# ===========================================================================
# 7.  track_application_status()
# ===========================================================================

def track_application_status(
    user_id:  str,
    job_id:   str,
    status:   str,
    notes:    str = "",
    job_data: Optional[dict] = None,
) -> dict:
    """
    Create or update an application tracking record.

    Valid statuses:
        saved | applied | interviewing | offer_received |
        rejected | withdrawn | accepted

    Args:
        user_id:  Authenticated user UUID.
        job_id:   Unique job identifier.
        status:   Application status string.
        notes:    Optional free-text notes.
        job_data: Optional job dict for metadata.

    Returns:
        {success, status, message}
    """
    _ensure_tables()

    status = status.lower().strip()
    if status not in VALID_STATUSES:
        return {
            "success": False,
            "status":  status,
            "message": f"Invalid status. Valid: {', '.join(sorted(VALID_STATUSES))}",
        }

    jd = job_data or {}
    now = datetime.now(timezone.utc).isoformat()
    applied_at = now if status == "applied" else None

    try:
        with _db(write=True) as conn:
            existing = conn.execute(
                "SELECT id, applied_at FROM application_tracker WHERE user_id=? AND job_id=?",
                (user_id, str(job_id)),
            ).fetchone()

            if existing:
                # Keep original applied_at if already set
                old_applied = existing["applied_at"]
                conn.execute(
                    """
                    UPDATE application_tracker
                    SET status=?, notes=?, updated_at=?,
                        applied_at=COALESCE(applied_at, ?)
                    WHERE user_id=? AND job_id=?
                    """,
                    (status, notes, now, applied_at, user_id, str(job_id)),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO application_tracker
                        (user_id, job_id, job_title, company, status, notes, applied_at, updated_at)
                    VALUES (?,?,?,?,?,?,?,?)
                    """,
                    (
                        user_id, str(job_id),
                        jd.get("job_title", ""), jd.get("company_name", ""),
                        status, notes, applied_at, now,
                    ),
                )

        logger.info(
            "track_application_status: user='%s' job='%s' status='%s'",
            user_id[:8], job_id[:20], status,
        )
        return {"success": True, "status": status, "message": f"Application status updated to '{status}'."}

    except sqlite3.Error as exc:
        logger.error("track_application_status: %s", exc)
        return {"success": False, "status": status, "message": str(exc)}


# ===========================================================================
# 8.  get_saved_jobs()
# ===========================================================================

def get_saved_jobs(user_id: str, limit: int = 50) -> list[dict]:
    """
    Return all saved jobs for a user, sorted by rank_score.

    Args:
        user_id: Authenticated user UUID.
        limit:   Max rows to return (default 50).

    Returns:
        List of saved job dicts.
    """
    _ensure_tables()
    try:
        with _db() as conn:
            rows = conn.execute(
                """
                SELECT * FROM saved_jobs
                WHERE user_id = ?
                ORDER BY rank_score DESC, match_pct DESC
                LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.Error as exc:
        logger.error("get_saved_jobs: %s", exc)
        return []


# ===========================================================================
# 9.  get_application_history()
# ===========================================================================

def get_application_history(user_id: str, limit: int = 100) -> dict:
    """
    Return application history grouped by status for dashboard display.

    Args:
        user_id: Authenticated user UUID.
        limit:   Max records to fetch.

    Returns:
        {
            "all":          list[dict],
            "by_status":    dict[str → list[dict]],
            "counts":       dict[str → int],
            "total":        int,
        }
    """
    _ensure_tables()
    try:
        with _db() as conn:
            rows = conn.execute(
                """
                SELECT * FROM application_tracker
                WHERE user_id = ?
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()

        all_records = [dict(r) for r in rows]
        by_status: dict[str, list[dict]] = {s: [] for s in VALID_STATUSES}
        for rec in all_records:
            s = rec.get("status", "saved")
            by_status.setdefault(s, []).append(rec)

        counts = {s: len(v) for s, v in by_status.items()}

        return {
            "all":       all_records,
            "by_status": by_status,
            "counts":    counts,
            "total":     len(all_records),
        }
    except sqlite3.Error as exc:
        logger.error("get_application_history: %s", exc)
        return {"all": [], "by_status": {}, "counts": {}, "total": 0}


# ===========================================================================
# 10. get_full_recommendations()  — main orchestrator
# ===========================================================================

def get_full_recommendations(
    matched_jobs: list[dict],
    resume_data:  dict,
    user_prefs:   Optional[dict] = None,
    user_id:      str = "",
    top_n:        int = 10,
    min_grade:    str = "Fair",
) -> dict:
    """
    Complete pipeline: rank → select → generate tips for each top job.

    This is the primary entry point for the Streamlit UI and API layer.

    Args:
        matched_jobs: Output of job_matcher.match_resume_to_jobs().
        resume_data:  Parsed resume dict.
        user_prefs:   Optional user preference dict (prefer_remote, salary, etc.).
        user_id:      Optional user UUID for auto-save functionality.
        top_n:        Max recommendations to return (default 10).
        min_grade:    Minimum grade threshold (default "Fair").

    Returns:
        {
            "ranked_jobs":      list[dict],
            "selection":        dict,       ← from select_top_jobs()
            "recommendations":  list[dict], ← each with application_tips, cover_letter, interview_tips
            "summary":          str,
            "generated_at":     str,
        }
    """
    logger.info(
        "get_full_recommendations: %d jobs, user='%s', top_n=%d",
        len(matched_jobs), user_id[:8] if user_id else "anon", top_n,
    )

    # ── Step 1: Rank ──────────────────────────────────────────────────────
    ranked = rank_jobs(matched_jobs, user_prefs)

    # ── Step 2: Select top-N ──────────────────────────────────────────────
    selection = select_top_jobs(ranked, n=top_n, min_grade=min_grade)

    # ── Step 3: Generate personalised content for each top job ────────────
    recommendations: list[dict] = []
    for job in selection["top_jobs"]:
        app_tips    = generate_application_tips(job, resume_data)
        cover_pts   = generate_cover_letter_points(job, resume_data)
        interview   = generate_interview_tips(job, resume_data)

        rec = {
            # Core job info
            "rank":              job.get("rank"),
            "job_id":            job.get("job_id", ""),
            "job_title":         job.get("job_title", ""),
            "company_name":      job.get("company_name", ""),
            "location":          job.get("location", ""),
            "job_url":           job.get("job_url", ""),
            "salary":            job.get("salary", ""),
            "posted_date":       job.get("posted_date", ""),
            "source":            job.get("source", ""),
            # Match scores
            "overall_match":     job.get("overall_match", 0),
            "skills_match":      job.get("skills_match", 0),
            "experience_match":  job.get("experience_match", 0),
            "education_match":   job.get("education_match", 0),
            "responsibilities_match": job.get("responsibilities_match", 0),
            "matching_skills":   job.get("matching_skills", []),
            "missing_skills":    job.get("missing_skills", []),
            "match_explanation": job.get("match_explanation", ""),
            # Ranking
            "rank_score":        job.get("rank_score", 0),
            "grade":             job.get("grade", ""),
            "signal_scores":     job.get("signal_scores", {}),
            # Personalised content
            "application_tips":  app_tips,
            "cover_letter":      cover_pts,
            "interview_tips":    interview,
        }
        recommendations.append(rec)

        # Auto-save to DB if user is logged in
        if user_id:
            track_saved_jobs(user_id, rec["job_id"] or rec["job_title"], "save", job)

    summary = (
        f"Generated recommendations for {len(recommendations)} jobs "
        f"({len(selection['excellent'])} Excellent, "
        f"{len(selection['good'])} Good, "
        f"{len(selection['fair'])} Fair) "
        f"from {len(matched_jobs)} matched positions."
    )

    logger.info("get_full_recommendations: DONE — %s", summary)
    return {
        "ranked_jobs":     ranked,
        "selection":       selection,
        "recommendations": recommendations,
        "summary":         summary,
        "generated_at":    datetime.now(timezone.utc).isoformat(),
    }


# ===========================================================================
# Self-test — run with: python -m backend.recommendation_engine
# ===========================================================================

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")

    print()
    print("=" * 68)
    print("  backend/recommendation_engine.py — Self-Test Suite")
    print("=" * 68)

    # ── Sample matched jobs (output of job_matcher) ───────────────────────
    JOBS = [
        {
            "job_id": "job_001", "job_title": "Senior Python Developer",
            "company_name": "TechCorp", "location": "Remote",
            "posted_date": "1 day ago", "salary": "$130,000 - $160,000",
            "applicant_count": 45, "source": "linkedin",
            "job_url": "https://linkedin.com/jobs/view/001",
            "overall_match": 91, "skills_match": 95, "experience_match": 88,
            "education_match": 100, "responsibilities_match": 80,
            "matching_skills": ["Python", "FastAPI", "Docker", "AWS", "PostgreSQL"],
            "missing_skills": [], "experience_gap_years": 0,
            "match_explanation": "Excellent match across all dimensions.",
            "grade": "Excellent",
        },
        {
            "job_id": "job_002", "job_title": "Backend Engineer",
            "company_name": "StartupXYZ", "location": "New York, NY (Hybrid)",
            "posted_date": "5 days ago", "salary": "$90,000 - $110,000",
            "applicant_count": 180, "source": "remotive",
            "job_url": "https://remotive.com/jobs/002",
            "overall_match": 76, "skills_match": 80, "experience_match": 70,
            "education_match": 100, "responsibilities_match": 60,
            "matching_skills": ["Python", "REST API", "SQL"],
            "missing_skills": ["Kubernetes", "Kafka"], "experience_gap_years": 1,
            "match_explanation": "Good match, some gaps.",
            "grade": "Good",
        },
        {
            "job_id": "job_003", "job_title": "Data Scientist",
            "company_name": "DataInsights Co", "location": "Remote",
            "posted_date": "3 weeks ago", "salary": "$120,000",
            "applicant_count": 320, "source": "remotive",
            "job_url": "https://remotive.com/jobs/003",
            "overall_match": 63, "skills_match": 60, "experience_match": 65,
            "education_match": 80, "responsibilities_match": 55,
            "matching_skills": ["Python", "Machine Learning", "SQL"],
            "missing_skills": ["Spark", "Tableau", "R"], "experience_gap_years": 2,
            "match_explanation": "Fair match.",
            "grade": "Fair",
        },
    ]

    RESUME = {
        "identified_skills": [
            "Python", "FastAPI", "PostgreSQL", "Docker", "AWS",
            "Machine Learning", "SQL", "REST API", "Git",
        ],
        "years_of_experience": 5.0,
        "education": "Bachelor of Engineering in Computer Science",
        "extracted_text": "5 years Python backend developer...",
        "name": "Sai Ganesh",
    }

    USER_PREFS = {"prefer_remote": True, "preferred_salary": 130_000}

    # ── Test 1: rank_jobs ─────────────────────────────────────────────────
    print("\n[1/6] rank_jobs()")
    ranked = rank_jobs(JOBS, USER_PREFS)
    assert len(ranked) == 3
    assert ranked[0]["rank"] == 1
    assert ranked[0]["rank_score"] > ranked[-1]["rank_score"]
    print(f"  [OK] Ranked {len(ranked)} jobs")
    for j in ranked:
        print(f"       #{j['rank']} [{j['grade']:9s}] rank={j['rank_score']:.1f} "
              f"match={j['overall_match']}% — {j['job_title']} @ {j['company_name']}")

    # ── Test 2: select_top_jobs ───────────────────────────────────────────
    print("\n[2/6] select_top_jobs()")
    sel = select_top_jobs(ranked, n=5, min_grade="Fair")
    assert sel["total_selected"] == 3
    assert len(sel["excellent"]) >= 1
    print(f"  [OK] Selected: {sel['total_selected']} jobs")
    print(f"  [OK] Excellent: {len(sel['excellent'])} | Good: {len(sel['good'])} | Fair: {len(sel['fair'])}")
    print(f"  [OK] Summary: {sel['summary']}")

    # ── Test 3: generate_application_tips ─────────────────────────────────
    print("\n[3/6] generate_application_tips()")
    tips = generate_application_tips(ranked[0], RESUME)
    assert isinstance(tips["resume_tips"], list) and len(tips["resume_tips"]) >= 3
    assert tips["priority"] in ("High", "Medium", "Low")
    print(f"  [OK] Priority      : {tips['priority']}")
    print(f"  [OK] Timing advice : {tips['timing_advice'][:60]}...")
    print(f"  [OK] ATS keywords  : {tips['ats_keywords'][:5]}")
    for i, t in enumerate(tips["resume_tips"][:2], 1):
        print(f"  [OK] Tip {i}: {t[:70]}...")

    # ── Test 4: generate_cover_letter_points ──────────────────────────────
    print("\n[4/6] generate_cover_letter_points()")
    cl = generate_cover_letter_points(ranked[0], RESUME)
    assert len(cl["value_propositions"]) >= 2
    assert len(cl["full_template"]) > 100
    print(f"  [OK] Opening hook  : {cl['opening_hook'][:70]}...")
    print(f"  [OK] Value props   : {len(cl['value_propositions'])} points")
    print(f"  [OK] Full template : {len(cl['full_template'])} chars")

    # ── Test 5: generate_interview_tips ───────────────────────────────────
    print("\n[5/6] generate_interview_tips()")
    it = generate_interview_tips(ranked[0], RESUME)
    assert len(it["technical_topics"]) >= 4
    assert len(it["behavioural_questions"]) >= 5
    assert len(it["salary_negotiation"]) >= 3
    print(f"  [OK] Technical topics  : {len(it['technical_topics'])}")
    print(f"  [OK] Behavioural Qs   : {len(it['behavioural_questions'])}")
    print(f"  [OK] Company research  : {len(it['company_research'])}")
    print(f"  [OK] Salary negotiation: {len(it['salary_negotiation'])}")
    print(f"  [OK] Timeline: {it['preparation_timeline'][:60]}...")

    # ── Test 6: get_full_recommendations (no DB save) ─────────────────────
    print("\n[6/6] get_full_recommendations() — full pipeline")
    result = get_full_recommendations(
        matched_jobs=JOBS,
        resume_data=RESUME,
        user_prefs=USER_PREFS,
        user_id="",       # no DB save
        top_n=5,
        min_grade="Fair",
    )
    assert len(result["recommendations"]) == 3
    assert result["recommendations"][0]["rank"] == 1
    rec0 = result["recommendations"][0]
    assert "application_tips"  in rec0
    assert "cover_letter"      in rec0
    assert "interview_tips"    in rec0
    print(f"  [OK] Recommendations : {len(result['recommendations'])}")
    print(f"  [OK] Summary         : {result['summary']}")
    print(f"  [OK] Top job         : #{rec0['rank']} {rec0['job_title']} "
          f"@ {rec0['company_name']} (match={rec0['overall_match']}%)")

    print()
    print("=" * 68)
    print("  ALL SELF-TESTS PASSED")
    print("=" * 68)
    print()
    print("  Sample recommendation JSON (job #1):")
    print(json.dumps({
        "rank":             rec0["rank"],
        "job_title":        rec0["job_title"],
        "company_name":     rec0["company_name"],
        "overall_match":    rec0["overall_match"],
        "grade":            rec0["grade"],
        "rank_score":       rec0["rank_score"],
        "signal_scores":    rec0["signal_scores"],
        "resume_tips":      rec0["application_tips"]["resume_tips"][:2],
        "cover_letter_hook": rec0["cover_letter"]["opening_hook"][:120] + "...",
        "technical_topics": rec0["interview_tips"]["technical_topics"][:3],
        "priority":         rec0["application_tips"]["priority"],
    }, indent=2))
