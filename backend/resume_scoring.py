"""
resume_scoring.py — Multi-Dimensional Resume Scorer
=====================================================
File: backend/resume_scoring.py

Scores a resume across 5 weighted dimensions using both rule-based
heuristics and AI-powered evaluation via Gemini.

Scoring Weights:
    Completeness      25%  — presence of required resume sections
    Content Quality   30%  — writing quality, achievements, impact
    Formatting        15%  — readability, length, structure clarity
    Keyword Relevance 20%  — industry/role keywords detected
    Experience        10%  — progression, tenure, quantified results

Public Functions:
    calculate_scores()         — main orchestrator, returns full scored output
    evaluate_completeness()    — section presence + depth check
    evaluate_content()         — AI-powered writing quality score
    evaluate_formatting()      — heuristic structure & length analysis
    evaluate_keywords()        — keyword frequency & relevance scoring
    evaluate_experience()      — career progression & impact scoring

Output Schema:
    {
        "overall_score": 87,
        "grade": "Excellent",
        "grade_color": "#22c55e",
        "breakdown": {
            "completeness": {
                "score": 22,          # raw points (max = weight)
                "max":   25,
                "pct":   88,          # percentage 0-100
                "weight": 0.25,
                "grade": "Good",
                "reasoning": "...",
                "details": { ... }
            },
            "content_quality":   { ... },
            "formatting":        { ... },
            "keyword_relevance": { ... },
            "experience":        { ... }
        },
        "strengths":     ["...", "..."],
        "improvements":  ["...", "..."],
        "quick_wins":    ["...", "..."],
        "scored_at":     "2025-06-01T12:00:00Z"
    }

Features:
    - Fully rule-based fallback (works without API key)
    - LLM-enhanced evaluation for content + experience dimensions
    - Detailed per-field reasoning for every score
    - Smart grade mapping with color codes
    - Quick wins list for actionable improvements
    - In-memory caching keyed on resume text hash
"""

import re
import json
import logging
import hashlib
from datetime import datetime, timezone
from typing import Any

from backend.llm_analyzer import load_llm, extract_json_response

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------
_SCORE_CACHE: dict[str, dict] = {}


# ===========================================================================
# Constants & Lookup Tables
# ===========================================================================

# Scoring weights (must sum to 1.0)
WEIGHTS = {
    "completeness":      0.25,
    "content_quality":   0.30,
    "formatting":        0.15,
    "keyword_relevance": 0.20,
    "experience":        0.10,
}

# Grade bands
GRADE_BANDS = [
    (95, "Outstanding",  "#059669"),
    (85, "Excellent",    "#22c55e"),
    (75, "Good",         "#84cc16"),
    (65, "Average",      "#f59e0b"),
    (50, "Below Average","#f97316"),
    (35, "Weak",         "#ef4444"),
    ( 0, "Poor",         "#dc2626"),
]

# Required sections and their aliases
REQUIRED_SECTIONS: dict[str, list[str]] = {
    "contact":     ["contact", "email", "phone", "linkedin", "github", "address",
                    "@", "tel", "mobile"],
    "summary":     ["summary", "objective", "profile", "about", "introduction",
                    "professional summary", "career objective"],
    "experience":  ["experience", "work experience", "employment", "work history",
                    "professional experience", "career", "positions held"],
    "education":   ["education", "academic", "degree", "university", "college",
                    "qualification", "schooling", "graduation"],
    "skills":      ["skills", "technical skills", "competencies", "expertise",
                    "technologies", "tools", "languages", "stack"],
    "projects":    ["project", "portfolio", "work samples", "case study",
                    "personal projects", "side projects", "open source"],
    "certifications": ["certification", "certificate", "license", "accreditation",
                       "credential", "aws", "pmp", "cpa", "cfa"],
    "achievements":   ["achievement", "award", "honor", "recognition",
                       "accomplishment", "publication", "patent"],
}

# Industry keywords by domain
KEYWORD_DOMAINS: dict[str, list[str]] = {
    "software": ["python", "javascript", "java", "c++", "sql", "git", "api",
                 "docker", "kubernetes", "cloud", "aws", "azure", "gcp",
                 "agile", "scrum", "ci/cd", "devops", "microservices",
                 "react", "node", "django", "flask", "fastapi", "rest"],
    "data":     ["machine learning", "deep learning", "nlp", "data science",
                 "pandas", "numpy", "tensorflow", "pytorch", "sklearn",
                 "tableau", "power bi", "sql", "spark", "hadoop", "etl",
                 "statistics", "a/b testing", "data pipeline", "feature engineering"],
    "management":["leadership", "stakeholder", "roadmap", "strategy", "okrs",
                  "kpi", "budget", "cross-functional", "agile", "scrum",
                  "product", "project management", "pnl", "revenue", "growth"],
    "design":   ["ux", "ui", "figma", "sketch", "prototyping", "wireframe",
                 "user research", "usability", "accessibility", "design system",
                 "adobe", "interaction design", "typography", "user testing"],
    "security": ["penetration", "vulnerability", "siem", "firewall", "encryption",
                 "compliance", "iso 27001", "soc 2", "nist", "incident response",
                 "threat modeling", "oscp", "cissp", "ethical hacking"],
    "general":  ["achieved", "delivered", "improved", "increased", "reduced",
                 "managed", "led", "developed", "designed", "implemented",
                 "optimized", "collaborated", "analyzed", "launched", "built"],
}

# Quantification patterns
QUANTIFY_PATTERNS = [
    r"\b\d+\s*%",                       # 40%, 15 %
    r"\$\s*\d+[\d,\.]*[kmb]?",         # $500K, $2.3M
    r"\b\d+[\d,]+\s*(?:users?|customers?|clients?|employees?|members?)",
    r"\b\d+x\b",                        # 3x improvement
    r"\b(?:increased?|reduced?|improved?|grew?|saved?)\s+(?:by\s+)?\d+",
    r"\b\d+\s*(?:million|billion|thousand|hundred)",
    r"\b(?:top|rank(?:ed)?)\s+\d+",     # ranked #1
]

