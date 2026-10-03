"""
app.py — Main Entry Point
===========================
AI-Powered Resume Analysis and Job Recommendation System

This is the Streamlit application entry point. It handles:
    - Environment variable loading
    - Logging configuration
    - Database initialization
    - Session state management
    - Page routing and navigation
    - Global CSS injection for premium styling

Run with:
    streamlit run app.py
"""

import os
import sys
import logging
from pathlib import Path

# ---------------------------------------------------------------------------
# 1. Environment Variable Loading (must happen before any other imports)
# ---------------------------------------------------------------------------
from dotenv import load_dotenv

load_dotenv()  # Loads .env from the project root

# ---------------------------------------------------------------------------
# 2. Logging Configuration
# ---------------------------------------------------------------------------
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)

logger = logging.getLogger(__name__)
logger.info("Starting AI Resume Analyzer...")

# ---------------------------------------------------------------------------
# 3. Streamlit Imports (after env + logging are ready)
# ---------------------------------------------------------------------------
import streamlit as st

# ---------------------------------------------------------------------------
# 4. Database Initialization
# ---------------------------------------------------------------------------
from utils.database import create_database
from backend.auth import logout_user, is_logged_in

# Initialize the database (creates tables if they don't exist)
# This runs once per Streamlit server start
try:
    create_database()
    logger.info("Database ready.")
except Exception as e:
    logger.error("Failed to initialize database: %s", e)
    st.error(f"Database initialization failed: {e}")
    st.stop()


