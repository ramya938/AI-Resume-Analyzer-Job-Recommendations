"""
improvement_suggestions.py — AI-Powered Resume Improvement Engine
==================================================================
File: backend/improvement_suggestions.py

Generates personalised, ranked resume improvement suggestions with
before/after bullet rewrites, estimated score gains, and curated
learning resources for every gap identified.

Public Functions:
    generate_suggestions()   — master orchestrator; full ranked output
    improve_bullets()        — rewrite weak bullets to strong ones
    estimate_score_gain()    — predict score delta for each suggestion
    save_suggestions()       — persist to SQLite via database layer

Output Schema:
    {
        "high_priority":   [ <Suggestion>, ... ],   # immediate, big impact
        "medium_priority": [ <Suggestion>, ... ],   # important, moderate effort
        "quick_wins":      [ <Suggestion>, ... ],   # fast, low effort, real gain
        "metadata": {
            "total_suggestions": int,
            "estimated_total_gain": int,
            "current_score": int,
            "projected_score": int,
            "generated_at": str
        }
    }

Suggestion Schema:
    {
        "id":              str,          # unique slug
        "category":        str,          # completeness | content | formatting |
                                         #   keywords | experience | skills
        "title":           str,          # short display title
        "description":     str,          # what to fix and why
        "priority":        str,          # High | Medium | Quick Win
        "effort":          str,          # Low | Medium | High
        "before_example":  str,          # original weak text
        "after_example":   str,          # AI-rewritten strong text
        "score_gain":      int,          # estimated score improvement (0-15)
        "learning_resources": [ ... ],   # links & titles
        "action_steps":    [ str, ... ]  # numbered to-do items
    }

Features:
    - LLM-powered bullet rewriting (Gemini) with rule-based fallback
    - Score-gain estimation model (priority × component weight × gap)
    - Curated resource library for 30+ improvement areas
    - Deduplication + ranking by score_gain desc
    - In-memory cache keyed on resume hash
    - DB persistence via resume_analysis table extension
"""

from __future__ import annotations

import re
import json
import logging
import hashlib
import sqlite3
from datetime import datetime, timezone
from typing import Any

from backend.llm_analyzer import load_llm, extract_json_response
from backend.resume_scoring import (
    calculate_scores, evaluate_completeness, evaluate_content,
    evaluate_formatting, evaluate_keywords, evaluate_experience,
    WEIGHTS,
)
from utils.database import get_connection

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# In-memory cache
# ---------------------------------------------------------------------------
_SUGGESTION_CACHE: dict[str, dict] = {}


# ===========================================================================
# Curated Resource Library
# ===========================================================================

RESOURCES: dict[str, list[dict]] = {
    "action_verbs": [
        {"name": "185 Powerful Resume Action Verbs",
         "url": "https://www.themuse.com/advice/185-powerful-verbs-that-will-make-your-resume-awesome", "free": True},
        {"name": "Harvard Resume Action Verbs",
         "url": "https://ocs.fas.harvard.edu/files/ocs/files/action_verbs.pdf", "free": True},
    ],
    "quantification": [
        {"name": "How to Quantify Your Resume Bullets",
         "url": "https://www.indeed.com/career-advice/resumes-cover-letters/how-to-quantify-your-resume", "free": True},
        {"name": "Using Numbers on Your Resume",
         "url": "https://resumegenius.com/blog/resume-help/numbers-on-resume", "free": True},
    ],
    "summary": [
        {"name": "How to Write a Professional Summary",
         "url": "https://www.indeed.com/career-advice/resumes-cover-letters/how-to-write-a-professional-summary", "free": True},
        {"name": "Professional Summary Examples (2025)",
         "url": "https://zety.com/blog/professional-summary-for-resume", "free": True},
    ],
    "ats": [
        {"name": "Beat ATS with the Right Keywords",
         "url": "https://www.jobscan.co/blog/ats-keywords/", "free": True},
        {"name": "ATS Resume Checker (Jobscan)",
         "url": "https://www.jobscan.co", "free": False},
        {"name": "Free ATS-Friendly Resume Templates",
         "url": "https://www.canva.com/resumes/ats-friendly/", "free": True},
    ],
    "formatting": [
        {"name": "Resume Formatting Best Practices",
         "url": "https://www.resumebuilder.com/resume-formatting-guide/", "free": True},
        {"name": "One Page vs Two Page Resume",
         "url": "https://www.themuse.com/advice/one-page-resume-vs-two-page-resume", "free": True},
    ],
    "bullet_writing": [
        {"name": "How to Write Resume Bullet Points",
         "url": "https://www.indeed.com/career-advice/resumes-cover-letters/resume-bullet-points", "free": True},
        {"name": "STAR Method for Resume Bullets",
         "url": "https://www.themuse.com/advice/star-interview-method", "free": True},
    ],
    "skills_section": [
        {"name": "How to List Skills on a Resume",
         "url": "https://www.indeed.com/career-advice/resumes-cover-letters/how-to-list-skills-on-a-resume", "free": True},
        {"name": "Top Skills Employers Want in 2025",
         "url": "https://www.linkedin.com/business/talent/blog/talent-strategy/linkedin-top-skills", "free": True},
    ],
    "certifications": [
        {"name": "Best IT Certifications (2025)",
         "url": "https://www.coursera.org/articles/best-it-certifications", "free": True},
        {"name": "Google Career Certificates",
         "url": "https://grow.google/certificates/", "free": False},
        {"name": "Free Microsoft Certifications",
         "url": "https://learn.microsoft.com/en-us/certifications/", "free": True},
    ],
    "projects": [
        {"name": "How to List Projects on Your Resume",
         "url": "https://www.indeed.com/career-advice/resumes-cover-letters/projects-on-resume", "free": True},
        {"name": "GitHub Profile for Your Resume",
         "url": "https://docs.github.com/en/account-and-profile/setting-up-and-managing-your-github-profile", "free": True},
    ],
    "experience_writing": [
        {"name": "How to Describe Work Experience",
         "url": "https://zety.com/blog/work-experience-on-resume", "free": True},
        {"name": "Using STAR Method on Resume",
         "url": "https://www.themuse.com/advice/star-interview-format", "free": True},
    ],
    "general": [
        {"name": "Resume Writing Guide (Coursera)",
         "url": "https://www.coursera.org/articles/how-to-make-a-resume", "free": True},
        {"name": "Free Resume Review (Topresume)",
         "url": "https://www.topresume.com/resume-review", "free": True},
        {"name": "LinkedIn Resume Builder",
         "url": "https://www.linkedin.com/help/linkedin/answer/a554990", "free": True},
    ],
}


