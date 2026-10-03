# 🤖 AI Resume Analyzer & Job Recommendation System

<div align="center">

![Python](https://img.shields.io/badge/Python-3.11+-blue?logo=python)
![Streamlit](https://img.shields.io/badge/Streamlit-1.38+-red?logo=streamlit)
![Google Gemini](https://img.shields.io/badge/Google%20Gemini-AI-green?logo=google)
![LinkedIn](https://img.shields.io/badge/LinkedIn-Jobs-0A66C2?logo=linkedin)
![SQLite](https://img.shields.io/badge/SQLite-Database-003B57?logo=sqlite)
![Tests](https://img.shields.io/badge/Tests-98%20passing-brightgreen)

**Upload your resume → Get AI analysis → Discover matched LinkedIn jobs**

[Live Demo](#) · [Deployment Guide](deployment_guide.md) · [User Manual](user_manual.md)

</div>

---

## ✨ Features

| Feature | Description |
|---------|-------------|
| 📄 **Resume Upload** | PDF, DOCX, TXT — multi-format parsing |
| 🤖 **AI Analysis** | Google Gemini-powered skills extraction, scoring, recommendations |
| 💼 **LinkedIn Jobs** | Real-time scraping via Guest API + Selenium (no auth needed) |
| 🎯 **Smart Matching** | 4-signal scoring: Skills 50% · Experience 25% · Education 15% · Responsibilities 10% |
| 📊 **Dashboard** | Plotly charts, radar graphs, match breakdowns |
| 🔐 **Auth System** | bcrypt registration/login with SQLite sessions |
| 🔍 **Job Filters** | Location, remote, salary, experience, grade filters |
| 📬 **Application Tracking** | Save, apply, track status per job |

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Streamlit Frontend                    │
│  app.py · dashboard · resume_upload · job_recommendations│
│  job_search_preferences · recommendations               │
└────────────────────┬────────────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────────────┐
│                    Backend Modules                       │
│  auth.py · resume_parser.py · llm_analyzer.py           │
│  job_scraper.py · job_matcher.py · recommendation_engine │
└────────────────────┬────────────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────────────┐
│                   Data Layer                            │
│  SQLite (WAL mode) · data/database.db                   │
│  Tables: users · resume_analysis · scraped_jobs ·       │
│          job_search_prefs · saved_jobs · app_history    │
└─────────────────────────────────────────────────────────┘
```

### Job Scraping Pipeline (3 Tiers)

```
LinkedIn Selenium (auth)  →  LinkedIn Guest API  →  Remotive API  →  Curated Pool
         Tier 1                    Tier 1.5              Tier 2          Tier 3
   (needs cookies)           (no auth needed!)        (free API)     (always works)
```

---

## 🚀 Quick Start

### 1. Clone & Setup

```bash
git clone https://github.com/yourname/AI-Resume_Analyzer.git
cd AI-Resume_Analyzer
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # macOS/Linux
pip install -r requirements.txt
```

### 2. Configure Environment

```bash
cp .env.example .env
# Edit .env and add your GEMINI_API_KEY
```

`.env` required variables:
```env
GEMINI_API_KEY=your_google_gemini_api_key_here
DATABASE_PATH=data/database.db
SCRAPER_HEADLESS=true
LOG_LEVEL=INFO
```

### 3. Run the App

```bash
streamlit run app.py
```

Open **http://localhost:8501** in your browser.

---

## 📁 Project Structure

```
AI-Resume_Analyzer/
├── app.py                          # Main Streamlit entry point
├── requirements.txt                # Python dependencies
├── .env.example                    # Environment template
├── .gitignore
│
├── backend/                        # Core business logic
│   ├── auth.py                     # Registration, login, bcrypt
│   ├── resume_parser.py            # PDF/DOCX text extraction
│   ├── llm_analyzer.py             # Google Gemini AI analysis
│   ├── job_scraper.py              # LinkedIn + Remotive scraping
│   ├── job_matcher.py              # Resume-job matching engine
│   ├── recommendation_engine.py    # Ranking + tips generation
│   └── scraper.py                  # Selenium LinkedIn driver
│
├── frontend/                       # Streamlit UI pages
│   ├── dashboard.py
│   ├── resume_upload.py
│   ├── job_recommendations.py      # Premium job cards UI
│   ├── job_search_preferences.py   # Search config UI
│   └── recommendations.py         # Quick recommendations
│
├── utils/                          # Shared utilities
│   ├── database.py                 # SQLite connection + schema
│   ├── helpers.py                  # Format/display helpers
│   └── validators.py               # Input validation
│
├── data/                           # Runtime data
│   ├── database.db                 # SQLite database
│   └── sessions/                   # LinkedIn cookie files
│       └── linkedin_cookies.pkl
│
├── logs/                           # Application logs
├── tests/                          # QA test suite
│   └── test_suite.py               # 98 tests (100% passing)
│
├── deployment_guide.md
├── user_manual.md
├── presentation_outline.md
└── README.md
```

---

## 🧪 Testing

```bash
# Run full test suite
pytest tests/test_suite.py -v

# With coverage report
pytest tests/test_suite.py --cov=backend --cov=utils --cov-report=html

# Run specific test class
pytest tests/test_suite.py::TestAuth -v
pytest tests/test_suite.py::TestJobMatcher -v
pytest tests/test_suite.py::TestRecommendationEngine -v
pytest tests/test_suite.py::TestPerformance -v
```

**Test Coverage:**
- ✅ 98 tests passing · 4 skipped (optional AI API)
- ✅ Auth: registration, login, hashing, validation
- ✅ Resume: parsing, skill extraction, edge cases
- ✅ Job Matcher: skill/experience/education/overall scoring
- ✅ Recommendation Engine: ranking, tips, cover letters, tracking
- ✅ Job Search Preferences: save/load/history
- ✅ Edge Cases: empty inputs, Unicode, very long JDs
- ✅ Performance: 50-job ranking < 0.5s, full pipeline < 5s

---

## 🌐 API Keys

| Service | Variable | Where to Get |
|---------|----------|-------------|
| Google Gemini | `GEMINI_API_KEY` | [Google AI Studio](https://aistudio.google.com) — Free |
| LinkedIn (optional) | `LINKEDIN_EMAIL` / `LINKEDIN_PASSWORD` | Your LinkedIn account |

---

## 🔧 Environment Variables

```env
# Required
GEMINI_API_KEY=AIza...

# Database
DATABASE_PATH=data/database.db

# LinkedIn Scraping (optional — Guest API works without these)
LINKEDIN_EMAIL=your@email.com
LINKEDIN_PASSWORD=yourpassword
SCRAPER_HEADLESS=true

# App Config
LOG_LEVEL=INFO
SESSION_TIMEOUT_HOURS=24
MAX_RESUME_SIZE_MB=10
```

---

## 📊 Match Score Formula

```
Overall Match = (Skills × 50%) + (Experience × 25%) + (Education × 15%) + (Responsibilities × 10%)

Grade:  Excellent = 85–100  |  Good = 70–84  |  Fair = 60–69  |  Poor = 0–59
```

---

## 🚢 Deployment

| Platform | Guide |
|----------|-------|
| **Streamlit Cloud** | [deployment_guide.md#streamlit-cloud](deployment_guide.md) |
| **Render** | [deployment_guide.md#render](deployment_guide.md) |
| **Docker** | [deployment_guide.md#docker](deployment_guide.md) |

---# 🤖 AI Resume Analyzer & Job Recommendation System

**AI Resume Analyzer & Job Recommendation System** is an AI-powered career assistance platform designed to help users analyze their resumes, identify skills, evaluate their profiles, and discover relevant job opportunities.

The application allows users to upload resumes in multiple formats, receive AI-powered resume analysis using Google Gemini, search and filter job opportunities, calculate resume-to-job match scores, and track saved job applications through an interactive Streamlit dashboard.

---

## ✨ Key Features

### 📄 Resume Upload & Parsing

Users can upload their resumes in multiple formats:

- PDF
- DOCX
- TXT

The system extracts the resume content and processes it for further analysis.

---

### 🤖 AI Resume Analysis

The application uses **Google Gemini AI** to analyze the uploaded resume.

The AI analysis can identify:

- Technical skills
- Soft skills
- Education
- Experience
- Resume strengths
- Missing skills
- Career recommendations
- Areas for improvement

This helps users understand how well their resume represents their professional profile.

---

### 💼 Job Recommendations

The system searches for relevant job opportunities based on the user's resume and preferences.

Job information can be collected through multiple sources:

- LinkedIn
- LinkedIn Guest API
- Remotive API
- Curated fallback job data

The system is designed with multiple scraping and recommendation tiers so that job discovery can continue even when one source is unavailable.

---

### 🎯 Smart Job Matching

The application calculates a match score between the user's resume and available job descriptions.

The matching system considers four major factors:

- Skills – **50%**
- Experience – **25%**
- Education – **15%**
- Responsibilities – **10%**

The final score helps users understand how closely a job matches their profile.

---

### 📊 Match Score & Grade

Jobs are categorized based on their overall matching score.

| Score | Grade |
|---|---|
| 85–100 | Excellent |
| 70–84 | Good |
| 60–69 | Fair |
| 0–59 | Poor |

The dashboard displays the match score and individual scoring components.

---

### 📈 Interactive Dashboard

The application provides an interactive dashboard containing:

- Resume analysis results
- Skill information
- Job match scores
- Match breakdowns
- Charts
- Radar graphs
- Recommended jobs
- Application tracking information

The dashboard helps users understand their career profile visually.

---

### 🔐 User Authentication

The application includes a user authentication system.

Users can:

- Register
- Login
- Logout
- Maintain sessions
- Store account information securely

Passwords are protected using **bcrypt hashing**.

Authentication and session information are managed using SQLite.

---

### 🔍 Job Search & Filters

Users can customize their job search using different filters.

Available filters include:

- Location
- Remote / On-site
- Salary
- Experience level
- Job grade
- Job preferences

This allows users to discover jobs according to their requirements.

---

### 📬 Application Tracking

Users can save jobs and track their application progress.

The system can maintain information such as:

- Saved jobs
- Applied jobs
- Application status
- Job history
- Search preferences

This helps users organize their job search process.

---

## 🏗️ System Architecture

```text
┌─────────────────────────────────────────────┐
│              Streamlit Frontend             │
│                                             │
│ Dashboard                                   │
│ Resume Upload                               │
│ Job Recommendations                         │
│ Job Search Preferences                      │
│ Application Tracking                        │
└──────────────────────┬──────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────┐
│             Backend Modules                 │
│                                             │
│ Authentication                              │
│ Resume Parser                               │
│ Gemini AI Analyzer                          │
│ Job Scraper                                 │
│ Job Matcher                                 │
│ Recommendation Engine                       │
└──────────────────────┬──────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────┐
│                Data Layer                   │
│                                             │
│ SQLite Database                             │
│ Users                                       │
│ Resume Analysis                             │
│ Scraped Jobs                                │
│ Search Preferences                          │
│ Saved Jobs                                  │
│ Application History                         │
└─────────────────────────────────────────────┘
```

---

## 🔄 Job Scraping Pipeline

The application uses a multi-tier job discovery system.

```text
LinkedIn Selenium
       │
       ▼
LinkedIn Guest API
       │
       ▼
Remotive API
       │
       ▼
Curated Job Pool
```

### Tier 1 – LinkedIn Selenium

Uses Selenium to retrieve LinkedIn job information when the required authentication/session configuration is available.

### Tier 1.5 – LinkedIn Guest API

Attempts to retrieve publicly available LinkedIn job information without requiring authentication.

### Tier 2 – Remotive API

Uses the Remotive API as an additional job source.

### Tier 3 – Curated Job Pool

Provides fallback job information when external job sources are unavailable.

This multi-tier approach improves the reliability of the job recommendation system.

---

# 🔄 How the System Works

The overall workflow is:

```text
User
  ↓
Upload Resume
  ↓
Resume Parser
  ↓
Extract Resume Information
  ↓
Google Gemini AI
  ↓
Resume Analysis
  ↓
Extract Skills / Experience / Education
  ↓
Job Search
  ↓
Job Scraping APIs
  ↓
Job Matching Engine
  ↓
Calculate Match Score
  ↓
Recommendation Engine
  ↓
Recommended Jobs
  ↓
User Dashboard
```

### Job Matching Workflow

```text
Resume
  ↓
Extract Skills
  ↓
Extract Experience
  ↓
Extract Education
  ↓
Job Description
  ↓
Compare Resume & Job
  ↓
Skills Score
  +
Experience Score
  +
Education Score
  +
Responsibilities Score
  ↓
Overall Match Score
  ↓
Job Grade
```

---

# 🛠️ Technology Stack

## Frontend

- Streamlit
- Python
- Plotly
- Streamlit Components

## AI

- Google Gemini API
- AI-powered resume analysis
- Skill extraction
- Resume recommendations
- Career recommendations

## Backend

- Python
- Resume parsing modules
- Job scraping modules
- Matching engine
- Recommendation engine
- Authentication system

## Database

- SQLite
- SQLite WAL mode
- User sessions
- Resume analysis
- Job information
- Saved jobs
- Application history

## Job Data

- LinkedIn
- LinkedIn Guest API
- Remotive API
- Selenium

## Security

- bcrypt password hashing
- Environment variables
- Session management
- `.env` configuration

## Development Tools

- Visual Studio Code
- Git
- GitHub
- Python Virtual Environment
- Pytest

---

# 📁 Project Structure

```text
AI-Resume_Analyzer/
│
├── app.py
├── requirements.txt
├── .env.example
├── .gitignore
│
├── backend/
│   ├── auth.py
│   ├── resume_parser.py
│   ├── llm_analyzer.py
│   ├── job_scraper.py
│   ├── job_matcher.py
│   ├── recommendation_engine.py
│   └── scraper.py
│
├── frontend/
│   ├── dashboard.py
│   ├── resume_upload.py
│   ├── job_recommendations.py
│   ├── job_search_preferences.py
│   └── recommendations.py
│
├── utils/
│   ├── database.py
│   ├── helpers.py
│   └── validators.py
│
├── data/
│   ├── database.db
│   └── sessions/
│       └── linkedin_cookies.pkl
│
├── logs/
│
├── tests/
│   └── test_suite.py
│
├── deployment_guide.md
├── user_manual.md
├── presentation_outline.md
└── README.md
```

> **Important:** Runtime files such as databases, session cookies, logs, and API credentials should not be uploaded to GitHub.

---

# ⚙️ Installation & Setup

## 1. Clone the Repository

```bash
git clone https://github.com/ramya938/AI-Resume_Analyzer.git
```

Navigate into the project:

```bash
cd AI-Resume_Analyzer
```

---

## 2. Create a Virtual Environment

### Windows

```bash
python -m venv venv
```

Activate the environment:

```bash
venv\Scripts\activate
```

### macOS / Linux

```bash
python3 -m venv venv
```

Activate:

```bash
source venv/bin/activate
```

---

## 3. Install Dependencies

```bash
pip install -r requirements.txt
```

---

# 🔐 Environment Variables

Create a `.env` file in the project root.

```env
GEMINI_API_KEY=your_google_gemini_api_key_here

DATABASE_PATH=data/database.db

LINKEDIN_EMAIL=your_linkedin_email
LINKEDIN_PASSWORD=your_linkedin_password

SCRAPER_HEADLESS=true

LOG_LEVEL=INFO

SESSION_TIMEOUT_HOURS=24

MAX_RESUME_SIZE_MB=10
```

### Required Variable

```env
GEMINI_API_KEY=your_google_gemini_api_key_here
```

The Gemini API key can be obtained from **Google AI Studio**.

⚠️ **Never upload your real API key, LinkedIn credentials, passwords, or session cookies to GitHub.**

---

# 🚫 `.gitignore`

Your `.gitignore` should include sensitive and generated files:

```gitignore
# Environment
.env
.env.*

# Python
__pycache__/
*.pyc
*.pyo

# Virtual Environment
venv/
.venv/

# Database
data/*.db
data/*.sqlite
data/*.sqlite3

# LinkedIn Sessions
data/sessions/
*.pkl

# Logs
logs/
*.log

# Testing
.pytest_cache/
.coverage
htmlcov/

# Build
dist/
build/

# IDE
.vscode/
.idea/
```

---

# ▶️ Running the Application

After activating the virtual environment and installing the dependencies:

```bash
streamlit run app.py
```

The application will normally be available at:

```text
http://localhost:8501
```

Open the URL in your browser.

---

# 🧪 Testing

The project includes an automated test suite using **Pytest**.

Run the complete test suite:

```bash
pytest tests/test_suite.py -v
```

Run tests with coverage:

```bash
pytest tests/test_suite.py --cov=backend --cov=utils --cov-report=html
```

Run a specific test class:

```bash
pytest tests/test_suite.py::TestAuth -v
```

```bash
pytest tests/test_suite.py::TestJobMatcher -v
```

```bash
pytest tests/test_suite.py::TestRecommendationEngine -v
```

```bash
pytest tests/test_suite.py::TestPerformance -v
```

---

# ✅ Testing Coverage

The test suite covers areas including:

- Authentication
- User registration
- Login validation
- Password hashing
- Resume parsing
- Skill extraction
- Job matching
- Experience matching
- Education matching
- Overall scoring
- Recommendation ranking
- Recommendation tips
- Application tracking
- Job search preferences
- Edge cases
- Unicode input
- Large job descriptions
- Performance testing

The project currently reports:

```text
98 tests passing
4 tests skipped
```

Some skipped tests depend on optional AI API availability.

---

# 📊 Match Score Formula

The overall job match score is calculated using four major signals:

```text
Overall Match Score =

(Skills × 50%)
+
(Experience × 25%)
+
(Education × 15%)
+
(Responsibilities × 10%)
```

### Score Grades

```text
85 – 100  → Excellent
70 – 84   → Good
60 – 69   → Fair
0 – 59    → Poor
```

This scoring system is intended to provide an understandable comparison between a resume and a job description.

---

# 🔌 AI Integration

The application uses **Google Gemini** to process resume information.

The basic workflow is:

```text
Resume
  ↓
Resume Parser
  ↓
Extracted Text
  ↓
Gemini API
  ↓
AI Analysis
  ↓
Skills / Experience / Recommendations
  ↓
Job Matching
```

The AI component helps transform unstructured resume information into structured information that can be used by the recommendation engine.

---

# 🔎 Job Recommendation Workflow

```text
User Resume
      ↓
Resume Analysis
      ↓
Skill Extraction
      ↓
Job Search
      ↓
Collect Job Listings
      ↓
Filter Jobs
      ↓
Calculate Match Scores
      ↓
Rank Jobs
      ↓
Display Recommendations
```

Users can then save jobs and track their application progress.

---

# 🧩 Main Application Modules

| Module | Purpose |
|---|---|
| Resume Upload | Upload and process resumes |
| Resume Parser | Extract text from PDF, DOCX and TXT |
| AI Analyzer | Analyze resumes using Gemini |
| Job Scraper | Collect job listings |
| Job Matcher | Calculate resume-job compatibility |
| Recommendation Engine | Rank jobs and provide recommendations |
| Authentication | Registration and login |
| Dashboard | Display analysis and job information |
| Job Preferences | Configure job search |
| Application Tracking | Track saved and applied jobs |
| Database | Store application data |

---

# 🎯 Project Objective

The primary objective of the **AI Resume Analyzer & Job Recommendation System** is to simplify the job search process by combining resume analysis, artificial intelligence, and job matching into a single platform.

The project aims to help users:

- Understand their resume
- Identify important skills
- Discover missing skills
- Analyze their career profile
- Find relevant job opportunities
- Compare their resume with job descriptions
- Understand job compatibility
- Filter opportunities according to their preferences
- Track their applications

---

# 🔐 Security & Privacy Considerations

The application follows basic security practices including:

- API keys stored using environment variables
- `.env` excluded from Git
- Passwords protected using bcrypt
- Backend logic separated into modules
- Session management
- Database-based user information
- LinkedIn session files excluded from Git
- Sensitive configuration kept outside the source code

Users should avoid uploading highly sensitive personal information unless necessary.

---

# 📌 Limitations

The application has several practical limitations:

- AI-generated resume analysis may not always be completely accurate.
- Job listings can change or expire.
- External job sources may become temporarily unavailable.
- LinkedIn scraping behavior may change over time.
- Job recommendation quality depends on the available resume and job-description data.
- Match scores are indicators and should not be treated as guarantees of interview or employment outcomes.
- Some features require external API availability.
- LinkedIn authentication is optional and should be configured carefully.

---

# 🚀 Future Improvements

Possible future improvements include:

- Support for additional AI models
- Advanced resume scoring
- ATS compatibility analysis
- Resume improvement suggestions
- AI-generated resume summaries
- AI-generated cover letters
- More job APIs
- Additional job platforms
- Advanced semantic job matching
- Personalized career roadmaps
- Skill-gap learning recommendations
- Interview preparation
- Automated job alerts
- Email notifications
- Advanced analytics
- Cloud database integration
- User profile customization
- Mobile application
- Cloud deployment

---

# 🌐 Deployment

The application can be deployed using platforms such as:

### Streamlit Cloud

Suitable for deploying the Streamlit application.

### Render

Can be used for hosting the application with appropriate configuration.

### Docker

The application can also be containerized for deployment in Docker-compatible environments.

Refer to:

```text
deployment_guide.md
```

for detailed deployment instructions.

---

# 📜 License

This project is developed for educational and academic purposes.

MIT License — see `LICENSE` for details.

---

# 👩‍💻 Developer

**Ramya Sri Penke**

B.Tech – Computer Science and Engineering  
Specialization: Cyber Security

---

# ⭐ Project Highlights

- 🤖 AI-powered resume analysis
- 📄 PDF, DOCX and TXT resume support
- 🧠 Google Gemini AI integration
- 💼 Job recommendation system
- 🔍 LinkedIn job discovery
- 🎯 Resume-job matching
- 📊 Interactive Streamlit dashboard
- 📈 Match score visualization
- 🔐 Secure authentication
- 🔑 bcrypt password hashing
- 💾 SQLite database
- 🔎 Advanced job filters
- 📬 Application tracking
- 🧪 Automated Pytest test suite
- ⚡ Multi-tier job scraping pipeline
- 🛡️ Environment-based API key management

---

# ❤️ Why This Project?

Finding suitable jobs can be difficult when users have to manually analyze every job description and determine whether their skills match the requirements.

This project combines **AI-powered resume analysis + job discovery + intelligent matching + application tracking** into one platform.

The goal is to make the job-search process more structured, personalized, and easier to understand.

---

## ⭐ Give the Project a Star

If you find this project useful or interesting, consider giving the repository a ⭐ on GitHub.

## 📜 License

MIT License — see [LICENSE](LICENSE) for details.

---

## 👨‍💻 Author

Built with ❤️ using Streamlit + Google Gemini + LinkedIn Job Scraping

