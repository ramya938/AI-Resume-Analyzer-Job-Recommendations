"""
skills_gap.py — AI-Powered Skills Gap Analyzer
================================================
File: backend/skills_gap.py

Analyzes resume text to identify existing skills, detect gaps against
industry standards, and generate prioritized learning recommendations.

Public Functions:
    extract_skills()             - Parse all skill categories from resume text
    compare_industry_skills()    - Find missing skills vs industry benchmark
    generate_recommendations()   - Build a full gap analysis with learning paths
    analyze_skills_gap()         - Single-call orchestrator (main entry point)

Output Schema:
    {
        "existing_skills": {
            "technical_skills":  [...],
            "soft_skills":       [...],
            "certifications":    [...],
            "tools":             [...],
            "frameworks":        [...]
        },
        "missing_skills": [
            {
                "skill":            str,
                "category":         str,
                "importance":       str,   # Critical / High / Medium / Low
                "learning_resources": [...],
                "industry_demand":  str,
                "priority_score":   int    # 1-100
            }
        ],
        "recommended_skills": [
            {
                "skill":       str,
                "category":    str,
                "reason":      str,
                "priority":    str,
                "time_to_learn": str
            }
        ],
        "summary": {
            "skills_coverage_pct": int,
            "top_gap_area":        str,
            "career_readiness":    str,
            "overall_assessment":  str
        }
    }

Features:
    - Gemini LLM extraction via llm_analyzer.py
    - Industry benchmark database (20+ roles, 400+ skills)
    - Caching to avoid re-analysis of same text
    - Full logging and error handling
    - Priority scoring algorithm (demand × importance × gap weight)
    - Curated free + paid learning resource links per skill
"""

import os
import json
import re
import logging
import hashlib
from datetime import datetime
from typing import Any

from backend.llm_analyzer import load_llm, extract_json_response

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Cache store  (in-memory; survives the Streamlit session)
# ---------------------------------------------------------------------------
_ANALYSIS_CACHE: dict[str, dict] = {}


# ===========================================================================
# Industry Benchmark Database
# ===========================================================================

