from firebase_admin import auth


def verify_id_token(token: str) -> dict:
    """Verify a Firebase ID Token and return the decoded claims."""
    return auth.verify_id_token(token)