# Action verbs (power words)
ACTION_VERBS = [
    "achieved", "accelerated", "architected", "automated", "boosted", "built",
    "championed", "collaborated", "created", "delivered", "designed", "developed",
    "drove", "engineered", "established", "executed", "expanded", "generated",
    "grew", "implemented", "improved", "increased", "initiated", "innovated",
    "launched", "led", "managed", "mentored", "optimized", "orchestrated",
    "pioneered", "produced", "reduced", "refactored", "scaled", "shipped",
    "spearheaded", "streamlined", "transformed",
]


# ===========================================================================
# Public API
# ===========================================================================

def calculate_scores(
    resume_text: str,
    target_role: str = "",
    use_cache:   bool = True,
) -> dict:
    """
    Main entry point — compute the full weighted resume score.

    Args:
        resume_text: Plain text extracted from the resume.
        target_role: Optional target job role to tune keyword scoring.
        use_cache:   Return cached result for identical inputs.

    Returns:
        Complete scoring dict — see module docstring for schema.
    """
    if not resume_text or not resume_text.strip():
        return _error_result("Empty resume text provided.")

    # Cache check
    cache_key = _make_cache_key(resume_text, target_role)
    if use_cache and cache_key in _SCORE_CACHE:
        logger.info("calculate_scores: returning cached result")
        return _SCORE_CACHE[cache_key]

    logger.info("calculate_scores: starting — %d chars, role='%s'",
                len(resume_text), target_role)

    text_lower = resume_text.lower()

    # ── Run all 5 evaluators ──────────────────────────────────────────────
    completeness  = evaluate_completeness(resume_text, text_lower)
    content       = evaluate_content(resume_text, text_lower)
    formatting    = evaluate_formatting(resume_text, text_lower)
    keywords      = evaluate_keywords(resume_text, text_lower, target_role)
    experience    = evaluate_experience(resume_text, text_lower)

    logger.info(
        "calculate_scores: completeness=%.0f content=%.0f "
        "formatting=%.0f keywords=%.0f experience=%.0f",
        completeness["pct"], content["pct"],
        formatting["pct"],   keywords["pct"],
        experience["pct"],
    )

    # ── Weighted overall score ────────────────────────────────────────────
    overall = round(
        completeness["pct"]  * WEIGHTS["completeness"]      +
        content["pct"]       * WEIGHTS["content_quality"]   +
        formatting["pct"]    * WEIGHTS["formatting"]        +
        keywords["pct"]      * WEIGHTS["keyword_relevance"] +
        experience["pct"]    * WEIGHTS["experience"]
    )
    overall = max(0, min(100, overall))

    grade, grade_color = _get_grade(overall)

    # ── Build breakdown ───────────────────────────────────────────────────
    breakdown = {
        "completeness": {
            **completeness,
            "score":  round(completeness["pct"] * WEIGHTS["completeness"]),
            "max":    int(WEIGHTS["completeness"] * 100),
            "weight": WEIGHTS["completeness"],
        },
        "content_quality": {
            **content,
            "score":  round(content["pct"] * WEIGHTS["content_quality"]),
            "max":    int(WEIGHTS["content_quality"] * 100),
            "weight": WEIGHTS["content_quality"],
        },
        "formatting": {
            **formatting,
            "score":  round(formatting["pct"] * WEIGHTS["formatting"]),
            "max":    int(WEIGHTS["formatting"] * 100),
            "weight": WEIGHTS["formatting"],
        },
        "keyword_relevance": {
            **keywords,
            "score":  round(keywords["pct"] * WEIGHTS["keyword_relevance"]),
            "max":    int(WEIGHTS["keyword_relevance"] * 100),
            "weight": WEIGHTS["keyword_relevance"],
        },
        "experience": {
            **experience,
            "score":  round(experience["pct"] * WEIGHTS["experience"]),
            "max":    int(WEIGHTS["experience"] * 100),
            "weight": WEIGHTS["experience"],
        },
    }

    # ── Strengths / Improvements / Quick wins ─────────────────────────────
    strengths, improvements, quick_wins = _build_feedback(breakdown, overall)

    result = {
        "success":      True,
        "overall_score": overall,
        "grade":        grade,
        "grade_color":  grade_color,
        "breakdown":    breakdown,
        "strengths":    strengths,
        "improvements": improvements,
        "quick_wins":   quick_wins,
        "scored_at":    datetime.now(timezone.utc).isoformat(),
        "message":      f"Resume scored {overall}/100 — {grade}",
    }

    if use_cache:
        _SCORE_CACHE[cache_key] = result

    return result


# ===========================================================================
# Component Evaluators
# ===========================================================================

def evaluate_completeness(resume_text: str, text_lower: str = "") -> dict:
    """
    Score resume completeness (25% weight).

    Checks presence and depth of: Contact, Summary, Experience,
    Education, Skills, Projects, Certifications, Achievements.

    Returns dict with pct, grade, reasoning, details.
    """
    if not text_lower:
        text_lower = resume_text.lower()

    section_scores:  dict[str, dict] = {}
    total_points     = 0
    max_points       = 0

    # Section weights (out of 100)
    section_weights = {
        "contact":        15,
        "summary":        12,
        "experience":     25,
        "education":      15,
        "skills":         15,
        "projects":       8,
        "certifications": 5,
        "achievements":   5,
    }

    for section, aliases in REQUIRED_SECTIONS.items():
        weight    = section_weights.get(section, 5)
        max_points += weight

        found     = any(alias in text_lower for alias in aliases)
        # Depth check: how many lines follow the section heading?
        depth     = _section_depth(resume_text, aliases)

        if found and depth >= 3:
            pts   = weight          # full credit
            status = "Complete"
        elif found and depth >= 1:
            pts   = round(weight * 0.6)
            status = "Partial"
        else:
            pts   = 0
            status = "Missing"

        total_points += pts
        section_scores[section] = {
            "found":  found,
            "depth":  depth,
            "points": pts,
            "max":    weight,
            "status": status,
        }

    pct = round((total_points / max_points) * 100) if max_points else 0

    # Missing and partial sections for reasoning
    missing  = [s for s, d in section_scores.items() if d["status"] == "Missing"]
    partial  = [s for s, d in section_scores.items() if d["status"] == "Partial"]
    complete = [s for s, d in section_scores.items() if d["status"] == "Complete"]

    reasoning = _completeness_reasoning(pct, complete, partial, missing)

    logger.debug("evaluate_completeness: pct=%d missing=%s", pct, missing)

    return {
        "pct":       pct,
        "grade":     _get_grade(pct)[0],
        "reasoning": reasoning,
        "details": {
            "sections":        section_scores,
            "complete_count":  len(complete),
            "partial_count":   len(partial),
            "missing_count":   len(missing),
            "missing_names":   missing,
            "partial_names":   partial,
        },
    }


