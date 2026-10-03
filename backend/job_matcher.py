"""
job_matcher.py  —  AI-Powered Job–Resume Match Engine
======================================================
File   : backend/job_matcher.py
Project: AI Resume Analyzer & Job Recommendation System

Calculates a weighted composite match score between a parsed resume
and a job description across four dimensions:

    ┌─────────────────────────────────────────────────────────┐
    │  Dimension           Weight   Method                    │
    │  ─────────────────────────────────────────────────────  │
    │  Skills Match          50%    Token-overlap + synonyms  │
    │  Experience Match      25%    Years + seniority model   │
    │  Education Match       15%    Degree-tier hierarchy     │
    │  Responsibilities Match 10%   TF-IDF cosine similarity  │
    └─────────────────────────────────────────────────────────┘

Output schema:
    {
        "job_id":                  str,
        "overall_match":           int,   # 0-100
        "skills_match":            int,
        "experience_match":        int,
        "education_match":         int,
        "responsibilities_match":  int,
        "matching_skills":         list[str],
        "missing_skills":          list[str],
        "match_explanation":       str,
        "grade":                   str,   # Excellent / Good / Fair / Poor
        "recommendation":          str,
    }

Public API:
    extract_job_requirements(job_description)
    calculate_skill_match(resume_skills, job_requirements)
    calculate_experience_match(resume_data, job_requirements)
    calculate_education_match(resume_data, job_requirements)
    calculate_overall_score(skill_s, exp_s, edu_s, resp_s)
    generate_match_explanation(result_dict)
    save_match_results(user_id, job_id, result_dict)
    match_resume_to_job(resume_data, job_description, job_id, user_id)
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import sqlite3
import sys
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
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

# Scoring weights (must sum to 1.0)
WEIGHT_SKILLS          = 0.50
WEIGHT_EXPERIENCE      = 0.25
WEIGHT_EDUCATION       = 0.15
WEIGHT_RESPONSIBILITIES = 0.10

# Grade thresholds
_GRADES = [
    (90, "Excellent",  "Strong match — highly recommended to apply."),
    (75, "Good",       "Good match — tailor your resume and apply."),
    (55, "Fair",       "Moderate match — address skill gaps before applying."),
    (0,  "Poor",       "Low match — significant upskilling recommended."),
]


# ===========================================================================
# Skill synonym map  (normalise aliases to canonical names)
# ===========================================================================

_SKILL_SYNONYMS: dict[str, list[str]] = {
    "python":         ["py", "python3", "python2"],
    "javascript":     ["js", "ecmascript", "es6", "es2015", "node.js", "nodejs"],
    "typescript":     ["ts"],
    "react":          ["reactjs", "react.js", "react native"],
    "angular":        ["angularjs", "angular.js"],
    "vue":            ["vuejs", "vue.js"],
    "sql":            ["mysql", "postgresql", "postgres", "mssql", "sqlite", "oracle", "t-sql"],
    "nosql":          ["mongodb", "cassandra", "dynamodb", "couchdb", "redis"],
    "machine learning": ["ml", "deep learning", "dl", "ai", "artificial intelligence"],
    "tensorflow":     ["tf", "keras"],
    "pytorch":        ["torch"],
    "aws":            ["amazon web services", "ec2", "s3", "lambda", "sagemaker"],
    "gcp":            ["google cloud", "google cloud platform", "bigquery"],
    "azure":          ["microsoft azure", "azure devops"],
    "docker":         ["containerization", "containers"],
    "kubernetes":     ["k8s", "helm"],
    "ci/cd":          ["continuous integration", "continuous delivery", "jenkins",
                       "github actions", "gitlab ci", "circleci"],
    "rest api":       ["rest", "restful", "restful api", "api"],
    "graphql":        ["graph ql"],
    "java":           ["java8", "java11", "java17", "java21"],
    "spring":         ["spring boot", "spring mvc", "spring framework"],
    "c#":             ["csharp", "c sharp", ".net", "dotnet", "asp.net"],
    "git":            ["github", "gitlab", "bitbucket", "version control"],
    "linux":          ["unix", "ubuntu", "centos", "bash", "shell scripting"],
    "agile":          ["scrum", "kanban", "sprint"],
    "data analysis":  ["data analytics", "analytics"],
    "natural language processing": ["nlp", "text analytics"],
    "computer vision": ["cv", "image processing"],
    "llm":            ["large language model", "gpt", "gemini", "llama", "langchain"],
    "pandas":         ["dataframe"],
    "numpy":          ["np"],
    "tableau":        ["tableau desktop", "tableau server"],
    "power bi":       ["powerbi", "power-bi"],
    "spark":          ["apache spark", "pyspark"],
    "kafka":          ["apache kafka"],
    "selenium":       ["webdriver", "browser automation"],
    "flutter":        ["dart"],
    "kotlin":         [],
    "swift":          ["swiftui", "ios"],
    "android":        [],
    "devops":         ["devsecops", "site reliability", "sre"],
    "terraform":      ["iac", "infrastructure as code"],
}

# Build reverse lookup: alias -> canonical
_ALIAS_TO_CANONICAL: dict[str, str] = {}
for _canonical, _aliases in _SKILL_SYNONYMS.items():
    _ALIAS_TO_CANONICAL[_canonical] = _canonical
    for _alias in _aliases:
        _ALIAS_TO_CANONICAL[_alias] = _canonical


def _normalise_skill(skill: str) -> str:
    """Normalise a skill string to its canonical form."""
    s = skill.lower().strip()
    return _ALIAS_TO_CANONICAL.get(s, s)


# ===========================================================================
# Degree tier hierarchy
# ===========================================================================

_DEGREE_TIERS: dict[str, int] = {
    "phd":                   6,
    "doctorate":              6,
    "doctoral":               6,
    "master":                 5,
    "msc":                    5,
    "mba":                    5,
    "ms":                     5,
    "mtech":                  5,
    "me":                     5,
    "bachelor":               4,
    "bsc":                    4,
    "be":                     4,
    "btech":                  4,
    "ba":                     4,
    "beng":                   4,
    "undergraduate":          4,
    "associate":              3,
    "diploma":                2,
    "certification":          2,
    "certificate":            2,
    "bootcamp":               1,
    "self-taught":            1,
    "high school":            1,
}


def _degree_tier(text: str) -> int:
    """Return the highest degree tier found in a text string (0 if none)."""
    text_lower = text.lower()
    best = 0
    for keyword, tier in _DEGREE_TIERS.items():
        if keyword in text_lower:
            best = max(best, tier)
    return best


# ===========================================================================
# Experience year extraction
# ===========================================================================

_EXP_YEARS_RE = re.compile(
    r"(\d+)\+?\s*(?:to|-)\s*(\d+)\s*(?:years?|yrs?)|"  # "2-4 years"
    r"(\d+)\+\s*(?:years?|yrs?)|"                        # "5+ years"
    r"(\d+)\s*(?:years?|yrs?)\s*(?:of\s+)?(?:experience)?",  # "3 years"
    re.IGNORECASE,
)

_SENIORITY_MAP: dict[str, tuple[float, float]] = {
    # keyword → (min_years, ideal_years)
    "intern":       (0.0,  0.5),
    "internship":   (0.0,  0.5),
    "junior":       (0.0,  2.0),
    "entry":        (0.0,  2.0),
    "entry-level":  (0.0,  2.0),
    "associate":    (1.0,  3.0),
    "mid":          (2.0,  5.0),
    "mid-level":    (2.0,  5.0),
    "intermediate": (2.0,  5.0),
    "senior":       (5.0,  8.0),
    "sr.":          (5.0,  8.0),
    "lead":         (6.0, 10.0),
    "principal":    (8.0, 12.0),
    "staff":        (8.0, 12.0),
    "director":     (10.0, 15.0),
    "head":         (10.0, 15.0),
    "vp":           (12.0, 20.0),
}


def _extract_years_from_text(text: str) -> float:
    """Extract the maximum experience years requirement from a text block."""
    if not text:
        return 0.0
    max_years = 0.0
    for m in _EXP_YEARS_RE.finditer(text):
        g = m.groups()
        if g[0] and g[1]:          # "2-4 years" → take upper
            max_years = max(max_years, float(g[1]))
        elif g[2]:                  # "5+ years"
            max_years = max(max_years, float(g[2]))
        elif g[3]:                  # "3 years"
            max_years = max(max_years, float(g[3]))
    return max_years


# ===========================================================================
# TF-IDF cosine similarity  (lightweight — no sklearn needed)
# ===========================================================================

def _tokenise(text: str) -> list[str]:
    """Lower-case word tokeniser, removes stopwords and short tokens."""
    _STOP = {
        "a","an","the","and","or","but","in","on","at","to","for","of","with",
        "is","are","was","were","be","been","being","have","has","had","do",
        "does","did","will","would","could","should","may","might","this","that",
        "these","those","we","you","they","it","its","our","your","their","i",
        "as","by","from","into","not","no","so","if","then","also","when","where",
        "which","who","what","how","can","all","any","each","both","some","more",
        "other","about","than","through","during","before","after","above","below",
    }
    tokens = re.findall(r"\b[a-z][a-z0-9_\-\.]*\b", text.lower())
    return [t for t in tokens if t not in _STOP and len(t) > 2]


def _tfidf_cosine(text_a: str, text_b: str) -> float:
    """
    Compute TF-IDF weighted cosine similarity between two text strings.

    Returns a float in [0.0, 1.0]. Returns 0.0 if either string is empty.
    """
    if not text_a or not text_b:
        return 0.0

    tokens_a = _tokenise(text_a)
    tokens_b = _tokenise(text_b)

    if not tokens_a or not tokens_b:
        return 0.0

    # Vocabulary
    vocab = list(set(tokens_a) | set(tokens_b))

    # TF vectors (normalised term frequency)
    def _tf_vec(tokens: list[str]) -> dict[str, float]:
        counts = Counter(tokens)
        total  = len(tokens)
        return {w: counts[w] / total for w in vocab if w in counts}

    tf_a = _tf_vec(tokens_a)
    tf_b = _tf_vec(tokens_b)

    # IDF (treat the two docs as the corpus)
    idf: dict[str, float] = {}
    for w in vocab:
        df = int(w in tf_a) + int(w in tf_b)
        idf[w] = math.log((2 + 1) / (df + 1)) + 1.0   # smooth IDF

    # TF-IDF vectors
    def _tfidf(tf: dict[str, float]) -> dict[str, float]:
        return {w: tf[w] * idf[w] for w in tf}

    tfidf_a = _tfidf(tf_a)
    tfidf_b = _tfidf(tf_b)

    # Cosine similarity
    dot = sum(tfidf_a.get(w, 0) * tfidf_b.get(w, 0) for w in vocab)
    norm_a = math.sqrt(sum(v ** 2 for v in tfidf_a.values())) or 1e-9
    norm_b = math.sqrt(sum(v ** 2 for v in tfidf_b.values())) or 1e-9
    return min(round(dot / (norm_a * norm_b), 4), 1.0)


# ===========================================================================
# Database
# ===========================================================================

_MATCH_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS job_match_results (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id                 TEXT,
    job_id                  TEXT,
    overall_match           REAL,
    skills_match            REAL,
    experience_match        REAL,
    education_match         REAL,
    responsibilities_match  REAL,
    matching_skills         TEXT,   -- JSON array
    missing_skills          TEXT,   -- JSON array
    match_explanation       TEXT,
    grade                   TEXT,
    recommendation          TEXT,
    matched_at              TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_jmr_user    ON job_match_results(user_id);
CREATE INDEX IF NOT EXISTS idx_jmr_job     ON job_match_results(job_id);
CREATE INDEX IF NOT EXISTS idx_jmr_match   ON job_match_results(overall_match DESC);
"""


