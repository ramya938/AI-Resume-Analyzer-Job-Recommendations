# 📖 User Manual — AI Resume Analyzer & Job Recommendation System

> **Version:** 1.0.0 | **Audience:** End Users | **Platform:** Web Browser

---

## Table of Contents

1. [Getting Started](#getting-started)
2. [Creating an Account](#creating-an-account)
3. [Logging In](#logging-in)
4. [Uploading Your Resume](#uploading-your-resume)
5. [Understanding Your AI Analysis](#understanding-your-ai-analysis)
6. [Browsing Job Recommendations](#browsing-job-recommendations)
7. [Setting Job Search Preferences](#setting-job-search-preferences)
8. [Managing Saved Jobs](#managing-saved-jobs)
9. [Dashboard Overview](#dashboard-overview)
10. [Tips for Best Results](#tips-for-best-results)
11. [Frequently Asked Questions](#frequently-asked-questions)
12. [Troubleshooting](#troubleshooting)

---

## Getting Started

The AI Resume Analyzer is a web application that:

1. **Analyzes** your resume using Google Gemini AI
2. **Extracts** your skills, experience, and education automatically
3. **Searches** LinkedIn and other job boards for matching roles
4. **Ranks** jobs based on how well they match your profile
5. **Provides** personalized tips, cover letter suggestions, and interview prep

### System Requirements

- **Browser:** Chrome, Firefox, Safari, or Edge (latest version)
- **Internet connection** required
- **Resume formats supported:** PDF (recommended), DOCX, TXT
- **Maximum file size:** 10 MB

---

## Creating an Account

### Step 1: Open the Registration Page

When you visit the app for the first time, you'll see the **Login** page. Click **"Don't have an account? Register"** to open the registration form.

### Step 2: Fill in Your Details

| Field | Requirements |
|-------|-------------|
| **Full Name** | 2–100 characters |
| **Email Address** | Valid format (you@example.com) |
| **Password** | Minimum 8 characters |
| **Confirm Password** | Must match password |

### Step 3: Submit

Click **"Create Account"**. You'll see a success message and be redirected to the login page.

> **Privacy Note:** Your password is stored as a secure bcrypt hash — it is never stored in plain text. Your resume data is stored locally in our SQLite database and is not shared with third parties.

---

## Logging In

1. Enter your **email address** and **password**
2. Click **"Sign In"**
3. You'll be redirected to your **Dashboard**

### Forgot Your Password?

Currently, password reset requires contacting the administrator. Future versions will include email-based reset.

---

## Uploading Your Resume

Navigate to **"📄 Resume Upload"** in the sidebar.

### Supported Formats

| Format | Extension | Notes |
|--------|-----------|-------|
| PDF | `.pdf` | ✅ **Recommended** — best parsing accuracy |
| Word Document | `.docx` | ✅ Good support |
| Plain Text | `.txt` | ✅ Works, loses formatting |

### Upload Steps

1. Click **"Browse files"** or drag-and-drop your resume file
2. Wait for the file to upload (usually < 2 seconds)
3. Click **"🔍 Analyze Resume"**
4. Wait 10–30 seconds for AI analysis to complete

### What Gets Extracted

The AI automatically extracts:

- ✅ **Skills** — Technical and soft skills
- ✅ **Work Experience** — Job titles, companies, durations
- ✅ **Education** — Degrees, universities, graduation years
- ✅ **Contact Info** — Email, phone, LinkedIn URL
- ✅ **Certifications** — AWS, Google Cloud, etc.
- ✅ **Languages** — Programming and spoken languages
- ✅ **Years of Experience** — Calculated from work history

### Tips for Best Upload Results

- Use a **single-column layout** PDF for best parsing
- Avoid **tables and graphics** — text inside them may not parse correctly
- Include **dates** for all work experiences (e.g., "2021 – 2024")
- List **specific skill names** (e.g., "FastAPI" not just "Python frameworks")
- Keep the file under **5 MB** for fastest processing

---

## Understanding Your AI Analysis

After analysis, you'll see several sections:

### 📊 Resume Score (0–100)

Your overall resume quality score:

| Score | Rating | Meaning |
|-------|--------|---------|
| 85–100 | ⭐ Excellent | Ready to apply to top companies |
| 70–84 | ✅ Good | Strong resume, minor improvements possible |
| 60–69 | ⚠️ Fair | Some key sections need improvement |
| 0–59 | ❌ Needs Work | Significant gaps to address |

### 🛠️ Identified Skills

A list of all skills the AI found in your resume. If you notice missing skills:
- Check if they were spelled correctly in your resume
- Add them explicitly (e.g., "Proficient in React.js" not just "front-end development")

### 💼 Experience Summary

- **Years of Experience:** Calculated from your work history dates
- **Seniority Level:** Junior / Mid / Senior / Lead
- **Key Achievements:** Bullet points extracted from your descriptions

### 🎓 Education

- Highest degree detected
- University and graduation year
- GPA (if mentioned)

### ✅ Strengths

What the AI found impressive about your resume:
- Strong technical stack
- Quantified achievements (e.g., "reduced latency by 40%")
- Relevant certifications
- Progression in responsibility

### ⚠️ Weaknesses / Improvement Areas

Areas the AI identified for improvement:
- Missing quantified results
- Skills gaps for your target role
- Formatting issues
- Missing sections (summary, GitHub, LinkedIn)

### 🎯 Recommendations

Specific, actionable improvements:
1. Add metrics to your achievements
2. Include a professional summary
3. List certifications prominently
4. Add GitHub/portfolio URL

---

## Browsing Job Recommendations

Navigate to **"💼 Job Recommendations"** in the sidebar.

### How Jobs Are Fetched

The system searches multiple sources in order:

```
1️⃣ LinkedIn (authenticated, if cookies available)
2️⃣ LinkedIn Guest API (no login needed!) ← most common
3️⃣ Remotive.com API (remote tech jobs)
4️⃣ Curated pool (always available)
```

### Job Cards

Each job card shows:

| Element | Description |
|---------|-------------|
| 🏢 Company Avatar | Color-coded letter badge |
| 📋 Job Title | Role name |
| 📍 Location | City or Remote |
| 💰 Salary | If available |
| 👥 Applicants | Number of applicants |
| 🎯 Match % | Circular progress ring |
| 🏷️ Grade Badge | Excellent / Good / Fair / Poor |
| ✅ Matching Skills | Skills you have that match |

### Match Score Breakdown

| Component | Weight | Meaning |
|-----------|--------|---------|
| Skills Match | 50% | How many required skills you have |
| Experience Match | 25% | Your years vs. required years |
| Education Match | 15% | Your degree vs. required degree |
| Responsibilities Match | 10% | JD keyword alignment |

### Grade System

| Grade | Score Range | Action |
|-------|-------------|--------|
| 🟦 **Excellent** | 85–100 | Apply immediately! |
| 🟩 **Good** | 70–84 | Strong candidate — apply |
| 🟡 **Fair** | 60–69 | Address gaps first |
| 🔴 **Poor** | 0–59 | Not the right fit right now |

### Filtering Jobs

Use the **sidebar filters**:

- **🔎 Search** — Search by title, company, or skill keyword
- **🎯 Match Grade** — Show only Excellent/Good/Fair jobs
- **📍 Location** — Filter by city or "Remote"
- **🌐 Remote Only** — Toggle to show remote jobs only
- **💰 Salary Range** — Minimum salary filter
- **👥 Applicants** — Filter by number of applicants
- **🔌 Source** — LinkedIn / Remotive / Curated

### Sorting Options

- **🎯 Best Match** — Highest overall match score first
- **🕐 Most Recent** — Newest jobs first
- **💰 Highest Salary** — Highest-paying first
- **👥 Fewest Applicants** — Less competition first

### Pagination

- 20 jobs per page
- Use **← Previous** and **Next →** buttons

---

## Viewing Job Details

Click **"🔍 View Details"** on any job card to open the full detail view.

### Overview Tab

- Full job description
- Quick stats: Posted date, applicant count, source platform

### Match Analysis Tab

- **Radar Chart** — Visual breakdown of your 4 match scores
- **Signal Bars** — Ranking signals (match %, recency, salary, competition, remote fit)
- **✅ Matching Skills** — Green pills for skills you have
- **❌ Missing Skills** — Red pills for skills to develop
- **Component Score Detail** — Individual score bars

### Application Tips Tab

- **Priority Level** — High / Medium / Low (when to apply)
- **Timing Advice** — Best time to submit
- **📄 Resume Tips** — Specific improvements for this job
- **🔑 ATS Keywords** — Terms to add to your resume

### Cover Letter Tab

- **Opening Hook** — Compelling first paragraph
- **Value Propositions** — Your top 3 selling points
- **Full Template** — Complete cover letter draft
- **Customization Tips** — How to personalize it

### Interview Prep Tab

- **Technical Topics** — Topics likely to be tested
- **Behavioral Questions** — STAR-format practice questions
- **Company Research** — Key things to know
- **Salary Negotiation** — Tips and range suggestions
- **7-Day Preparation Plan** — Day-by-day schedule

---

## Job Actions

Each job has three action buttons:

| Button | Action |
|--------|--------|
| **💾 Save Job** | Bookmarks the job for later review |
| **✅ Apply** | Opens the job URL in a new tab and marks as "Applied" |
| **❌ Skip** | Hides the job from your current view |

---

## Setting Job Search Preferences

Navigate to **"⚙️ Search Preferences"** in the sidebar.

### Basic Preferences

| Field | Description |
|-------|-------------|
| **Job Title** | Target role (e.g., "Data Scientist") |
| **Preferred Locations** | Multiple cities or "Remote" |
| **Remote Preference** | Remote Only / Hybrid / On-site / No Preference |
| **Experience Level** | Entry / Mid / Senior / Lead |
| **Job Type** | Full-time / Part-time / Contract / Freelance |
| **Salary Min/Max** | Expected compensation range |
| **Industries** | Technology / Finance / Healthcare / etc. |
| **Company Size** | Startup / SMB / Enterprise |

### Advanced Filters

| Filter | Description |
|--------|-------------|
| **Include Keywords** | Must-have words in job description |
| **Exclude Keywords** | Filter out unwanted terms (e.g., "PHP") |
| **Visa Sponsorship** | Only show sponsoring companies |
| **Preferred Companies** | Target specific companies |

### Preference Actions

- **💾 Save Preferences** — Stores to database for next visit
- **🔄 Load Saved** — Restores your last saved preferences
- **🗑️ Reset** — Clears all filters to defaults
- **🔍 Find Jobs Now** — Launches a new search with current preferences

### Search History

The last 10 searches are saved, showing:
- Search criteria used
- Number of results found
- Date/time of search
- Search duration

---

## Managing Saved Jobs

### Viewing Saved Jobs

Navigate to **"💾 Saved Jobs"** (in the sidebar or dashboard).

You'll see all bookmarked jobs with their:
- Current status (Saved / Applied / Interviewing / Offered / Rejected)
- Match score
- Application deadline (if available)

### Application Status Tracking

Update job status as you progress:

```
Saved → Applied → Interviewing → Offered → Accepted
                                          → Rejected
                              → Withdrawn
```

---

## Dashboard Overview

The **Dashboard** (home page after login) shows:

### KPI Cards (Top Row)

| Card | Description |
|------|-------------|
| 📊 Resume Score | Your latest analysis score |
| 🛠️ Skills Identified | Count of extracted skills |
| 💼 Jobs Found | Total job matches |
| ⭐ Excellent Matches | Jobs scoring 85%+ |

### Recent Activity

- Last 5 job recommendations
- Recent analysis history
- Application status summary

### Quick Actions

- **📤 Upload New Resume** — Re-analyze with updated resume
- **🔍 Find More Jobs** — Trigger a fresh job search
- **💾 View Saved Jobs** — Review bookmarked applications

---

## Tips for Best Results

### Resume Tips

1. **Quantify everything:** "Reduced API latency by 40%" beats "improved performance"
2. **Use standard section names:** "EXPERIENCE" not "CAREER JOURNEY"
3. **List skills explicitly:** Have a dedicated Skills section
4. **Include dates:** All work and education entries should have start/end dates
5. **One-column layout:** Avoid two-column PDFs for better parsing

### Job Search Tips

1. **Set preferences first:** The more specific your preferences, the better the matches
2. **Enable remote:** More options available for remote positions
3. **Check Excellent matches first:** Focus your energy on 85%+ matches
4. **Review missing skills:** These show you what to learn next for your target role
5. **Apply early:** Use "Sort by Most Recent" to find fresh postings

### Match Score Tips

1. **Skills = 50% of score:** Add more relevant skills to your resume
2. **Include certifications:** AWS, GCP, Docker certifications boost match scores
3. **Match experience level:** Apply to jobs matching your seniority
4. **Use keywords from JDs:** Include exact terminology from target job descriptions

---

## Frequently Asked Questions

**Q: How often does the job list update?**
A: Jobs are fetched fresh each time you search. Results are cached for 5 minutes to avoid redundant API calls.

**Q: Can I upload multiple resumes?**
A: Yes — each upload replaces the previous analysis. Previous job matches are preserved.

**Q: Does the app store my resume permanently?**
A: The extracted text and analysis are stored in the local database. The original file is processed in memory and not permanently stored.

**Q: Why do I see curated jobs instead of LinkedIn jobs?**
A: The system uses LinkedIn's Guest API which works without authentication. If it's temporarily blocked by LinkedIn's rate limiting, it falls back to Remotive and the curated pool automatically.

**Q: What does "4 skipped" mean in the test results?**
A: Those are optional AI-integration tests that only run when a live Gemini API key is available. They're skipped in offline/test environments — all core logic tests pass 100%.

**Q: Is my data private?**
A: Yes. All data is stored locally (SQLite database). Nothing is sent to third parties except:
- Your resume text → Google Gemini API (for AI analysis)
- Job search terms → LinkedIn/Remotive APIs (public endpoints)

**Q: How do I improve a "Poor" match score?**
A: Look at the "Missing Skills" section in the job detail. These are the specific skills the job requires that you don't have listed. Either develop those skills or search for jobs that better match your current profile.

**Q: Can I use the app without a Google Gemini API key?**
A: Resume upload and job search work without a Gemini key, but the AI analysis (score, recommendations, tips) requires a valid `GEMINI_API_KEY`. Get one free at [Google AI Studio](https://aistudio.google.com).

**Q: What if LinkedIn blocks job scraping?**
A: The system has three fallback tiers. Even if all LinkedIn access fails, Remotive API and the curated job pool always return relevant tech jobs.

---

## Troubleshooting

### Problem: Resume Won't Upload

**Symptoms:** File upload spinner never stops, or error message appears

**Solutions:**
1. Check file is PDF, DOCX, or TXT
2. Reduce file size (max 10 MB) — try "Save as PDF" with compression
3. Avoid password-protected PDFs
4. Try a different browser
5. Check internet connection

### Problem: AI Analysis Takes Too Long

**Symptoms:** "Analyzing..." spinner runs for more than 60 seconds

**Solutions:**
1. Wait up to 90 seconds (first analysis can be slower)
2. Check `GEMINI_API_KEY` is valid
3. Try with a shorter resume text
4. Check if Gemini API is under maintenance at [status.cloud.google.com](https://status.cloud.google.com)

### Problem: No Jobs Found

**Symptoms:** "0 jobs" returned or empty state shown

**Solutions:**
1. Click "Refresh" to trigger a new search
2. Ensure at least one skill is listed in your preferences
3. Try broader location (e.g., "Remote" instead of a specific city)
4. Check internet connection (Remotive API requires internet)
5. The curated pool always has 10+ jobs available — if even this is empty, restart the app

### Problem: Can't Log In

**Symptoms:** "Invalid email or password" even with correct credentials

**Solutions:**
1. Check for typos in email (all lowercase)
2. Check Caps Lock for password
3. Try registering again — account may not have been saved
4. Contact administrator to check database

### Problem: Match Scores All Show 0%

**Symptoms:** Every job shows 0% match

**Solutions:**
1. Upload your resume and run AI analysis first
2. Check that skills were extracted (go to Dashboard → Resume Score)
3. If skills list is empty, your resume may need reformatting
4. Add skills manually in Job Search Preferences

---

*User Manual v1.0 — AI Resume Analyzer & Job Recommendation System*  
*Last updated: June 2026*