# ===========================================================================
# 5. Page Configuration (must be the first Streamlit command after imports)
# ===========================================================================
st.set_page_config(
    page_title="AI Resume Analyzer",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ===========================================================================
# 6. Global Custom CSS — Premium Dark Theme
# ===========================================================================
def inject_custom_css():
    """Beautiful light gradient theme — no black, vibrant and clear."""
    st.markdown("""
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800;900&display=swap');

        /* ── Design Tokens ──────────────────────────────────── */
        :root {
            --purple:      #2563eb;
            --purple-lt:   #93c5fd;
            --purple-dk:   #1d4ed8;
            --indigo:      #3b82f6;
            --cyan:        #0891b2;
            --teal:        #0d9488;
            --emerald:     #059669;
            --amber:       #d97706;
            --rose:        #e11d48;
            --pink:        #db2777;

            /* Backgrounds — soft warm gradient, never black */
            --bg-page:     #f0f4ff;
            --bg-section:  #f8faff;
            --bg-card:     #ffffff;
            --bg-card2:    #f0f9ff;
            --bg-input:    #ffffff;

            /* Text */
            --txt-h:       #0f2748;
            --txt-body:    #374151;
            --txt-sub:     #6b7280;
            --txt-muted:   #9ca3af;

            /* Borders */
            --border:      #bae6fd;
            --border-dark: #bae6fd;

            --radius: 14px;
            --shadow: 0 4px 24px rgba(37,99,235,0.10);
            --shadow-lg: 0 8px 40px rgba(37,99,235,0.16);
        }

        /* ── Base font ──────────────────────────────────────── */
        html, body, [class*="css"] {
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif !important;
        }

        /* ── Page background — beautiful soft gradient ───────── */
        .stApp, [data-testid="stAppViewContainer"],
        [data-testid="stMain"], .main {
            background: linear-gradient(135deg,#f0f9ff 0%,#ecfeff 40%,#f0fdf4 100%) !important;
            background-attachment: fixed !important;
        }
        .main .block-container {
            background: transparent !important;
            padding-top: 1.5rem !important;
        }

        /* ── All Markdown text ──────────────────────────────── */
        .stMarkdown p, .stMarkdown li,
        .element-container p, .element-container li {
            color: var(--txt-body) !important;
            font-size: 0.95rem;
            line-height: 1.75;
        }
        .stMarkdown h1 { color: var(--txt-h) !important; font-size: 2rem !important; font-weight: 900 !important; }
        .stMarkdown h2 { color: var(--txt-h) !important; font-size: 1.5rem !important; font-weight: 800 !important; }
        .stMarkdown h3 { color: #1e3a5f !important;    font-size: 1.15rem !important; font-weight: 700 !important; }
        .stMarkdown h4, .stMarkdown h5, .stMarkdown h6 { color: #1d4ed8 !important; font-weight: 700 !important; }
        .stMarkdown strong, .stMarkdown b, strong, b { color: var(--txt-h) !important; font-weight: 700; }
        .stMarkdown a { color: var(--purple) !important; }
        .stMarkdown a:hover { color: var(--purple-dk) !important; text-decoration: underline; }
        .stMarkdown code {
            background: #ffffff;
            color: #1d4ed8;
            padding: 2px 7px;
            border-radius: 5px;
            font-size: 0.87rem;
            font-weight: 600;
        }

        /* ── Tabs ───────────────────────────────────────────── */
        .stTabs [data-baseweb="tab-list"] {
            background: #ffffff;
            border: 1.5px solid var(--border-dark);
            border-radius: 12px;
            padding: 5px 6px;
            gap: 4px;
            box-shadow: 0 2px 8px rgba(37,99,235,0.08);
        }
        .stTabs [data-baseweb="tab"] {
            color: var(--txt-sub) !important;
            font-weight: 600;
            font-size: 0.88rem;
            border-radius: 9px;
            padding: 0.5rem 1.1rem;
            transition: all 0.2s;
        }
        .stTabs [aria-selected="true"] {
            background: linear-gradient(135deg, #2563eb, #1d4ed8) !important;
            color: #ffffff !important;
            font-weight: 700 !important;
            box-shadow: 0 3px 12px rgba(29,78,216,0.45);
        }
        .stTabs [data-baseweb="tab"]:hover:not([aria-selected="true"]) {
            background: #ffffff !important;
            color: #1d4ed8 !important;
        }
        .stTabs [data-baseweb="tab-panel"] { padding: 1.2rem 0; }

        /* ── Expanders ──────────────────────────────────────── */
        .streamlit-expanderHeader {
            background: #ffffff !important;
            color: #1e3a5f !important;
            font-weight: 700 !important;
            font-size: 0.97rem !important;
            border: 1.5px solid var(--border-dark) !important;
            border-radius: 10px !important;
        }
        .streamlit-expanderContent {
            background: #fafafa !important;
            border: 1.5px solid var(--border) !important;
            border-top: none !important;
            border-radius: 0 0 10px 10px !important;
        }

        /* ── Form labels ───────────────────────────────────── */
        .stTextInput label, .stSelectbox label,
        .stTextArea label, .stCheckbox label,
        .stRadio label, .stSlider label,
        .stPasswordInput label, .stFileUploader label {
            color: #1e3a5f !important;
            font-weight: 700 !important;
            font-size: 0.9rem !important;
        }

        /* ── Input fields ───────────────────────────────────── */
        .stTextInput input, .stTextArea textarea,
        input[type="text"], input[type="password"], input[type="email"] {
            color: #1e3a5f !important;
            background: #ffffff !important;
            border: 1.5px solid #bae6fd !important;
            border-radius: 10px !important;
        }
        .stTextInput input:focus, .stTextArea textarea:focus {
            border-color: #1d4ed8 !important;
            box-shadow: 0 0 0 3px rgba(29,78,216,0.2) !important;
        }
        ::placeholder { color: #9ca3af !important; }

        /* ── Selectbox ──────────────────────────────────────── */
        [data-testid="stSelectbox"] > div > div {
            color: #1e3a5f !important;
            background: #ffffff !important;
            border: 1.5px solid #bae6fd !important;
            border-radius: 10px !important;
        }

        /* ── Buttons — vibrant purple gradient ──────────────── */
        .stButton > button {
            background: linear-gradient(135deg, #2563eb 0%, #0891b2 100%);
            color: white !important;
            border: none;
            border-radius: 10px;
            padding: 0.6rem 1.6rem;
            font-weight: 700;
            font-size: 0.92rem;
            transition: all 0.25s ease;
            box-shadow: 0 3px 14px rgba(29,78,216,0.45);
            letter-spacing: 0.01em;
        }
        .stButton > button:hover {
            background: linear-gradient(135deg, #1d4ed8 0%, #0369a1 100%);
            transform: translateY(-2px);
            box-shadow: 0 6px 24px rgba(37,99,235,0.45);
            color: white !important;
        }

        /* ── Alerts ─────────────────────────────────────────── */
        [data-testid="stAlert"] {
            border-radius: 12px !important;
            border-left-width: 4px !important;
        }
        [data-testid="stAlert"] p, [data-testid="stAlert"] div, .stAlert p {
            color: #1e3a5f !important;
            font-weight: 500;
        }

        /* ── Metrics ────────────────────────────────────────── */
        [data-testid="stMetric"] {
            background: #ffffff;
            border: 1.5px solid var(--border-dark);
            border-radius: var(--radius);
            padding: 1.1rem;
            box-shadow: var(--shadow);
        }
        [data-testid="stMetricLabel"] > div {
            color: #6b7280 !important;
            font-weight: 700;
            font-size: 0.82rem !important;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }
        [data-testid="stMetricValue"] > div {
            color: #1e3a5f !important;
            font-weight: 900;
        }

        /* ── Progress bar ───────────────────────────────────── */
        [data-testid="stProgressBar"] > div {
            background: linear-gradient(90deg, #2563eb, #0891b2);
            border-radius: 6px;
        }

        /* ── File uploader ──────────────────────────────────── */
        [data-testid="stFileUploader"] {
            background: #f0f9ff !important;
            border: 2px dashed #93c5fd !important;
            border-radius: var(--radius);
        }
        [data-testid="stFileUploader"]:hover {
            background: #ffffff !important;
            border-color: #1d4ed8 !important;
        }
        [data-testid="stFileUploader"] span,
        [data-testid="stFileUploader"] p { color: #1e40af !important; font-weight: 500; }

        /* ── Sidebar — light sky-blue, text always readable ────── */
        [data-testid="stSidebar"] {
            background: linear-gradient(180deg, #dbeafe 0%, #e0f2fe 50%, #dbeafe 100%) !important;
            border-right: 2px solid #bae6fd !important;
            box-shadow: 4px 0 16px rgba(37,99,235,0.10);
        }
        [data-testid="stSidebar"] .stMarkdown h1,
        [data-testid="stSidebar"] .stMarkdown h2,
        [data-testid="stSidebar"] .stMarkdown h3 {
            color: #0f2748 !important;
            font-weight: 800 !important;
        }
        [data-testid="stSidebar"] .stMarkdown p,
        [data-testid="stSidebar"] .stMarkdown span,
        [data-testid="stSidebar"] .stMarkdown div,
        [data-testid="stSidebar"] p,
        [data-testid="stSidebar"] span {
            color: #0f2748 !important;
            font-weight: 500;
        }
        [data-testid="stSidebar"] .stButton > button {
            color: #0f2748 !important;
            background: #ffffff !important;
            box-shadow: 0 1px 4px rgba(37,99,235,0.12) !important;
            border: 1.5px solid #93c5fd !important;
            text-align: left;
            font-weight: 700;
            font-size: 0.92rem;
            border-radius: 10px;
            margin-bottom: 5px;
            letter-spacing: 0.01em;
        }
        [data-testid="stSidebar"] .stButton > button:hover {
            background: #2563eb !important;
            border-color: #2563eb !important;
            color: #ffffff !important;
            transform: translateX(3px);
            box-shadow: 0 3px 12px rgba(37,99,235,0.30) !important;
        }

        /* ── Checkbox / Radio ────────────────────────────────── */
        [data-testid="stCheckbox"] label span,
        [data-testid="stRadio"] label span { color: var(--txt-body) !important; }

        /* ── Divider ─────────────────────────────────────────── */
        hr {
            border: none;
            border-top: 2px solid #bae6fd;
            margin: 1.8rem 0;
        }

        /* ── Spinner ─────────────────────────────────────────── */
        [data-testid="stSpinner"] > div { color: #1d4ed8 !important; }

        /* ── Scrollbar ───────────────────────────────────────── */
        ::-webkit-scrollbar { width: 6px; height: 6px; }
        ::-webkit-scrollbar-track { background: #eff6ff; }
        ::-webkit-scrollbar-thumb { background: #60a5fa; border-radius: 3px; }
        ::-webkit-scrollbar-thumb:hover { background: #2563eb; }

        /* ── Animations ──────────────────────────────────────── */
        @keyframes fadeInUp {
            from { opacity: 0; transform: translateY(20px); }
            to   { opacity: 1; transform: translateY(0); }
        }
        @keyframes shimmer {
            0%   { background-position: -200% center; }
            100% { background-position:  200% center; }
        }
        .fade-in { animation: fadeInUp 0.5s ease-out; }

        /* ── Utility card ────────────────────────────────────── */
        .glass-card {
            background: #ffffff;
            border: 1.5px solid #bae6fd;
            border-radius: var(--radius);
            padding: 1.5rem;
            box-shadow: var(--shadow);
            transition: transform 0.2s ease, box-shadow 0.2s ease;
        }
        .glass-card:hover {
            transform: translateY(-3px);
            box-shadow: var(--shadow-lg);
            border-color: #93c5fd;
        }

        /* ── Hide Streamlit chrome ───────────────────────────── */
        #MainMenu { visibility: hidden; }
        footer    { visibility: hidden; }
        header    { visibility: hidden; }
    </style>
    """, unsafe_allow_html=True)



# ===========================================================================
# 7. Session State Initialization
# ===========================================================================
def init_session_state():
    """Initialize all session state variables with defaults."""
    defaults = {
        "authenticated": False,
        "user_id": None,
        "username": None,
        "email": None,
        "current_page": "login",
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


# ===========================================================================
# 8. Navigation
# ===========================================================================
def render_sidebar():
    """Render the sidebar with navigation links."""
    with st.sidebar:
        # App branding
        st.markdown("""
        <div style="text-align:center; padding: 1rem 0 1.5rem;">
            <span style="font-size: 2.8rem;">🤖</span>
            <h2 style="
                margin: 0.5rem 0 0;
                font-weight: 900;
                color: #0f2748;
                font-size: 1.25rem;
                letter-spacing: -0.02em;
            ">AI Resume Analyzer</h2>
            <p style="color:#0369a1; font-size:0.78rem; margin:3px 0 0; font-weight:600;">Powered by Gemini AI</p>
        </div>
        """, unsafe_allow_html=True)

        st.divider()

        if st.session_state.get("authenticated"):
            # -- Welcome chip --
            uname = st.session_state.get('username', 'User')
            st.markdown(f"""
            <div style="background:#ffffff; border:1.5px solid #93c5fd; border-radius:10px;
                        padding:0.6rem 0.9rem; margin-bottom:0.5rem; display:flex;
                        align-items:center; gap:8px;">
                <span style="font-size:1.3rem;">👋</span>
                <div>
                    <div style="color:#0f2748; font-size:0.78rem; font-weight:600;">Welcome back</div>
                    <div style="color:#0284c7; font-size:0.95rem; font-weight:800;">{uname}</div>
                </div>
            </div>
            """, unsafe_allow_html=True)

            # Navigation buttons — dark text, clearly visible
            nav_items = [
                ("📊", "Dashboard",          "dashboard",       "nav_dashboard"),
                ("📄", "Upload Resume",       "upload",          "nav_upload"),
                ("🧠", "Analyze Resume",      "analysis",        "nav_analysis"),
                ("📝", "Score Dashboard",     "score_dashboard", "nav_score"),
                ("🔍", "Skills Gap",          "skills_gap",      "nav_skills_gap"),
                ("💼", "Job Recommendations", "recommendations",  "nav_jobs"),
                ("👤", "My Profile",          "profile",         "nav_profile"),
                ("⚙️", "Settings",            "settings",        "nav_settings"),
            ]
            for icon, label, page, key in nav_items:
                if st.button(f"{icon}  {label}", use_container_width=True, key=key):
                    st.session_state.current_page = page
                    st.rerun()

            st.markdown("<div style='height:4px'></div>", unsafe_allow_html=True)

            # Logout — red tinted
            st.markdown("""
            <style>
            [data-testid="stSidebar"] [data-testid="nav_logout"] > button,
            div[data-testid="stSidebar"] div[data-testid="stVerticalBlock"] div:last-child .stButton > button {
                background: #fff1f2 !important;
                border-color: #fca5a5 !important;
                color: #9f1239 !important;
            }
            </style>
            """, unsafe_allow_html=True)
            if st.button("🚪  Logout", use_container_width=True, key="nav_logout"):
                logout_user()
                st.rerun()

        else:
            # -- Guest menu --
            st.markdown("""
            <div style="color:#0369a1; font-size:0.82rem; font-weight:700;
                        margin-bottom:8px; text-transform:uppercase; letter-spacing:0.5px;">
                Get Started
            </div>
            """, unsafe_allow_html=True)
            if st.button("🔑  Login", use_container_width=True, key="nav_login"):
                st.session_state.current_page = "login"
                st.rerun()
            if st.button("📝  Register", use_container_width=True, key="nav_register"):
                st.session_state.current_page = "register"
                st.rerun()

        # Footer
        st.divider()
        st.markdown(
            '<p style="text-align:center; font-size:0.75rem; color:#0369a1; font-weight:600;">'
            "v1.0.0 &bull; Streamlit &amp; Gemini AI"
            "</p>",
            unsafe_allow_html=True,
        )


# ===========================================================================
# 9. Page Router
# ===========================================================================
def route_page():
    """
    Route to the correct page based on session state.

    Pages are lazily imported to avoid circular imports and to keep
    startup time fast.
    """
    page = st.session_state.get("current_page", "login")

    # Guard: redirect unauthenticated users to login
    protected_pages = {"dashboard", "upload", "analysis", "score_dashboard",
                        "skills_gap", "recommendations", "profile", "settings"}
    if page in protected_pages and not is_logged_in():
        st.session_state.current_page = "login"
        page = "login"

    try:
        if page == "login":
            from frontend.login import show
            show()

        elif page == "register":
            from frontend.registration import show
            show()

        elif page == "dashboard":
            from frontend.dashboard import show
            show()

        elif page == "upload":
            from frontend.upload_resume import show
            show()

        elif page == "analysis":
            from frontend.analysis_page import show
            show()

        elif page == "skills_gap":
            from frontend.skills_gap_page import show
            show()

        elif page == "score_dashboard":
            from frontend.analysis import show
            show()

        elif page == "recommendations":
            from frontend.recommendations import show
            show()

        elif page == "profile":
            from frontend.profile import show
            show()

        elif page == "settings":
            from frontend.settings import show
            show()

        else:
            st.error(f"Unknown page: {page}")
            logger.warning("Attempted to route to unknown page: %s", page)

    except ImportError as e:
        # Graceful fallback while pages are being developed
        st.warning(f"⚠️ Page module not yet implemented: **{page}**")
        st.info(f"Import error: `{e}`")
        logger.warning("Page import failed for '%s': %s", page, e)

    except Exception as e:
        st.error(f"An error occurred: {e}")
        logger.exception("Unhandled error on page '%s'", page)


# ===========================================================================
# 10. Main Application
# ===========================================================================
def main():
    """Main application entry point."""
    # Inject premium CSS
    inject_custom_css()

    # Initialize session state
    init_session_state()

    # Render sidebar navigation
    render_sidebar()

    # Route to the active page
    route_page()


# ---------------------------------------------------------------------------
# Entry Point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    main()