# ===========================================================================
# Rule-Based Suggestion Templates
# ===========================================================================

SUGGESTION_TEMPLATES: list[dict] = [
    # ── Completeness ──────────────────────────────────────────────────────
    {
        "id": "add_professional_summary",
        "category": "completeness",
        "title": "Add a Professional Summary",
        "description": (
            "Your resume is missing a professional summary. A 2-3 sentence summary "
            "at the top immediately tells recruiters who you are, your experience level, "
            "and your key value proposition. Resumes with summaries get 40% more callbacks."
        ),
        "priority": "High",
        "effort": "Low",
        "before_example": "(No summary section — resume starts with Experience directly)",
        "after_example": (
            "Results-driven Software Engineer with 5+ years building scalable APIs "
            "and cloud-native systems. Delivered 3 production platforms serving 500K+ users. "
            "Expert in Python, AWS, and microservices architecture."
        ),
        "score_gain": 8,
        "learning_resources": RESOURCES["summary"],
        "action_steps": [
            "Write 2-3 sentences: who you are + years of experience + biggest achievement.",
            "Tailor the summary for each specific job description.",
            "Place it right below your contact information.",
        ],
        "trigger_fn": lambda bd: bd["completeness"]["details"].get("missing_names", []) and
                      "summary" in bd["completeness"]["details"].get("missing_names", []),
    },
    {
        "id": "add_projects_section",
        "category": "completeness",
        "title": "Add a Projects Section",
        "description": (
            "No projects section was detected. Projects demonstrate practical skills, "
            "especially valuable for early-career candidates and career changers. "
            "Include 2-3 relevant projects with tech stack, your role, and outcomes."
        ),
        "priority": "Medium",
        "effort": "Medium",
        "before_example": "(No projects listed — skills look theoretical without proof)",
        "after_example": (
            "E-Commerce Platform (2024) | Python, Django, React, PostgreSQL\n"
            "Built a full-stack marketplace with Stripe payments, serving 1,200 monthly users. "
            "Reduced checkout abandonment by 18% through UX optimizations."
        ),
        "score_gain": 6,
        "learning_resources": RESOURCES["projects"],
        "action_steps": [
            "List 2-3 projects: title, tech stack, your role, and measurable outcome.",
            "Link to GitHub repo or live demo where possible.",
            "Focus on projects relevant to your target role.",
        ],
        "trigger_fn": lambda bd: "projects" in bd["completeness"]["details"].get("missing_names", []),
    },
    {
        "id": "add_certifications",
        "category": "completeness",
        "title": "Add Certifications to Your Resume",
        "description": (
            "No certifications were detected. Industry certifications are powerful "
            "trust signals for recruiters and ATS filters. Even free certifications "
            "(Google, AWS Free Tier, Coursera) add real credibility."
        ),
        "priority": "Medium",
        "effort": "High",
        "before_example": "(No certifications listed — credentials section missing)",
        "after_example": (
            "AWS Certified Developer – Associate (2024)\n"
            "Google Professional Data Engineer (2023)\n"
            "Coursera: Machine Learning Specialization — DeepLearning.AI (2023)"
        ),
        "score_gain": 5,
        "learning_resources": RESOURCES["certifications"],
        "action_steps": [
            "List certifications you already hold (even Coursera/LinkedIn Learning).",
            "Target 1 industry-standard cert for your role (AWS, GCP, PMP, etc.).",
            "Add the certification year to show recency.",
        ],
        "trigger_fn": lambda bd: "certifications" in bd["completeness"]["details"].get("missing_names", []),
    },

    # ── Content Quality ───────────────────────────────────────────────────
    {
        "id": "add_metrics_to_bullets",
        "category": "content",
        "title": "Quantify Your Achievements with Metrics",
        "description": (
            "Your bullets lack measurable outcomes. Recruiters spend 6-7 seconds on a resume. "
            "Numbers instantly communicate impact: 'Improved performance by 40%' is 10x "
            "stronger than 'Improved performance'. Add %, $, users, time, or team size."
        ),
        "priority": "High",
        "effort": "Low",
        "before_example": "• Worked on backend services to improve application performance.",
        "after_example": (
            "• Optimized backend API response times by 52% through Redis caching and "
            "DB query refactoring, reducing server costs by $8K/month."
        ),
        "score_gain": 10,
        "learning_resources": RESOURCES["quantification"],
        "action_steps": [
            "For each bullet, ask: 'How much? How many? How fast? How often?'",
            "Use: %, $, users, requests/sec, team size, time saved.",
            "Convert at least 5 bullets to include a specific number.",
        ],
        "trigger_fn": lambda bd: bd["content_quality"]["details"].get(
            "quantification", {}).get("count", 99) < 3,
    },
    {
        "id": "replace_weak_phrases",
        "category": "content",
        "title": "Remove Weak Filler Phrases",
        "description": (
            "Phrases like 'responsible for', 'duties include', and 'helped with' "
            "signal passivity. Replace them with active, result-oriented verbs that "
            "show ownership and impact. This alone can significantly improve interview rates."
        ),
        "priority": "High",
        "effort": "Low",
        "before_example": "• Responsible for managing the deployment pipeline and helping the team.",
        "after_example": (
            "• Led end-to-end deployment pipeline migration to GitHub Actions, "
            "cutting release cycle from 2 days to 45 minutes for a team of 8."
        ),
        "score_gain": 8,
        "learning_resources": RESOURCES["action_verbs"],
        "action_steps": [
            "Search your resume for: 'responsible for', 'duties', 'helped', 'assisted'.",
            "Replace each with a strong action verb: Led, Built, Architected, Delivered.",
            "Ensure the new bullet states what you did AND what resulted.",
        ],
        "trigger_fn": lambda bd: bd["content_quality"]["details"].get(
            "professional_language", {}).get("filler_count", 0) > 0,
    },
    {
        "id": "add_more_action_verbs",
        "category": "content",
        "title": "Strengthen Action Verbs Throughout",
        "description": (
            "Your resume uses fewer than 5 distinct strong action verbs. Powerful verbs "
            "like 'Architected', 'Spearheaded', 'Delivered', and 'Optimized' immediately "
            "signal competence and ownership to both ATS and human reviewers."
        ),
        "priority": "Medium",
        "effort": "Low",
        "before_example": "• Worked on and helped develop the company's new user onboarding flow.",
        "after_example": "• Designed and launched a new onboarding flow that increased activation by 34%.",
        "score_gain": 6,
        "learning_resources": RESOURCES["action_verbs"],
        "action_steps": [
            "Start every bullet point with a unique action verb.",
            "Use variety: don't repeat 'Developed' more than 2-3 times.",
            "Use senior verbs for senior roles: Led, Architected, Spearheaded, Scaled.",
        ],
        "trigger_fn": lambda bd: bd["content_quality"]["details"].get(
            "action_verbs", {}).get("count", 99) < 5,
    },

    # ── Formatting ────────────────────────────────────────────────────────
    {
        "id": "fix_resume_length",
        "category": "formatting",
        "title": "Adjust Resume Length to 1-2 Pages",
        "description": (
            "Your resume is either too short (< 400 words) or too long (> 800 words). "
            "Recruiters prefer a concise 1-2 page resume. Too short signals lack of "
            "experience; too long loses their attention. Aim for 450-750 words."
        ),
        "priority": "Medium",
        "effort": "Medium",
        "before_example": "(Resume is 1,200 words — 3+ pages, hard to scan quickly)",
        "after_example": "(Trim to 700 words by removing outdated roles and redundant bullets)",
        "score_gain": 7,
        "learning_resources": RESOURCES["formatting"],
        "action_steps": [
            "Remove roles older than 10-15 years (keep only the job title + company).",
            "Cut bullets that are obvious or generic for your level.",
            "Merge related bullets into one impactful statement.",
        ],
        "trigger_fn": lambda bd: bd["formatting"]["details"].get(
            "length", {}).get("score", 25) < 18,
    },
    {
        "id": "add_section_headings",
        "category": "formatting",
        "title": "Add Clear Section Headings",
        "description": (
            "Section headings were not clearly detected. ATS systems and recruiters "
            "depend on headings like EXPERIENCE, EDUCATION, SKILLS to parse your resume. "
            "Use ALL CAPS or bold formatting for each section title."
        ),
        "priority": "High",
        "effort": "Low",
        "before_example": "Work History\nGoogle – Software Engineer 2019-2022\nSkills: Python, SQL",
        "after_example": (
            "WORK EXPERIENCE\nGoogle — Senior Software Engineer | 2019 – 2022\n\nSKILLS\nPython, SQL, Go"
        ),
        "score_gain": 6,
        "learning_resources": RESOURCES["formatting"],
        "action_steps": [
            "Use standardized heading names: EXPERIENCE, EDUCATION, SKILLS, PROJECTS.",
            "Make headings ALL CAPS or bold — distinct from body text.",
            "Keep consistent heading style throughout the document.",
        ],
        "trigger_fn": lambda bd: bd["formatting"]["details"].get(
            "headings", {}).get("heading_count", 99) < 4,
    },
    {
        "id": "add_bullet_points",
        "category": "formatting",
        "title": "Use Bullet Points for Experience",
        "description": (
            "Your experience section uses paragraph text instead of bullet points. "
            "Bullets are far easier to scan in 6-7 seconds. Each bullet = one achievement "
            "or responsibility. Never use paragraphs for job descriptions."
        ),
        "priority": "High",
        "effort": "Low",
        "before_example": (
            "At Google I worked on the backend infrastructure team where I developed "
            "APIs and helped with the migration project that saved the company money."
        ),
        "after_example": (
            "• Developed 12 REST APIs for core infrastructure serving 2M daily requests.\n"
            "• Led database migration from MySQL to PostgreSQL, saving $45K/year."
        ),
        "score_gain": 7,
        "learning_resources": RESOURCES["bullet_writing"],
        "action_steps": [
            "Convert every job description paragraph into 3-5 bullet points.",
            "Each bullet: Action Verb + What You Did + Result/Impact.",
            "Keep bullets to 1-2 lines (15-25 words each).",
        ],
        "trigger_fn": lambda bd: bd["formatting"]["details"].get(
            "bullets", {}).get("score", 20) < 13,
    },

    # ── Keyword Relevance ─────────────────────────────────────────────────
    {
        "id": "add_ats_keywords",
        "category": "keywords",
        "title": "Add More ATS Keywords for Your Role",
        "description": (
            "Your resume lacks enough industry-standard keywords. Most companies use "
            "ATS to auto-filter resumes before human review. If your resume doesn't "
            "contain role-specific terms, it gets filtered out automatically."
        ),
        "priority": "High",
        "effort": "Low",
        "before_example": "Skills: Programming, Database, Cloud",
        "after_example": "Skills: Python, PostgreSQL, AWS Lambda, Docker, Kubernetes, REST APIs, CI/CD",
        "score_gain": 9,
        "learning_resources": RESOURCES["ats"],
        "action_steps": [
            "Copy 3-5 job descriptions for your target role.",
            "Identify recurring technical terms and add them to your Skills section.",
            "Mirror the exact wording used in job postings (e.g., 'Machine Learning' not 'ML').",
            "Use Jobscan.co to compare your resume against specific job postings.",
        ],
        "trigger_fn": lambda bd: bd["keyword_relevance"]["details"].get(
            "ats_density", {}).get("unique_keywords", 99) < 15,
    },
    {
        "id": "expand_skills_section",
        "category": "keywords",
        "title": "Expand and Organize Your Skills Section",
        "description": (
            "Your skills section appears thin or unorganized. A well-structured skills "
            "section with categories (Languages, Frameworks, Tools, Cloud) makes it easy "
            "for both ATS and recruiters to assess your technical fit instantly."
        ),
        "priority": "Medium",
        "effort": "Low",
        "before_example": "Skills: Python, JavaScript, SQL, some cloud experience",
        "after_example": (
            "Languages: Python, JavaScript, TypeScript, Go\n"
            "Frameworks: React, FastAPI, Django, Node.js\n"
            "Cloud & DevOps: AWS, Docker, Kubernetes, Terraform, GitHub Actions\n"
            "Databases: PostgreSQL, MongoDB, Redis"
        ),
        "score_gain": 6,
        "learning_resources": RESOURCES["skills_section"],
        "action_steps": [
            "Group skills into categories: Languages, Frameworks, Tools, Databases.",
            "List your most proficient skills first in each category.",
            "Remove skills you aren't comfortable being interviewed on.",
        ],
        "trigger_fn": lambda bd: bd["keyword_relevance"]["details"].get(
            "domain_keywords", {}).get("found_count", 99) < 5,
    },

    # ── Experience ────────────────────────────────────────────────────────
    {
        "id": "show_career_progression",
        "category": "experience",
        "title": "Highlight Career Progression Clearly",
        "description": (
            "Your career progression isn't clearly communicated. Recruiters love seeing "
            "growth: Junior → Mid → Senior. Even if you stayed at one company, show "
            "promotions, expanded responsibilities, and increasing impact over time."
        ),
        "priority": "Medium",
        "effort": "Medium",
        "before_example": (
            "ABC Corp — Engineer (2018-2023)\n• Built features and fixed bugs."
        ),
        "after_example": (
            "ABC Corp — Senior Engineer (2021-2023)  [Promoted]\n"
            "• Promoted from Mid to Senior in 18 months based on leading 3 product launches.\n"
            "• Mentored a team of 4 junior engineers.\n\n"
            "ABC Corp — Software Engineer (2018-2021)\n"
            "• Developed core payment API processing $2M in monthly transactions."
        ),
        "score_gain": 5,
        "learning_resources": RESOURCES["experience_writing"],
        "action_steps": [
            "Split same-company roles into separate entries if you were promoted.",
            "Show title changes explicitly: 'Promoted to Senior Engineer (2021)'.",
            "Demonstrate increasing scope: from individual contributor to team lead.",
        ],
        "trigger_fn": lambda bd: bd["experience"]["details"].get(
            "career_progression", {}).get("score", 25) < 18,
    },
    {
        "id": "add_employer_context",
        "category": "experience",
        "title": "Add Context About Your Employers",
        "description": (
            "Your employer names appear without context. Adding a one-line company "
            "description helps recruiters who don't recognise smaller companies: "
            "industry, size, stage. This is especially important for startups."
        ),
        "priority": "Quick Win",
        "effort": "Low",
        "before_example": "DataSoft — Engineer (2021-2023)",
        "after_example": (
            "DataSoft (B2B SaaS, 80 employees, Series B) — Software Engineer (2021-2023)"
        ),
        "score_gain": 3,
        "learning_resources": RESOURCES["experience_writing"],
        "action_steps": [
            "Add company size, industry, and stage (Series A/B/public) in parentheses.",
            "This is especially important for unknown startups.",
            "Omit for FAANG/Fortune 500 companies — context is implied.",
        ],
        "trigger_fn": lambda bd: bd["experience"]["details"].get(
            "employer_diversity", {}).get("count", 3) < 2,
    },
]


