#!/usr/bin/env python3
"""
Bot runtime smoke tests for MangaForge.

This keeps the historical filename but now validates the Pyrofork bot stack.
"""

import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def test_main_imports():
    """Main entry and dependency checker should import."""
    try:
        from main import check_dependencies, main

        _ = check_dependencies
        _ = main
        logger.info("✓ main.py imports successful")
        return True
    except Exception as exc:
        logger.error("Main imports test failed: %s", exc)
        return False


def test_bot_config_non_strict():
    """Non-strict env config should work for local smoke tests."""
    try:
        from bot.app import BotRuntimeConfig

        # Ensure strict mode is not required for smoke tests.
        os.environ.pop("API_ID", None)
        os.environ.pop("API_HASH", None)
        os.environ.pop("BOT_TOKEN", None)
        cfg = BotRuntimeConfig.from_env(strict=False)

        assert isinstance(cfg.api_id, int)
        assert cfg.api_hash
        assert cfg.bot_token
        logger.info("✓ BotRuntimeConfig.from_env(strict=False) works")
        return True
    except Exception as exc:
        logger.error("Bot config test failed: %s", exc)
        return False


def test_bot_initialization():
    """Bot object should initialize without connecting."""
    try:
        from bot.app import BotRuntimeConfig, MangaForgeBot

        runtime = BotRuntimeConfig.from_env(strict=False)
        bot = MangaForgeBot(runtime_config=runtime)

        assert bot.config is not None
        assert bot.provider_manager is not None
        logger.info("✓ MangaForgeBot initialized successfully")
        return True
    except Exception as exc:
        logger.error("Bot initialization test failed: %s", exc)
        return False


def test_mangakakalot_removed():
    """MangaKakalot should not be available anymore."""
    try:
        from core.provider_manager import ProviderManager
        from core.config import Config

        manager = ProviderManager()
        providers = manager.list_providers()
        if "mangakakalot" in providers:
            logger.error("mangakakalot is still loaded in provider manager!")
            return False

        cfg = Config()
        if "mangakakalot" in cfg.enabled_providers:
            logger.error("mangakakalot is still present in enabled providers config!")
            return False

        logger.info("✓ MangaKakalot removed from providers/config")
        return True
    except Exception as exc:
        logger.error("MangaKakalot removal test failed: %s", exc)
        return False


def main():
    logger.info("Starting MangaForge bot system tests...")

    tests = [
        ("Main Imports", test_main_imports),
        ("Bot Config", test_bot_config_non_strict),
        ("Bot Initialization", test_bot_initialization),
        ("MangaKakalot Removed", test_mangakakalot_removed),
    ]

    passed = 0
    failed = 0
    for name, fn in tests:
        logger.info("\n%s", "=" * 52)
        logger.info("Running test: %s", name)
        logger.info("%s", "=" * 52)
        ok = fn()
        if ok:
            logger.info("✓ %s PASSED", name)
            passed += 1
        else:
            logger.error("✗ %s FAILED", name)
            failed += 1

    logger.info("\n%s", "=" * 60)
    logger.info("BOT TEST SUMMARY")
    logger.info("%s", "=" * 60)
    logger.info("Total: %d", len(tests))
    logger.info("Passed: %d", passed)
    logger.info("Failed: %d", failed)

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

