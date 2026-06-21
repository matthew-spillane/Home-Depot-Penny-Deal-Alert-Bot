"""Entrypoint for the Home Depot Penny Deal Alert Bot.

One persistent process: the Discord bot owns the event loop and runs the Reddit
poll as a background task. On Railway this should run as a *worker* service
(no exposed port) — see README / Procfile.
"""
from __future__ import annotations

import logging

from bot.config_types import Config
from bot.discord_bot import PennyBot


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )
    cfg = Config.load()
    bot = PennyBot(cfg)
    # discord.py manages the loop, reconnection, and graceful shutdown.
    bot.run(cfg.discord_bot_token, log_handler=None)


if __name__ == "__main__":
    main()