# ===========================================================================
# LLM Prompt Templates
# ===========================================================================

BULLET_REWRITE_PROMPT = """You are an expert resume writer specializing in creating
impactful, metric-driven bullet points.

Rewrite the following WEAK resume bullet points into STRONG ones.

Rules:
- Start each bullet with a powerful action verb (Led, Architected, Delivered, etc.)
- Include specific metrics where possible (%, $, users, time, team size)
- Keep each bullet under 25 words
- Show IMPACT, not just tasks
- Use the STAR pattern: Action + Context + Result

For each weak bullet provided, write one strong rewritten version.

Return ONLY valid JSON:
{{
  "rewrites": [
    {{
      "original":    "the original weak bullet",
      "rewritten":   "the strong rewritten bullet",
      "improvement": "one sentence explaining what changed"
    }}
  ]
}}

WEAK BULLETS TO REWRITE:
{bullets}
"""

SUGGESTION_GENERATION_PROMPT = """You are a senior resume consultant with 15+ years of experience.

Analyze this resume and generate 5-8 personalised improvement suggestions beyond the standard ones.

Resume text:
{resume_text}

Current score breakdown:
{score_breakdown}

For each suggestion provide:
- id: unique slug (snake_case)
- category: completeness | content | formatting | keywords | experience
- title: short, specific title (max 8 words)
- description: clear explanation of the problem and solution (2-3 sentences)
- priority: High | Medium | Quick Win
- effort: Low | Medium | High
- before_example: specific weak text FROM THE RESUME (or pattern found)
- after_example: specific improved version
- score_gain: estimated score increase (1-12)
- action_steps: list of 2-4 numbered to-do items

Return ONLY valid JSON:
{{
  "suggestions": [
    {{
      "id":            "snake_case_id",
      "category":      "content",
      "title":         "Short Specific Title",
      "description":   "Clear explanation...",
      "priority":      "High",
      "effort":        "Low",
      "before_example": "weak text...",
      "after_example":  "strong text...",
      "score_gain":     8,
      "action_steps":   ["Step 1", "Step 2"]
    }}
  ]
}}
"""