@contextmanager
def _db_conn() -> Generator[sqlite3.Connection, None, None]:
    """Yield an auto-committed SQLite connection."""
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH), timeout=15, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
        raise
    finally:
        conn.close()


def _ensure_match_table() -> None:
    """Create job_match_results table if it doesn't exist."""
    try:
        with _db_conn() as conn:
            conn.executescript(_MATCH_TABLE_DDL)
    except sqlite3.Error as exc:
        logger.error("_ensure_match_table: %s", exc)


# ===========================================================================
# 1.  extract_job_requirements()
# ===========================================================================

def extract_job_requirements(job_description: str | dict) -> dict:
    """
    Parse a job description (raw text OR dict) into structured requirements.

    Extracted fields:
        required_skills    list[str]  — normalised canonical skills
        preferred_skills   list[str]  — "nice to have" / "preferred" skills
        min_experience     float      — minimum years of experience required
        ideal_experience   float      — ideal / preferred years
        required_degree    int        — minimum degree tier (1–6, 0 = unspecified)
        seniority_level    str        — inferred seniority keyword
        responsibilities   str        — raw responsibilities text block
        job_title          str        — inferred or provided title
        raw_text           str        — cleaned full text used for parsing

    Args:
        job_description: Either a plain-text JD string or a dict with keys
                         such as "full_description", "required_skills", etc.
                         (compatible with job_scraper.py output format).

    Returns:
        Structured requirements dict.
    """
    # ── Normalise input ────────────────────────────────────────────────────
    if isinstance(job_description, dict):
        jd_dict = job_description
        raw_text = str(jd_dict.get("full_description", ""))
        # Pre-extracted skills from scraper → trust them
        pre_skills = [
            _normalise_skill(s)
            for s in jd_dict.get("required_skills", [])
            if isinstance(s, str) and s.strip()
        ]
        job_title = jd_dict.get("job_title", "")
        salary    = jd_dict.get("salary", "")
    else:
        raw_text   = str(job_description or "")
        pre_skills = []
        job_title  = ""
        salary     = ""

    text_lower = raw_text.lower()

    # ── Required skills (lexicon scan on full text) ────────────────────────
    all_known = list(_ALIAS_TO_CANONICAL.keys())
    found_skills: set[str] = set(pre_skills)
    for token in all_known:
        if re.search(r"\b" + re.escape(token) + r"\b", text_lower):
            found_skills.add(_ALIAS_TO_CANONICAL[token])

    # ── Preferred vs required split ────────────────────────────────────────
    required_skills:  list[str] = []
    preferred_skills: list[str] = []

    pref_section_re = re.compile(
        r"(?:preferred|nice.to.have|bonus|plus|optional|advantageous)",
        re.IGNORECASE,
    )
    req_section_re = re.compile(
        r"(?:required|must.have|essential|mandatory|need)",
        re.IGNORECASE,
    )

    # Simple heuristic: skills in "preferred" section → preferred_skills
    lines = raw_text.splitlines()
    in_preferred = False
    preferred_lines: set[str] = set()
    for line in lines:
        if pref_section_re.search(line) and len(line.strip()) < 80:
            in_preferred = True
        elif req_section_re.search(line) and len(line.strip()) < 80:
            in_preferred = False
        if in_preferred:
            preferred_lines.add(line.lower())

    for skill in sorted(found_skills):
        skill_in_pref = any(
            re.search(r"\b" + re.escape(skill) + r"\b", ln)
            for ln in preferred_lines
        )
        if skill_in_pref:
            preferred_skills.append(skill)
        else:
            required_skills.append(skill)

    # ── Experience ────────────────────────────────────────────────────────
    min_exp   = _extract_years_from_text(raw_text)
    seniority = ""
    ideal_exp = min_exp

    for key, (min_y, ideal_y) in _SENIORITY_MAP.items():
        if key in text_lower:
            seniority = key
            min_exp   = min_exp or min_y
            ideal_exp = ideal_y
            break

    if not seniority and job_title:
        for key, (min_y, ideal_y) in _SENIORITY_MAP.items():
            if key in job_title.lower():
                seniority = key
                min_exp   = min_exp or min_y
                ideal_exp = ideal_y
                break

    # ── Education ─────────────────────────────────────────────────────────
    req_degree = _degree_tier(raw_text)

    # ── Responsibilities text block ────────────────────────────────────────
    resp_text = ""
    resp_re   = re.compile(
        r"(?:responsibilities?|duties|you.?ll?\s+(?:be\s+)?(?:responsible|work|build|own))",
        re.IGNORECASE,
    )
    resp_lines: list[str] = []
    in_resp = False
    for line in lines:
        if resp_re.search(line) and len(line.strip()) < 80:
            in_resp = True
            continue
        if in_resp:
            if (line.strip()
                    and len(line.strip()) < 60
                    and line.strip()[0].isupper()
                    and not line.strip().startswith(("-", "•", "*"))):
                break
            clean = re.sub(r"^[\s\u2022\u25cf\-\*\u2013]+", "", line).strip()
            if clean:
                resp_lines.append(clean)
        if len(resp_lines) >= 15:
            break
    resp_text = " ".join(resp_lines) or raw_text[:600]

    reqs = {
        "required_skills":   required_skills,
        "preferred_skills":  preferred_skills,
        "all_skills":        list(found_skills),
        "min_experience":    round(min_exp, 1),
        "ideal_experience":  round(ideal_exp, 1),
        "required_degree":   req_degree,
        "seniority_level":   seniority,
        "responsibilities":  resp_text,
        "job_title":         job_title,
        "salary":            salary,
        "raw_text":          raw_text,
    }

    logger.debug(
        "extract_job_requirements: %d required_skills, %d preferred, "
        "min_exp=%.1f, degree_tier=%d, seniority='%s'",
        len(required_skills), len(preferred_skills),
        min_exp, req_degree, seniority,
    )
    return reqs


