"""
scraper.py — Production-Grade Selenium Scraper
================================================
File: backend/scraper.py
Project: AI Resume Analyzer & Job Recommendation System

Senior-level automation module that drives a headless Chrome browser to
interact with LinkedIn (job search, profile data) with full resilience:

    ┌─────────────────────────────────────────────────────────┐
    │  Features                                               │
    │  ─────────────────────────────────────────────────────  │
    │  Selenium 4 + ChromeDriver (auto-managed via WDM)       │
    │  Headless mode with anti-detection hardening            │
    │  11-agent User-Agent pool with per-session rotation     │
    │  Token-bucket rate limiter (configurable RPS)           │
    │  Gaussian-jitter random delays between actions          │
    │  Pickle-based session / cookie persistence              │
    │  CAPTCHA detection + optional manual-fallback handler   │
    │  Exponential-backoff retry decorator (configurable)     │
    │  Structured logging (file + console, JSON-safe)         │
    │  SIGTERM / KeyboardInterrupt graceful shutdown          │
    └─────────────────────────────────────────────────────────┘

Environment variables (in .env):
    LINKEDIN_EMAIL    — account e-mail
    LINKEDIN_PASSWORD — account password
    SCRAPER_HEADLESS  — "true" / "false"  (default true)
    SCRAPER_MAX_RPS   — max requests per second (default 0.5)
    SCRAPER_TIMEOUT   — page-load timeout in seconds (default 30)
    CHROME_PROFILE_DIR — path to persist Chrome profile data

Usage:
    from backend.scraper import LinkedInScraper

    with LinkedInScraper() as scraper:
        scraper.login_to_linkedin()
        jobs = scraper.search_jobs(keywords="Python Developer", location="Remote")

Or standalone:
    python -m backend.scraper
"""

from __future__ import annotations

import atexit
import functools
import logging
import math
import os
import pickle
import random
import signal
import sys
import time
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

# ---------------------------------------------------------------------------
# Third-party imports — all installed in venv
# ---------------------------------------------------------------------------
from dotenv import load_dotenv

try:
    from selenium import webdriver
    from selenium.common.exceptions import (
        ElementClickInterceptedException,
        ElementNotInteractableException,
        NoSuchElementException,
        StaleElementReferenceException,
        TimeoutException,
        WebDriverException,
    )
    from selenium.webdriver.chrome.options import Options as ChromeOptions
    from selenium.webdriver.chrome.service import Service as ChromeService
    from selenium.webdriver.common.action_chains import ActionChains
    from selenium.webdriver.common.by import By
    from selenium.webdriver.common.keys import Keys
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import WebDriverWait
    SELENIUM_AVAILABLE = True
except ImportError:
    SELENIUM_AVAILABLE = False

try:
    from webdriver_manager.chrome import ChromeDriverManager
    WDM_AVAILABLE = True
except ImportError:
    WDM_AVAILABLE = False

# ---------------------------------------------------------------------------
# Load environment variables
# ---------------------------------------------------------------------------
load_dotenv()

# ---------------------------------------------------------------------------
# Module-level logger
# ---------------------------------------------------------------------------
LOG_LEVEL_STR = os.getenv("LOG_LEVEL", "INFO").upper()
LOG_LEVEL     = getattr(logging, LOG_LEVEL_STR, logging.INFO)

_log_dir = Path("logs")
_log_dir.mkdir(exist_ok=True)
_log_file = _log_dir / f"scraper_{datetime.now().strftime('%Y%m%d')}.log"

# Root logger for this module
logger = logging.getLogger("scraper")
logger.setLevel(LOG_LEVEL)