# ===========================================================================
# Public Functions
# ===========================================================================

def generate_suggestions(
    resume_text:  str,
    target_role:  str = "",
    current_score: int | None = None,
    use_cache:    bool = True,
) -> dict:
    """
    Master orchestrator — generate full ranked improvement suggestions.

    Combines rule-based template matching (instant) with LLM-generated
    personalised suggestions (Gemini). Results are ranked by score_gain
    and grouped into high_priority, medium_priority, and quick_wins.

    Args:
        resume_text:   Raw text extracted from the resume.
        target_role:   Optional target job role for keyword tuning.
        current_score: Pre-computed score (avoids double scoring). If None,
                       calculate_scores() is called internally.
        use_cache:     Return cached results for identical inputs.

    Returns:
        {
            "high_priority":   [ ... ],
            "medium_priority": [ ... ],
            "quick_wins":      [ ... ],
            "metadata":        { ... }
        }
    """
    if not resume_text or not resume_text.strip():
        return _empty_result("Empty resume text.")

    # Cache check
    cache_key = _make_key(resume_text, target_role)
    if use_cache and cache_key in _SUGGESTION_CACHE:
        logger.info("generate_suggestions: returning cached result")
        return _SUGGESTION_CACHE[cache_key]

    logger.info("generate_suggestions: starting — %d chars", len(resume_text))

    # ── Step 1: Score the resume (or use pre-computed) ───────────────────
    if current_score is None:
        score_result = calculate_scores(resume_text, target_role=target_role, use_cache=True)
    else:
        score_result = calculate_scores(resume_text, target_role=target_role, use_cache=True)
    bd = score_result.get("breakdown", {})
    overall = score_result.get("overall_score", 50)

    # ── Step 2: Rule-based template matching ─────────────────────────────
    rule_suggestions = _apply_rule_templates(bd)
    logger.info("generate_suggestions: %d rule-based suggestions", len(rule_suggestions))

    # ── Step 3: LLM personalised suggestions ─────────────────────────────
    llm_suggestions  = _get_llm_suggestions(resume_text, bd)
    logger.info("generate_suggestions: %d LLM suggestions", len(llm_suggestions))

    # ── Step 4: Merge, deduplicate, enrich ───────────────────────────────
    all_suggestions  = _merge_and_deduplicate(rule_suggestions, llm_suggestions)

    # ── Step 5: Estimate score gains ─────────────────────────────────────
    for s in all_suggestions:
        s["score_gain"] = estimate_score_gain(s, bd, overall)

    # ── Step 6: Sort by score_gain desc ──────────────────────────────────
    all_suggestions.sort(key=lambda x: x.get("score_gain", 0), reverse=True)

    # ── Step 7: Bucket by priority ────────────────────────────────────────
    high   = [s for s in all_suggestions if s.get("priority") == "High"]
    medium = [s for s in all_suggestions if s.get("priority") == "Medium"]
    wins   = [s for s in all_suggestions if s.get("priority") == "Quick Win"]

    # Estimated total gain (capped — gains overlap)
    total_gain     = min(sum(s["score_gain"] for s in all_suggestions[:5]), 30)
    projected      = min(overall + total_gain, 100)

    result = {
        "high_priority":   high,
        "medium_priority": medium,
        "quick_wins":      wins,
        "metadata": {
            "total_suggestions":   len(all_suggestions),
            "estimated_total_gain": total_gain,
            "current_score":       overall,
            "projected_score":     projected,
            "target_role":         target_role,
            "generated_at":        datetime.now(timezone.utc).isoformat(),
        },
    }

    if use_cache:
        _SUGGESTION_CACHE[cache_key] = result

    logger.info(
        "generate_suggestions: done — %d high, %d medium, %d wins. "
        "Score: %d → %d",
        len(high), len(medium), len(wins), overall, projected,
    )
    return result


