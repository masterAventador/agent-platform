import hashlib
import secrets


class SessionTokenManager:
    @staticmethod
    def digest(raw_token: str) -> str:
        return hashlib.sha256(raw_token.encode()).hexdigest()

    def issue(self) -> tuple[str, str]:
        raw_token = secrets.token_urlsafe(32)
        return raw_token, self.digest(raw_token)