# ===========================================================================
# 2.  calculate_skill_match()
# ===========================================================================

def calculate_skill_match(
    resume_skills: list[str],
    job_requirements: dict,
) -> dict:
    """
    Calculate the skills component score (0–100) and lists of
    matching / missing skills.

    Algorithm:
        1. Normalise both skill sets to canonical names
        2. Check exact canonical matches (base score)
        3. Apply a partial-credit bonus for preferred (nice-to-have) skills
        4. Deduct a small penalty for each missing required skill
           (softened so 1 missing skill ≠ 0 score)

    Weights within this function:
        required skills  → 80% of the score
        preferred skills → 20% of the score

    Args:
        resume_skills:    List of skill strings extracted from the resume.
        job_requirements: Output dict from extract_job_requirements().

    Returns:
        dict: {score, matching_skills, missing_skills, preferred_matched}
    """
    if not resume_skills:
        return {
            "score": 0,
            "matching_skills": [],
            "missing_skills": job_requirements.get("required_skills", []),
            "preferred_matched": [],
        }

    # Normalise
    resume_set  = {_normalise_skill(s) for s in resume_skills if s}
    required    = set(job_requirements.get("required_skills", []))
    preferred   = set(job_requirements.get("preferred_skills", []))
    all_job_sk  = required | preferred

    if not all_job_sk:
        return {
            "score": 50,   # no info → neutral
            "matching_skills": list(resume_set)[:10],
            "missing_skills": [],
            "preferred_matched": [],
        }

    # Matches
    req_matched  = resume_set & required
    pref_matched = resume_set & preferred
    req_missing  = required - resume_set

    # Scores
    req_score  = (len(req_matched)  / len(required))   * 100 if required   else 100.0
    pref_score = (len(pref_matched) / len(preferred))  * 100 if preferred  else 0.0

    # Weighted blend
    if preferred:
        raw_score = req_score * 0.80 + pref_score * 0.20
    else:
        raw_score = req_score

    # Soft penalty: having > 3 missing required skills caps at 85
    if len(req_missing) > 3:
        penalty  = min((len(req_missing) - 3) * 3, 15)
        raw_score = max(0.0, raw_score - penalty)

    score = min(round(raw_score), 100)

    # Human-readable skill lists (de-normalised back to title case)
    def _display(skills: set[str]) -> list[str]:
        return sorted(s.title() for s in skills)

    logger.debug(
        "calculate_skill_match: score=%d req_matched=%d/%d pref_matched=%d/%d",
        score, len(req_matched), len(required), len(pref_matched), len(preferred),
    )
    return {
        "score":             score,
        "matching_skills":   _display(req_matched | pref_matched),
        "missing_skills":    _display(req_missing),
        "preferred_matched": _display(pref_matched),
    }