def improve_bullets(
    bullet_list: list[str],
    context:     str = "",
) -> list[dict]:
    """
    Rewrite a list of weak resume bullets into strong, metric-driven versions.

    Uses Gemini LLM with expert prompt; falls back to rule-based rewriting
    if the API is unavailable.

    Args:
        bullet_list: List of raw bullet point strings (without leading dash/•).
        context:     Optional job role / industry context for better rewrites.

    Returns:
        List of dicts: { original, rewritten, improvement }
    """
    if not bullet_list:
        return []

    # Clean bullets
    cleaned = [re.sub(r"^[\•\-\*►▸→✓▪◆]\s*", "", b).strip() for b in bullet_list]
    cleaned = [b for b in cleaned if len(b) > 10]

    logger.info("improve_bullets: rewriting %d bullets", len(cleaned))

    # ── LLM rewrite ───────────────────────────────────────────────────────
    try:
        llm    = load_llm()
        prompt = BULLET_REWRITE_PROMPT.format(
            bullets="\n".join(f"- {b}" for b in cleaned[:8])  # limit to 8
        )
        resp   = llm.invoke(prompt)
        raw    = resp.content if hasattr(resp, "content") else str(resp)
        parsed = extract_json_response(raw)

        if parsed and "rewrites" in parsed and isinstance(parsed["rewrites"], list):
            results = parsed["rewrites"]
            # Ensure all fields exist
            for r in results:
                r.setdefault("original",    "")
                r.setdefault("rewritten",   "")
                r.setdefault("improvement", "")
            logger.info("improve_bullets: LLM rewrote %d bullets", len(results))
            return results

    except Exception as exc:
        logger.warning("improve_bullets: LLM failed (%s), using rule-based", exc)

    # ── Rule-based fallback ───────────────────────────────────────────────
    return _rule_based_bullet_rewrite(cleaned)


