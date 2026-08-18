"""Configuration and secrets.

Reads the Steam Web API key from a local `.env`. The key is a hard
prerequisite for the player path (PROJECT.md, "The bets"): when it's needed
and missing we fail loudly with an actionable message rather than limping on
with a blank string and producing a confusing 401 deep in the Steam client.
"""

import os
from pathlib import Path

from dotenv import load_dotenv


class MissingConfigError(RuntimeError):
    """Raised when a required piece of configuration is absent."""


_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_env_loaded = False


def load_env() -> None:
    """Load `.env` from the project root into the environment, once.

    Idempotent and does nothing if the file is absent — real environment
    variables (e.g. set in the shell or CI) still take effect.
    """
    global _env_loaded
    if not _env_loaded:
        load_dotenv(_PROJECT_ROOT / ".env")
        _env_loaded = True


def steam_api_key() -> str:
    """Return the Steam Web API key, or raise if it isn't configured.

    Callers on the player path invoke this and let the error propagate; there
    is deliberately no default. A blank key would fail later and less clearly.
    """
    load_env()
    key = os.environ.get("STEAM_API_KEY", "").strip()
    if not key:
        raise MissingConfigError(
            "STEAM_API_KEY is not set. B4CKL0G needs a free Steam Web API key "
            "to read player data. Get one at "
            "https://steamcommunity.com/dev/apikey and add it to a .env file "
            "in the project root as:\n\n    STEAM_API_KEY=your_key_here\n"
        )
    return key
