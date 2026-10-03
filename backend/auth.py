"""
auth.py — Authentication & Session Management
================================================
Handles user registration, login, logout, and session state
using bcrypt for password hashing and UUID v4 for user IDs.

Registration:
    - Validates all input fields
    - Checks for duplicate email
    - Hashes password with bcrypt (12 rounds)
    - Generates UUID v4 user_id
    - Stores user in SQLite

Login:
    - Verifies email exists
    - Verifies password against bcrypt hash
    - Sets Streamlit session_state with user data

Session Management:
    - login_user()   → Set session state after authentication
    - logout_user()  → Clear all session state
    - is_logged_in() → Check current authentication status
"""

import uuid
import logging
import bcrypt
import streamlit as st

from utils.database import insert_user, get_user_by_email
from utils.validators import (
    validate_full_name,
    validate_email,
    validate_password,
    validate_password_match,
)

logger = logging.getLogger(__name__)


# ===========================================================================
# 1. Password Hashing (bcrypt)
# ===========================================================================

def hash_password(password: str) -> str:
    """
    Hash a plain-text password using bcrypt with auto-generated salt.

    Uses 12 rounds of key stretching for a good balance between
    security and performance (~250ms per hash on modern hardware).

    Args:
        password: The plain-text password to hash.

    Returns:
        A bcrypt hash string (UTF-8 decoded for SQLite TEXT storage).

    Raises:
        ValueError: If password is empty or None.
    """
    if not password:
        raise ValueError("Password cannot be empty.")

    salt = bcrypt.gensalt(rounds=12)
    hashed = bcrypt.hashpw(password.encode("utf-8"), salt)
    return hashed.decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    """
    Verify a plain-text password against a stored bcrypt hash.

    Args:
        password:      The plain-text password to check.
        password_hash: The stored bcrypt hash string.

    Returns:
        True if the password matches, False otherwise.
    """
    if not password or not password_hash:
        return False

    try:
        return bcrypt.checkpw(
            password.encode("utf-8"),
            password_hash.encode("utf-8"),
        )
    except (ValueError, TypeError) as e:
        logger.error("Password verification error: %s", e)
        return False


# ===========================================================================
# 2. UUID Generation
# ===========================================================================

def generate_user_id() -> str:
    """
    Generate a new UUID v4 string for use as a user_id.

    Returns:
        A 36-character UUID string (e.g. 'a1b2c3d4-e5f6-7890-abcd-ef1234567890').
    """
    return str(uuid.uuid4())


# ===========================================================================
# 3. Registration
# ===========================================================================