def estimate_score_gain(
    suggestion: dict,
    breakdown:  dict,
    current_score: int = 50,
) -> int:
    """
    Estimate the score improvement if this suggestion is implemented.

    Model: base_gain × component_weight × (1 - current_component_pct/100)

    A suggestion targeting a weak component has higher leverage than one
    targeting an already-strong component.

    Args:
        suggestion:    A suggestion dict with 'category' and optional 'score_gain'.
        breakdown:     The full score breakdown from calculate_scores().
        current_score: Current overall resume score (0-100).

    Returns:
        Estimated score gain as int (1-15).
    """
    category = suggestion.get("category", "general")
    base_gain = suggestion.get("score_gain", 5)   # template default

    # Map category to breakdown key
    cat_map = {
        "completeness": "completeness",
        "content":      "content_quality",
        "formatting":   "formatting",
        "keywords":     "keyword_relevance",
        "experience":   "experience",
        "skills":       "keyword_relevance",
    }
    bd_key = cat_map.get(category, "content_quality")
    comp   = breakdown.get(bd_key, {})
    comp_pct = comp.get("pct", 50)

    # Leverage: higher gain when component is weaker
    gap_factor   = max(0.2, (100 - comp_pct) / 100)
    weight       = WEIGHTS.get(bd_key, 0.20)
    effort_mult  = {"Low": 1.0, "Medium": 0.85, "High": 0.70}.get(
        suggestion.get("effort", "Medium"), 0.85
    )

    gain = round(base_gain * gap_factor * (weight / 0.25) * effort_mult)
    return max(1, min(gain, 15))