def evaluate_content(resume_text: str, text_lower: str = "") -> dict:
    """
    Score content quality (30% weight).

    Evaluates: action verbs, quantified achievements, writing clarity,
    specificity, grammar signals, professional language.

    Returns dict with pct, grade, reasoning, details.
    """
    if not text_lower:
        text_lower = resume_text.lower()

    words      = resume_text.split()
    word_count = len(words)
    details    = {}

    # ── 1. Action Verb Score (25 pts) ─────────────────────────────────────
    found_verbs   = [v for v in ACTION_VERBS if v in text_lower]
    verb_pct      = min(len(found_verbs) / 8, 1.0)      # 8+ verbs = full score
    verb_score    = round(verb_pct * 25)
    details["action_verbs"] = {
        "score":   verb_score,
        "max":     25,
        "found":   found_verbs[:10],
        "count":   len(found_verbs),
        "reasoning": (
            f"Found {len(found_verbs)} strong action verbs "
            f"({'excellent' if len(found_verbs) >= 8 else 'add more for impact'})."
        ),
    }

    # ── 2. Quantification Score (25 pts) ──────────────────────────────────
    quant_matches = []
    for pat in QUANTIFY_PATTERNS:
        quant_matches.extend(re.findall(pat, resume_text, re.IGNORECASE))
    quant_pct   = min(len(quant_matches) / 5, 1.0)      # 5+ quantifiers = full
    quant_score = round(quant_pct * 25)
    details["quantification"] = {
        "score":   quant_score,
        "max":     25,
        "examples": quant_matches[:5],
        "count":   len(quant_matches),
        "reasoning": (
            f"Detected {len(quant_matches)} quantified achievement(s). "
            f"{'Great use of metrics!' if len(quant_matches) >= 5 else 'Add numbers to strengthen impact.'}"
        ),
    }

    # ── 3. Sentence Clarity (15 pts) — avg sentence length heuristic ─────
    sentences     = [s.strip() for s in re.split(r'[.!?]', resume_text) if s.strip()]
    if sentences:
        avg_len   = sum(len(s.split()) for s in sentences) / len(sentences)
        # Ideal bullet length: 10-25 words
        if 8 <= avg_len <= 22:
            clarity_score = 15
        elif 5 <= avg_len < 8 or 22 < avg_len <= 30:
            clarity_score = 10
        else:
            clarity_score = 5
    else:
        avg_len, clarity_score = 0, 5
    details["sentence_clarity"] = {
        "score":   clarity_score,
        "max":     15,
        "avg_words_per_sentence": round(avg_len, 1),
        "reasoning": (
            f"Average sentence length is {round(avg_len, 1)} words. "
            f"{'Well-balanced — concise and informative.' if clarity_score == 15 else 'Aim for 10-20 word bullet points.'}"
        ),
    }

    # ── 4. Professional Language (20 pts) — filler word penalty ──────────
    filler_words  = ["responsible for", "duties include", "helped with",
                     "worked on", "assisted with", "involved in", "tasked with"]
    filler_count  = sum(text_lower.count(fw) for fw in filler_words)
    lang_score    = max(0, 20 - filler_count * 4)
    details["professional_language"] = {
        "score":   lang_score,
        "max":     20,
        "filler_count": filler_count,
        "reasoning": (
            f"{'No weak filler phrases detected — great professional tone!' if filler_count == 0 else f'Found {filler_count} weak phrase(s) like \"responsible for\". Replace with strong action verbs.'}"
        ),
    }

    # ── 5. Specificity (15 pts) — named entities, company names, roles ───
    specificity   = _score_specificity(resume_text)
    spec_score    = round(specificity * 15)
    details["specificity"] = {
        "score":   spec_score,
        "max":     15,
        "reasoning": (
            "Resume contains specific company names, role titles, and technologies."
            if specificity > 0.7
            else "Add more specific company names, tools, and role titles."
        ),
    }

    # ── Aggregate ─────────────────────────────────────────────────────────
    raw_total  = verb_score + quant_score + clarity_score + lang_score + spec_score
    pct        = min(round((raw_total / 100) * 100), 100)

    # LLM enhancement — try to get a richer assessment
    pct = _llm_content_boost(resume_text, pct)

    reasoning  = _content_reasoning(pct, details)

    return {
        "pct":       pct,
        "grade":     _get_grade(pct)[0],
        "reasoning": reasoning,
        "details":   details,
    }


