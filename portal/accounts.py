"""Password hashing, session tokens, and login throttling. Standard library only."""
import base64
import hashlib
import hmac
import re
import secrets
import time

ITERATIONS = 240_000
SESSION_DAYS = 14
USERNAME_PATTERN = re.compile(r'^[a-z0-9][a-z0-9._-]{2,31}$')
MIN_PASSWORD = 8
MAX_PASSWORD = 128


def hash_password(password, iterations=ITERATIONS):
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, iterations)
    encode = lambda raw: base64.b64encode(raw).decode('ascii')
    return f'pbkdf2_sha256${iterations}${encode(salt)}${encode(digest)}'


def verify_password(stored, password):
    try:
        algorithm, iterations, salt, digest = stored.split('$')
        if algorithm != 'pbkdf2_sha256':
            return False
        candidate = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), base64.b64decode(salt), int(iterations))
    except (ValueError, AttributeError):
        return False
    return hmac.compare_digest(candidate, base64.b64decode(digest))


def new_session_token():
    return secrets.token_urlsafe(32)


def hash_token(token):
    # Sessions are stored hashed, so a database copy alone cannot be replayed.
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def normalize_username(username):
    return (username or '').strip().lower()


def validate_signup(username, password, display_name):
    """Return a Korean error message, or None when the values are acceptable."""
    if not USERNAME_PATTERN.fullmatch(normalize_username(username)):
        return '아이디는 영문 소문자·숫자로 시작하는 3~32자이며 . _ - 를 쓸 수 있습니다.'
    if not MIN_PASSWORD <= len(password or '') <= MAX_PASSWORD:
        return f'비밀번호는 {MIN_PASSWORD}자 이상 {MAX_PASSWORD}자 이하로 입력하세요.'
    if (password or '').strip() in ('', normalize_username(username)):
        return '비밀번호를 아이디와 다르게 입력하세요.'
    if not 1 <= len((display_name or '').strip()) <= 40:
        return '사용자 이름을 1~40자로 입력하세요.'
    return None


class LoginThrottle:
    """In-process lockout. One portal container serves one lab, so memory is enough."""
    def __init__(self, limit=5, window=300, lockout=300):
        self.limit, self.window, self.lockout = limit, window, lockout
        self.failures = {}

    def locked_for(self, key, now=None):
        now = now or time.monotonic()
        attempts = [t for t in self.failures.get(key, []) if now-t < self.window]
        self.failures[key] = attempts
        if len(attempts) < self.limit:
            return 0
        return max(0, round(self.lockout-(now-attempts[-1])))

    def record_failure(self, key, now=None):
        self.failures.setdefault(key, []).append(now or time.monotonic())

    def reset(self, key):
        self.failures.pop(key, None)