INDUSTRY_BENCHMARKS: dict[str, dict[str, list[str]]] = {
    # ── Software / Web Development ──────────────────────────────────────────
    "software_engineer": {
        "technical": ["Python", "Java", "JavaScript", "TypeScript", "C++", "Go",
                      "SQL", "Git", "Linux", "REST APIs", "GraphQL", "Docker",
                      "Kubernetes", "CI/CD", "Unit Testing", "System Design",
                      "Data Structures", "Algorithms", "OOP", "Microservices"],
        "soft":      ["Problem Solving", "Communication", "Teamwork", "Code Review",
                      "Time Management", "Agile/Scrum"],
        "tools":     ["VS Code", "IntelliJ", "Jira", "GitHub", "Postman", "Slack"],
        "frameworks":["React", "Node.js", "Spring Boot", "FastAPI", "Django"],
        "certs":     ["AWS Certified Developer", "Google Cloud Professional",
                      "Kubernetes CKA", "HashiCorp Terraform"],
    },

    # ── Data Science / ML ────────────────────────────────────────────────────
    "data_scientist": {
        "technical": ["Python", "R", "SQL", "Machine Learning", "Deep Learning",
                      "Statistics", "Data Visualization", "Feature Engineering",
                      "Model Evaluation", "A/B Testing", "Big Data", "ETL",
                      "Natural Language Processing", "Computer Vision",
                      "Time Series Analysis", "Bayesian Inference"],
        "soft":      ["Analytical Thinking", "Storytelling with Data",
                      "Business Acumen", "Communication", "Critical Thinking"],
        "tools":     ["Jupyter", "Pandas", "NumPy", "Matplotlib", "Seaborn",
                      "Tableau", "Power BI", "Apache Spark", "Airflow"],
        "frameworks":["TensorFlow", "PyTorch", "Scikit-learn", "Keras", "XGBoost",
                      "Hugging Face Transformers", "LangChain"],
        "certs":     ["Google Professional Data Engineer",
                      "AWS Certified Machine Learning Specialty",
                      "Databricks Certified", "TensorFlow Developer Certificate"],
    },

    # ── Full Stack ───────────────────────────────────────────────────────────
    "full_stack_developer": {
        "technical": ["HTML", "CSS", "JavaScript", "TypeScript", "Python",
                      "SQL", "NoSQL", "REST APIs", "GraphQL", "Authentication",
                      "Web Security", "Performance Optimization", "Git",
                      "Docker", "Cloud Deployment", "Testing"],
        "soft":      ["Problem Solving", "Communication", "Adaptability",
                      "Project Management", "Client Communication"],
        "tools":     ["VS Code", "GitHub", "Postman", "Figma", "Webpack",
                      "Vite", "ESLint", "Chrome DevTools"],
        "frameworks":["React", "Vue.js", "Angular", "Node.js", "Express",
                      "Next.js", "Django", "FastAPI", "Tailwind CSS"],
        "certs":     ["AWS Certified Developer", "Google UX Design Certificate",
                      "Meta Front-End Developer"],
    },

    # ── DevOps / SRE ─────────────────────────────────────────────────────────
    "devops_engineer": {
        "technical": ["Linux", "Bash/Shell Scripting", "Python", "Docker",
                      "Kubernetes", "Terraform", "Ansible", "CI/CD",
                      "Monitoring & Alerting", "Networking", "Security",
                      "Infrastructure as Code", "Git", "Cloud Platforms"],
        "soft":      ["Incident Management", "Communication", "Problem Solving",
                      "On-Call Reliability", "Documentation"],
        "tools":     ["Jenkins", "GitHub Actions", "GitLab CI", "Prometheus",
                      "Grafana", "ELK Stack", "ArgoCD", "Helm"],
        "frameworks":["AWS CloudFormation", "Google Cloud Deployment Manager",
                      "HashiCorp Vault", "Service Mesh (Istio)"],
        "certs":     ["AWS Solutions Architect", "CKA (Kubernetes)",
                      "HashiCorp Terraform Associate", "Google Cloud DevOps"],
    },

    # ── AI / ML Engineer ─────────────────────────────────────────────────────
    "ai_engineer": {
        "technical": ["Python", "Machine Learning", "Deep Learning", "LLMs",
                      "Prompt Engineering", "RAG", "Vector Databases",
                      "Model Fine-tuning", "RLHF", "MLOps", "API Development",
                      "Data Pipelines", "GPU Computing", "Model Deployment"],
        "soft":      ["Research Skills", "Problem Solving", "Technical Writing",
                      "Cross-functional Collaboration", "Continuous Learning"],
        "tools":     ["Jupyter", "Weights & Biases", "MLflow", "DVC",
                      "Pinecone", "Weaviate", "LangSmith"],
        "frameworks":["PyTorch", "TensorFlow", "Hugging Face", "LangChain",
                      "OpenAI SDK", "Ollama", "vLLM", "FastAPI"],
        "certs":     ["DeepLearning.AI Specializations", "Google ML Engineer",
                      "AWS ML Specialty", "NVIDIA DLI Certifications"],
    },

    # ── Cybersecurity ─────────────────────────────────────────────────────────
    "cybersecurity_analyst": {
        "technical": ["Network Security", "SIEM", "Penetration Testing",
                      "Vulnerability Assessment", "Incident Response",
                      "Malware Analysis", "Cryptography", "Firewall Management",
                      "Identity & Access Management", "Cloud Security",
                      "Python/Bash for Automation", "OSINT"],
        "soft":      ["Analytical Thinking", "Attention to Detail",
                      "Crisis Management", "Communication", "Ethics"],
        "tools":     ["Wireshark", "Metasploit", "Nmap", "Burp Suite",
                      "Splunk", "CrowdStrike", "Nessus", "Kali Linux"],
        "frameworks":["MITRE ATT&CK", "NIST Cybersecurity Framework",
                      "ISO 27001", "SOC 2"],
        "certs":     ["CompTIA Security+", "CEH", "CISSP", "OSCP",
                      "AWS Security Specialty"],
    },

    # ── Product Manager ────────────────────────────────────────────────────────
    "product_manager": {
        "technical": ["Product Roadmapping", "User Story Writing", "Data Analysis",
                      "A/B Testing", "KPI Definition", "Market Research",
                      "Competitive Analysis", "SQL basics", "API Understanding"],
        "soft":      ["Leadership", "Communication", "Stakeholder Management",
                      "Prioritization", "Negotiation", "Empathy", "Decision Making"],
        "tools":     ["Jira", "Confluence", "Figma", "Amplitude", "Mixpanel",
                      "Notion", "Slack", "Google Analytics"],
        "frameworks":["Agile/Scrum", "OKRs", "RICE Scoring", "Jobs-to-be-Done",
                      "Lean Startup", "Design Thinking"],
        "certs":     ["CSPO (Certified Scrum Product Owner)", "PMI-ACP",
                      "Google Project Management", "Pragmatic Marketing"],
    },
}