# ===========================================================================
# 3.  calculate_experience_match()
# ===========================================================================

def calculate_experience_match(
    resume_data: dict,
    job_requirements: dict,
) -> dict:
    """
    Calculate the experience component score (0–100).

    Factors considered:
        1. Total years of experience (extracted from resume)
        2. Required minimum years (from JD)
        3. Seniority keyword alignment (e.g. "senior" in both)
        4. Relevant domain experience bonus

    Args:
        resume_data:      Dict with keys like "experience", "total_experience",
                          "years_of_experience", "extracted_text", "job_title".
        job_requirements: Output dict from extract_job_requirements().

    Returns:
        dict: {score, resume_years, required_years, gap_years, reasoning}
    """
    # ── Extract resume experience years ───────────────────────────────────
    resume_years = 0.0

    # Direct field (some parsers provide this)
    for field in ("years_of_experience", "total_experience", "experience_years"):
        val = resume_data.get(field)
        if isinstance(val, (int, float)) and val > 0:
            resume_years = float(val)
            break
        if isinstance(val, str):
            y = _extract_years_from_text(val)
            if y > 0:
                resume_years = y
                break

    # Fall back to scanning extracted text / experience section
    if resume_years == 0.0:
        exp_text = ""
        exp_raw  = resume_data.get("experience", "")
        if isinstance(exp_raw, list):
            exp_text = " ".join(str(e) for e in exp_raw)
        elif isinstance(exp_raw, str):
            exp_text = exp_raw
        exp_text += " " + resume_data.get("extracted_text", "")[:2000]
        resume_years = _extract_years_from_text(exp_text)

    required_years = job_requirements.get("min_experience", 0.0)
    ideal_years    = job_requirements.get("ideal_experience", required_years)
    seniority      = job_requirements.get("seniority_level", "")

    # ── Score model ────────────────────────────────────────────────────────
    if required_years == 0.0 and not seniority:
        # No experience requirement stated → neutral
        score     = 70
        reasoning = "No specific experience requirement stated."
    elif resume_years >= ideal_years:
        # At or above ideal → excellent
        score     = 100
        reasoning = (
            f"You have {resume_years:.0f} years — meets/exceeds the ideal "
            f"{ideal_years:.0f} years for this role."
        )
    elif resume_years >= required_years:
        # Between minimum and ideal → good, scale 75-95
        ratio  = (resume_years - required_years) / max(ideal_years - required_years, 0.5)
        score  = int(75 + ratio * 20)
        reasoning = (
            f"You have {resume_years:.0f} years — meets the minimum "
            f"{required_years:.0f} years, working toward ideal {ideal_years:.0f} years."
        )
    elif resume_years > 0:
        # Below minimum — scale 0-75 based on how close
        gap    = required_years - resume_years
        ratio  = max(0.0, 1.0 - gap / max(required_years, 1))
        score  = int(ratio * 75)
        reasoning = (
            f"You have {resume_years:.0f} years — {gap:.0f} years below "
            f"the minimum {required_years:.0f} years required."
        )
    else:
        # Unknown experience
        score     = 40
        reasoning = (
            f"Experience level unclear. Role requires {required_years:.0f}+ years."
        )

    # ── Seniority alignment bonus/penalty (±10) ───────────────────────────
    resume_text_lower = (
        resume_data.get("extracted_text", "").lower()
        + " "
        + str(resume_data.get("job_title", "")).lower()
    )
    if seniority and seniority in resume_text_lower:
        score     = min(100, score + 8)
        reasoning += f" Seniority keyword '{seniority}' found in resume."

    score     = min(max(score, 0), 100)
    gap_years = max(0.0, required_years - resume_years)

    logger.debug(
        "calculate_experience_match: score=%d resume=%.1fy required=%.1fy",
        score, resume_years, required_years,
    )
    return {
        "score":          score,
        "resume_years":   round(resume_years, 1),
        "required_years": round(required_years, 1),
        "gap_years":      round(gap_years, 1),
        "reasoning":      reasoning,
    }