def register_user(
    full_name: str,
    email: str,
    password: str,
    confirm_password: str,
) -> dict:
    """
    Validate inputs, hash password, generate UUID, and register a new user.

    Performs the full registration flow:
        1. Validate all input fields
        2. Check for duplicate email in database
        3. Generate a UUID v4 user_id
        4. Hash the password with bcrypt
        5. Insert into SQLite

    Args:
        full_name:        User's full display name.
        email:            User's email address.
        password:         Plain-text password.
        confirm_password: Password confirmation (must match).

    Returns:
        dict with keys:
            success (bool)      — True if registration succeeded.
            user_id (str|None)  — UUID string on success.
            message (str)       — Human-readable status message.
            errors  (list[str]) — List of validation error strings.
    """
    errors = []

    # ── Step 1: Validate all fields ───────────────────────────────
    ok, msg = validate_full_name(full_name)
    if not ok:
        errors.append(msg)

    ok, msg = validate_email(email)
    if not ok:
        errors.append(msg)

    ok, msg = validate_password(password)
    if not ok:
        errors.append(msg)

    ok, msg = validate_password_match(password, confirm_password)
    if not ok:
        errors.append(msg)

    if errors:
        logger.warning("Registration validation failed for '%s': %s", email, errors)
        return {
            "success": False,
            "user_id": None,
            "message": "Please fix the errors below.",
            "errors": errors,
        }

    # ── Step 2: Check duplicate email ─────────────────────────────
    try:
        existing = get_user_by_email(email)
        if existing:
            logger.warning("Duplicate email registration attempt: %s", email)
            return {
                "success": False,
                "user_id": None,
                "message": "An account with this email already exists.",
                "errors": ["An account with this email already exists."],
            }
    except Exception as e:
        logger.error("Error checking duplicate email: %s", e)
        return {
            "success": False,
            "user_id": None,
            "message": "Unable to verify email. Please try again.",
            "errors": ["Database connection error."],
        }

    # ── Step 3: Generate UUID ─────────────────────────────────────
    user_id = generate_user_id()

    # ── Step 4: Hash password ─────────────────────────────────────
    try:
        hashed = hash_password(password)
    except Exception as e:
        logger.error("Password hashing failed: %s", e)
        return {
            "success": False,
            "user_id": None,
            "message": "Registration failed. Please try again.",
            "errors": ["Internal error during password processing."],
        }

    # ── Step 5: Insert into database ──────────────────────────────
    try:
        result = insert_user(
            user_id=user_id,
            full_name=full_name.strip(),
            email=email.strip().lower(),
            password_hash=hashed,
        )

        if result["success"]:
            logger.info(
                "User registered successfully: id=%s, name='%s', email='%s'",
                user_id[:8], full_name.strip(), email.strip().lower(),
            )
            return {
                "success": True,
                "user_id": user_id,
                "message": "Account created successfully! You can now log in.",
                "errors": [],
            }
        else:
            return {
                "success": False,
                "user_id": None,
                "message": result["message"],
                "errors": [result["message"]],
            }

    except Exception as e:
        logger.exception("Registration failed for '%s': %s", email, e)
        return {
            "success": False,
            "user_id": None,
            "message": "An unexpected error occurred. Please try again.",
            "errors": [str(e)],
        }


# ===========================================================================
# 4. Login
# ===========================================================================

def authenticate_user(email: str, password: str) -> dict:
    """
    Authenticate a user by email and password.

    Does NOT modify session state — call login_user() after successful
    authentication to set the session.

    Args:
        email:    User's email address.
        password: Plain-text password to verify.

    Returns:
        dict with keys:
            success (bool)      — True if credentials are valid.
            user    (dict|None) — Safe user record (no password_hash).
            message (str)       — Human-readable status message.
    """
    # ── Basic validation ──────────────────────────────────────────
    if not email or not email.strip():
        return {"success": False, "user": None, "message": "Email is required."}

    if not password:
        return {"success": False, "user": None, "message": "Password is required."}

    # ── Lookup user ───────────────────────────────────────────────
    try:
        user = get_user_by_email(email.strip().lower())

        if not user:
            # Generic message prevents email enumeration attacks
            logger.warning("Login attempt for non-existent email: %s", email)
            return {
                "success": False,
                "user": None,
                "message": "Invalid email or password.",
            }

        # ── Verify password ───────────────────────────────────────
        if not verify_password(password, user["password_hash"]):
            logger.warning("Failed login attempt for: %s", email)
            return {
                "success": False,
                "user": None,
                "message": "Invalid email or password.",
            }

        # ── Build safe user dict (strip sensitive fields) ─────────
        safe_user = {
            "user_id": user["user_id"],
            "full_name": user["full_name"],
            "email": user["email"],
            "registration_date": user["registration_date"],
            "resume_path": user.get("resume_path"),
        }

        logger.info("Successful authentication: %s (id=%s)", email, user["user_id"][:8])
        return {
            "success": True,
            "user": safe_user,
            "message": f"Welcome back, {user['full_name']}!",
        }

    except Exception as e:
        logger.exception("Authentication error for '%s': %s", email, e)
        return {
            "success": False,
            "user": None,
            "message": "An unexpected error occurred. Please try again.",
        }


# ===========================================================================
# 5. Session Management (Streamlit session_state)
# ===========================================================================

