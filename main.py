#!/usr/bin/env python3
"""MangaForge Telegram bot entrypoint (Pyrofork)."""

import logging
import sys
from pathlib import Path

# Add current directory to path for imports
current_dir = Path(__file__).parent
sys.path.insert(0, str(current_dir))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


def check_dependencies() -> bool:
    """Check if required runtime dependencies are installed."""
    required_modules = [
        ("pyrogram", "pyrofork"),
        ("httpx", "httpx"),
        ("bs4", "beautifulsoup4"),
        ("lxml", "lxml"),
        ("yaml", "PyYAML"),
        ("PIL", "Pillow"),
        ("reportlab", "reportlab"),
    ]

    missing_modules = []
    for import_name, package_name in required_modules:
        try:
            __import__(import_name)
        except ImportError:
            missing_modules.append(package_name)

    if missing_modules:
        print("❌ Missing required dependencies:")
        for module in missing_modules:
            print(f"   • {module}")
        print("\n💡 Install with:")
        print(f"   pip install {' '.join(missing_modules)}")
        print("\n   Or install all requirements:")
        print("   pip install -r requirements.txt")
        return False

    return True


def main() -> int:
    """Launch the MangaForge Telegram bot."""
    print("🤖 Starting MangaForge Pyrofork Bot...")
    if not check_dependencies():
        return 1

    (current_dir / "logs").mkdir(exist_ok=True)
    file_handler = logging.FileHandler(current_dir / "logs" / "mangaforge.log", encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
    logging.getLogger().addHandler(file_handler)

    try:
        from bot.app import BotRuntimeConfig, MangaForgeBot

        runtime_config = BotRuntimeConfig.from_env(strict=True)
        bot = MangaForgeBot(runtime_config=runtime_config)
        bot.run()
        return 0
    except KeyboardInterrupt:
        print("\n👋 MangaForge bot stopped by user")
        return 0
    except Exception as exc:
        logger.error("Unexpected error in main: %s", exc)
        print(f"\n❌ An unexpected error occurred: {exc}")
        print("Check logs/mangaforge.log for details")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())