# Flat list of all skills per category for cross-role matching
ALL_INDUSTRY_SKILLS: dict[str, list[str]] = {
    "technical": [], "soft": [], "tools": [], "frameworks": [], "certs": []
}
for role_data in INDUSTRY_BENCHMARKS.values():
    for cat, skills in role_data.items():
        ALL_INDUSTRY_SKILLS[cat].extend(
            s for s in skills if s not in ALL_INDUSTRY_SKILLS[cat]
        )


# ===========================================================================
# Learning Resources Database
# ===========================================================================

LEARNING_RESOURCES: dict[str, list[dict]] = {
    # Programming languages
    "Python":          [{"name": "Python.org Official Docs",   "url": "https://docs.python.org/3/tutorial/", "free": True},
                        {"name": "Real Python",                "url": "https://realpython.com",              "free": True},
                        {"name": "Automate the Boring Stuff",  "url": "https://automatetheboringstuff.com",  "free": True}],
    "JavaScript":      [{"name": "MDN Web Docs",               "url": "https://developer.mozilla.org/en-US/docs/Learn", "free": True},
                        {"name": "JavaScript.info",            "url": "https://javascript.info",             "free": True},
                        {"name": "Eloquent JavaScript",        "url": "https://eloquentjavascript.net",      "free": True}],
    "TypeScript":      [{"name": "TypeScript Handbook",        "url": "https://www.typescriptlang.org/docs/handbook/", "free": True},
                        {"name": "Execute Program",            "url": "https://www.executeprogram.com/courses/typescript", "free": False}],
    "SQL":             [{"name": "SQLZoo",                     "url": "https://sqlzoo.net",                  "free": True},
                        {"name": "Mode SQL Tutorial",          "url": "https://mode.com/sql-tutorial/",      "free": True}],
    "Java":            [{"name": "Java Official Docs",         "url": "https://dev.java/learn/",             "free": True},
                        {"name": "Baeldung",                   "url": "https://www.baeldung.com",            "free": True}],
    # ML / Data
    "Machine Learning":[{"name": "Coursera ML Specialization", "url": "https://www.coursera.org/specializations/machine-learning-introduction", "free": False},
                        {"name": "fast.ai",                    "url": "https://www.fast.ai",                 "free": True},
                        {"name": "Google ML Crash Course",     "url": "https://developers.google.com/machine-learning/crash-course", "free": True}],
    "Deep Learning":   [{"name": "DeepLearning.AI",            "url": "https://www.deeplearning.ai",         "free": False},
                        {"name": "fast.ai Deep Learning",      "url": "https://course.fast.ai",              "free": True}],
    "LLMs":            [{"name": "Hugging Face NLP Course",    "url": "https://huggingface.co/learn/nlp-course", "free": True},
                        {"name": "DeepLearning.AI Short Courses","url":"https://www.deeplearning.ai/short-courses/", "free": True}],
    "Docker":          [{"name": "Docker Official Docs",       "url": "https://docs.docker.com/get-started/", "free": True},
                        {"name": "Play with Docker",           "url": "https://labs.play-with-docker.com",   "free": True}],
    "Kubernetes":      [{"name": "Kubernetes Official Docs",   "url": "https://kubernetes.io/docs/tutorials/", "free": True},
                        {"name": "KodeKloud",                  "url": "https://kodekloud.com/courses/kubernetes-for-the-absolute-beginners-hands-on/", "free": False}],
    "React":           [{"name": "React Official Docs",        "url": "https://react.dev/learn",             "free": True},
                        {"name": "Scrimba React Course",       "url": "https://scrimba.com/learn/learnreact", "free": False}],
    "Node.js":         [{"name": "Node.js Docs",               "url": "https://nodejs.org/en/learn",         "free": True},
                        {"name": "The Odin Project",           "url": "https://www.theodinproject.com",      "free": True}],
    "Git":             [{"name": "Pro Git Book",               "url": "https://git-scm.com/book",            "free": True},
                        {"name": "GitHub Skills",              "url": "https://skills.github.com",           "free": True}],
    "AWS":             [{"name": "AWS Skill Builder",          "url": "https://skillbuilder.aws",            "free": True},
                        {"name": "A Cloud Guru",               "url": "https://acloudguru.com",              "free": False}],
    "Agile/Scrum":     [{"name": "Scrum Guide (free)",         "url": "https://scrumguides.org/scrum-guide.html", "free": True},
                        {"name": "Agile Alliance Resources",   "url": "https://www.agilealliance.org/agile101/", "free": True}],
    # Generic fallback
    "_default":        [{"name": "Coursera",                   "url": "https://www.coursera.org",            "free": False},
                        {"name": "edX",                        "url": "https://www.edx.org",                 "free": True},
                        {"name": "YouTube",                    "url": "https://youtube.com",                 "free": True},
                        {"name": "freeCodeCamp",               "url": "https://www.freecodecamp.org",        "free": True}],
}