def login_user(user: dict) -> None:
    """
    Set Streamlit session state after successful authentication.

    This stores the authenticated user's information in session_state
    so all pages can access it. Must be called AFTER authenticate_user()
    returns success.

    Args:
        user: The safe user dict returned by authenticate_user().
              Keys: user_id, full_name, email, registration_date, resume_path.
    """
    if not user:
        logger.error("login_user() called with None user.")
        return

    st.session_state["authenticated"] = True
    st.session_state["user_id"] = user["user_id"]
    st.session_state["username"] = user["full_name"]
    st.session_state["email"] = user["email"]
    st.session_state["registration_date"] = user.get("registration_date")
    st.session_state["resume_path"] = user.get("resume_path")
    st.session_state["current_page"] = "dashboard"

    logger.info("Session created for user: %s (%s)", user["full_name"], user["user_id"][:8])


def logout_user() -> None:
    """
    Clear all session state and log the user out.

    Removes all keys from st.session_state, effectively resetting
    the application to the logged-out state.
    """
    user_id = st.session_state.get("user_id", "unknown")
    username = st.session_state.get("username", "unknown")

    # Clear every key in session state
    for key in list(st.session_state.keys()):
        del st.session_state[key]

    logger.info("Session destroyed for user: %s (%s)", username, str(user_id)[:8])


def is_logged_in() -> bool:
    """
    Check whether a user is currently authenticated.

    Returns:
        True if the user has an active session, False otherwise.
    """
    return bool(
        st.session_state.get("authenticated", False)
        and st.session_state.get("user_id")
    )


def get_current_user() -> dict | None:
    """
    Retrieve the current logged-in user's info from session state.

    Returns:
        dict with user_id, username, email, etc., or None if not logged in.
    """
    if not is_logged_in():
        return None

    return {
        "user_id": st.session_state.get("user_id"),
        "full_name": st.session_state.get("username"),
        "email": st.session_state.get("email"),
        "registration_date": st.session_state.get("registration_date"),
        "resume_path": st.session_state.get("resume_path"),
    }


# ===========================================================================
# Self-Test — run with: python -m backend.auth
# ===========================================================================

if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG, format="%(levelname)s | %(message)s")

    from utils.database import create_database, get_connection
    create_database()

    # --- Test hash + verify ---
    pw_hash = hash_password("Str0ng!Pass")
    assert verify_password("Str0ng!Pass", pw_hash), "Password verify failed"
    assert not verify_password("WrongPass1!", pw_hash), "Wrong password should fail"
    print("[OK] hash_password / verify_password")

    # --- Test UUID generation ---
    uid = generate_user_id()
    assert len(uid) == 36 and uid.count("-") == 4, "Invalid UUID format"
    print(f"[OK] generate_user_id: {uid[:8]}...")

    # --- Test registration ---
    result = register_user("Jane Doe", "jane@example.com", "Str0ng!Pass", "Str0ng!Pass")
    assert result["success"], f"Registration failed: {result}"
    assert result["user_id"] is not None
    print(f"[OK] register_user: id={result['user_id'][:8]}...")

    # --- Test duplicate ---
    dup = register_user("Jane Again", "jane@example.com", "Str0ng!Pass", "Str0ng!Pass")
    assert not dup["success"]
    print(f"[OK] Duplicate blocked: {dup['message']}")

    # --- Test validation ---
    bad = register_user("", "bad-email", "weak", "mismatch")
    assert not bad["success"]
    assert len(bad["errors"]) >= 3
    print(f"[OK] Validation caught {len(bad['errors'])} errors: {bad['errors']}")

    # --- Test authentication ---
    auth = authenticate_user("jane@example.com", "Str0ng!Pass")
    assert auth["success"]
    assert auth["user"]["full_name"] == "Jane Doe"
    assert "password_hash" not in auth["user"]
    print(f"[OK] authenticate_user: {auth['message']}")

    # --- Test failed login ---
    bad_auth = authenticate_user("jane@example.com", "WrongPass1!")
    assert not bad_auth["success"]
    print(f"[OK] Failed auth: {bad_auth['message']}")

    bad_email = authenticate_user("nobody@example.com", "Str0ng!Pass")
    assert not bad_email["success"]
    print(f"[OK] Unknown email: {bad_email['message']}")

    # --- Cleanup ---
    with get_connection() as conn:
        conn.execute("DELETE FROM users WHERE email = ?", ("jane@example.com",))
    print("[OK] Cleaned up test data.")
    print("\n=== All auth tests passed ===")
