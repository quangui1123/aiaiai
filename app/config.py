import os
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    admin_email: str = "admin@aiaiai.com"
    admin_password: str = "admin123"
    jwt_secret: str = ""
    host: str = "0.0.0.0"
    port: int = 8000
    default_quota: float = 0.5
    cors_origins: str = "*"
    jwt_algorithm: str = "HS256"
    jwt_expire_hours: int = 168
    db_dir: str = "data"
    db_name: str = "proxy.db"
    log_level: str = "INFO"

    @property
    def db_path(self) -> str:
        d = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), self.db_dir)
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, self.db_name)

    @property
    def effective_jwt_secret(self) -> str:
        import secrets
        return self.jwt_secret or secrets.token_urlsafe(32)


settings = Settings()