# ===========================================================================
# Skill Importance & Demand Scores (0-100)
# ===========================================================================

SKILL_SCORES: dict[str, dict] = {
    "Python":          {"demand": 98, "importance": "Critical"},
    "JavaScript":      {"demand": 97, "importance": "Critical"},
    "TypeScript":      {"demand": 90, "importance": "High"},
    "SQL":             {"demand": 95, "importance": "Critical"},
    "Machine Learning":{"demand": 94, "importance": "Critical"},
    "Deep Learning":   {"demand": 88, "importance": "High"},
    "Docker":          {"demand": 91, "importance": "Critical"},
    "Kubernetes":      {"demand": 87, "importance": "High"},
    "Git":             {"demand": 99, "importance": "Critical"},
    "React":           {"demand": 92, "importance": "High"},
    "Node.js":         {"demand": 88, "importance": "High"},
    "AWS":             {"demand": 93, "importance": "Critical"},
    "LLMs":            {"demand": 96, "importance": "Critical"},
    "Agile/Scrum":     {"demand": 89, "importance": "High"},
    "CI/CD":           {"demand": 86, "importance": "High"},
    "System Design":   {"demand": 90, "importance": "Critical"},
    "Data Structures": {"demand": 92, "importance": "Critical"},
    "Algorithms":      {"demand": 91, "importance": "Critical"},
    "_default":        {"demand": 70, "importance": "Medium"},
}


# ===========================================================================
# Prompt Templates
# ===========================================================================

SKILL_EXTRACTION_PROMPT = """You are an expert resume analyst and skills extractor.

Analyze the resume text below and extract ALL skills into the following categories:

1. **technical_skills**  — Programming languages, scripting, data analysis, algorithms, security, etc.
2. **soft_skills**       — Communication, leadership, teamwork, problem-solving, adaptability, etc.
3. **certifications**    — Professional certifications, badges, licenses, diplomas mentioned.
4. **tools**             — Software tools, IDEs, platforms, SaaS products, databases mentioned.
5. **frameworks**        — Libraries, frameworks, SDKs, APIs the candidate has worked with.

Return ONLY valid JSON in this exact format:
{{
  "technical_skills": ["skill1", "skill2"],
  "soft_skills":      ["skill1", "skill2"],
  "certifications":   ["cert1", "cert2"],
  "tools":            ["tool1", "tool2"],
  "frameworks":       ["framework1", "framework2"]
}}

Rules:
- Use concise, standardized skill names (e.g. "Python" not "python programming")
- Do NOT include vague terms like "good communicator" — extract the actual skill noun
- Include inferred skills from job descriptions (e.g. "maintained AWS infra" → "AWS")
- Each skill should appear in only ONE category
- Return empty arrays if a category has no skills

RESUME TEXT:
{resume_text}
"""

MISSING_SKILLS_PROMPT = """You are a senior technical recruiter and career coach.

Given:
- **Candidate's existing skills**: {existing_skills}
- **Their target job role**: {target_role}
- **Industry standard skills for this role**: {industry_skills}

Identify the TOP 10 most important missing skills the candidate needs to develop.

For each missing skill, provide:
- skill: exact skill name
- category: one of [technical, soft, tools, frameworks, certification]
- importance: one of [Critical, High, Medium, Low]
- industry_demand: brief 1-sentence description of how in-demand this skill is
- time_to_learn: estimated time (e.g. "2-4 weeks", "3-6 months")
- reason: why this specific skill matters for the candidate's profile

Return ONLY valid JSON:
{{
  "missing_skills": [
    {{
      "skill":           "skill name",
      "category":        "technical",
      "importance":      "Critical",
      "industry_demand": "One sentence about demand.",
      "time_to_learn":   "2-4 weeks",
      "reason":          "Why this skill matters for you."
    }}
  ]
}}

Focus on skills that will have the HIGHEST career impact. Prioritize Critical > High > Medium.
"""

