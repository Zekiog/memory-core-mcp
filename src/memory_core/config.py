"""Configuration loaded from environment (.env)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Load .env from CWD first, then fall back to the project root next to the
# package so the stdio MCP server works regardless of its working directory.
load_dotenv()
_project_env = Path(__file__).resolve().parents[2] / ".env"
if _project_env.is_file():
    load_dotenv(_project_env, override=False)
_explicit = os.getenv("ZMEM_ENV_FILE")
if _explicit and Path(_explicit).is_file():
    load_dotenv(_explicit, override=True)


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Config:
    dsn: str
    user: str
    password: str
    wallet_dir: str
    wallet_password: str | None
    embed_model: str
    default_scope: str
    vault_dir: str | None
    vault_mirror: bool

    @staticmethod
    def load() -> "Config":
        password = os.getenv("ZMEM_PASSWORD", "")
        if not password:
            raise RuntimeError("ZMEM_PASSWORD is required (set it in .env, chmod 600)")
        wallet_dir = os.getenv("ZMEM_WALLET_DIR", "")
        if not wallet_dir or not Path(wallet_dir).is_dir():
            raise RuntimeError(f"ZMEM_WALLET_DIR not found: {wallet_dir!r}")
        return Config(
            dsn=os.getenv("ZMEM_DSN", "zmemory_high"),
            user=os.getenv("ZMEM_USER", "ZMEM"),
            password=password,
            wallet_dir=wallet_dir,
            wallet_password=os.getenv("ZMEM_WALLET_PASSWORD") or None,
            embed_model=os.getenv("ZMEM_EMBED_MODEL", "ZMEM_EMBED"),
            default_scope=os.getenv("ZMEM_DEFAULT_SCOPE", "global"),
            vault_dir=os.getenv("ZMEM_VAULT_DIR") or None,
            vault_mirror=_bool("ZMEM_VAULT_MIRROR", False),
        )