# ===========================================================================
# 4.  calculate_education_match()
# ===========================================================================

def calculate_education_match(
    resume_data: dict,
    job_requirements: dict,
) -> dict:
    """
    Calculate the education component score (0–100).

    Hierarchy (tier values):
        6 = PhD / Doctorate
        5 = Master's (MSc, MBA, MTech)
        4 = Bachelor's (BSc, BE, BTech, BA)
        3 = Associate's Degree
        2 = Diploma / Certification
        1 = Bootcamp / Self-taught / High School
        0 = Not specified

    Scoring:
        resume_tier >= required_tier → 100 (full match)
        resume_tier == required_tier - 1 → 75 (one below)
        resume_tier == required_tier - 2 → 50 (two below)
        lower → proportional
        required_tier == 0 → 80 (neutral, no requirement stated)

    Args:
        resume_data:      Dict with keys "education", "extracted_text".
        job_requirements: Output dict from extract_job_requirements().

    Returns:
        dict: {score, resume_tier, required_tier, reasoning}
    """
    # ── Extract resume education ──────────────────────────────────────────
    edu_text = ""
    edu_raw  = resume_data.get("education", "")
    if isinstance(edu_raw, list):
        edu_text = " ".join(str(e) for e in edu_raw)
    elif isinstance(edu_raw, str):
        edu_text = edu_raw

    # Also scan extracted text for degree keywords
    edu_text += " " + resume_data.get("extracted_text", "")[:3000]

    resume_tier   = _degree_tier(edu_text)
    required_tier = job_requirements.get("required_degree", 0)

    # ── Scoring ───────────────────────────────────────────────────────────
    if required_tier == 0:
        score     = 80
        reasoning = "No specific education requirement mentioned in the JD."
    elif resume_tier >= required_tier:
        score     = 100
        tier_name = [k for k, v in _DEGREE_TIERS.items() if v == required_tier]
        reasoning = (
            f"Your education meets/exceeds the required level "
            f"({tier_name[0].title() if tier_name else 'required'})."
        )
    else:
        gap   = required_tier - resume_tier
        score = max(0, 100 - gap * 25)
        reasoning = (
            f"Education is {gap} tier(s) below the required level. "
            f"Consider supplementary certifications."
        )

    logger.debug(
        "calculate_education_match: score=%d resume_tier=%d required_tier=%d",
        score, resume_tier, required_tier,
    )
    return {
        "score":          score,
        "resume_tier":    resume_tier,
        "required_tier":  required_tier,
        "reasoning":      reasoning,
    }


# ===========================================================================
# 5.  calculate_overall_score()
# ===========================================================================

def calculate_overall_score(
    skill_score:   float,
    exp_score:     float,
    edu_score:     float,
    resp_score:    float,
) -> dict:
    """
    Compute the weighted composite overall match score.

    Weights:
        Skills           50%
        Experience       25%
        Education        15%
        Responsibilities 10%

    Args:
        skill_score:  0–100 skills component score.
        exp_score:    0–100 experience component score.
        edu_score:    0–100 education component score.
        resp_score:   0–100 responsibilities component score.

    Returns:
        dict: {
            overall_score, grade, recommendation,
            component_weights, component_scores
        }
    """
    raw = (
        skill_score  * WEIGHT_SKILLS
        + exp_score  * WEIGHT_EXPERIENCE
        + edu_score  * WEIGHT_EDUCATION
        + resp_score * WEIGHT_RESPONSIBILITIES
    )
    overall = min(round(raw), 100)

    # Determine grade
    grade = "Poor"
    recommendation = "Low match — significant upskilling recommended."
    for threshold, g, rec in _GRADES:
        if overall >= threshold:
            grade          = g
            recommendation = rec
            break

    logger.debug(
        "calculate_overall_score: overall=%d grade=%s "
        "(skills=%.0f exp=%.0f edu=%.0f resp=%.0f)",
        overall, grade, skill_score, exp_score, edu_score, resp_score,
    )
    return {
        "overall_score":  overall,
        "grade":          grade,
        "recommendation": recommendation,
        "component_weights": {
            "skills":          WEIGHT_SKILLS,
            "experience":      WEIGHT_EXPERIENCE,
            "education":       WEIGHT_EDUCATION,
            "responsibilities": WEIGHT_RESPONSIBILITIES,
        },
        "component_scores": {
            "skills":          round(skill_score),
            "experience":      round(exp_score),
            "education":       round(edu_score),
            "responsibilities": round(resp_score),
        },
    }


# ===========================================================================
# 6.  generate_match_explanation()
# ===========================================================================

