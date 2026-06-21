"""Central configuration, loaded from environment variables.

Local dev reads from a .env file (via python-dotenv); on Railway these come
from the service's environment variables. See .env.example for the full list.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


def _req(name: str) -> str:
    """Fetch a required env var or fail loudly at startup."""
    val = os.environ.get(name, "").strip()
    if not val:
        raise RuntimeError(
            f"Missing required environment variable: {name}. "
            f"See .env.example for setup."
        )
    return val


def _csv(name: str, default: str = "") -> list[str]:
    raw = os.environ.get(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


@dataclass(frozen=True)
class Config:
    # Discord
    discord_bot_token: str
    discord_alerts_channel_id: int
    discord_commands_channel_id: int | None
    discord_owner_id: int | None

    # Reddit
    reddit_client_id: str
    reddit_client_secret: str
    reddit_user_agent: str
    subreddits: list[str]
    reddit_poll_seconds: int
    reddit_fetch_limit: int

    # Store / inventory (Phase 2)
    default_store_id: str | None
    extra_store_ids: list[str]
    inventory_provider: str
    serpapi_key: str | None
    penny_price_threshold: float

    # LLM SKU fallback (Phase 3)
    anthropic_api_key: str | None
    llm_fallback_enabled: bool
    llm_model: str
    llm_max_calls_per_poll: int

    # State
    db_path: str

    @staticmethod
    def load() -> "Config":
        commands_channel = os.environ.get("DISCORD_COMMANDS_CHANNEL_ID", "").strip()
        owner = os.environ.get("DISCORD_OWNER_ID", "").strip()
        anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        return Config(
            discord_bot_token=_req("DISCORD_BOT_TOKEN"),
            discord_alerts_channel_id=int(_req("DISCORD_ALERTS_CHANNEL_ID")),
            discord_commands_channel_id=int(commands_channel) if commands_channel else None,
            discord_owner_id=int(owner) if owner else None,
            reddit_client_id=_req("REDDIT_CLIENT_ID"),
            reddit_client_secret=_req("REDDIT_CLIENT_SECRET"),
            reddit_user_agent=_req("REDDIT_USER_AGENT"),
            subreddits=_csv("REDDIT_SUBREDDITS", "homedepot,pennydeals"),
            reddit_poll_seconds=int(os.environ.get("REDDIT_POLL_SECONDS", "90")),
            reddit_fetch_limit=int(os.environ.get("REDDIT_FETCH_LIMIT", "50")),
            default_store_id=os.environ.get("DEFAULT_STORE_ID", "").strip() or None,
            extra_store_ids=_csv("STORE_IDS"),
            inventory_provider=os.environ.get("INVENTORY_PROVIDER", "none").strip().lower(),
            serpapi_key=os.environ.get("SERPAPI_KEY", "").strip() or None,
            penny_price_threshold=float(os.environ.get("PENNY_PRICE_THRESHOLD", "1.00")),
            anthropic_api_key=anthropic_key or None,
            # Fallback is on by default when a key is present; explicit opt-out via
            # LLM_FALLBACK_ENABLED=false.
            llm_fallback_enabled=(
                os.environ.get("LLM_FALLBACK_ENABLED", "true").strip().lower()
                in ("1", "true", "yes")
                and bool(anthropic_key)
            ),
            llm_model=os.environ.get("LLM_MODEL", "claude-opus-4-8").strip(),
            llm_max_calls_per_poll=int(os.environ.get("LLM_MAX_CALLS_PER_POLL", "5")),
            db_path=os.environ.get("DB_PATH", "data/penny.sqlite3").strip(),
        )
