from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPOS: list[tuple[str, str]] = [
    ("Libki", "libki-server"),
    ("Libki", "libki-client"),
    ("Libki", "libki-print-station"),
    ("Libki", "libki-print-manager"),
    ("Libki", "libki-manual"),
]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="LIBKI_TRIAGE_",
        extra="ignore",
    )

    github_token: str | None = None
    db_path: Path = Path("./data/libki-triage.db")


settings = Settings()
