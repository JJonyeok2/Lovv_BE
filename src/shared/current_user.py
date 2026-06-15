# @file src/shared/current_user.py
# @description Resolves authenticated user claims from API Gateway context or bearer JWT.
# @lastModified 2026-06-12

from shared.auth import extract_bearer_token, verify_access_token


def authenticated_claims(event):
    authorizer = ((event.get("requestContext") or {}).get("authorizer") or {})
    claims = authorizer.get("lambda") or authorizer.get("claims") or {}
    if claims.get("userId") or claims.get("sub"):
        return claims

    # Fallback supports routes without API Gateway authorizer so Lambda responses keep CORS headers.
    token_claims = verify_access_token(extract_bearer_token(event.get("headers") or {}))
    return {
        "sub": token_claims["sub"],
        "userId": token_claims["sub"],
        "sessionId": token_claims.get("sid", ""),
        "roles": token_claims.get("roles") if "roles" in token_claims else ["R-USER"],
        "provider": token_claims.get("provider", ""),
    }


# EOF: src/shared/current_user.py