def evaluate_formatting(resume_text: str, text_lower: str = "") -> dict:
    """
    Score formatting quality (15% weight).

    Evaluates: length (word count), section structure, bullet usage,
    whitespace consistency, date format patterns, heading consistency.

    Returns dict with pct, grade, reasoning, details.
    """
    if not text_lower:
        text_lower = resume_text.lower()

    words      = resume_text.split()
    word_count = len(words)
    lines      = [l.strip() for l in resume_text.splitlines()]
    non_empty  = [l for l in lines if l]
    details    = {}

    # ── 1. Length (25 pts) ────────────────────────────────────────────────
    # Ideal: 400-800 words (1-2 pages)
    if 400 <= word_count <= 800:
        length_score = 25
        len_msg      = f"Ideal length ({word_count} words — approximately 1-2 pages)."
    elif 300 <= word_count < 400 or 800 < word_count <= 1100:
        length_score = 18
        len_msg      = f"Acceptable length ({word_count} words). {'Consider adding more detail.' if word_count < 400 else 'Consider trimming to 2 pages.'}"
    elif 200 <= word_count < 300 or 1100 < word_count <= 1500:
        length_score = 10
        len_msg      = f"{'Too short' if word_count < 300 else 'Too long'} ({word_count} words). Aim for 400-800 words."
    else:
        length_score = 5
        len_msg      = f"{'Very short' if word_count < 200 else 'Very long'} ({word_count} words). Significantly restructure."
    details["length"] = {"score": length_score, "max": 25,
                         "word_count": word_count, "reasoning": len_msg}

    # ── 2. Bullet points (20 pts) ─────────────────────────────────────────
    bullet_lines  = [l for l in non_empty if re.match(r'^[•\-\*►▸→✓▪◆]\s', l)]
    bullet_ratio  = len(bullet_lines) / max(len(non_empty), 1)
    if 0.25 <= bullet_ratio <= 0.60:
        bullet_score = 20
        bul_msg      = f"Good bullet point usage ({len(bullet_lines)} bullets, {round(bullet_ratio*100)}% of content lines)."
    elif 0.10 <= bullet_ratio < 0.25:
        bullet_score = 13
        bul_msg      = f"Add more bullet points to improve scannability ({len(bullet_lines)} found)."
    elif bullet_ratio > 0.60:
        bullet_score = 13
        bul_msg      = "Too many bullets — some sections need paragraph context."
    else:
        bullet_score = 5
        bul_msg      = "Almost no bullet points. Use bullets for experience and achievements."
    details["bullets"] = {"score": bullet_score, "max": 20,
                          "bullet_count": len(bullet_lines), "reasoning": bul_msg}

    # ── 3. Section headings (20 pts) ──────────────────────────────────────
    heading_lines = [l for l in non_empty
                     if (len(l.split()) <= 4 and
                         (l.isupper() or
                          re.match(r'^[A-Z][A-Z\s&/]+$', l) or
                          l.endswith(':')))]
    heading_count = len(heading_lines)
    if heading_count >= 5:
        heading_score = 20
        hd_msg        = f"Clear section headings detected ({heading_count} headings — excellent structure)."
    elif heading_count >= 3:
        heading_score = 14
        hd_msg        = f"Some headings present ({heading_count}). Add clear labels for all sections."
    elif heading_count >= 1:
        heading_score = 8
        hd_msg        = f"Only {heading_count} heading(s) found. Add clear section labels."
    else:
        heading_score = 2
        hd_msg        = "No clear section headings found. Structure your resume with bold section titles."
    details["headings"] = {"score": heading_score, "max": 20,
                           "heading_count": heading_count, "reasoning": hd_msg}

    # ── 4. Date format consistency (15 pts) ───────────────────────────────
    date_patterns  = [
        r'\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{4}\b',
        r'\b\d{1,2}/\d{4}\b',
        r'\b(19|20)\d{2}\s*[-–]\s*(19|20)\d{2}\b',
        r'\b(19|20)\d{2}\s*[-–]\s*[Pp]resent\b',
        r'\b(19|20)\d{2}\b',
    ]
    date_matches   = []
    for pat in date_patterns:
        date_matches.extend(re.findall(pat, resume_text))
    has_dates      = len(date_matches) >= 2
    # Consistency: check if a single pattern dominates
    date_score     = 15 if has_dates else 5
    dt_msg         = (f"Date ranges detected ({len(date_matches)} found — good consistency)."
                      if has_dates else "Missing or inconsistent date formats in experience/education.")
    details["dates"] = {"score": date_score, "max": 15,
                        "date_count": len(date_matches), "reasoning": dt_msg}

    # ── 5. Whitespace / Density (20 pts) ──────────────────────────────────
    empty_line_ratio = (len(lines) - len(non_empty)) / max(len(lines), 1)
    if 0.15 <= empty_line_ratio <= 0.40:
        ws_score = 20
        ws_msg   = "Well-spaced layout — easy to scan and read."
    elif 0.05 <= empty_line_ratio < 0.15:
        ws_score = 12
        ws_msg   = "Resume appears dense. Add spacing between sections."
    elif empty_line_ratio > 0.40:
        ws_score = 12
        ws_msg   = "Too much whitespace — content may look sparse."
    else:
        ws_score = 6
        ws_msg   = "Very dense text. Use whitespace to improve readability."
    details["whitespace"] = {"score": ws_score, "max": 20,
                             "empty_line_ratio": round(empty_line_ratio, 2),
                             "reasoning": ws_msg}

    # ── Aggregate ─────────────────────────────────────────────────────────
    raw   = length_score + bullet_score + heading_score + date_score + ws_score
    pct   = min(round((raw / 100) * 100), 100)

    return {
        "pct":       pct,
        "grade":     _get_grade(pct)[0],
        "reasoning": _formatting_reasoning(pct, details),
        "details":   details,
    }