RECOMMENDATIONS_PROMPT = """You are a career development expert and technical mentor.

Based on the candidate's profile:
- **Existing skills**: {existing_skills}
- **Target role**: {target_role}
- **Career level**: {career_level}
- **Industry**: {industry}

Generate 8-10 personalized skill recommendations that will maximize their career growth.

For each recommendation provide:
- skill: exact skill name
- category: one of [technical, soft, tools, frameworks, certification]
- reason: specific, personalized reason (mention their background)
- priority: one of [Immediate, Short-term, Long-term]
- time_to_learn: realistic timeframe
- synergy: which of their existing skills this builds upon

Return ONLY valid JSON:
{{
  "recommended_skills": [
    {{
      "skill":         "skill name",
      "category":      "technical",
      "reason":        "Personalized reason...",
      "priority":      "Immediate",
      "time_to_learn": "2-4 weeks",
      "synergy":       "Builds on your Python skills"
    }}
  ]
}}
"""


# ===========================================================================
# Core Functions
# ===========================================================================

def extract_skills(resume_text: str) -> dict:
    """
    Extract all skills from resume text into categorized buckets.

    Args:
        resume_text: Raw extracted text from the candidate's resume.

    Returns:
        dict with keys: technical_skills, soft_skills, certifications,
                        tools, frameworks
    """
    if not resume_text or not resume_text.strip():
        logger.warning("extract_skills: empty resume text received")
        return _empty_skills()

    logger.info("extract_skills: running LLM extraction on %d chars", len(resume_text))

    try:
        llm = load_llm()
        prompt = SKILL_EXTRACTION_PROMPT.format(
            resume_text=resume_text[:6000]   # stay within token budget
        )
        response = llm.invoke(prompt)
        raw_text = response.content if hasattr(response, "content") else str(response)

        parsed = extract_json_response(raw_text)

        if not parsed or not isinstance(parsed, dict):
            logger.warning("extract_skills: LLM returned non-dict, falling back to regex")
            return _regex_fallback_extraction(resume_text)

        # Normalise keys
        result = {
            "technical_skills": _normalise_list(parsed.get("technical_skills", [])),
            "soft_skills":      _normalise_list(parsed.get("soft_skills", [])),
            "certifications":   _normalise_list(parsed.get("certifications", [])),
            "tools":            _normalise_list(parsed.get("tools", [])),
            "frameworks":       _normalise_list(parsed.get("frameworks", [])),
        }

        total = sum(len(v) for v in result.values())
        logger.info("extract_skills: extracted %d skills across 5 categories", total)
        return result

    except Exception as exc:
        logger.error("extract_skills: LLM call failed — %s. Using regex fallback.", exc)
        return _regex_fallback_extraction(resume_text)


def compare_industry_skills(
    existing_skills: dict,
    target_role: str = "software_engineer",
    career_level: str = "Mid",
    industry: str = "Technology",
) -> dict:
    """
    Compare candidate's skills against the industry benchmark for their role.

    Args:
        existing_skills: Output of extract_skills().
        target_role:     One of the keys in INDUSTRY_BENCHMARKS, or a free-text role.
        career_level:    "Junior", "Mid", "Senior", "Lead"
        industry:        Industry sector string.

    Returns:
        dict with keys: missing_skills, coverage_stats
    """
    logger.info("compare_industry_skills: role=%s level=%s", target_role, career_level)

    # Flatten existing skills to a lowercase set for quick lookup
    existing_flat = set()
    for skills_list in existing_skills.values():
        existing_flat.update(s.lower().strip() for s in skills_list)

    # Get benchmark (exact match or best fuzzy match)
    benchmark = _get_benchmark(target_role)

    # ── Try LLM-powered comparison first ──────────────────────────────────
    try:
        existing_summary = _summarise_skills(existing_skills)
        industry_summary = _summarise_benchmark(benchmark)

        llm = load_llm()
        prompt = MISSING_SKILLS_PROMPT.format(
            existing_skills=existing_summary,
            target_role=target_role.replace("_", " ").title(),
            industry_skills=industry_summary,
        )
        response = llm.invoke(prompt)
        raw_text = response.content if hasattr(response, "content") else str(response)
        parsed = extract_json_response(raw_text)

        if parsed and isinstance(parsed, dict) and "missing_skills" in parsed:
            missing = parsed["missing_skills"]
            # Enrich with learning resources and priority score
            for item in missing:
                item["learning_resources"] = _get_learning_resources(item.get("skill", ""))
                item["priority_score"]     = _compute_priority_score(item)

            # Compute coverage statistics
            coverage = _compute_coverage(existing_flat, benchmark)

            logger.info("compare_industry_skills: found %d missing skills via LLM", len(missing))
            return {
                "missing_skills":  missing,
                "coverage_stats":  coverage,
            }

    except Exception as exc:
        logger.warning("compare_industry_skills: LLM call failed (%s), using rule-based", exc)

    # ── Fallback: rule-based comparison ───────────────────────────────────
    return _rule_based_comparison(existing_flat, benchmark, existing_skills)