def generate_match_explanation(result: dict) -> str:
    """
    Generate a professional, human-readable match explanation paragraph.

    The explanation covers:
        - Overall grade and score
        - Skill match highlights (what's good, what's missing)
        - Experience alignment
        - Education notes
        - Personalised call-to-action

    Args:
        result: The full match result dict (output of match_resume_to_job()).

    Returns:
        Multi-sentence explanation string (plain text, no markdown).
    """
    overall   = result.get("overall_match", 0)
    grade     = result.get("grade", "")
    skills_s  = result.get("skills_match", 0)
    exp_s     = result.get("experience_match", 0)
    edu_s     = result.get("education_match", 0)
    resp_s    = result.get("responsibilities_match", 0)
    matched   = result.get("matching_skills", [])
    missing   = result.get("missing_skills", [])
    exp_gap   = result.get("experience_gap_years", 0)
    job_title = result.get("job_title", "this role")

    parts: list[str] = []

    # ── Overall headline ──────────────────────────────────────────────────
    parts.append(
        f"Your resume is a {grade.lower()} match for {job_title} "
        f"with an overall score of {overall}%."
    )

    # ── Skills ────────────────────────────────────────────────────────────
    if matched:
        top_match = ", ".join(matched[:5])
        parts.append(
            f"Strong skill alignment on: {top_match}"
            + (f" and {len(matched) - 5} more" if len(matched) > 5 else "") + "."
        )
    else:
        parts.append("No direct skill matches were detected.")

    if missing:
        top_miss = ", ".join(missing[:4])
        parts.append(
            f"Key missing skills: {top_miss}"
            + (f" and {len(missing) - 4} others" if len(missing) > 4 else "") + "."
        )
    else:
        parts.append("Your skill set covers all required competencies.")

    # ── Experience ────────────────────────────────────────────────────────
    if exp_s >= 90:
        parts.append("Your experience level is an excellent fit for this role.")
    elif exp_s >= 70:
        parts.append("You meet the experience requirements for this position.")
    elif exp_gap > 0:
        parts.append(
            f"You are approximately {exp_gap:.0f} year(s) short of the "
            f"required experience — consider highlighting transferable skills."
        )
    else:
        parts.append(
            "Your experience level could not be fully assessed from the resume."
        )

    # ── Education ─────────────────────────────────────────────────────────
    if edu_s >= 100:
        parts.append("Your educational background fully satisfies the requirements.")
    elif edu_s >= 75:
        parts.append(
            "Your education is close to the requirements; "
            "relevant certifications can bridge any gap."
        )
    elif edu_s < 50:
        parts.append(
            "Your education is below the stated requirement — "
            "certifications or equivalent experience may compensate."
        )

    # ── Responsibilities ──────────────────────────────────────────────────
    if resp_s >= 80:
        parts.append(
            "Your background aligns well with the job responsibilities."
        )
    elif resp_s < 50:
        parts.append(
            "Your background shows limited overlap with the listed responsibilities — "
            "consider tailoring your resume summary to this role."
        )

    # ── CTA ──────────────────────────────────────────────────────────────
    if overall >= 75:
        parts.append(
            "We recommend applying — customise your cover letter to highlight "
            "your strongest matching skills."
        )
    elif overall >= 55:
        parts.append(
            "Upskill in the missing areas and tailor your resume before applying."
        )
    else:
        parts.append(
            "Focus on building the missing skills through online courses or "
            "side projects before applying to this role."
        )

    explanation = " ".join(parts)
    logger.debug("generate_match_explanation: %d chars", len(explanation))
    return explanation


# ===========================================================================
# 7.  save_match_results()
# ===========================================================================

def save_match_results(
    user_id: str,
    job_id:  str,
    result:  dict,
) -> dict:
    """
    Persist a match result to the job_match_results SQLite table.

    Args:
        user_id: Authenticated user UUID.
        job_id:  Job identifier string (URL, DB id, or title hash).
        result:  Full match result dict from match_resume_to_job().

    Returns:
        dict: {success, row_id, message}
    """
    _ensure_match_table()
    try:
        with _db_conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO job_match_results (
                    user_id, job_id, overall_match, skills_match,
                    experience_match, education_match, responsibilities_match,
                    matching_skills, missing_skills, match_explanation,
                    grade, recommendation
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    user_id,
                    str(job_id),
                    float(result.get("overall_match", 0)),
                    float(result.get("skills_match", 0)),
                    float(result.get("experience_match", 0)),
                    float(result.get("education_match", 0)),
                    float(result.get("responsibilities_match", 0)),
                    json.dumps(result.get("matching_skills", [])),
                    json.dumps(result.get("missing_skills", [])),
                    result.get("match_explanation", ""),
                    result.get("grade", ""),
                    result.get("recommendation", ""),
                ),
            )
            row_id = cur.lastrowid

        logger.info(
            "save_match_results: saved row_id=%d overall=%d%% grade=%s (user=%s job=%s)",
            row_id,
            result.get("overall_match", 0),
            result.get("grade", ""),
            str(user_id)[:8],
            str(job_id)[:20],
        )
        return {"success": True, "row_id": row_id, "message": "Match result saved."}

    except sqlite3.Error as exc:
        logger.error("save_match_results: DB error — %s", exc)
        return {"success": False, "row_id": None, "message": str(exc)}