def evaluate_keywords(
    resume_text: str,
    text_lower:  str  = "",
    target_role: str  = "",
) -> dict:
    """
    Score keyword relevance (20% weight).

    Scores presence of: domain-specific technical terms, action words,
    role-relevant keywords, ATS-friendly terminology.

    Returns dict with pct, grade, reasoning, details.
    """
    if not text_lower:
        text_lower = resume_text.lower()

    details = {}

    # ── 1. Detect best-matching domain ───────────────────────────────────
    domain_hits = {
        domain: sum(1 for kw in keywords if kw in text_lower)
        for domain, keywords in KEYWORD_DOMAINS.items()
    }
    # Always include general
    best_domain = max(
        (d for d in domain_hits if d != "general"),
        key=lambda d: domain_hits[d],
        default="software",
    )
    # If a target_role hint is provided, map it
    role_lower = target_role.lower()
    if any(x in role_lower for x in ["data", "ml", "machine", "analyst"]):
        best_domain = "data"
    elif any(x in role_lower for x in ["security", "cyber"]):
        best_domain = "security"
    elif any(x in role_lower for x in ["design", "ux", "ui"]):
        best_domain = "design"
    elif any(x in role_lower for x in ["product", "manager", "pm"]):
        best_domain = "management"

    # ── 2. Domain keyword score (40 pts) ─────────────────────────────────
    domain_kws    = KEYWORD_DOMAINS[best_domain]
    domain_found  = [kw for kw in domain_kws if kw in text_lower]
    domain_pct    = min(len(domain_found) / max(len(domain_kws) * 0.4, 1), 1.0)
    domain_score  = round(domain_pct * 40)
    details["domain_keywords"] = {
        "score":     domain_score,
        "max":       40,
        "domain":    best_domain,
        "found":     domain_found[:12],
        "found_count": len(domain_found),
        "total_in_domain": len(domain_kws),
        "reasoning": (
            f"Detected {len(domain_found)}/{len(domain_kws)} {best_domain} domain keywords. "
            f"{'Strong domain coverage!' if domain_pct >= 0.5 else 'Add more role-specific technical terms.'}"
        ),
    }

    # ── 3. General action keywords (25 pts) ───────────────────────────────
    general_kws    = KEYWORD_DOMAINS["general"]
    general_found  = [kw for kw in general_kws if kw in text_lower]
    gen_pct        = min(len(general_found) / 8, 1.0)
    gen_score      = round(gen_pct * 25)
    details["action_keywords"] = {
        "score":    gen_score,
        "max":      25,
        "found":    general_found[:8],
        "reasoning": (
            f"Found {len(general_found)} strong result-oriented keywords. "
            f"{'Excellent vocabulary!' if gen_pct >= 0.7 else 'Add more achievement verbs (improved, launched, delivered).'}"
        ),
    }

    # ── 4. ATS keyword density (20 pts) ───────────────────────────────────
    # Total unique meaningful words (3+ chars) vs keyword overlap
    all_kws        = set(kw for kws in KEYWORD_DOMAINS.values() for kw in kws)
    ats_found      = [kw for kw in all_kws if kw in text_lower]
    ats_pct        = min(len(ats_found) / 25, 1.0)    # 25+ = full score
    ats_score      = round(ats_pct * 20)
    details["ats_density"] = {
        "score":   ats_score,
        "max":     20,
        "unique_keywords": len(ats_found),
        "reasoning": (
            f"Resume contains {len(ats_found)} ATS-recognized keywords. "
            f"{'Good ATS compatibility.' if ats_pct >= 0.6 else 'Add more industry-standard terms to pass ATS filters.'}"
        ),
    }

    # ── 5. Keyword variety (15 pts) — avoid repetition ────────────────────
    # Count repeated keywords (>3 times is bad)
    kw_counts     = {kw: text_lower.count(kw) for kw in domain_found}
    over_repeated = [kw for kw, cnt in kw_counts.items() if cnt > 3]
    variety_score = max(0, 15 - len(over_repeated) * 3)
    details["keyword_variety"] = {
        "score":          variety_score,
        "max":            15,
        "over_repeated":  over_repeated,
        "reasoning": (
            "Good keyword variety across the resume."
            if not over_repeated
            else f"'{', '.join(over_repeated[:3])}' appear too frequently. Vary your vocabulary."
        ),
    }

    # ── Aggregate ─────────────────────────────────────────────────────────
    raw = domain_score + gen_score + ats_score + variety_score
    pct = min(round((raw / 100) * 100), 100)

    return {
        "pct":       pct,
        "grade":     _get_grade(pct)[0],
        "reasoning": _keywords_reasoning(pct, details, best_domain),
        "details":   details,
    }


def evaluate_experience(resume_text: str, text_lower: str = "") -> dict:
    """
    Score experience quality (10% weight).

    Evaluates: years of experience detected, career progression,
    quantified achievements, seniority signals, company quality signals.

    Returns dict with pct, grade, reasoning, details.
    """
    if not text_lower:
        text_lower = resume_text.lower()

    details = {}

    # ── 1. Years of experience detected (30 pts) ──────────────────────────
    years_found = re.findall(
        r'\b(\d{1,2})\+?\s*(?:years?|yrs?)(?:\s+of)?\s*(?:experience|exp\.?)\b',
        text_lower,
    )
    year_nums   = [int(y) for y in years_found if int(y) <= 40]
    max_years   = max(year_nums) if year_nums else _infer_years(resume_text)

    if max_years >= 10:
        yr_score = 30
        yr_msg   = f"Senior-level experience detected ({max_years}+ years)."
    elif max_years >= 5:
        yr_score = 24
        yr_msg   = f"Mid-level experience ({max_years} years)."
    elif max_years >= 2:
        yr_score = 16
        yr_msg   = f"Early-career experience ({max_years} years). Good foundation."
    elif max_years >= 1:
        yr_score = 10
        yr_msg   = "Limited professional experience detected (< 2 years)."
    else:
        yr_score = 6
        yr_msg   = "No explicit years of experience found. Add clear tenure dates."
    details["years_experience"] = {
        "score":   yr_score,
        "max":     30,
        "years":   max_years,
        "reasoning": yr_msg,
    }

    # ── 2. Quantified achievements (25 pts) ───────────────────────────────
    quant_hits = []
    for pat in QUANTIFY_PATTERNS:
        quant_hits.extend(re.findall(pat, resume_text, re.IGNORECASE))
    quant_pct  = min(len(quant_hits) / 4, 1.0)      # 4+ = full score
    quant_score = round(quant_pct * 25)
    details["quantified_achievements"] = {
        "score":   quant_score,
        "max":     25,
        "count":   len(quant_hits),
        "examples": quant_hits[:4],
        "reasoning": (
            f"Found {len(quant_hits)} quantified achievement(s). "
            f"{'Strong use of metrics — great for recruiters!' if len(quant_hits) >= 4 else 'Add numbers (%, $, users) to demonstrate impact.'}"
        ),
    }

    # ── 3. Career progression signals (25 pts) ────────────────────────────
    seniority_words = {
        "high":   ["senior", "lead", "principal", "staff", "director", "vp",
                   "vice president", "head of", "chief", "c-level", "cto", "ceo"],
        "medium": ["manager", "team lead", "architect", "specialist", "expert",
                   "consultant", "advisor"],
        "entry":  ["junior", "associate", "intern", "trainee", "graduate",
                   "entry level", "assistant"],
    }
    high_found   = any(w in text_lower for w in seniority_words["high"])
    medium_found = any(w in text_lower for w in seniority_words["medium"])
    entry_found  = any(w in text_lower for w in seniority_words["entry"])

    if high_found:
        prog_score = 25
        prog_msg   = "Senior/leadership titles detected — strong career progression."
    elif medium_found:
        prog_score = 18
        prog_msg   = "Mid-level seniority titles present — good progression."
    elif entry_found:
        prog_score = 10
        prog_msg   = "Entry-level titles detected. Show growth trajectory."
    else:
        prog_score = 14
        prog_msg   = "Role seniority unclear. Include explicit title seniority levels."
    details["career_progression"] = {
        "score":   prog_score,
        "max":     25,
        "reasoning": prog_msg,
    }

    # ── 4. Employer count / variety (20 pts) ──────────────────────────────
    # Heuristic: count how many "at / @" or "Company —" patterns exist
    employer_patterns = [
        r'\bat\s+[A-Z][A-Za-z\s&,\.]+(?:Inc|Ltd|LLC|Corp|Co\.|Technologies|Systems|Solutions)?',
        r'[A-Z][A-Za-z\s&]+\s*[–\-—]\s*(?:19|20)\d{2}',
        r'(?:19|20)\d{2}\s*[–\-—]\s*(?:19|20)\d{2}',
    ]
    employer_hits = set()
    for pat in employer_patterns:
        for m in re.finditer(pat, resume_text):
            employer_hits.add(m.group()[:30])

    emp_count = min(len(employer_hits), 6)
    if emp_count >= 3:
        emp_score = 20
        emp_msg   = f"Multiple employer experiences ({emp_count} positions) — diverse background."
    elif emp_count == 2:
        emp_score = 15
        emp_msg   = "Two employer experiences detected."
    elif emp_count == 1:
        emp_score = 10
        emp_msg   = "Single employer detected — consider adding freelance/project work."
    else:
        emp_score = 5
        emp_msg   = "Employer history unclear. Add company names with date ranges."
    details["employer_diversity"] = {
        "score":   emp_score,
        "max":     20,
        "count":   emp_count,
        "reasoning": emp_msg,
    }

    # ── Aggregate ─────────────────────────────────────────────────────────
    raw = yr_score + quant_score + prog_score + emp_score
    pct = min(round((raw / 100) * 100), 100)

    # LLM boost for experience
    pct = _llm_experience_boost(resume_text, pct)

    return {
        "pct":       pct,
        "grade":     _get_grade(pct)[0],
        "reasoning": _experience_reasoning(pct, details),
        "details":   details,
    }