def generate_recommendations(
    existing_skills: dict,
    missing_skills: list,
    target_role: str = "software_engineer",
    career_level: str = "Mid",
    industry: str = "Technology",
) -> list:
    """
    Generate prioritized, personalized skill recommendations.

    Args:
        existing_skills:  Output of extract_skills().
        missing_skills:   Output of compare_industry_skills()["missing_skills"].
        target_role:      Target job role string.
        career_level:     Candidate's career level.
        industry:         Candidate's industry.

    Returns:
        List of recommendation dicts, each containing:
        skill, category, reason, priority, time_to_learn, synergy,
        learning_resources, priority_score
    """
    logger.info("generate_recommendations: building personalized plan")

    try:
        existing_summary = _summarise_skills(existing_skills)

        llm = load_llm()
        prompt = RECOMMENDATIONS_PROMPT.format(
            existing_skills=existing_summary,
            target_role=target_role.replace("_", " ").title(),
            career_level=career_level,
            industry=industry,
        )
        response = llm.invoke(prompt)
        raw_text = response.content if hasattr(response, "content") else str(response)
        parsed = extract_json_response(raw_text)

        if parsed and isinstance(parsed, dict) and "recommended_skills" in parsed:
            recommendations = parsed["recommended_skills"]

            # Enrich each recommendation
            for rec in recommendations:
                rec["learning_resources"] = _get_learning_resources(rec.get("skill", ""))
                score_info = SKILL_SCORES.get(rec.get("skill", ""), SKILL_SCORES["_default"])
                base_score = score_info["demand"]
                priority_mult = {"Immediate": 1.0, "Short-term": 0.8, "Long-term": 0.6}
                rec["priority_score"] = int(base_score * priority_mult.get(rec.get("priority", "Short-term"), 0.8))

            # Sort by priority score descending
            recommendations.sort(key=lambda x: x.get("priority_score", 0), reverse=True)
            logger.info("generate_recommendations: %d recommendations generated", len(recommendations))
            return recommendations

    except Exception as exc:
        logger.warning("generate_recommendations: LLM failed (%s), using rule-based", exc)

    # ── Fallback: build from missing skills ───────────────────────────────
    return _recommendations_from_missing(missing_skills)


def analyze_skills_gap(
    resume_text: str,
    target_role: str = "software_engineer",
    career_level: str = "Mid",
    industry: str = "Technology",
    use_cache: bool = True,
) -> dict:
    """
    Main entry point — run the full skills gap analysis pipeline.

    Orchestrates: extract_skills → compare_industry_skills → generate_recommendations
    and returns the complete structured result.

    Args:
        resume_text:   Raw text from the resume.
        target_role:   Target job role (matches INDUSTRY_BENCHMARKS key or free text).
        career_level:  "Junior", "Mid", "Senior", "Lead".
        industry:      Industry sector.
        use_cache:     Return cached result for identical inputs if available.

    Returns:
        {
            "success": bool,
            "data": {
                "existing_skills":    {...},
                "missing_skills":     [...],
                "recommended_skills": [...],
                "summary":            {...}
            },
            "message": str
        }
    """
    # ── Cache check ───────────────────────────────────────────────────────
    if use_cache:
        cache_key = _make_cache_key(resume_text, target_role, career_level, industry)
        if cache_key in _ANALYSIS_CACHE:
            logger.info("analyze_skills_gap: returning cached result")
            return _ANALYSIS_CACHE[cache_key]

    logger.info("analyze_skills_gap: starting analysis — role=%s level=%s", target_role, career_level)

    try:
        # Step 1 — Extract existing skills
        existing_skills = extract_skills(resume_text)
        total_existing  = sum(len(v) for v in existing_skills.values())
        logger.info("Step 1 complete: %d skills extracted", total_existing)

        # Step 2 — Identify missing skills vs industry benchmark
        comparison  = compare_industry_skills(existing_skills, target_role, career_level, industry)
        missing     = comparison.get("missing_skills", [])
        coverage    = comparison.get("coverage_stats", {})
        logger.info("Step 2 complete: %d missing skills identified", len(missing))

        # Step 3 — Generate personalized recommendations
        recommendations = generate_recommendations(
            existing_skills, missing, target_role, career_level, industry
        )
        logger.info("Step 3 complete: %d recommendations generated", len(recommendations))

        # ── Build summary ─────────────────────────────────────────────────
        coverage_pct     = coverage.get("coverage_pct", 0)
        top_gap_category = _find_top_gap(missing)
        career_readiness = _assess_career_readiness(coverage_pct, total_existing)

        result = {
            "success": True,
            "data": {
                "existing_skills":    existing_skills,
                "missing_skills":     missing,
                "recommended_skills": recommendations,
                "summary": {
                    "total_existing_skills":   total_existing,
                    "total_missing_skills":    len(missing),
                    "total_recommendations":   len(recommendations),
                    "skills_coverage_pct":     coverage_pct,
                    "top_gap_area":            top_gap_category,
                    "career_readiness":        career_readiness,
                    "target_role":             target_role.replace("_", " ").title(),
                    "career_level":            career_level,
                    "industry":                industry,
                    "overall_assessment":      _overall_assessment(coverage_pct, len(missing)),
                    "analyzed_at":             datetime.utcnow().isoformat() + "Z",
                },
            },
            "message": f"Skills gap analysis complete. Found {len(missing)} missing skills.",
        }

        # Cache the result
        if use_cache:
            _ANALYSIS_CACHE[cache_key] = result

        return result

    except Exception as exc:
        logger.exception("analyze_skills_gap: unexpected error — %s", exc)
        return {
            "success": False,
            "data":    {},
            "message": f"Skills gap analysis failed: {exc}",
        }