def get_match_results(user_id: str, limit: int = 20) -> list[dict]:
    """
    Retrieve saved match results for a user, sorted by overall_match.

    Args:
        user_id: Authenticated user UUID.
        limit:   Max number of results to return.

    Returns:
        List of match result dicts.
    """
    _ensure_match_table()
    try:
        with _db_conn() as conn:
            rows = conn.execute(
                """
                SELECT * FROM job_match_results
                WHERE user_id = ?
                ORDER BY overall_match DESC, matched_at DESC
                LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()
        results = []
        for row in rows:
            d = dict(row)
            for key in ("matching_skills", "missing_skills"):
                try:
                    d[key] = json.loads(d.get(key) or "[]")
                except (json.JSONDecodeError, TypeError):
                    d[key] = []
            results.append(d)
        return results
    except sqlite3.Error as exc:
        logger.error("get_match_results: %s", exc)
        return []


# ===========================================================================
# 8.  match_resume_to_job()  — main orchestrator
# ===========================================================================

def match_resume_to_job(
    resume_data:     dict,
    job_description: str | dict,
    job_id:          str  = "",
    user_id:         str  = "",
    save:            bool = True,
) -> dict:
    """
    Full pipeline: extract requirements → score all dimensions → explain.

    This is the primary entry point. Calls all other functions in sequence
    and assembles the final output dict.

    Args:
        resume_data:      Dict from resume parser. Expected keys:
                            "identified_skills" / "skills" — list[str]
                            "experience"                   — str or list
                            "education"                    — str or list
                            "extracted_text"               — str (full resume text)
                            "job_title"                    — str (current/target)
                            "years_of_experience"          — float (optional)
        job_description:  Raw JD text or job dict from job_scraper.search_jobs().
        job_id:           Optional identifier for the job (stored in DB).
        user_id:          Optional user UUID for persistence.
        save:             If True, persist result to SQLite.

    Returns:
        {
            "job_id":                  str,
            "job_title":               str,
            "overall_match":           int,
            "skills_match":            int,
            "experience_match":        int,
            "education_match":         int,
            "responsibilities_match":  int,
            "matching_skills":         list[str],
            "missing_skills":          list[str],
            "preferred_matched":       list[str],
            "match_explanation":       str,
            "grade":                   str,
            "recommendation":          str,
            "experience_gap_years":    float,
            "component_weights":       dict,
            "component_scores":        dict,
            "matched_at":              str  (ISO-8601 UTC),
        }
    """
    logger.info(
        "match_resume_to_job: job_id='%s' user='%s'",
        str(job_id)[:30], str(user_id)[:8],
    )

    # ── Step 1: Extract job requirements ──────────────────────────────────
    reqs = extract_job_requirements(job_description)

    # ── Step 2: Extract resume skills ─────────────────────────────────────
    resume_skills: list[str] = []
    for field in ("identified_skills", "skills", "extracted_skills", "skill_set"):
        raw = resume_data.get(field, [])
        if isinstance(raw, list) and raw:
            resume_skills = [str(s) for s in raw if s]
            break
        if isinstance(raw, str) and raw:
            resume_skills = [s.strip() for s in raw.split(",") if s.strip()]
            break

    # Also scan extracted text for unlisted skills
    ext_text = resume_data.get("extracted_text", "")
    if ext_text:
        ext_lower = ext_text.lower()
        for token in list(_ALIAS_TO_CANONICAL.keys()):
            if (re.search(r"\b" + re.escape(token) + r"\b", ext_lower)
                    and token not in [s.lower() for s in resume_skills]):
                resume_skills.append(_ALIAS_TO_CANONICAL[token])

    # ── Step 3: Skills score ──────────────────────────────────────────────
    skill_result = calculate_skill_match(resume_skills, reqs)

    # ── Step 4: Experience score ──────────────────────────────────────────
    exp_result = calculate_experience_match(resume_data, reqs)

    # ── Step 5: Education score ───────────────────────────────────────────
    edu_result = calculate_education_match(resume_data, reqs)

    # ── Step 6: Responsibilities score (TF-IDF cosine) ────────────────────
    resume_summary = (
        resume_data.get("extracted_text", "")[:1500]
        + " "
        + " ".join(str(e) for e in (resume_data.get("experience", []) or []))[:500]
    )
    resp_similarity = _tfidf_cosine(resume_summary, reqs["responsibilities"])
    resp_score      = min(round(resp_similarity * 100), 100)

    # ── Step 7: Overall weighted score ───────────────────────────────────
    overall_result = calculate_overall_score(
        skill_result["score"],
        exp_result["score"],
        edu_result["score"],
        resp_score,
    )

    # ── Step 8: Assemble result dict ──────────────────────────────────────
    job_title = reqs.get("job_title") or (
        job_description.get("job_title", "") if isinstance(job_description, dict) else ""
    )

    result: dict[str, Any] = {
        "job_id":                  str(job_id),
        "job_title":               job_title,
        "overall_match":           overall_result["overall_score"],
        "skills_match":            skill_result["score"],
        "experience_match":        exp_result["score"],
        "education_match":         edu_result["score"],
        "responsibilities_match":  resp_score,
        "matching_skills":         skill_result["matching_skills"],
        "missing_skills":          skill_result["missing_skills"],
        "preferred_matched":       skill_result["preferred_matched"],
        "grade":                   overall_result["grade"],
        "recommendation":          overall_result["recommendation"],
        "experience_gap_years":    exp_result["gap_years"],
        "experience_reasoning":    exp_result["reasoning"],
        "education_reasoning":     edu_result["reasoning"],
        "component_weights":       overall_result["component_weights"],
        "component_scores":        overall_result["component_scores"],
        "matched_at":              datetime.now(timezone.utc).isoformat(),
    }

    # ── Step 9: Generate explanation ──────────────────────────────────────
    result["match_explanation"] = generate_match_explanation(result)

    # ── Step 10: Persist ──────────────────────────────────────────────────
    if save and user_id:
        save_match_results(user_id, job_id, result)

    logger.info(
        "match_resume_to_job: DONE overall=%d%% grade=%s "
        "skills=%d%% exp=%d%% edu=%d%% resp=%d%%",
        result["overall_match"], result["grade"],
        result["skills_match"], result["experience_match"],
        result["education_match"], result["responsibilities_match"],
    )
    return result


# ===========================================================================
# Convenience: batch matching
# ===========================================================================

def match_resume_to_jobs(
    resume_data: dict,
    jobs:        list[dict],
    user_id:     str = "",
    save:        bool = True,
) -> list[dict]:
    """
    Match a resume against a list of job dicts and return sorted results.

    Args:
        resume_data: Parsed resume dict.
        jobs:        List of job dicts (from job_scraper.search_jobs()).
        user_id:     Optional user UUID for persistence.
        save:        Persist each match result to SQLite.

    Returns:
        List of match result dicts, sorted by overall_match descending.
    """
    results = []
    for job in jobs:
        jid  = str(job.get("job_id") or job.get("id") or job.get("job_url", ""))[:60]
        res  = match_resume_to_job(
            resume_data=resume_data,
            job_description=job,
            job_id=jid,
            user_id=user_id,
            save=save,
        )
        results.append(res)

    results.sort(key=lambda r: r["overall_match"], reverse=True)
    logger.info(
        "match_resume_to_jobs: %d jobs scored — top=%.0f%% bottom=%.0f%%",
        len(results),
        results[0]["overall_match"]  if results else 0,
        results[-1]["overall_match"] if results else 0,
    )
    return results


# ===========================================================================
# Self-test — run with: python -m backend.job_matcher
# ===========================================================================

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")

    print()
    print("=" * 65)
    print("  backend/job_matcher.py — Self-Test Suite")
    print("=" * 65)

    # ── Sample resume ─────────────────────────────────────────────────────
    SAMPLE_RESUME = {
        "identified_skills": [
            "Python", "FastAPI", "PostgreSQL", "Docker", "Redis",
            "Kubernetes", "AWS", "Git", "REST API", "SQL",
            "Machine Learning", "pandas", "scikit-learn",
        ],
        "experience": [
            "Software Engineer at TechCorp (2021-2024): Built REST APIs with FastAPI and Python",
            "Junior Developer at StartupXYZ (2019-2021): Python scripts, SQL queries",
        ],
        "education": "Bachelor of Engineering in Computer Science, 2019",
        "extracted_text": (
            "Experienced Python developer with 5 years in backend development. "
            "Built scalable REST APIs using FastAPI and Django. "
            "Proficient in PostgreSQL, Redis, Docker, Kubernetes, AWS. "
            "Strong background in machine learning with scikit-learn and pandas. "
            "Bachelor of Engineering in Computer Science."
        ),
        "years_of_experience": 5.0,
        "job_title": "Senior Python Developer",
    }

    # ── Sample job description ────────────────────────────────────────────
    SAMPLE_JD = """
    Senior Python Backend Engineer

    We are looking for a Senior Python Engineer to join our platform team.

    Responsibilities:
    - Design and build scalable REST APIs using FastAPI
    - Own microservices architecture on AWS
    - Collaborate with ML team to integrate ML models
    - Write clean, well-tested Python code

    Required Skills:
    Python, FastAPI, PostgreSQL, Docker, Kubernetes, AWS, REST API, Redis

    Nice to Have:
    GraphQL, Terraform, Spark

    Requirements:
    - 5+ years of Python experience
    - Strong knowledge of SQL databases
    - Experience with containerization (Docker, Kubernetes)
    - Bachelor's degree in Computer Science or related field

    Salary: $130,000 - $170,000 per year
    150 applicants.
    """

    # ── Test 1: extract_job_requirements ──────────────────────────────────
    print("\n[1/6] extract_job_requirements()")
    reqs = extract_job_requirements(SAMPLE_JD)
    assert isinstance(reqs["required_skills"], list)
    assert reqs["min_experience"] >= 5.0, f"Expected >=5, got {reqs['min_experience']}"
    assert reqs["required_degree"] >= 4,  f"Expected >=4 (bachelor), got {reqs['required_degree']}"
    print(f"  [OK] Required skills   : {reqs['required_skills'][:6]}")
    print(f"  [OK] Min experience    : {reqs['min_experience']} years")
    print(f"  [OK] Seniority         : '{reqs['seniority_level']}'")
    print(f"  [OK] Required degree   : tier {reqs['required_degree']}")
    print(f"  [OK] Preferred skills  : {reqs['preferred_skills'][:4]}")

    # ── Test 2: calculate_skill_match ─────────────────────────────────────
    print("\n[2/6] calculate_skill_match()")
    skill_res = calculate_skill_match(SAMPLE_RESUME["identified_skills"], reqs)
    assert skill_res["score"] >= 70, f"Expected >=70, got {skill_res['score']}"
    print(f"  [OK] Skill score       : {skill_res['score']}%")
    print(f"  [OK] Matching skills   : {skill_res['matching_skills'][:5]}")
    print(f"  [OK] Missing skills    : {skill_res['missing_skills'][:4]}")

    # ── Test 3: calculate_experience_match ────────────────────────────────
    print("\n[3/6] calculate_experience_match()")
    exp_res = calculate_experience_match(SAMPLE_RESUME, reqs)
    assert exp_res["score"] >= 70, f"Expected >=70, got {exp_res['score']}"
    print(f"  [OK] Exp score         : {exp_res['score']}%")
    print(f"  [OK] Resume years      : {exp_res['resume_years']}")
    print(f"  [OK] Required years    : {exp_res['required_years']}")
    print(f"  [OK] Reasoning         : {exp_res['reasoning'][:70]}")

    # ── Test 4: calculate_education_match ─────────────────────────────────
    print("\n[4/6] calculate_education_match()")
    edu_res = calculate_education_match(SAMPLE_RESUME, reqs)
    assert edu_res["score"] >= 75, f"Expected >=75, got {edu_res['score']}"
    print(f"  [OK] Edu score         : {edu_res['score']}%")
    print(f"  [OK] Resume tier       : {edu_res['resume_tier']} (Bachelor=4)")
    print(f"  [OK] Required tier     : {edu_res['required_tier']}")

    # ── Test 5: calculate_overall_score ───────────────────────────────────
    print("\n[5/6] calculate_overall_score()")
    overall = calculate_overall_score(
        skill_res["score"], exp_res["score"], edu_res["score"], 75
    )
    assert overall["overall_score"] >= 60
    print(f"  [OK] Overall score     : {overall['overall_score']}%")
    print(f"  [OK] Grade             : {overall['grade']}")
    print(f"  [OK] Recommendation    : {overall['recommendation'][:60]}")

    # ── Test 6: Full pipeline — match_resume_to_job ───────────────────────
    print("\n[6/6] match_resume_to_job() — full pipeline")
    result = match_resume_to_job(
        resume_data=SAMPLE_RESUME,
        job_description=SAMPLE_JD,
        job_id="test_job_001",
        user_id="",    # skip DB save for test
        save=False,
    )

    assert result["overall_match"] >= 60
    assert isinstance(result["matching_skills"], list)
    assert isinstance(result["missing_skills"], list)
    assert len(result["match_explanation"]) > 50

    print(f"  [OK] Overall match     : {result['overall_match']}%")
    print(f"  [OK] Skills match      : {result['skills_match']}%")
    print(f"  [OK] Experience match  : {result['experience_match']}%")
    print(f"  [OK] Education match   : {result['education_match']}%")
    print(f"  [OK] Resp match        : {result['responsibilities_match']}%")
    print(f"  [OK] Grade             : {result['grade']}")
    print(f"  [OK] Matching skills   : {result['matching_skills'][:4]}")
    print(f"  [OK] Missing skills    : {result['missing_skills'][:3]}")
    print(f"  [OK] Explanation       : {result['match_explanation'][:100]}...")

    print()
    print("=" * 65)
    print("  ALL SELF-TESTS PASSED")
    print("=" * 65)
    print()
    print("  Full output JSON:")
    print(json.dumps({
        "job_id":                 result["job_id"],
        "overall_match":          result["overall_match"],
        "skills_match":           result["skills_match"],
        "experience_match":       result["experience_match"],
        "education_match":        result["education_match"],
        "responsibilities_match": result["responsibilities_match"],
        "matching_skills":        result["matching_skills"],
        "missing_skills":         result["missing_skills"],
        "match_explanation":      result["match_explanation"],
    }, indent=2))