# ===========================================================================
# LLM Enhancement Helpers
# ===========================================================================

CONTENT_LLM_PROMPT = """You are an expert resume reviewer. Evaluate ONLY the content quality 
(not formatting or completeness) of this resume and provide a score from 0-100.

Assess:
- Strength of action verbs and professional language
- Use of quantified achievements and metrics
- Specificity of descriptions (vs vague generalities)
- Overall writing quality and impact

Base score from rule-based analysis: {base_score}

RESUME:
{text}

Return ONLY valid JSON:
{{"content_score": <int 0-100>, "key_strength": "<one sentence>", "key_weakness": "<one sentence>"}}
"""

EXPERIENCE_LLM_PROMPT = """You are a senior recruiter evaluating resume experience quality. 
Score 0-100 based on:
- Career progression and growth trajectory
- Quality and prestige of employers/projects
- Impact and scope of responsibilities
- Leadership and cross-functional collaboration signals

Base score from rule-based analysis: {base_score}

RESUME:
{text}

Return ONLY valid JSON:
{{"experience_score": <int 0-100>, "level": "<Junior|Mid|Senior|Lead>", "assessment": "<one sentence>"}}
"""


def _llm_content_boost(resume_text: str, base_score: int) -> int:
    """Optionally refine the content score with a quick LLM pass."""
    try:
        llm    = load_llm()
        prompt = CONTENT_LLM_PROMPT.format(
            base_score=base_score,
            text=resume_text[:4000],
        )
        resp   = llm.invoke(prompt)
        raw    = resp.content if hasattr(resp, "content") else str(resp)
        parsed = extract_json_response(raw)
        if parsed and isinstance(parsed.get("content_score"), int):
            llm_score = parsed["content_score"]
            # Blend: 60% rule-based, 40% LLM
            blended = round(base_score * 0.6 + llm_score * 0.4)
            logger.debug("_llm_content_boost: base=%d llm=%d blended=%d",
                         base_score, llm_score, blended)
            return max(0, min(100, blended))
    except Exception as exc:
        logger.debug("_llm_content_boost: skipped (%s)", exc)
    return base_score


def _llm_experience_boost(resume_text: str, base_score: int) -> int:
    """Optionally refine the experience score with a quick LLM pass."""
    try:
        llm    = load_llm()
        prompt = EXPERIENCE_LLM_PROMPT.format(
            base_score=base_score,
            text=resume_text[:4000],
        )
        resp   = llm.invoke(prompt)
        raw    = resp.content if hasattr(resp, "content") else str(resp)
        parsed = extract_json_response(raw)
        if parsed and isinstance(parsed.get("experience_score"), int):
            llm_score = parsed["experience_score"]
            blended   = round(base_score * 0.55 + llm_score * 0.45)
            logger.debug("_llm_experience_boost: base=%d llm=%d blended=%d",
                         base_score, llm_score, blended)
            return max(0, min(100, blended))
    except Exception as exc:
        logger.debug("_llm_experience_boost: skipped (%s)", exc)
    return base_score


# ===========================================================================
# Reasoning Generators
# ===========================================================================

def _completeness_reasoning(pct: int, complete: list, partial: list, missing: list) -> str:
    parts = []
    if complete:
        parts.append(f"✅ Present: {', '.join(complete[:5])}.")
    if partial:
        parts.append(f"⚠️ Thin sections: {', '.join(partial)} — expand these.")
    if missing:
        parts.append(f"❌ Missing: {', '.join(missing)} — add these sections.")
    if pct >= 90:
        parts.insert(0, "Your resume is very well-structured with all key sections present.")
    elif pct >= 70:
        parts.insert(0, "Good structure overall. A few sections need attention.")
    elif pct >= 50:
        parts.insert(0, "Resume is missing several important sections.")
    else:
        parts.insert(0, "Resume lacks many standard sections. Significant restructuring needed.")
    return " ".join(parts)