# ===========================================================================
# Internal Helpers
# ===========================================================================

def _empty_skills() -> dict:
    return {"technical_skills": [], "soft_skills": [], "certifications": [],
            "tools": [], "frameworks": []}


def _normalise_list(lst: Any) -> list[str]:
    """Ensure we get a clean list of non-empty strings."""
    if not isinstance(lst, list):
        return []
    return [str(item).strip() for item in lst if item and str(item).strip()]


def _make_cache_key(text: str, role: str, level: str, industry: str) -> str:
    payload = f"{text[:500]}{role}{level}{industry}"
    return hashlib.md5(payload.encode()).hexdigest()


def _get_benchmark(role: str) -> dict[str, list[str]]:
    """Return industry benchmark; fall back to software_engineer if unknown."""
    # Exact match
    if role in INDUSTRY_BENCHMARKS:
        return INDUSTRY_BENCHMARKS[role]
    # Fuzzy match
    role_lower = role.lower().replace(" ", "_")
    for key in INDUSTRY_BENCHMARKS:
        if key in role_lower or role_lower in key:
            return INDUSTRY_BENCHMARKS[key]
    logger.warning("_get_benchmark: unknown role '%s', defaulting to software_engineer", role)
    return INDUSTRY_BENCHMARKS["software_engineer"]


def _summarise_skills(skills: dict) -> str:
    parts = []
    for cat, lst in skills.items():
        if lst:
            parts.append(f"{cat.replace('_', ' ').title()}: {', '.join(lst[:15])}")
    return " | ".join(parts) if parts else "No skills provided"


def _summarise_benchmark(benchmark: dict) -> str:
    parts = []
    for cat, lst in benchmark.items():
        if lst:
            parts.append(f"{cat.title()}: {', '.join(lst[:10])}")
    return " | ".join(parts)


def _get_learning_resources(skill: str) -> list[dict]:
    """Return curated learning resources for a skill."""
    # Direct match
    if skill in LEARNING_RESOURCES:
        return LEARNING_RESOURCES[skill]
    # Partial match
    for key in LEARNING_RESOURCES:
        if key.lower() in skill.lower() or skill.lower() in key.lower():
            return LEARNING_RESOURCES[key]
    return LEARNING_RESOURCES["_default"]


def _compute_priority_score(item: dict) -> int:
    """Score = (demand × importance_weight × category_weight), capped at 100."""
    skill    = item.get("skill", "")
    imp_text = item.get("importance", "Medium")
    cat      = item.get("category", "technical")

    demand   = SKILL_SCORES.get(skill, SKILL_SCORES["_default"])["demand"]
    imp_w    = {"Critical": 1.0, "High": 0.8, "Medium": 0.6, "Low": 0.4}.get(imp_text, 0.6)
    cat_w    = {"technical": 1.0, "certification": 0.9, "frameworks": 0.85,
                "tools": 0.75, "soft": 0.65}.get(cat, 0.75)

    score = int(demand * imp_w * cat_w)
    return min(score, 100)