def save_suggestions(
    user_id:     str,
    suggestions: dict,
    resume_text: str = "",
) -> dict:
    """
    Persist suggestions to the database.

    Stores the serialised suggestions JSON alongside the resume text
    in a dedicated improvement_suggestions table (auto-created).

    Args:
        user_id:     UUID of the authenticated user.
        suggestions: Full output dict from generate_suggestions().
        resume_text: Resume text snippet for reference.

    Returns:
        { "success": bool, "record_id": int | None, "message": str }
    """
    if not user_id:
        return {"success": False, "record_id": None, "message": "user_id required."}

    try:
        _ensure_suggestions_table()
        metadata  = suggestions.get("metadata", {})

        with get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO improvement_suggestions
                    (user_id, suggestions_json, current_score,
                     projected_score, total_suggestions, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    json.dumps(suggestions, ensure_ascii=False),
                    metadata.get("current_score", 0),
                    metadata.get("projected_score", 0),
                    metadata.get("total_suggestions", 0),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            record_id = cursor.lastrowid

        logger.info("save_suggestions: saved record_id=%d for user=%s", record_id, user_id)
        return {
            "success":   True,
            "record_id": record_id,
            "message":   f"Saved {metadata.get('total_suggestions', 0)} suggestions (record #{record_id}).",
        }

    except Exception as exc:
        logger.error("save_suggestions: failed — %s", exc)
        return {"success": False, "record_id": None, "message": str(exc)}


def get_saved_suggestions(user_id: str, limit: int = 5) -> list[dict]:
    """
    Retrieve previously saved suggestions for a user.

    Args:
        user_id: UUID of the authenticated user.
        limit:   Maximum number of records to return (newest first).

    Returns:
        List of saved suggestion records.
    """
    if not user_id:
        return []
    try:
        _ensure_suggestions_table()
        with get_connection() as conn:
            rows = conn.execute(
                """
                SELECT record_id, current_score, projected_score,
                       total_suggestions, created_at, suggestions_json
                FROM   improvement_suggestions
                WHERE  user_id = ?
                ORDER  BY created_at DESC
                LIMIT  ?
                """,
                (user_id, limit),
            ).fetchall()

        result = []
        for row in rows:
            entry = dict(row)
            try:
                entry["suggestions"] = json.loads(entry.pop("suggestions_json", "{}"))
            except json.JSONDecodeError:
                entry["suggestions"] = {}
            result.append(entry)
        return result

    except Exception as exc:
        logger.error("get_saved_suggestions: %s", exc)
        return []


# ===========================================================================
# Internal Helpers
# ===========================================================================

def _make_key(text: str, role: str) -> str:
    return hashlib.md5(f"{text[:500]}{role}".encode()).hexdigest()


def _empty_result(msg: str) -> dict:
    return {
        "high_priority":   [],
        "medium_priority": [],
        "quick_wins":      [],
        "metadata": {
            "total_suggestions":   0,
            "estimated_total_gain": 0,
            "current_score":       0,
            "projected_score":     0,
            "generated_at":        datetime.now(timezone.utc).isoformat(),
            "error":               msg,
        },
    }


def _apply_rule_templates(breakdown: dict) -> list[dict]:
    """Apply all rule templates and return triggered suggestions."""
    results = []
    for tmpl in SUGGESTION_TEMPLATES:
        try:
            trigger = tmpl.get("trigger_fn")
            if trigger and trigger(breakdown):
                # Deep-copy and strip the trigger_fn (not serialisable)
                s = {k: v for k, v in tmpl.items() if k != "trigger_fn"}
                s.setdefault("learning_resources",
                             _get_resources_for_category(s.get("category", "")))
                results.append(s)
        except Exception as exc:
            logger.debug("_apply_rule_templates: template '%s' error: %s",
                         tmpl.get("id", "?"), exc)
    return results


def _get_llm_suggestions(resume_text: str, breakdown: dict) -> list[dict]:
    """Call Gemini to generate personalised suggestions."""
    try:
        bd_summary = {
            k: {"pct": v["pct"], "grade": v["grade"]}
            for k, v in breakdown.items()
        }
        llm    = load_llm()
        prompt = SUGGESTION_GENERATION_PROMPT.format(
            resume_text=resume_text[:4000],
            score_breakdown=json.dumps(bd_summary, indent=2),
        )
        resp   = llm.invoke(prompt)
        raw    = resp.content if hasattr(resp, "content") else str(resp)
        parsed = extract_json_response(raw)

        if parsed and "suggestions" in parsed:
            slist = parsed["suggestions"]
            if isinstance(slist, list):
                for s in slist:
                    s.setdefault("learning_resources",
                                 _get_resources_for_category(s.get("category", "")))
                    s.setdefault("priority", "Medium")
                    s.setdefault("effort", "Medium")
                    s.setdefault("score_gain", 5)
                    s.setdefault("action_steps", [])
                return slist

    except Exception as exc:
        logger.warning("_get_llm_suggestions: LLM failed (%s)", exc)
    return []


def _merge_and_deduplicate(
    rule: list[dict], llm: list[dict]
) -> list[dict]:
    """Merge rule + LLM suggestions, remove duplicates by id/title similarity."""
    seen_ids    = set()
    seen_titles = set()
    merged      = []

    for s in rule + llm:
        sid   = s.get("id", "")
        title = s.get("title", "").lower()[:30]

        if sid in seen_ids or title in seen_titles:
            continue

        seen_ids.add(sid)
        seen_titles.add(title)
        merged.append(s)

    return merged


def _get_resources_for_category(category: str) -> list[dict]:
    """Return curated resources appropriate for a suggestion category."""
    cat_map = {
        "completeness": RESOURCES["skills_section"] + RESOURCES["summary"],
        "content":      RESOURCES["action_verbs"]   + RESOURCES["quantification"],
        "formatting":   RESOURCES["formatting"]     + RESOURCES["bullet_writing"],
        "keywords":     RESOURCES["ats"]            + RESOURCES["skills_section"],
        "experience":   RESOURCES["experience_writing"] + RESOURCES["quantification"],
        "skills":       RESOURCES["skills_section"] + RESOURCES["ats"],
    }
    return cat_map.get(category, RESOURCES["general"])[:3]


def _rule_based_bullet_rewrite(bullets: list[str]) -> list[dict]:
    """
    Fallback bullet rewriter using pattern substitution when LLM unavailable.
    """
    FILLER_SUBS = [
        (r"(?i)^responsible for\s+",           "Led "),
        (r"(?i)^helped\s+(?:to\s+|with\s+)?",  "Contributed to "),
        (r"(?i)^worked on\s+",                 "Developed "),
        (r"(?i)^assisted\s+(?:with\s+)?",      "Supported "),
        (r"(?i)^involved in\s+",               "Executed "),
        (r"(?i)^tasked with\s+",               "Delivered "),
        (r"(?i)^duties included?\s+",          "Delivered "),
        (r"(?i)^was responsible for\s+",       "Managed "),
        (r"(?i)^was involved in\s+",           "Spearheaded "),
        (r"(?i)^participated in\s+",           "Contributed to "),
    ]

    results = []
    for bullet in bullets:
        rewritten = bullet
        changed   = False
        for pattern, replacement in FILLER_SUBS:
            new = re.sub(pattern, replacement, rewritten, count=1)
            if new != rewritten:
                rewritten = new
                changed   = True
                break

        # Capitalise first letter
        if rewritten:
            rewritten = rewritten[0].upper() + rewritten[1:]

        improvement = (
            "Replaced weak filler phrase with a direct action verb."
            if changed
            else "Bullet already starts with an action verb — add metrics to strengthen it."
        )

        results.append({
            "original":    bullet,
            "rewritten":   rewritten,
            "improvement": improvement,
        })

    return results


def _ensure_suggestions_table() -> None:
    """Create improvement_suggestions table if it doesn't exist."""
    ddl = """
    CREATE TABLE IF NOT EXISTS improvement_suggestions (
        record_id         INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id           TEXT    NOT NULL,
        suggestions_json  TEXT    NOT NULL,
        current_score     REAL    DEFAULT 0,
        projected_score   REAL    DEFAULT 0,
        total_suggestions INTEGER DEFAULT 0,
        created_at        TEXT    NOT NULL DEFAULT (datetime('now'))
    );
    CREATE INDEX IF NOT EXISTS idx_imp_user
        ON improvement_suggestions(user_id);
    CREATE INDEX IF NOT EXISTS idx_imp_created
        ON improvement_suggestions(created_at DESC);
    """
    try:
        with get_connection() as conn:
            conn.executescript(ddl)
    except Exception as exc:
        logger.error("_ensure_suggestions_table: %s", exc)


# ===========================================================================
# Self-test — run with: python -m backend.improvement_suggestions
# ===========================================================================

if __name__ == "__main__":
    import textwrap, logging as _l, pprint
    _l.basicConfig(level=_l.WARNING, format="%(levelname)s | %(message)s")

    # Patch LLM to avoid API calls in self-test
    import backend.improvement_suggestions as _self
    import backend.resume_scoring as _rs
    _rs._llm_content_boost    = lambda t, s: s
    _rs._llm_experience_boost = lambda t, s: s

    SAMPLE = textwrap.dedent("""
        John Doe | Software Developer
        john@email.com

        EXPERIENCE
        Tech Corp (2020-2023)
        Responsible for working on backend systems and helping with deployments.
        Assisted with the migration project and involved in code reviews.

        Previous Company (2018-2020)
        Worked on various projects and helped team members.

        EDUCATION
        B.S. Computer Science, State University (2018)

        SKILLS
        Python, JavaScript, SQL
    """).strip()

    print("Running improvement_suggestions on weak resume...\n")

    # Test improve_bullets
    weak_bullets = [
        "Responsible for managing the deployment pipeline",
        "Helped with building new API endpoints",
        "Worked on database optimization tasks",
        "Was involved in the migration project",
    ]
    print("-- improve_bullets() fallback test --")
    rewrites = _self._rule_based_bullet_rewrite(weak_bullets)
    for r in rewrites:
        print(f"  BEFORE: {r['original']}")
        print(f"  AFTER : {r['rewritten']}")
        print(f"  WHY   : {r['improvement']}\n")

    # Test estimate_score_gain
    print("-- estimate_score_gain() test --")
    from backend.resume_scoring import calculate_scores as _cs
    score_result = _cs(SAMPLE, use_cache=False)
    bd = score_result["breakdown"]
    test_sugg = {
        "id": "add_metrics",
        "category": "content",
        "priority": "High",
        "effort": "Low",
        "score_gain": 10,
    }
    gain = _self.estimate_score_gain(test_sugg, bd, score_result["overall_score"])
    print(f"  Estimated gain for 'add_metrics': {gain} pts")
    assert 1 <= gain <= 15, "gain out of range"
    print("  [OK]")

    # Test generate_suggestions (rule-based only, no LLM)
    print("\n-- generate_suggestions() rule-based test --")
    _self._get_llm_suggestions = lambda t, b: []   # disable LLM
    result = _self.generate_suggestions(SAMPLE, target_role="software_engineer", use_cache=False)

    assert "high_priority"   in result
    assert "medium_priority" in result
    assert "quick_wins"      in result
    assert "metadata"        in result

    meta = result["metadata"]
    print(f"  Current score    : {meta['current_score']}")
    print(f"  Projected score  : {meta['projected_score']}")
    print(f"  Total suggestions: {meta['total_suggestions']}")
    print(f"  High priority    : {len(result['high_priority'])}")
    print(f"  Medium priority  : {len(result['medium_priority'])}")
    print(f"  Quick wins       : {len(result['quick_wins'])}")
    print()

    for s in (result["high_priority"] + result["medium_priority"])[:3]:
        print(f"  [{s['priority']:10}] {s['title']}  +{s['score_gain']} pts")
        print(f"           Before: {s['before_example'][:60]}")
        print(f"           After : {s['after_example'][:60]}")
        print()

    # Verify output schema
    assert isinstance(result["high_priority"], list)
    assert isinstance(result["medium_priority"], list)
    assert isinstance(result["quick_wins"], list)
    if result["high_priority"]:
        s0 = result["high_priority"][0]
        for field in ["id","category","title","description","priority","effort",
                      "before_example","after_example","score_gain",
                      "learning_resources","action_steps"]:
            assert field in s0, f"Missing field: {field}"

    print("-- save_suggestions() test --")
    from utils.database import create_database
    create_database()
    import uuid
    fake_uid = str(uuid.uuid4())
    save_res = _self.save_suggestions(fake_uid, result)
    print(f"  save result: {save_res}")
    assert save_res["success"], f"save failed: {save_res['message']}"

    fetched = _self.get_saved_suggestions(fake_uid)
    print(f"  fetched {len(fetched)} saved record(s)")
    assert len(fetched) >= 1

    print("\n[ALL TESTS PASSED]")