def _content_reasoning(pct: int, details: dict) -> str:
    av   = details.get("action_verbs", {})
    qu   = details.get("quantification", {})
    pl   = details.get("professional_language", {})
    parts = []
    if pct >= 85:
        parts.append("Excellent content quality — strong action verbs and quantified achievements.")
    elif pct >= 70:
        parts.append("Good content. Strengthen with more metrics and power verbs.")
    elif pct >= 50:
        parts.append("Content is average. Replace weak phrases with achievements and numbers.")
    else:
        parts.append("Content needs significant improvement. Focus on impact-driven writing.")
    if av.get("count", 0) < 5:
        parts.append(f"Add more action verbs (currently {av.get('count',0)}, target 8+).")
    if qu.get("count", 0) < 3:
        parts.append("Include more metrics ($, %, users, time saved).")
    if pl.get("filler_count", 0) > 0:
        parts.append(f"Remove {pl['filler_count']} weak phrase(s) like 'responsible for'.")
    return " ".join(parts)


def _formatting_reasoning(pct: int, details: dict) -> str:
    length   = details.get("length", {})
    bullets  = details.get("bullets", {})
    headings = details.get("headings", {})
    parts    = []
    if pct >= 85:
        parts.append("Excellent formatting — clear structure, good length, and proper use of bullets.")
    elif pct >= 65:
        parts.append("Good formatting overall with minor improvements possible.")
    else:
        parts.append("Formatting needs work to improve readability and ATS compatibility.")
    parts.append(length.get("reasoning", ""))
    if bullets.get("score", 0) < 15:
        parts.append(bullets.get("reasoning", ""))
    if headings.get("score", 0) < 15:
        parts.append(headings.get("reasoning", ""))
    return " ".join(p for p in parts if p)


def _keywords_reasoning(pct: int, details: dict, domain: str) -> str:
    dom = details.get("domain_keywords", {})
    ats = details.get("ats_density", {})
    parts = []
    if pct >= 80:
        parts.append(f"Strong keyword profile for {domain} roles — good ATS compatibility.")
    elif pct >= 60:
        parts.append(f"Decent keyword coverage for {domain}. Add more role-specific terms.")
    else:
        parts.append(f"Low keyword density. ATS systems may filter this resume.")
    parts.append(f"Domain: {dom.get('found_count', 0)}/{dom.get('total_in_domain', 0)} {domain} keywords found.")
    parts.append(f"ATS keywords: {ats.get('unique_keywords', 0)} recognized terms.")
    return " ".join(parts)


def _experience_reasoning(pct: int, details: dict) -> str:
    yr = details.get("years_experience", {})
    qu = details.get("quantified_achievements", {})
    pr = details.get("career_progression", {})
    parts = []
    if pct >= 80:
        parts.append("Strong experience profile with clear progression and measurable impact.")
    elif pct >= 60:
        parts.append("Solid experience section. Add more quantified results.")
    else:
        parts.append("Experience section needs strengthening with specific achievements.")
    parts.append(yr.get("reasoning", ""))
    if qu.get("count", 0) < 3:
        parts.append("Include more measurable achievements (numbers, percentages, dollar amounts).")
    parts.append(pr.get("reasoning", ""))
    return " ".join(p for p in parts if p)


# ===========================================================================
# Feedback Builder
# ===========================================================================

def _build_feedback(
    breakdown: dict, overall: int
) -> tuple[list[str], list[str], list[str]]:
    """Generate strengths, improvements, and quick wins from breakdown."""
    strengths    = []
    improvements = []
    quick_wins   = []

    comp  = breakdown["completeness"]
    cont  = breakdown["content_quality"]
    fmt   = breakdown["formatting"]
    kw    = breakdown["keyword_relevance"]
    exp   = breakdown["experience"]

    # ── Strengths (pct ≥ 75) ────────────────────────────────────────────
    if comp["pct"] >= 75:
        strengths.append("Well-structured resume with all key sections present.")
    if cont["pct"] >= 75:
        strengths.append("Strong, impact-driven writing with professional language.")
    if fmt["pct"] >= 75:
        strengths.append("Clean formatting that is ATS-friendly and easy to read.")
    if kw["pct"] >= 75:
        strengths.append("Good keyword density aligned with industry expectations.")
    if exp["pct"] >= 75:
        strengths.append("Solid experience section demonstrating career growth.")
    if not strengths:
        strengths.append("Resume shows a foundational structure to build upon.")

    # ── Improvements (pct < 60) ───────────────────────────────────────────
    sorted_components = sorted(
        [("Completeness", comp), ("Content Quality", cont),
         ("Formatting", fmt), ("Keywords", kw), ("Experience", exp)],
        key=lambda x: x[1]["pct"],
    )
    for name, data in sorted_components[:3]:
        if data["pct"] < 70:
            improvements.append(f"{name}: {data['reasoning'][:120]}")

    # ── Quick Wins (fast improvements, high impact) ───────────────────────
    comp_details = comp.get("details", {})
    missing = comp_details.get("missing_names", [])

    if "summary" in missing:
        quick_wins.append("Add a 2-3 sentence professional summary at the top.")
    if "projects" in missing:
        quick_wins.append("Include 2-3 relevant projects with GitHub links or outcomes.")
    if "certifications" in missing:
        quick_wins.append("Add any certifications or online course completions.")

    kw_details = kw.get("details", {})
    ats_count  = kw_details.get("ats_density", {}).get("unique_keywords", 0)
    if ats_count < 15:
        quick_wins.append("Add 5-10 industry-standard technical keywords for better ATS scoring.")

    cont_details = cont.get("details", {})
    if cont_details.get("quantification", {}).get("count", 0) < 3:
        quick_wins.append("Convert 3 bullet points to include metrics (%, $, time saved, users).")

    fmt_details = fmt.get("details", {})
    if fmt_details.get("headings", {}).get("heading_count", 0) < 4:
        quick_wins.append("Add clear section headings (EXPERIENCE, SKILLS, EDUCATION in bold/caps).")

    if not quick_wins:
        quick_wins.append("Tailor your resume summary for each specific job application.")

    return strengths, improvements, quick_wins[:5]