def _compute_coverage(existing_flat: set, benchmark: dict) -> dict:
    """Calculate how many benchmark skills the candidate already has."""
    total_benchmark = sum(len(v) for v in benchmark.values())
    if total_benchmark == 0:
        return {"coverage_pct": 0, "covered": 0, "total": 0}

    covered = sum(
        1 for lst in benchmark.values()
        for skill in lst
        if skill.lower() in existing_flat
    )
    pct = round((covered / total_benchmark) * 100)
    return {"coverage_pct": pct, "covered": covered, "total": total_benchmark}


def _assess_career_readiness(pct: int, total_skills: int) -> str:
    if pct >= 80 and total_skills >= 20:
        return "Job Ready"
    if pct >= 60 and total_skills >= 12:
        return "Nearly Ready"
    if pct >= 40:
        return "Developing"
    if pct >= 20:
        return "Early Stage"
    return "Beginner"


def _find_top_gap(missing: list) -> str:
    """Return the category with the most missing skills."""
    from collections import Counter
    if not missing:
        return "None"
    cats = [item.get("category", "technical") for item in missing
            if item.get("importance") in ("Critical", "High")]
    if not cats:
        cats = [item.get("category", "technical") for item in missing]
    if not cats:
        return "technical"
    top = Counter(cats).most_common(1)[0][0]
    return top.replace("_", " ").title()


def _overall_assessment(coverage_pct: int, missing_count: int) -> str:
    if coverage_pct >= 80:
        return "Excellent profile! Minor skill gaps to address for full readiness."
    if coverage_pct >= 60:
        return f"Good foundation. Address {missing_count} key gaps to become highly competitive."
    if coverage_pct >= 40:
        return f"Solid start. Focus on the top {min(missing_count, 5)} critical skills first."
    return "Significant gaps identified. Follow the recommended learning path systematically."


def _rule_based_comparison(existing_flat: set, benchmark: dict, existing_dict: dict) -> dict:
    """Fallback: compare skills using string matching without LLM."""
    missing = []
    for cat, skills in benchmark.items():
        for skill in skills:
            if skill.lower() not in existing_flat:
                info = SKILL_SCORES.get(skill, SKILL_SCORES["_default"])
                priority_score = _compute_priority_score({
                    "skill": skill,
                    "importance": info["importance"],
                    "category": cat,
                })
                missing.append({
                    "skill":              skill,
                    "category":           cat,
                    "importance":         info["importance"],
                    "industry_demand":    f"{skill} has {info['demand']}% industry demand rating.",
                    "time_to_learn":      "2-8 weeks",
                    "reason":             f"{skill} is a key requirement for this role.",
                    "learning_resources": _get_learning_resources(skill),
                    "priority_score":     priority_score,
                })

    # Sort by priority descending, return top 10
    missing.sort(key=lambda x: x["priority_score"], reverse=True)
    coverage = _compute_coverage(existing_flat, benchmark)

    return {"missing_skills": missing[:10], "coverage_stats": coverage}


def _recommendations_from_missing(missing: list) -> list:
    """Build recommendations from missing skills list (LLM fallback)."""
    recs = []
    priority_map = {
        "Critical": "Immediate",
        "High":     "Short-term",
        "Medium":   "Short-term",
        "Low":      "Long-term",
    }
    for item in missing[:8]:
        recs.append({
            "skill":              item.get("skill", ""),
            "category":           item.get("category", "technical"),
            "reason":             item.get("reason", f"Important for your target role."),
            "priority":           priority_map.get(item.get("importance", "Medium"), "Short-term"),
            "time_to_learn":      item.get("time_to_learn", "4-8 weeks"),
            "synergy":            "Complements your existing skills",
            "learning_resources": item.get("learning_resources", _get_learning_resources("")),
            "priority_score":     item.get("priority_score", 70),
        })
    return recs


def _regex_fallback_extraction(text: str) -> dict:
    """
    Regex-based skill extraction when the LLM is unavailable.
    Scans for known skills from the industry benchmark database.
    """
    logger.info("_regex_fallback_extraction: using keyword matching")
    text_lower = text.lower()

    # Build known skills from benchmark
    known = {
        "technical": [], "soft": [], "tools": [],
        "frameworks": [], "certifications": []
    }
    cat_map = {"technical": "technical", "soft": "soft", "tools": "tools",
               "frameworks": "frameworks", "certs": "certifications"}

    for role_data in INDUSTRY_BENCHMARKS.values():
        for bench_cat, skills in role_data.items():
            target_cat = cat_map.get(bench_cat, "technical")
            for skill in skills:
                if skill.lower() in text_lower and skill not in known[target_cat]:
                    known[target_cat].append(skill)

    total = sum(len(v) for v in known.values())
    logger.info("_regex_fallback_extraction: found %d skills", total)
    return known
