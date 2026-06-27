import os
import uuid
import secrets
from datetime import datetime, timedelta
from typing import Optional
from fastapi import Request, HTTPException, status, Depends
from sqlalchemy.orm import Session
import bcrypt
import jwt
from app.database import get_db, User, TokenDenylist

# ─── JWT Configuration ────────────────────────────────────────────────────────
# SECRET_KEY MUST be set as an environment variable (in .env).
# No ephemeral fallback in production — fail loudly if missing.
SECRET_KEY = os.getenv("SECRET_KEY")
if not SECRET_KEY:
    # Development-only fallback with a loud warning
    _dev_key = secrets.token_hex(32)
    SECRET_KEY = _dev_key
    print("\n" + "!" * 60)
    print("  WARNING: SECRET_KEY environment variable is not set.")
    print("  A random ephemeral key has been generated for this session.")
    print("  All existing sessions will be invalidated on server restart.")
    print("  Set SECRET_KEY in your .env file for persistent sessions.")
    print("!" * 60 + "\n")

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60


def hash_password(password: str) -> str:
    """
    Hashes a password using bcrypt with a work factor of 12.
    """
    salt = bcrypt.gensalt(rounds=12)
    hashed = bcrypt.hashpw(password.encode("utf-8"), salt)
    return hashed.decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Verifies a plain password against a bcrypt hashed password.
    """
    try:
        return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))
    except Exception:
        return False


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """
    Encodes a JWT access token with a unique JTI claim for revocation support.
    """
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    jti = str(uuid.uuid4())          # Unique token ID — used for denylist revocation
    to_encode.update({"exp": expire, "jti": jti})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def invalidate_token(token: str, db: Session) -> None:
    """
    Adds a token's JTI to the denylist, effectively revoking it at logout.
    """
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        jti = payload.get("jti")
        if jti:
            entry = TokenDenylist(jti=jti, invalidated_at=datetime.utcnow())
            db.add(entry)
            db.commit()
    except Exception:
        pass  # Expired or malformed tokens are already invalid


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    """
    FastAPI dependency that extracts the JWT session token from HTTP-only cookies,
    verifies it is not revoked, and retrieves the corresponding User.

    Raises 401 Unauthorized if the session is missing, expired, revoked, or invalid.
    """
    token = request.cookies.get("bees_session")
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated. Please log in.",
        )

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        jti: str = payload.get("jti")

        if username is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid session token.",
            )

        # Check token denylist (revoked via explicit logout)
        if jti and db.query(TokenDenylist).filter(TokenDenylist.jti == jti).first():
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Session has been revoked. Please log in again.",
            )

    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session has expired. Please log in again.",
        )
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid session token.",
        )

    user = db.query(User).filter(User.username == username).first()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found.",
        )
    return user