if not logger.handlers:
    # Console handler — INFO+
    _ch = logging.StreamHandler(sys.stdout)
    _ch.setLevel(logging.INFO)
    _ch.setFormatter(logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    logger.addHandler(_ch)

    # File handler — DEBUG+
    _fh = logging.FileHandler(_log_file, encoding="utf-8")
    _fh.setLevel(logging.DEBUG)
    _fh.setFormatter(logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(funcName)s:%(lineno)d | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    logger.addHandler(_fh)

logger.info("Scraper module loaded — log file: %s", _log_file)


# ===========================================================================
# Configuration Constants
# ===========================================================================

# LinkedIn URLs
LINKEDIN_BASE     = "https://www.linkedin.com"
LINKEDIN_LOGIN    = "https://www.linkedin.com/login"
LINKEDIN_FEED     = "https://www.linkedin.com/feed/"
LINKEDIN_JOBS     = "https://www.linkedin.com/jobs/search/"
LINKEDIN_LOGOUT   = "https://www.linkedin.com/m/logout/"

# Session file locations
SESSION_DIR  = Path("data") / "sessions"
COOKIE_FILE  = SESSION_DIR / "linkedin_cookies.pkl"
PROFILE_DIR  = Path(os.getenv("CHROME_PROFILE_DIR", str(SESSION_DIR / "chrome_profile")))

# Scraper tuning
HEADLESS          = os.getenv("SCRAPER_HEADLESS", "true").lower() == "true"
MAX_RPS           = float(os.getenv("SCRAPER_MAX_RPS", "0.5"))      # max requests/sec
PAGE_TIMEOUT      = int(os.getenv("SCRAPER_TIMEOUT", "30"))          # seconds
IMPLICIT_WAIT     = 5                                                 # seconds
MAX_RETRIES       = 3
RETRY_BACKOFF_BASE= 2.0                                               # exponential base
MIN_DELAY_S       = 1.5                                               # min inter-action delay
MAX_DELAY_S       = 4.5                                               # max inter-action delay
JITTER_SIGMA      = 0.8                                               # Gaussian jitter σ

# LinkedIn credentials (never hard-code — always from .env)
LINKEDIN_EMAIL    = os.getenv("LINKEDIN_EMAIL", "")
LINKEDIN_PASSWORD = os.getenv("LINKEDIN_PASSWORD", "")


# ===========================================================================
# User-Agent Pool  (desktop Chrome, rotated per session)
# ===========================================================================

# Desktop-only pool — mobile UAs trigger different LinkedIn layouts
USER_AGENTS: list[str] = [
    # Chrome 124 / Windows  (primary)
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    # Chrome 123 / Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    # Chrome 122 / Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    # Chrome 121 / Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    # Chrome 124 / macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    # Chrome 122 / macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    # Edge 124 / Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
    # Edge 123 / Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36 Edg/123.0.0.0",
]


# ===========================================================================
# Utility: Token-Bucket Rate Limiter
# ===========================================================================

class _TokenBucket:
    """
    Thread-safe token-bucket rate limiter.

    Ensures no more than `rate` requests per second are fired.
    Excess calls block (sleep) until a token is available.

    Args:
        rate:     Maximum requests per second (float, e.g. 0.5 = one per 2 s).
        capacity: Maximum burst size (defaults to max(1, rate)).
    """

    def __init__(self, rate: float = 0.5, capacity: Optional[float] = None):
        self._rate     = max(rate, 0.01)
        self._capacity = capacity or max(1.0, rate)
        self._tokens   = self._capacity
        self._last     = time.monotonic()
        self._lock     = threading.Lock()

    def acquire(self) -> None:
        """Block until a token is available, then consume one."""
        with self._lock:
            now     = time.monotonic()
            elapsed = now - self._last
            self._tokens = min(
                self._capacity,
                self._tokens + elapsed * self._rate,
            )
            self._last = now

            if self._tokens < 1.0:
                sleep_for = (1.0 - self._tokens) / self._rate
                logger.debug("Rate limiter: sleeping %.2fs", sleep_for)
                time.sleep(sleep_for)
                self._tokens = 0.0
            else:
                self._tokens -= 1.0


# Module-level rate limiter (shared across all driver instances)
_rate_limiter = _TokenBucket(rate=MAX_RPS)


# ===========================================================================
# Utility: Retry Decorator
# ===========================================================================

def retry_request(
    max_attempts: int    = MAX_RETRIES,
    backoff_base: float  = RETRY_BACKOFF_BASE,
    exceptions:   tuple  = (WebDriverException, TimeoutException, IOError),
    reraise:      bool   = True,
) -> Callable:
    """
    Exponential-backoff retry decorator for Selenium operations.

    Behaviour:
        - Attempt 1  : immediate
        - Attempt N>1: sleep backoff_base^(N-1) + jitter seconds
        - If all attempts fail and reraise=True: re-raises the last exception
        - If reraise=False: returns None and logs the failure

    Args:
        max_attempts: Maximum number of total attempts (default 3).
        backoff_base: Base for exponential backoff (default 2.0 → 2, 4, 8 s).
        exceptions:   Tuple of exception types to catch and retry.
        reraise:      Whether to re-raise the last exception after exhausting retries.

    Usage:
        @retry_request(max_attempts=4, backoff_base=3)
        def fetch_page(url): ...
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exc: Optional[Exception] = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as exc:
                    last_exc = exc
                    if attempt == max_attempts:
                        logger.error(
                            "retry_request: '%s' failed after %d attempts — %s",
                            func.__name__, max_attempts, exc,
                        )
                        if reraise:
                            raise
                        return None

                    # Exponential backoff with ±jitter
                    delay = (backoff_base ** (attempt - 1)) + random.uniform(0.3, 1.0)
                    logger.warning(
                        "retry_request: '%s' attempt %d/%d failed (%s). "
                        "Retrying in %.1fs...",
                        func.__name__, attempt, max_attempts, exc, delay,
                    )
                    time.sleep(delay)
            return None  # unreachable but satisfies type checker
        return wrapper
    return decorator


# ===========================================================================
# Utility: Human-like random delay
# ===========================================================================

def _human_delay(
    min_s:   float = MIN_DELAY_S,
    max_s:   float = MAX_DELAY_S,
    sigma:   float = JITTER_SIGMA,
) -> None:
    """
    Sleep for a Gaussian-jittered duration between min_s and max_s.

    Simulates human reaction time — harder to detect via timing analysis
    than uniform random delays.

    Args:
        min_s:  Minimum sleep duration in seconds.
        max_s:  Maximum sleep duration in seconds.
        sigma:  Standard deviation of the Gaussian jitter.
    """
    mid   = (min_s + max_s) / 2.0
    jitter = random.gauss(0, sigma)
    sleep  = max(min_s, min(max_s, mid + jitter))
    logger.debug("Human delay: %.2fs", sleep)
    time.sleep(sleep)


# ===========================================================================
# Core Scraper Class
# ===========================================================================

class LinkedInScraper:
    """
    Production-grade LinkedIn automation driver.

    Manages the full lifecycle of a Selenium Chrome session:
        1. Initialise headless Chrome with anti-detection options
        2. Optionally restore a previous session (cookies + local storage)
        3. Authenticate to LinkedIn with credential validation
        4. Expose structured job-search and profile-data methods
        5. Persist the session for reuse on next launch
        6. Handle CAPTCHA detection with configurable callbacks
        7. Shut down gracefully on SIGTERM / KeyboardInterrupt

    Usage:
        # Context manager (recommended)
        with LinkedInScraper(headless=True) as scraper:
            scraper.login_to_linkedin()
            jobs = scraper.search_jobs("Python Developer", "Remote")

        # Manual lifecycle
        scraper = LinkedInScraper()
        scraper.initialize_driver()
        scraper.login_to_linkedin()
        ...
        scraper.close_driver()

    Args:
        headless:       Run Chrome in headless mode (no visible window).
        user_agent:     Override the User-Agent. If None, a random one is picked.
        profile_dir:    Chrome user-data-dir for cookie / storage persistence.
        session_file:   Path to the pickle file for cookie backup.
        rate:           Max requests per second for the token-bucket limiter.
        page_timeout:   Selenium page-load timeout in seconds.
        captcha_callback: Optional callable invoked when a CAPTCHA is detected.
                          Signature: callback(driver) → bool (True = solved).
    """

    def __init__(
        self,
        headless:          bool                         = HEADLESS,
        user_agent:        Optional[str]                = None,
        profile_dir:       Path                         = PROFILE_DIR,
        session_file:      Path                         = COOKIE_FILE,
        rate:              float                        = MAX_RPS,
        page_timeout:      int                          = PAGE_TIMEOUT,
        captcha_callback:  Optional[Callable]           = None,
    ) -> None:
        self.headless          = headless
        self.user_agent        = user_agent or random.choice(USER_AGENTS)
        self.profile_dir       = Path(profile_dir)
        self.session_file      = Path(session_file)
        self.page_timeout      = page_timeout
        self.captcha_callback  = captcha_callback
        self._rate_limiter     = _TokenBucket(rate=rate)

        self.driver: Optional["webdriver.Chrome"] = None
        self._is_logged_in: bool  = False
        self._shutdown_called: bool = False

        # Ensure data dirs exist
        self.session_file.parent.mkdir(parents=True, exist_ok=True)
        self.profile_dir.mkdir(parents=True, exist_ok=True)

        # Register graceful shutdown hooks
        atexit.register(self._atexit_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
        if sys.platform != "win32":
            signal.signal(signal.SIGHUP, self._signal_handler)

        logger.info(
            "LinkedInScraper created — headless=%s UA='%s...'",
            headless, self.user_agent[:50],
        )

    # =========================================================================
    # Context Manager Support
    # =========================================================================

    def __enter__(self) -> "LinkedInScraper":
        self.initialize_driver()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        if exc_type:
            logger.error("Scraper context exiting with error: %s — %s", exc_type.__name__, exc_val)
        self.close_driver()
        return False  # do not suppress exceptions

    # =========================================================================
    # 1. initialize_driver()
    # =========================================================================

    def initialize_driver(self) -> "webdriver.Chrome":
        """
        Build and return a configured Selenium Chrome WebDriver.

        Configuration applied:
            - Headless mode (configurable)
            - Anti-detection: disable automation flags, blink features
            - Persistent user-data-dir for cookies & local storage
            - Custom User-Agent
            - Disabled images/plugins for speed
            - Window size set to common 1920×1080
            - Page-load + implicit-wait timeouts

        Returns:
            Configured webdriver.Chrome instance (also stored as self.driver).

        Raises:
            RuntimeError: If Selenium or webdriver-manager is not installed.
            WebDriverException: If Chrome cannot be launched.
        """
        if not SELENIUM_AVAILABLE:
            raise RuntimeError(
                "Selenium is not installed. Run: pip install selenium"
            )
        if not WDM_AVAILABLE:
            raise RuntimeError(
                "webdriver-manager is not installed. Run: pip install webdriver-manager"
            )

        logger.info("initialize_driver: configuring Chrome options...")

        options = ChromeOptions()

        # ── Core headless / display settings ─────────────────────────────
        if self.headless:
            # new-headless mode (Chrome 112+) — better JS support
            options.add_argument("--headless=new")

        options.add_argument("--window-size=1920,1080")
        options.add_argument("--start-maximized")

        # ── Anti-detection hardening ──────────────────────────────────────
        #   LinkedIn actively checks for automation fingerprints
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option("useAutomationExtension", False)

        # ── Performance & stability ───────────────────────────────────────
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--disable-extensions")
        options.add_argument("--disable-infobars")
        options.add_argument("--disable-notifications")
        options.add_argument("--disable-popup-blocking")
        options.add_argument("--disable-default-apps")
        options.add_argument("--disable-web-security")       # for cookie injection
        options.add_argument("--allow-running-insecure-content")
        options.add_argument("--ignore-certificate-errors")
        options.add_argument("--log-level=3")                # suppress Chrome console spam
        options.add_argument("--silent")

        # ── Memory optimisation ───────────────────────────────────────────
        options.add_argument("--disable-plugins-discovery")
        options.add_argument("--disable-translate")
        options.add_argument("--safebrowsing-disable-auto-update")
        prefs = {
            "profile.managed_default_content_settings.images": 2,   # block images
            "profile.default_content_setting_values.notifications": 2,
            "credentials_enable_service": False,
            "profile.password_manager_enabled": False,
        }
        options.add_experimental_option("prefs", prefs)

        # ── User-Agent ────────────────────────────────────────────────────
        options.add_argument(f"--user-agent={self.user_agent}")

        # ── Persistent user-data-dir (Chrome profile) ─────────────────────
        options.add_argument(f"--user-data-dir={self.profile_dir.resolve()}")

        # ── Auto-managed ChromeDriver ──────────────────────────────────────
        logger.info("initialize_driver: resolving ChromeDriver via webdriver-manager...")
        try:
            driver_path = ChromeDriverManager().install()
            service     = ChromeService(executable_path=driver_path)
            logger.info("initialize_driver: ChromeDriver resolved at %s", driver_path)
        except Exception as exc:
            logger.warning(
                "initialize_driver: webdriver-manager failed (%s), "
                "falling back to PATH ChromeDriver", exc
            )
            service = ChromeService()   # will use chromedriver from PATH

        # ── Launch browser ─────────────────────────────────────────────────
        logger.info("initialize_driver: launching Chrome (headless=%s)...", self.headless)
        self.driver = webdriver.Chrome(service=service, options=options)

        # ── Post-launch JavaScript patches ─────────────────────────────────
        #   These override navigator.webdriver which Selenium sets to True
        self.driver.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument",
            {
                "source": """
                // Hide webdriver property
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined
                });

                // Restore normal plugin array length
                Object.defineProperty(navigator, 'plugins', {
                    get: () => [1, 2, 3, 4, 5]
                });

                // Restore normal languages
                Object.defineProperty(navigator, 'languages', {
                    get: () => ['en-US', 'en']
                });

                // Fake chrome runtime
                window.chrome = {
                    runtime: {}
                };

                // Mask permission query
                const originalQuery = window.navigator.permissions.query;
                window.navigator.permissions.query = (parameters) => (
                    parameters.name === 'notifications'
                        ? Promise.resolve({ state: Notification.permission })
                        : originalQuery(parameters)
                );
                """
            },
        )

        # ── Timeouts ──────────────────────────────────────────────────────
        self.driver.set_page_load_timeout(self.page_timeout)
        self.driver.implicitly_wait(IMPLICIT_WAIT)

        logger.info(
            "initialize_driver: Chrome launched successfully (session_id=%s)",
            self.driver.session_id,
        )
        return self.driver

    # =========================================================================
    # 2. login_to_linkedin()
    # =========================================================================

    @retry_request(max_attempts=3, backoff_base=2)
    def login_to_linkedin(
        self,
        email:    str = "",
        password: str = "",
    ) -> bool:
        """
        Authenticate to LinkedIn using credentials from .env or arguments.

        Strategy:
            1. Attempt to restore a previous session via load_session()
            2. If restored session is still valid → return True (no login needed)
            3. Otherwise navigate to /login and submit credentials
            4. Wait for the feed page to confirm success
            5. Save session cookies for future reuse

        Args:
            email:    LinkedIn account e-mail (defaults to LINKEDIN_EMAIL env var).
            password: LinkedIn password (defaults to LINKEDIN_PASSWORD env var).

        Returns:
            True on success, False on failure.

        Raises:
            RuntimeError: If driver is not initialised.
            ValueError:   If credentials are empty.
        """
        self._require_driver()
        email    = email    or LINKEDIN_EMAIL
        password = password or LINKEDIN_PASSWORD

        if not email or not password:
            raise ValueError(
                "LinkedIn credentials not found. "
                "Set LINKEDIN_EMAIL and LINKEDIN_PASSWORD in your .env file."
            )

        logger.info("login_to_linkedin: attempting login for %s", email[:4] + "***")

        # ── Step 1: Try session restore ───────────────────────────────────
        if self.load_session():
            logger.info("login_to_linkedin: session restored — verifying validity...")
            self._rate_limiter.acquire()
            try:
                self.driver.get(LINKEDIN_FEED)
                _human_delay(2.0, 4.0)

                if self._is_on_feed():
                    logger.info("login_to_linkedin: session valid. Skipping credential login.")
                    self._is_logged_in = True
                    return True
                else:
                    logger.info("login_to_linkedin: restored session expired. Proceeding with login.")
            except Exception as exc:
                logger.warning("login_to_linkedin: session restore check failed — %s", exc)

        # ── Step 2: Navigate to login page ────────────────────────────────
        self._rate_limiter.acquire()
        logger.info("login_to_linkedin: navigating to %s", LINKEDIN_LOGIN)
        self.driver.get(LINKEDIN_LOGIN)
        _human_delay(2.5, 4.0)

        # ── Step 3: Handle CAPTCHA on login page ──────────────────────────
        if self.handle_captcha():
            logger.info("login_to_linkedin: CAPTCHA resolved on login page")
            _human_delay(1.5, 3.0)

        # ── Step 4: Fill credentials ──────────────────────────────────────
        try:
            wait = WebDriverWait(self.driver, 15)

            # Email field
            email_field = wait.until(
                EC.presence_of_element_located((By.ID, "username"))
            )
            email_field.clear()
            self._type_like_human(email_field, email)
            _human_delay(0.8, 1.5)

            # Password field
            pw_field = self.driver.find_element(By.ID, "password")
            pw_field.clear()
            self._type_like_human(pw_field, password)
            _human_delay(0.6, 1.2)

            # Submit
            logger.debug("login_to_linkedin: clicking Sign In button")
            submit_btn = self.driver.find_element(
                By.CSS_SELECTOR, "button[type='submit'], button[data-litms-control-urn='login-submit']"
            )
            submit_btn.click()

        except (NoSuchElementException, TimeoutException) as exc:
            logger.error("login_to_linkedin: could not locate login form fields — %s", exc)
            return False

        # ── Step 5: Wait for successful redirect ──────────────────────────
        _human_delay(3.0, 6.0)

        # Check for CAPTCHA challenge post-submit
        if self.handle_captcha():
            logger.info("login_to_linkedin: CAPTCHA detected post-submit — resolved")
            _human_delay(2.0, 4.0)

        # Verify we landed on the feed
        if self._is_on_feed():
            logger.info("login_to_linkedin: SUCCESS — logged in as %s", email[:4] + "***")
            self._is_logged_in = True
            self.save_session()
            return True

        # Check for security verification page
        current_url = self.driver.current_url
        if "checkpoint" in current_url or "security-verification" in current_url:
            logger.warning(
                "login_to_linkedin: LinkedIn security challenge triggered at %s. "
                "Manual intervention may be required.", current_url
            )
            return False

        # Check for wrong-password error
        try:
            error_elem = self.driver.find_element(By.CSS_SELECTOR, ".alert-content, #error-for-password")
            error_text = error_elem.text.strip()
            logger.error("login_to_linkedin: login error — '%s'", error_text)
        except NoSuchElementException:
            logger.error(
                "login_to_linkedin: unknown failure — current URL: %s", current_url
            )

        return False

    # =========================================================================
    # 3. save_session()
    # =========================================================================

    def save_session(self) -> bool:
        """
        Persist current browser cookies to a pickle file for session reuse.

        Also captures localStorage and sessionStorage snapshots via JavaScript
        and stores them alongside the cookies for a more complete session state.

        Returns:
            True if saved successfully, False otherwise.
        """
        self._require_driver()
        try:
            cookies = self.driver.get_cookies()
            if not cookies:
                logger.warning("save_session: no cookies to save")
                return False

            # Capture storage blobs
            local_storage = {}
            session_storage = {}
            try:
                local_storage = self.driver.execute_script(
                    "return Object.fromEntries(Object.entries(localStorage));"
                )
                session_storage = self.driver.execute_script(
                    "return Object.fromEntries(Object.entries(sessionStorage));"
                )
            except Exception:
                pass  # storage access may be denied on some pages

            payload = {
                "cookies":          cookies,
                "local_storage":    local_storage,
                "session_storage":  session_storage,
                "user_agent":       self.user_agent,
                "saved_at":         datetime.now(timezone.utc).isoformat(),
                "saved_url":        self.driver.current_url,
            }

            self.session_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.session_file, "wb") as f:
                pickle.dump(payload, f, protocol=pickle.HIGHEST_PROTOCOL)

            logger.info(
                "save_session: saved %d cookies to %s",
                len(cookies), self.session_file,
            )
            return True

        except Exception as exc:
            logger.error("save_session: failed — %s", exc)
            return False

    # =========================================================================
    # 4. load_session()
    # =========================================================================

    def load_session(self) -> bool:
        """
        Restore a previously saved session by injecting pickled cookies.

        Process:
            1. Check that the session file exists and is < 24 hours old
            2. Navigate to LinkedIn base URL (required before cookie injection)
            3. Delete all existing cookies
            4. Inject saved cookies one by one (skip expired ones)
            5. Optionally restore localStorage

        Returns:
            True if cookies were injected, False if no valid session file.
        """
        self._require_driver()

        if not self.session_file.exists():
            logger.info("load_session: no session file found at %s", self.session_file)
            return False

        # li_at cookie expires in ~8760h (1 year) — keep session for 30 days
        age_seconds = time.time() - self.session_file.stat().st_mtime
        age_hours   = age_seconds / 3600
        if age_hours > 720:   # 30 days
            logger.warning(
                "load_session: session file is %.1fh old (>720h) — likely expired",
                age_hours,
            )
            try:
                self.session_file.unlink()
            except OSError:
                pass
            return False

        try:
            with open(self.session_file, "rb") as f:
                payload = pickle.load(f)

            cookies         = payload.get("cookies", [])
            local_storage   = payload.get("local_storage", {})
            saved_at        = payload.get("saved_at", "unknown")

            if not cookies:
                logger.warning("load_session: session file contains no cookies")
                return False

            logger.info(
                "load_session: loading session saved at %s (%d cookies)",
                saved_at, len(cookies),
            )

            # Must navigate to domain before injecting cookies
            self._rate_limiter.acquire()
            self.driver.get(LINKEDIN_BASE)
            _human_delay(1.5, 2.5)
            self.driver.delete_all_cookies()

            now = time.time()
            injected = 0
            for cookie in cookies:
                # Skip already-expired cookies
                expiry = cookie.get("expiry", now + 3600)
                if expiry < now:
                    logger.debug("load_session: skipping expired cookie '%s'", cookie.get("name"))
                    continue
                try:
                    # Selenium requires 'expiry' as int
                    cookie_clean = {k: v for k, v in cookie.items()
                                    if k in ("name", "value", "domain", "path",
                                             "expiry", "secure", "httpOnly", "sameSite")}
                    if "expiry" in cookie_clean:
                        cookie_clean["expiry"] = int(cookie_clean["expiry"])
                    self.driver.add_cookie(cookie_clean)
                    injected += 1
                except Exception as exc:
                    logger.debug("load_session: could not inject cookie '%s' — %s",
                                 cookie.get("name"), exc)

            # Restore localStorage
            if local_storage:
                try:
                    for key, value in local_storage.items():
                        self.driver.execute_script(
                            "localStorage.setItem(arguments[0], arguments[1]);", key, value
                        )
                except Exception:
                    pass

            logger.info("load_session: injected %d/%d cookies", injected, len(cookies))
            return injected > 0

        except (pickle.UnpicklingError, EOFError, KeyError) as exc:
            logger.error("load_session: corrupt session file — %s. Deleting.", exc)
            try:
                self.session_file.unlink()
            except OSError:
                pass
            return False

        except Exception as exc:
            logger.error("load_session: unexpected error — %s", exc)
            return False

    # =========================================================================
    # 5. handle_captcha()
    # =========================================================================

    def handle_captcha(self) -> bool:
        """
        Detect and attempt to resolve CAPTCHAs on the current page.

        Detection strategy:
            - Checks page title / URL for common CAPTCHA indicators
            - Looks for reCAPTCHA / hCaptcha iframes
            - Checks for LinkedIn's specific challenge elements

        Resolution strategy:
            1. If a custom captcha_callback was provided → invoke it
            2. Otherwise → log a warning and wait (manual solving window)
            3. After resolution, check if the challenge page is gone

        Returns:
            True  — CAPTCHA was detected (regardless of resolution outcome).
            False — No CAPTCHA detected on the current page.
        """
        self._require_driver()

        captcha_indicators = [
            # URL-based indicators
            lambda: "checkpoint" in self.driver.current_url,
            lambda: "security-verification" in self.driver.current_url,
            lambda: "captcha" in self.driver.current_url.lower(),
            # Title-based
            lambda: "security check" in self.driver.title.lower(),
            lambda: "verification" in self.driver.title.lower(),
        ]

        captcha_selectors = [
            # reCAPTCHA
            "iframe[src*='recaptcha']",
            "iframe[src*='google.com/recaptcha']",
            # hCaptcha
            "iframe[src*='hcaptcha.com']",
            "div.h-captcha",
            # LinkedIn-specific
            "div[data-test-id='challenge-dialog']",
            "input[name='captchaUserResponseToken']",
            "#captcha-internal",
            ".captcha__image",
        ]

        # Check URL / title indicators
        detected = False
        try:
            detected = any(fn() for fn in captcha_indicators)
        except Exception:
            pass

        # Check DOM selectors
        if not detected:
            for selector in captcha_selectors:
                try:
                    if self.driver.find_elements(By.CSS_SELECTOR, selector):
                        detected = True
                        break
                except Exception:
                    continue

        if not detected:
            return False

        logger.warning(
            "handle_captcha: CAPTCHA detected on %s", self.driver.current_url
        )

        # ── Resolution path 1: custom callback ───────────────────────────
        if self.captcha_callback:
            try:
                logger.info("handle_captcha: invoking custom captcha_callback")
                resolved = self.captcha_callback(self.driver)
                if resolved:
                    logger.info("handle_captcha: callback reports CAPTCHA resolved")
                    return True
                else:
                    logger.warning("handle_captcha: callback reports CAPTCHA NOT resolved")
            except Exception as exc:
                logger.error("handle_captcha: callback raised exception — %s", exc)

        # ── Resolution path 2: wait for manual solving ────────────────────
        if not self.headless:
            # Non-headless: human can see the screen and solve it
            logger.warning(
                "handle_captcha: please solve the CAPTCHA in the browser window. "
                "Waiting up to 120 seconds..."
            )
            deadline = time.time() + 120
            while time.time() < deadline:
                time.sleep(3)
                # Check if CAPTCHA is gone
                try:
                    still_captcha = any(fn() for fn in captcha_indicators)
                    if not still_captcha:
                        logger.info("handle_captcha: CAPTCHA appears resolved by user")
                        return True
                except Exception:
                    pass
            logger.error("handle_captcha: CAPTCHA timeout — manual solving window expired")
        else:
            # Headless: cannot solve visually — just log and move on
            logger.error(
                "handle_captcha: CAPTCHA detected in headless mode — cannot solve automatically. "
                "Consider running with SCRAPER_HEADLESS=false for initial session creation."
            )

        return True  # detected (even if not resolved)

    # =========================================================================
    # 6. close_driver()
    # =========================================================================

    def close_driver(self) -> None:
        """
        Gracefully shut down the WebDriver and clean up resources.

        Actions performed:
            1. Save current session cookies (if logged in)
            2. Attempt a clean browser logout (optional, avoids stale sessions)
            3. Close all browser windows
            4. Quit the WebDriver process

        Safe to call multiple times (idempotent).
        """
        if self._shutdown_called:
            return

        self._shutdown_called = True
        logger.info("close_driver: initiating graceful shutdown...")

        if self.driver is None:
            logger.info("close_driver: no driver to close")
            return

        # ── Save session before closing ───────────────────────────────────
        if self._is_logged_in:
            try:
                logger.info("close_driver: saving session before shutdown...")
                self.save_session()
            except Exception as exc:
                logger.warning("close_driver: failed to save session — %s", exc)

        # ── Close browser ─────────────────────────────────────────────────
        try:
            # Close all tabs gracefully
            for handle in list(self.driver.window_handles):
                try:
                    self.driver.switch_to.window(handle)
                    self.driver.close()
                except Exception:
                    pass
        except Exception:
            pass

        try:
            self.driver.quit()
            logger.info("close_driver: Chrome driver terminated cleanly")
        except Exception as exc:
            logger.warning("close_driver: error during driver.quit() — %s", exc)
        finally:
            self.driver = None
            self._is_logged_in = False

    # =========================================================================
    # Public Job Search Methods
    # =========================================================================

    @retry_request(max_attempts=2, reraise=False)
    def search_jobs(
        self,
        keywords: str,
        location: str  = "Remote",
        limit:    int  = 25,
        date_posted: str = "",   # "", "r86400"=24h, "r604800"=week, "r2592000"=month
    ) -> list[dict]:
        """
        Search LinkedIn jobs and return structured listings.

        Args:
            keywords:    Job search query (e.g. "Python Developer").
            location:    Location filter (e.g. "Remote", "New York").
            limit:       Maximum number of jobs to return (default 25).
            date_posted: Time filter — see LinkedIn URL parameter f_TPR.

        Returns:
            List of job dicts, each with:
                title, company, location, url, description,
                posted_date, applicants, job_id
        """
        self._require_driver()
        self._require_login()

        # Build search URL
        params = {
            "keywords": keywords,
            "location": location,
            "f_WT":     "2",           # work type: remote
        }
        if date_posted:
            params["f_TPR"] = date_posted

        query_str = "&".join(f"{k}={v.replace(' ', '%20')}" for k, v in params.items())
        url = f"{LINKEDIN_JOBS}?{query_str}"

        logger.info("search_jobs: querying '%s' in '%s'", keywords, location)
        self._rate_limiter.acquire()
        self.driver.get(url)
        _human_delay(3.0, 5.0)

        if self.handle_captcha():
            _human_delay(2.0, 4.0)

        jobs: list[dict] = []
        seen_ids: set[str] = set()
        scroll_attempts = 0
        max_scrolls = math.ceil(limit / 10) + 2

        while len(jobs) < limit and scroll_attempts < max_scrolls:
            # Parse visible job cards
            new_cards = self._parse_job_cards()
            for card in new_cards:
                jid = card.get("job_id", "")
                if jid and jid not in seen_ids:
                    seen_ids.add(jid)
                    jobs.append(card)
                    if len(jobs) >= limit:
                        break

            if len(jobs) >= limit:
                break

            # Scroll to load more
            self._scroll_page()
            _human_delay(1.5, 3.0)
            scroll_attempts += 1

        logger.info("search_jobs: collected %d jobs for '%s'", len(jobs), keywords)
        return jobs[:limit]

    @retry_request(max_attempts=2, reraise=False)
    def get_job_details(self, job_url: str) -> dict:
        """
        Fetch full job description for a single LinkedIn job posting.

        Args:
            job_url: Full LinkedIn job URL.

        Returns:
            dict with: title, company, location, description,
                       requirements, posted_date, apply_url, job_id
        """
        self._require_driver()
        self._require_login()

        logger.info("get_job_details: fetching %s", job_url)
        self._rate_limiter.acquire()
        self.driver.get(job_url)
        _human_delay(2.5, 4.5)

        details: dict[str, Any] = {"url": job_url}

        try:
            wait = WebDriverWait(self.driver, 15)

            # Job title
            try:
                details["title"] = wait.until(
                    EC.presence_of_element_located((
                        By.CSS_SELECTOR,
                        "h1.top-card-layout__title, h1.job-details-jobs-unified-top-card__job-title"
                    ))
                ).text.strip()
            except TimeoutException:
                details["title"] = ""

            # Company
            try:
                details["company"] = self.driver.find_element(
                    By.CSS_SELECTOR,
                    "a.topcard__org-name-link, .job-details-jobs-unified-top-card__company-name a"
                ).text.strip()
            except NoSuchElementException:
                details["company"] = ""

            # Location
            try:
                details["location"] = self.driver.find_element(
                    By.CSS_SELECTOR,
                    ".topcard__flavor--bullet, .job-details-jobs-unified-top-card__bullet"
                ).text.strip()
            except NoSuchElementException:
                details["location"] = ""

            # Full description
            try:
                # Click "Show more" if present
                try:
                    self.driver.find_element(
                        By.CSS_SELECTOR, "button[aria-label='Click to see more description']"
                    ).click()
                    _human_delay(0.5, 1.0)
                except (NoSuchElementException, ElementClickInterceptedException):
                    pass

                desc_elem = self.driver.find_element(
                    By.CSS_SELECTOR,
                    "div.show-more-less-html__markup, div.jobs-description__content"
                )
                details["description"] = desc_elem.text.strip()
            except NoSuchElementException:
                details["description"] = ""

            # Posted date
            try:
                details["posted_date"] = self.driver.find_element(
                    By.CSS_SELECTOR, "span.posted-time-ago__text, .topcard__flavor--metadata"
                ).text.strip()
            except NoSuchElementException:
                details["posted_date"] = ""

            # Job ID from URL
            import re as _re
            m = _re.search(r"/view/(\d+)", job_url)
            details["job_id"] = m.group(1) if m else ""

            # Apply URL
            try:
                apply_btn = self.driver.find_element(
                    By.CSS_SELECTOR,
                    "a.apply-button, a[data-tracking-control-name='public_jobs_apply-link-offsite_sign-up']"
                )
                details["apply_url"] = apply_btn.get_attribute("href") or job_url
            except NoSuchElementException:
                details["apply_url"] = job_url

        except Exception as exc:
            logger.warning("get_job_details: partial failure — %s", exc)

        return details

    # =========================================================================
    # Private Helpers
    # =========================================================================

    def _require_driver(self) -> None:
        """Raise RuntimeError if driver is not initialised."""
        if self.driver is None:
            raise RuntimeError(
                "Driver not initialised. Call initialize_driver() first, "
                "or use the class as a context manager."
            )

    def _require_login(self) -> None:
        """Raise RuntimeError if not logged in to LinkedIn."""
        if not self._is_logged_in:
            raise RuntimeError(
                "Not logged in. Call login_to_linkedin() first."
            )

    def _is_on_feed(self) -> bool:
        """Check whether the browser is on the LinkedIn feed page."""
        try:
            return (
                "linkedin.com/feed" in self.driver.current_url
                or "linkedin.com/in/" in self.driver.current_url
            )
        except Exception:
            return False

    def _type_like_human(self, element, text: str) -> None:
        """
        Type text into a WebElement character-by-character with random delays.

        Mimics realistic typing speed (60-120 WPM) with occasional pauses,
        making automation harder to detect via keystroke timing analysis.
        """
        for char in text:
            element.send_keys(char)
            # Random inter-keystroke delay: 30-180 ms
            time.sleep(random.uniform(0.03, 0.18))
            # Occasional longer pause (typo simulation) — 5% chance
            if random.random() < 0.05:
                time.sleep(random.uniform(0.3, 0.7))

    def _scroll_page(self, pixels: int = 800) -> None:
        """Scroll the page down by `pixels` with human-like behavior."""
        try:
            # Scroll in chunks for more natural movement
            chunk = random.randint(200, 400)
            scrolled = 0
            while scrolled < pixels:
                self.driver.execute_script(
                    f"window.scrollBy(0, {chunk});"
                )
                scrolled += chunk
                time.sleep(random.uniform(0.1, 0.3))
        except Exception as exc:
            logger.debug("_scroll_page: %s", exc)

    def _parse_job_cards(self) -> list[dict]:
        """
        Parse all visible job cards from the current search results page.

        Returns:
            List of lightweight job dicts with: title, company, location, url, job_id.
        """
        jobs = []
        try:
            selectors = [
                "div.job-search-card",
                "li.jobs-search-results__list-item",
                "div.base-card",
            ]
            cards = []
            for sel in selectors:
                cards = self.driver.find_elements(By.CSS_SELECTOR, sel)
                if cards:
                    break

            for card in cards:
                try:
                    # Title
                    title = ""
                    for t_sel in ["h3.base-search-card__title", "a.job-card-list__title", "h3"]:
                        try:
                            title = card.find_element(By.CSS_SELECTOR, t_sel).text.strip()
                            if title:
                                break
                        except NoSuchElementException:
                            continue

                    # Company
                    company = ""
                    for c_sel in ["h4.base-search-card__subtitle", "a.job-card-container__company-name"]:
                        try:
                            company = card.find_element(By.CSS_SELECTOR, c_sel).text.strip()
                            if company:
                                break
                        except NoSuchElementException:
                            continue

                    # Location
                    location = ""
                    try:
                        location = card.find_element(
                            By.CSS_SELECTOR, "span.job-search-card__location"
                        ).text.strip()
                    except NoSuchElementException:
                        pass

                    # URL + job_id
                    url    = ""
                    job_id = ""
                    try:
                        link = card.find_element(By.CSS_SELECTOR, "a.base-card__full-link, a")
                        url  = link.get_attribute("href") or ""
                        import re as _re
                        m    = _re.search(r"/view/(\d+)", url)
                        job_id = m.group(1) if m else ""
                    except (NoSuchElementException, StaleElementReferenceException):
                        pass

                    if title:
                        jobs.append({
                            "title":    title,
                            "company":  company,
                            "location": location,
                            "url":      url,
                            "job_id":   job_id,
                        })

                except StaleElementReferenceException:
                    continue
                except Exception as exc:
                    logger.debug("_parse_job_cards: card parse error — %s", exc)

        except Exception as exc:
            logger.warning("_parse_job_cards: %s", exc)

        return jobs

    # =========================================================================
    # Graceful Shutdown Handlers
    # =========================================================================

    def _signal_handler(self, signum: int, frame: Any) -> None:
        """Handle OS signals (SIGTERM, SIGHUP) for graceful shutdown."""
        sig_name = signal.Signals(signum).name
        logger.warning(
            "Received signal %s (%d) — initiating graceful shutdown...",
            sig_name, signum,
        )
        self.close_driver()
        sys.exit(0)

    def _atexit_handler(self) -> None:
        """atexit hook — ensures driver is closed even on normal exit."""
        if not self._shutdown_called and self.driver is not None:
            logger.info("atexit: closing driver on interpreter exit")
            self.close_driver()


# ===========================================================================
# Module-level Convenience Functions
# (matches the function signatures requested in the task spec)
# ===========================================================================

def initialize_driver(
    headless:   bool         = HEADLESS,
    user_agent: Optional[str] = None,
) -> "webdriver.Chrome":
    """
    Module-level factory: create a LinkedInScraper and return its driver.

    For one-off usage when you don't need the full class interface.
    Remember to call close_driver() when done.

    Args:
        headless:   Run Chrome headless (default True).
        user_agent: Override user-agent string.

    Returns:
        Configured webdriver.Chrome instance.
    """
    _scraper = LinkedInScraper(headless=headless, user_agent=user_agent)
    _scraper.initialize_driver()
    return _scraper.driver


def login_to_linkedin(
    driver: "webdriver.Chrome",
    email:    str = "",
    password: str = "",
) -> bool:
    """
    Module-level wrapper: log in to LinkedIn using an existing driver.

    Args:
        driver:   An already-initialised webdriver.Chrome.
        email:    LinkedIn email (defaults to .env).
        password: LinkedIn password (defaults to .env).

    Returns:
        True on success, False on failure.
    """
    # Build a lightweight scraper that wraps the provided driver
    scraper = LinkedInScraper.__new__(LinkedInScraper)
    scraper.driver           = driver
    scraper.headless         = HEADLESS
    scraper.user_agent       = random.choice(USER_AGENTS)
    scraper.session_file     = COOKIE_FILE
    scraper.profile_dir      = PROFILE_DIR
    scraper.page_timeout     = PAGE_TIMEOUT
    scraper.captcha_callback = None
    scraper._rate_limiter    = _rate_limiter
    scraper._is_logged_in    = False
    scraper._shutdown_called = False
    return scraper.login_to_linkedin(email=email, password=password)


def save_session(driver: "webdriver.Chrome", session_file: Path = COOKIE_FILE) -> bool:
    """
    Module-level wrapper: save browser cookies to disk.

    Args:
        driver:       An active webdriver.Chrome.
        session_file: Path to write the pickle file.

    Returns:
        True on success.
    """
    scraper = _wrap_driver(driver, session_file)
    return scraper.save_session()


def load_session(driver: "webdriver.Chrome", session_file: Path = COOKIE_FILE) -> bool:
    """
    Module-level wrapper: inject saved cookies into an existing driver.

    Args:
        driver:       An active webdriver.Chrome.
        session_file: Path to the pickle file.

    Returns:
        True if cookies were injected.
    """
    scraper = _wrap_driver(driver, session_file)
    return scraper.load_session()


def handle_captcha(
    driver: "webdriver.Chrome",
    captcha_callback: Optional[Callable] = None,
) -> bool:
    """
    Module-level wrapper: detect and handle CAPTCHAs.

    Args:
        driver:           An active webdriver.Chrome.
        captcha_callback: Optional solver callable.

    Returns:
        True if a CAPTCHA was detected.
    """
    scraper = _wrap_driver(driver)
    scraper.captcha_callback = captcha_callback
    return scraper.handle_captcha()


def close_driver(driver: "webdriver.Chrome") -> None:
    """
    Module-level wrapper: gracefully close a Chrome driver.

    Args:
        driver: An active webdriver.Chrome to close.
    """
    try:
        for handle in list(driver.window_handles):
            try:
                driver.switch_to.window(handle)
                driver.close()
            except Exception:
                pass
        driver.quit()
        logger.info("close_driver: driver terminated cleanly")
    except Exception as exc:
        logger.warning("close_driver: error during shutdown — %s", exc)


def _wrap_driver(driver, session_file: Path = COOKIE_FILE) -> LinkedInScraper:
    """Internal helper: create a minimal scraper shell around an existing driver."""
    scraper = LinkedInScraper.__new__(LinkedInScraper)
    scraper.driver           = driver
    scraper.headless         = HEADLESS
    scraper.user_agent       = random.choice(USER_AGENTS)
    scraper.session_file     = session_file
    scraper.profile_dir      = PROFILE_DIR
    scraper.page_timeout     = PAGE_TIMEOUT
    scraper.captcha_callback = None
    scraper._rate_limiter    = _rate_limiter
    scraper._is_logged_in    = False
    scraper._shutdown_called = False
    return scraper


# ===========================================================================
# Self-test — run with:  python -m backend.scraper
# ===========================================================================

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.DEBUG, format="%(levelname)s | %(message)s")

    print()
    print("=" * 65)
    print("  backend/scraper.py — Self-Test Suite")
    print("=" * 65)

    # ── Dependency checks ─────────────────────────────────────────────────
    print("\n[1/5] Dependency check")
    if not SELENIUM_AVAILABLE:
        print("  [SKIP] selenium not installed — run: pip install selenium")
        sys.exit(0)
    if not WDM_AVAILABLE:
        print("  [SKIP] webdriver-manager not installed — run: pip install webdriver-manager")
        sys.exit(0)
    print("  [OK]  selenium and webdriver-manager are available")

    # ── Token-bucket rate limiter test ────────────────────────────────────
    print("\n[2/5] Rate limiter test (0.5 RPS -> expect ~2s delay for 2 calls)")
    rl = _TokenBucket(rate=0.5)
    t0 = time.monotonic()
    rl.acquire()   # 1st call — immediate
    rl.acquire()   # 2nd call — should wait ~2s
    elapsed = time.monotonic() - t0
    assert 1.5 <= elapsed <= 3.5, f"Expected ~2s delay, got {elapsed:.1f}s"
    print(f"  [OK]  rate limiter: 2 calls took {elapsed:.2f}s")

    # ── Retry decorator test ──────────────────────────────────────────────
    print("\n[3/5] Retry decorator test")
    attempts = []

    @retry_request(max_attempts=3, backoff_base=0.1, exceptions=(ValueError,), reraise=False)
    def _flaky():
        attempts.append(1)
        if len(attempts) < 3:
            raise ValueError("simulated failure")
        return "success"

    result = _flaky()
    assert result == "success",  f"Expected 'success', got: {result}"
    assert len(attempts) == 3,   f"Expected 3 attempts, got: {len(attempts)}"
    print(f"  [OK]  retry decorator: succeeded on attempt {len(attempts)}")

    # ── Human delay test ──────────────────────────────────────────────────
    print("\n[4/5] Human delay test (1.5–2.5s window)")
    t0 = time.monotonic()
    _human_delay(1.5, 2.5, sigma=0.2)
    elapsed = time.monotonic() - t0
    assert 1.0 <= elapsed <= 3.5, f"Delay out of range: {elapsed:.2f}s"
    print(f"  [OK]  human delay: slept {elapsed:.2f}s")

    # ── Session file helpers test (no browser required) ───────────────────
    print("\n[5/5] Session helpers test")
    import tempfile, os as _os
    with tempfile.TemporaryDirectory() as tmpdir:
        # write a fake pickle session
        fake_path = Path(tmpdir) / "test_session.pkl"
        fake_payload = {
            "cookies": [
                {"name": "li_at", "value": "fake_token", "domain": ".linkedin.com",
                 "path": "/", "expiry": int(time.time()) + 3600,
                 "secure": True, "httpOnly": True}
            ],
            "local_storage":   {},
            "session_storage": {},
            "user_agent":      "TestAgent/1.0",
            "saved_at":        datetime.now(timezone.utc).isoformat(),
            "saved_url":       "https://www.linkedin.com/feed/",
        }
        with open(fake_path, "wb") as fh:
            pickle.dump(fake_payload, fh)
        # Verify file exists + can be round-tripped
        with open(fake_path, "rb") as fh:
            loaded = pickle.load(fh)
        assert loaded["cookies"][0]["name"] == "li_at"
        print(f"  [OK]  session pickle round-trip verified")

    print()
    print("=" * 65)
    print("  ALL SELF-TESTS PASSED")
    print()
    print("  To test the full browser flow:")
    print("  1. Set LINKEDIN_EMAIL and LINKEDIN_PASSWORD in your .env")
    print("  2. Set SCRAPER_HEADLESS=false for first run (CAPTCHA solving)")
    print("  3. Run:")
    print("       python -c \"")
    print("       from backend.scraper import LinkedInScraper")
    print("       with LinkedInScraper(headless=False) as s:")
    print("           s.login_to_linkedin()")
    print("           jobs = s.search_jobs('Python Developer', 'Remote')")
    print("           print(jobs[:3])")
    print("       \"")
    print("=" * 65)
