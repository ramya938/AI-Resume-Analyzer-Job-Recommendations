"""
linkedin_login_setup.py
=======================
Run this ONCE with a visible Chrome window to:
  1. Open LinkedIn login page
  2. Auto-fill your credentials
  3. Wait for you to solve any CAPTCHA / verification
  4. Confirm you are logged in
  5. Save session cookies → data/sessions/linkedin_cookies.pkl

After this, all future scraping runs will restore the session
automatically without opening a visible browser window.

Run with:
    python linkedin_login_setup.py
"""

import sys
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Ensure project root is on sys.path
# ---------------------------------------------------------------------------
ROOT = Path(__file__).parent.resolve()
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv()

import os
print()
print("=" * 60)
print("  LinkedIn Session Setup  (non-headless mode)")
print("=" * 60)
print()

# ── Credential check ───────────────────────────────────────────────────────
email    = os.getenv("LINKEDIN_EMAIL", "")
password = os.getenv("LINKEDIN_PASSWORD", "")

if not email or not password:
    print("[ERROR] LINKEDIN_EMAIL or LINKEDIN_PASSWORD not set in .env")
    print("        Please add them and try again.")
    sys.exit(1)

print(f"  Email    : {email[:6]}***{email[-10:]}")
print(f"  Password : {'*' * len(password)} ({len(password)} chars)")
print(f"  Headless : {os.getenv('SCRAPER_HEADLESS', 'false')}")
print()

# ── Import scraper ─────────────────────────────────────────────────────────
try:
    from backend.scraper import LinkedInScraper
except ImportError as e:
    print(f"[ERROR] Could not import LinkedInScraper: {e}")
    print("        Make sure selenium and webdriver-manager are installed.")
    sys.exit(1)

# ── Run login with visible browser ─────────────────────────────────────────
print("[INFO] Launching Chrome (visible window)...")
print("[INFO] A browser window will open shortly.")
print()
print("  INSTRUCTIONS:")
print("  1. Watch the browser — it will auto-fill your credentials")
print("  2. If LinkedIn shows a CAPTCHA or phone verification,")
print("     solve it MANUALLY in the browser window")
print("  3. Once you see the LinkedIn feed/home page, session is saved")
print("  4. DO NOT close the browser — this script will close it")
print()
print("  Waiting for login to complete (up to 3 minutes)...")
print()

success = False

try:
    with LinkedInScraper(headless=False) as scraper:

        # Attempt login
        logged_in = scraper.login_to_linkedin(email=email, password=password)

        if logged_in:
            print()
            print("[OK] Successfully logged in to LinkedIn!")
            print("[OK] Session cookies saved to:")
            print(f"     {scraper.session_file.resolve()}")
            success = True

        else:
            # May need manual CAPTCHA resolution — give extra wait time
            print()
            print("[WAIT] Auto-login did not complete immediately.")
            print("       If a CAPTCHA / verification is shown:")
            print("       -> Solve it in the browser window now.")
            print()

            deadline = time.time() + 180   # 3-minute window
            dots     = 0
            while time.time() < deadline:
                time.sleep(3)
                dots = (dots + 1) % 20
                print(f"\r  Waiting{'.' * (dots + 1)}{' ' * (20 - dots)}", end="", flush=True)

                try:
                    url = scraper.driver.current_url
                    if "linkedin.com/feed" in url or "linkedin.com/in/" in url:
                        print()
                        print()
                        print("[OK] LinkedIn feed detected — login successful!")
                        scraper._is_logged_in = True
                        scraper.save_session()
                        print("[OK] Session cookies saved to:")
                        print(f"     {scraper.session_file.resolve()}")
                        success = True
                        break
                except Exception:
                    pass

            if not success:
                print()
                print("[TIMEOUT] Could not confirm login within 3 minutes.")
                print("          Please check your credentials and try again.")

        # Final session status
        if success:
            cookie_count = len(scraper.driver.get_cookies())
            print(f"[OK] Total cookies saved: {cookie_count}")
            print()
            time.sleep(2)   # brief pause so user can see the feed

except KeyboardInterrupt:
    print()
    print("[CANCELLED] Setup cancelled by user.")
    sys.exit(0)

except Exception as exc:
    print()
    print(f"[ERROR] Unexpected error: {exc}")
    sys.exit(1)

# ── Post-setup: switch back to headless ────────────────────────────────────
if success:
    env_path = ROOT / ".env"
    try:
        content = env_path.read_text(encoding="utf-8")
        content = content.replace(
            "SCRAPER_HEADLESS=false",
            "SCRAPER_HEADLESS=true ",
        )
        env_path.write_text(content, encoding="utf-8")
        print("[OK] .env updated: SCRAPER_HEADLESS=true")
        print("     All future scraping runs will use headless mode.")
    except Exception as exc:
        print(f"[WARN] Could not auto-update .env: {exc}")
        print("       Manually set SCRAPER_HEADLESS=true in .env")

print()
print("=" * 60)
if success:
    print("  SETUP COMPLETE!")
    print()
    print("  You can now run the full scraper with:")
    print("    python -m backend.job_scraper")
    print()
    print("  Or use it in code:")
    print("    from backend.job_scraper import search_jobs")
    print("    jobs = search_jobs(skills=['Python','SQL'], limit=25)")
else:
    print("  SETUP INCOMPLETE — Please try again.")
print("=" * 60)
print()
