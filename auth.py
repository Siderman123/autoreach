import os
from functools import wraps

import jwt
from flask import g, jsonify, request

# Set in Railway env vars: copy the JWT Public Key from Clerk Dashboard → API Keys
_CLERK_JWT_KEY = os.environ.get("CLERK_JWT_KEY", "").replace("\\n", "\n")


def verify_token(token: str) -> dict:
    return jwt.decode(token, _CLERK_JWT_KEY, algorithms=["RS256"])


def require_auth(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return jsonify({"error": "Unauthorized"}), 401
        try:
            payload = verify_token(header[7:])
            g.clerk_user_id = payload["sub"]
        except Exception:
            return jsonify({"error": "Invalid token"}), 401
        return f(*args, **kwargs)
    return wrapper