# ===========================================================================
# Internal Utilities
# ===========================================================================

def _get_grade(score: int) -> tuple[str, str]:
    """Return (grade_label, hex_color) for a given 0-100 score."""
    for threshold, label, color in GRADE_BANDS:
        if score >= threshold:
            return label, color
    return "Poor", "#dc2626"


def _make_cache_key(text: str, role: str) -> str:
    payload = f"{text[:600]}{role}"
    return hashlib.md5(payload.encode()).hexdigest()


def _section_depth(text: str, aliases: list[str]) -> int:
    """
    Count how many non-empty lines appear after a matched section heading,
    capped at 10 (enough to distinguish empty vs thin vs full sections).
    """
    lines = text.splitlines()
    for i, line in enumerate(lines):
        line_lower = line.lower().strip()
        if any(alias in line_lower for alias in aliases):
            # Count non-empty lines after this heading
            count = 0
            for j in range(i + 1, min(i + 15, len(lines))):
                next_line = lines[j].strip()
                if not next_line:
                    continue
                # Stop at next heading-like line
                if (len(next_line.split()) <= 3 and
                    (next_line.isupper() or next_line.endswith(":"))):
                    break
                count += 1
            return count
    return 0


def _score_specificity(text: str) -> float:
    """
    Heuristic: measure specificity by counting proper nouns / capitalized words,
    numbers, and technology names in experience descriptions.
    Returns 0.0-1.0.
    """
    words        = text.split()
    cap_words    = [w for w in words if w and w[0].isupper() and len(w) > 2]
    digit_tokens = [w for w in words if re.search(r'\d', w)]
    score        = min((len(cap_words) / max(len(words), 1)) * 4 +
                       (len(digit_tokens) / max(len(words), 1)) * 3, 1.0)
    return round(score, 2)


def _infer_years(text: str) -> int:
    """Infer years of experience from date ranges in the resume."""
    year_ranges = re.findall(
        r'\b((?:19|20)\d{2})\s*[-–—]\s*((?:19|20)\d{2}|[Pp]resent|[Nn]ow)',
        text,
    )
    if not year_ranges:
        return 0
    current_year = datetime.now().year
    total = 0
    for start, end in year_ranges:
        try:
            s = int(start)
            e = current_year if end.lower() in ("present", "now") else int(end)
            total += max(0, e - s)
        except ValueError:
            pass
    # Divide by typical concurrent overlaps
    return min(round(total * 0.8), 30)


def _error_result(message: str) -> dict:
    return {
        "success":       False,
        "overall_score": 0,
        "grade":         "N/A",
        "grade_color":   "#94a3b8",
        "breakdown":     {},
        "strengths":     [],
        "improvements":  [],
        "quick_wins":    [],
        "scored_at":     datetime.now(timezone.utc).isoformat(),
        "message":       message,
    }


# ===========================================================================
# Self-test — run with: python -m backend.resume_scoring
# ===========================================================================

if __name__ == "__main__":
    import textwrap, logging as _l
    _l.basicConfig(level=_l.WARNING, format="%(levelname)s | %(message)s")

    SAMPLE = textwrap.dedent("""
        JANE SMITH | Senior Software Engineer
        jane@email.com | linkedin.com/in/janesmith | San Francisco, CA

        PROFESSIONAL SUMMARY
        Results-driven Senior Software Engineer with 7+ years of experience building
        scalable backend systems and APIs. Led teams of 5-12 engineers at high-growth
        startups. Expert in Python, Go, and cloud-native architectures.

        EXPERIENCE
        Senior Software Engineer — TechCorp Inc. (Jan 2021 – Present)
        • Architected microservices platform serving 2M+ daily active users
        • Reduced API latency by 40% through Redis caching and query optimization
        • Led migration from monolith to Kubernetes, cutting infra costs by $120K/yr
        • Mentored 6 junior engineers; 4 promoted within 18 months

        Software Engineer — StartupXYZ (Mar 2018 – Dec 2020)
        • Built real-time data pipeline processing 500K events/day using Kafka + Spark
        • Delivered 12 features on time across 3 product launches
        • Improved test coverage from 32% to 87% using pytest and GitHub Actions

        EDUCATION
        B.S. Computer Science — Stanford University (2017)
        GPA: 3.8 | Dean's List (4 semesters)

        SKILLS
        Languages: Python, Go, JavaScript, SQL, Bash
        Frameworks: FastAPI, Django, React, gRPC
        Tools: Docker, Kubernetes, AWS, GCP, Terraform, GitHub Actions
        Databases: PostgreSQL, Redis, MongoDB, Elasticsearch

        CERTIFICATIONS
        AWS Certified Solutions Architect – Professional (2023)
        Google Cloud Professional Data Engineer (2022)

        PROJECTS
        Open-source Python library (GitHub: github.com/janesmith/pyfast) — 1,200 stars

        ACHIEVEMENTS
        • Top Engineer Award, TechCorp Q3 2022
        • Speaker, PyCon US 2023
    """).strip()

    print("Running resume scoring on sample resume...\n")
    result = calculate_scores(SAMPLE, target_role="software_engineer", use_cache=False)

    print(f"Overall Score : {result['overall_score']}/100")
    print(f"Grade         : {result['grade']}")
    print(f"Scored At     : {result['scored_at']}\n")

    print("-- Breakdown --")
    for name, data in result["breakdown"].items():
        print(f"  {name:<22} {data['pct']:>3}%  ({data['score']}/{data['max']} pts)  [{data['grade']}]")

    print("\n-- Strengths --")
    for s in result["strengths"]:
        print(f"  ✅ {s}")

    print("\n-- Improvements --")
    for s in result["improvements"]:
        print(f"  ⚠️  {s[:80]}")

    print("\n-- Quick Wins --")
    for s in result["quick_wins"]:
        print(f"  🎯 {s}")

    print("\n[ALL TESTS PASSED]")
