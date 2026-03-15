"""Pyrofork-powered Telegram bot interface for MangaForge."""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from pyrogram import Client, filters
from pyrogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from core.config import Config
from core.converter import Converter
from core.downloader import Downloader
from core.provider_manager import ProviderManager
from models import Chapter, MangaInfo, MangaSearchResult

logger = logging.getLogger(__name__)


@dataclass
class BotRuntimeConfig:
    """Runtime credentials for the Telegram bot."""

    api_id: int
    api_hash: str
    bot_token: str
    session_name: str = "mangaforge_pyrofork_bot"

    @classmethod
    def from_env(cls, strict: bool = True) -> "BotRuntimeConfig":
        """Load runtime config from environment variables."""
        api_id_raw = os.getenv("API_ID", "").strip()
        api_hash = os.getenv("API_HASH", "").strip()
        bot_token = os.getenv("BOT_TOKEN", "").strip()

        if strict:
            missing = [
                key
                for key, value in (
                    ("API_ID", api_id_raw),
                    ("API_HASH", api_hash),
                    ("BOT_TOKEN", bot_token),
                )
                if not value
            ]
            if missing:
                raise ValueError(
                    "Missing required environment variables: "
                    + ", ".join(missing)
                    + ". Set them before starting the bot."
                )

        if not api_id_raw:
            api_id_raw = "12345"
        try:
            api_id = int(api_id_raw)
        except ValueError as exc:
            raise ValueError("API_ID must be a numeric value.") from exc

        if not api_hash:
            api_hash = "replace_me_api_hash"
        if not bot_token:
            bot_token = "12345:replace_me_bot_token"

        session_name = os.getenv("BOT_SESSION_NAME", "mangaforge_pyrofork_bot").strip() or "mangaforge_pyrofork_bot"
        return cls(api_id=api_id, api_hash=api_hash, bot_token=bot_token, session_name=session_name)


@dataclass
class UserSession:
    """In-memory user session state."""

    awaiting: Optional[str] = None
    search_query: str = ""
    provider_id: str = ""
    search_page: int = 1
    has_next_page: bool = False
    search_results: List[MangaSearchResult] = field(default_factory=list)
    manga_info: Optional[MangaInfo] = None
    chapters: List[Chapter] = field(default_factory=list)
    chapter_preview_page: int = 1


class MangaForgeBot:
    """Telegram bot UI for the MangaForge manga engine."""

    PROVIDERS_PER_PAGE = 8
    SEARCH_RESULTS_PER_PAGE = 10
    CHAPTERS_PER_PREVIEW = 10

    def __init__(self, runtime_config: Optional[BotRuntimeConfig] = None):
        self.runtime_config = runtime_config or BotRuntimeConfig.from_env(strict=True)
        self.config = Config()
        self.provider_manager = ProviderManager()
        self.converter = Converter()
        self.sessions: Dict[int, UserSession] = {}
        self.app = Client(
            self.runtime_config.session_name,
            api_id=self.runtime_config.api_id,
            api_hash=self.runtime_config.api_hash,
            bot_token=self.runtime_config.bot_token,
        )
        self._register_handlers()

    def run(self):
        """Start bot polling."""
        logger.info("Starting MangaForge Pyrofork bot...")
        self.app.run()

    def _register_handlers(self):
        @self.app.on_message(filters.private & filters.command(["start", "help"]))
        async def start_handler(client: Client, message: Message):
            _ = client
            await self._show_home(message)

        @self.app.on_callback_query(filters.private)
        async def callback_handler(client: Client, query: CallbackQuery):
            _ = client
            try:
                await self._handle_callback(query)
            except Exception as exc:  # pragma: no cover - defensive runtime handler
                logger.exception("Callback handling failed: %s", exc)
                await query.answer("Action failed. Try again.", show_alert=True)

        @self.app.on_message(filters.private & filters.text & ~filters.command(["start", "help"]))
        async def text_handler(client: Client, message: Message):
            _ = client
            try:
                await self._handle_text(message)
            except Exception as exc:  # pragma: no cover - defensive runtime handler
                logger.exception("Text handling failed: %s", exc)
                await message.reply_text("❌ Something went wrong while processing your input.")

    def _session(self, user_id: int) -> UserSession:
        if user_id not in self.sessions:
            self.sessions[user_id] = UserSession()
        return self.sessions[user_id]

    def _enabled_provider_ids(self) -> List[str]:
        loaded = self.provider_manager.list_providers()
        configured = set(self.config.enabled_providers)
        filtered = [pid for pid in loaded if pid in configured and pid != "mangakakalot"]
        if filtered:
            return filtered
        return [pid for pid in loaded if pid != "mangakakalot"]

    def _provider_display_name(self, provider_id: str) -> str:
        provider = self.provider_manager.get_provider(provider_id)
        return provider.provider_name

    def _new_provider_instance(self, provider_id: str):
        provider = self.provider_manager.get_provider(provider_id)
        return provider.__class__()

    async def _show_home(self, message: Message):
        user_id = message.from_user.id if message.from_user else 0
        self._session(user_id)  # create session
        text = (
            "✨ **MangaForge Pyrofork Bot**\n\n"
            "Use the buttons below to search manga, fetch by URL, manage settings, "
            "and download chapters in your preferred format."
        )
        await message.reply_text(text, reply_markup=self._home_keyboard())

    async def _handle_callback(self, query: CallbackQuery):
        if not query.message or not query.from_user:
            await query.answer()
            return

        user_id = query.from_user.id
        session = self._session(user_id)
        data = query.data or ""

        if data == "menu:home":
            await query.answer()
            await query.message.edit_text(
                "✨ **MangaForge Pyrofork Bot**\n\n"
                "Search manga by title, load from URL, tune settings, and download chapters.",
                reply_markup=self._home_keyboard(),
            )
            return

        if data == "menu:help":
            await query.answer()
            await query.message.edit_text(self._help_text(), reply_markup=self._help_keyboard())
            return

        if data == "menu:search":
            session.awaiting = "search_query"
            await query.answer()
            await query.message.edit_text(
                "🔎 Send a manga title to search.\n\n"
                "_Example_: `solo leveling`",
                reply_markup=self._back_home_keyboard(),
            )
            return

        if data == "menu:url":
            session.awaiting = "manga_url"
            await query.answer()
            await query.message.edit_text(
                "🔗 Send a direct manga URL from any supported provider.",
                reply_markup=self._back_home_keyboard(),
            )
            return

        if data == "menu:providers":
            await query.answer()
            await self._render_provider_list(query.message, page=1)
            return

        if data == "menu:settings":
            await query.answer()
            await self._render_settings(query.message)
            return

        if data.startswith("provp:"):
            _, page_raw = data.split(":", 1)
            await query.answer()
            await self._render_provider_list(query.message, page=int(page_raw))
            return

        if data == "settings:format":
            await query.answer()
            await query.message.edit_text(
                "⚙️ Choose your default download format:",
                reply_markup=self._format_keyboard(),
            )
            return

        if data.startswith("settings:setfmt:"):
            _, _, fmt = data.split(":", 2)
            self.config.set("output.default_format", fmt)
            self.config.save()
            await query.answer("Format updated ✅")
            await self._render_settings(query.message)
            return

        if data == "settings:language":
            session.awaiting = "preferred_language"
            await query.answer()
            await query.message.edit_text(
                "🌐 Send preferred language code.\n\n"
                "Examples: `en`, `es`, `fr`, `pt-br`\n"
                "Send `none` to clear filter.",
                reply_markup=self._settings_back_keyboard(),
            )
            return

        if data == "settings:scanlator":
            session.awaiting = "preferred_scanlator"
            await query.answer()
            await query.message.edit_text(
                "🧩 Send preferred scanlator name.\n\n"
                "Send `none` to clear scanlator filter.",
                reply_markup=self._settings_back_keyboard(),
            )
            return

        if data == "settings:chapter_workers":
            session.awaiting = "chapter_workers"
            await query.answer()
            await query.message.edit_text(
                "🧵 Send max chapter workers as a number (1-8).",
                reply_markup=self._settings_back_keyboard(),
            )
            return

        if data == "settings:image_workers":
            session.awaiting = "image_workers"
            await query.answer()
            await query.message.edit_text(
                "🖼️ Send max image workers as a number (1-24).",
                reply_markup=self._settings_back_keyboard(),
            )
            return

        if data.startswith("search:provider_page:"):
            _, _, _, page_raw = data.split(":", 3)
            await query.answer()
            await self._render_provider_picker(query.message, session, page=int(page_raw))
            return

        if data.startswith("search:provider:"):
            _, _, provider_id = data.split(":", 2)
            await query.answer("Searching...")
            await self._run_search(query.message, session, provider_id=provider_id, page=1)
            return

        if data.startswith("search:page:"):
            _, _, page_raw = data.split(":", 2)
            await query.answer("Loading page...")
            await self._run_search(
                query.message,
                session,
                provider_id=session.provider_id,
                page=int(page_raw),
            )
            return

        if data.startswith("search:pick:"):
            _, _, index_raw = data.split(":", 2)
            await query.answer("Loading manga...")
            await self._open_search_result(query.message, session, index=int(index_raw))
            return

        if data == "search:providers":
            await query.answer()
            await self._render_provider_picker(query.message, session, page=1)
            return

        if data == "search:back":
            await query.answer()
            await self._render_search_results(query.message, session)
            return

        if data == "chapter:prev":
            session.chapter_preview_page = max(1, session.chapter_preview_page - 1)
            await query.answer()
            await self._render_manga_view(query.message, session)
            return

        if data == "chapter:next":
            max_page = self._max_chapter_pages(session.chapters)
            session.chapter_preview_page = min(max_page, session.chapter_preview_page + 1)
            await query.answer()
            await self._render_manga_view(query.message, session)
            return

        if data.startswith("download:"):
            _, mode = data.split(":", 1)
            await query.answer()
            await self._handle_download_mode(query.message, session, mode=mode)
            return

        await query.answer("Unknown action.", show_alert=True)

    async def _handle_text(self, message: Message):
        if not message.from_user:
            return
        user_id = message.from_user.id
        session = self._session(user_id)
        text = (message.text or "").strip()

        if session.awaiting == "search_query":
            if not text:
                await message.reply_text("Please send a non-empty search query.")
                return
            session.search_query = text
            session.awaiting = None
            await self._render_provider_picker(message, session, page=1)
            return

        if session.awaiting == "manga_url":
            session.awaiting = None
            if not text.startswith(("http://", "https://")):
                await message.reply_text("❌ Please send a valid URL starting with http:// or https://")
                return
            await self._run_url_flow(message, session, url=text)
            return

        if session.awaiting == "preferred_language":
            language = "" if text.lower() == "none" else text.lower()
            self.config.set("providers.preferred_language", language)
            self.config.save()
            session.awaiting = None
            await message.reply_text("✅ Preferred language updated.")
            await self._render_settings(message)
            return

        if session.awaiting == "preferred_scanlator":
            scanlator = "" if text.lower() == "none" else text
            self.config.set("providers.preferred_scanlator", scanlator)
            self.config.save()
            session.awaiting = None
            await message.reply_text("✅ Preferred scanlator updated.")
            await self._render_settings(message)
            return

        if session.awaiting == "chapter_workers":
            try:
                workers = int(text)
            except ValueError:
                await message.reply_text("Please send a number between 1 and 8.")
                return
            workers = max(1, min(workers, 8))
            self.config.set("download.max_chapter_workers", workers)
            self.config.save()
            session.awaiting = None
            await message.reply_text(f"✅ Chapter workers set to {workers}.")
            await self._render_settings(message)
            return

        if session.awaiting == "image_workers":
            try:
                workers = int(text)
            except ValueError:
                await message.reply_text("Please send a number between 1 and 24.")
                return
            workers = max(1, min(workers, 24))
            self.config.set("download.max_image_workers", workers)
            self.config.save()
            session.awaiting = None
            await message.reply_text(f"✅ Image workers set to {workers}.")
            await self._render_settings(message)
            return

        if session.awaiting == "custom_range":
            session.awaiting = None
            await self._handle_custom_range(message, session, text=text)
            return

        await message.reply_text(
            "Use /start to open the main menu and begin.",
            reply_markup=self._home_keyboard(),
        )

    async def _render_provider_picker(self, message: Message, session: UserSession, page: int):
        provider_ids = sorted(self._enabled_provider_ids(), key=self._provider_display_name)
        if not provider_ids:
            await message.reply_text("❌ No providers are currently enabled.")
            return

        total_pages = max(1, (len(provider_ids) + self.PROVIDERS_PER_PAGE - 1) // self.PROVIDERS_PER_PAGE)
        page = max(1, min(page, total_pages))
        start = (page - 1) * self.PROVIDERS_PER_PAGE
        chunk = provider_ids[start : start + self.PROVIDERS_PER_PAGE]

        buttons: List[List[InlineKeyboardButton]] = []
        for provider_id in chunk:
            buttons.append(
                [
                    InlineKeyboardButton(
                        self._provider_display_name(provider_id),
                        callback_data=f"search:provider:{provider_id}",
                    )
                ]
            )

        nav_row: List[InlineKeyboardButton] = []
        if page > 1:
            nav_row.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"search:provider_page:{page - 1}"))
        if page < total_pages:
            nav_row.append(InlineKeyboardButton("Next ➡️", callback_data=f"search:provider_page:{page + 1}"))
        if nav_row:
            buttons.append(nav_row)

        buttons.append([InlineKeyboardButton("🏠 Home", callback_data="menu:home")])
        keyboard = InlineKeyboardMarkup(buttons)

        query_label = self._trim(session.search_query, 40)
        text = (
            f"🧭 **Choose Provider**\n\n"
            f"Query: `{query_label}`\n"
            f"Page: {page}/{total_pages}"
        )
        await self._safe_edit_or_reply(message, text, keyboard)

    async def _run_search(self, message: Message, session: UserSession, provider_id: str, page: int):
        if provider_id not in self._enabled_provider_ids():
            await self._safe_edit_or_reply(message, "❌ That provider is disabled.", self._back_home_keyboard())
            return

        if not session.search_query:
            await self._safe_edit_or_reply(
                message,
                "Search query is missing. Tap search again from home.",
                self._back_home_keyboard(),
            )
            return

        searching_text = (
            f"🔎 Searching `{self._trim(session.search_query, 50)}`\n"
            f"Provider: **{self._provider_display_name(provider_id)}**\n"
            f"Page: {page}"
        )
        await self._safe_edit_or_reply(message, searching_text, self._back_home_keyboard())

        provider = self._new_provider_instance(provider_id)
        results, has_next = await asyncio.to_thread(provider.search, session.search_query, page)
        session.provider_id = provider_id
        session.search_page = page
        session.search_results = results[: self.SEARCH_RESULTS_PER_PAGE]
        session.has_next_page = has_next
        session.manga_info = None
        session.chapters.clear()
        session.chapter_preview_page = 1

        await self._render_search_results(message, session)

    async def _render_search_results(self, message: Message, session: UserSession):
        if not session.search_results:
            text = (
                "😕 No manga found.\n\n"
                "Try another provider or adjust your query."
            )
            keyboard = InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("🔁 Change Provider", callback_data="search:providers")],
                    [InlineKeyboardButton("🏠 Home", callback_data="menu:home")],
                ]
            )
            await self._safe_edit_or_reply(message, text, keyboard)
            return

        provider_name = self._provider_display_name(session.provider_id)
        lines = [
            f"🔎 **Results for** `{self._trim(session.search_query, 50)}`",
            f"Provider: **{provider_name}**",
            f"Page: {session.search_page}",
            "",
        ]
        buttons: List[List[InlineKeyboardButton]] = []
        for index, result in enumerate(session.search_results, start=1):
            lines.append(f"`{index}.` {self._trim(result.title, 70)}")
            buttons.append(
                [
                    InlineKeyboardButton(
                        f"{index}. {self._trim(result.title, 45)}",
                        callback_data=f"search:pick:{index - 1}",
                    )
                ]
            )

        nav_row: List[InlineKeyboardButton] = []
        if session.search_page > 1:
            nav_row.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"search:page:{session.search_page - 1}"))
        if session.has_next_page:
            nav_row.append(InlineKeyboardButton("Next ➡️", callback_data=f"search:page:{session.search_page + 1}"))
        if nav_row:
            buttons.append(nav_row)

        buttons.append(
            [
                InlineKeyboardButton("🔁 Provider", callback_data="search:providers"),
                InlineKeyboardButton("🏠 Home", callback_data="menu:home"),
            ]
        )
        await self._safe_edit_or_reply(message, "\n".join(lines), InlineKeyboardMarkup(buttons))

    async def _open_search_result(self, message: Message, session: UserSession, index: int):
        if index < 0 or index >= len(session.search_results):
            await self._safe_edit_or_reply(message, "Invalid selection.", self._back_home_keyboard())
            return

        result = session.search_results[index]
        provider = self._new_provider_instance(session.provider_id)
        loading_text = f"📚 Loading **{self._trim(result.title, 80)}**..."
        await self._safe_edit_or_reply(message, loading_text, self._back_home_keyboard())

        manga_info = await asyncio.to_thread(provider.get_manga_info, result.manga_id, None)
        chapters = await asyncio.to_thread(provider.get_chapters, manga_info.manga_id)
        session.manga_info = manga_info
        session.chapters = chapters
        session.chapter_preview_page = 1

        await self._render_manga_view(message, session)

    async def _run_url_flow(self, message: Message, session: UserSession, url: str):
        status = await message.reply_text("🔗 Detecting provider...")
        provider_hint = self.provider_manager.get_provider_from_url(url)
        if not provider_hint:
            await status.edit_text("❌ Could not detect provider from that URL.")
            return
        if provider_hint.provider_id == "mangakakalot":
            await status.edit_text("❌ MangaKakalot has been removed from this bot.")
            return
        if provider_hint.provider_id not in self._enabled_provider_ids():
            await status.edit_text("❌ That provider is currently disabled in config.")
            return

        provider = self._new_provider_instance(provider_hint.provider_id)
        await status.edit_text(
            f"✅ Provider: **{provider.provider_name}**\nFetching manga info..."
        )
        manga_info = await asyncio.to_thread(provider.get_manga_info, None, url)
        chapters = await asyncio.to_thread(provider.get_chapters, manga_info.manga_id)

        session.provider_id = provider.provider_id
        session.manga_info = manga_info
        session.chapters = chapters
        session.chapter_preview_page = 1
        await self._render_manga_view(status, session)

    async def _render_manga_view(self, message: Message, session: UserSession):
        if not session.manga_info:
            await self._safe_edit_or_reply(message, "Manga info is not loaded yet.", self._back_home_keyboard())
            return

        manga = session.manga_info
        chapters_desc = self._chapters_desc(session.chapters)
        max_page = self._max_chapter_pages(session.chapters)
        session.chapter_preview_page = max(1, min(session.chapter_preview_page, max_page))
        preview = self._chapter_preview(session.chapters, session.chapter_preview_page)

        text = (
            f"📘 **{self._trim(manga.title, 90)}**\n"
            f"🧷 Provider: **{self._provider_display_name(session.provider_id)}**\n"
            f"📊 Status: `{manga.status or 'Unknown'}`\n"
            f"🏷️ Genres: `{', '.join(manga.genres[:5]) if manga.genres else 'N/A'}`\n"
            f"🗂️ Chapters: **{chapters_desc}**\n"
            f"🎞️ Default Format: **{self.config.default_format.upper()}**\n\n"
            f"{preview}"
        )
        await self._safe_edit_or_reply(message, text, self._manga_keyboard(session))

    async def _handle_download_mode(self, message: Message, session: UserSession, mode: str):
        if not session.manga_info or not session.chapters:
            await self._safe_edit_or_reply(
                message,
                "No manga selected. Start a search first.",
                self._back_home_keyboard(),
            )
            return

        descending = self._descending_chapters(session.chapters)
        if mode == "range":
            session.awaiting = "custom_range"
            await self._safe_edit_or_reply(
                message,
                "🎯 Send custom range in latest-first indices.\n\n"
                f"Example: `1-5` (latest 5 chapters), `3-3` (only the 3rd latest)\n"
                f"Total available: **{len(descending)}** chapters",
                self._manga_keyboard(session),
            )
            return

        if mode == "latest1":
            selected_desc = descending[:1]
        elif mode == "latest5":
            selected_desc = descending[:5]
        elif mode == "all":
            selected_desc = descending
        else:
            await self._safe_edit_or_reply(message, "Unknown download mode.", self._manga_keyboard(session))
            return

        selected = list(reversed(selected_desc))
        await self._start_download(message, session, selected)

    async def _handle_custom_range(self, message: Message, session: UserSession, text: str):
        descending = self._descending_chapters(session.chapters)
        if "-" not in text:
            await message.reply_text("❌ Invalid range format. Use `start-end`, for example `1-5`.")
            return
        left, right = text.split("-", 1)
        try:
            start = int(left.strip())
            end = int(right.strip())
        except ValueError:
            await message.reply_text("❌ Range values must be numeric.")
            return
        if start <= 0 or end <= 0 or end < start:
            await message.reply_text("❌ Range must be positive and start <= end.")
            return
        if start > len(descending):
            await message.reply_text("❌ Start index is larger than available chapter count.")
            return

        end = min(end, len(descending))
        selected_desc = descending[start - 1 : end]
        selected = list(reversed(selected_desc))
        await self._start_download(message, session, selected)

    async def _start_download(self, message: Message, session: UserSession, chapters: Sequence[Chapter]):
        if not chapters:
            await message.reply_text("No chapters selected for download.")
            return

        manga = session.manga_info
        if manga is None:
            await message.reply_text("No manga loaded.")
            return

        fmt = (self.config.default_format or "cbz").lower()
        provider = self._new_provider_instance(session.provider_id)
        status = await message.reply_text(
            f"⏬ Starting download\n"
            f"📘 {self._trim(manga.title, 80)}\n"
            f"📦 Chapters: {len(chapters)}\n"
            f"🧰 Format: {fmt.upper()}"
        )

        try:
            artifacts = await asyncio.to_thread(
                self._download_and_convert,
                provider,
                manga,
                list(chapters),
                fmt,
            )
        except Exception as exc:
            logger.exception("Download failed: %s", exc)
            await status.edit_text(f"❌ Download failed: `{exc}`")
            return

        root_dir = self.config.download_dir / self._safe_path_name(manga.title)
        upload_stats = await self._send_generated_files(status, artifacts, manga.title, fmt)
        await status.edit_text(
            "✅ **Download complete**\n\n"
            f"📘 Title: {self._trim(manga.title, 80)}\n"
            f"📦 Chapters: {len(chapters)}\n"
            f"📁 Output: `{root_dir}`\n"
            f"📤 Sent files: {upload_stats[0]} sent / {upload_stats[1]} skipped"
        )

    def _download_and_convert(
        self,
        provider,
        manga: MangaInfo,
        chapters: List[Chapter],
        format_type: str,
    ) -> List[Path]:
        output_dir = self.config.download_dir
        output_dir.mkdir(parents=True, exist_ok=True)

        with Downloader(
            max_chapter_workers=self.config.max_chapter_workers,
            max_image_workers=self.config.max_image_workers,
        ) as downloader:
            chapter_dirs = downloader.download_chapters(provider, manga, chapters, output_dir)

        artifacts: List[Path] = []
        if format_type == "images":
            return chapter_dirs

        for chapter_dir in chapter_dirs:
            if format_type == "cbz":
                artifacts.append(
                    self.converter.to_cbz(
                        chapter_dir,
                        chapter_dir.with_suffix(".cbz"),
                        delete_images=self.config.delete_images_after,
                    )
                )
            elif format_type == "pdf":
                artifacts.append(
                    self.converter.to_pdf(
                        chapter_dir,
                        chapter_dir.with_suffix(".pdf"),
                        delete_images=self.config.delete_images_after,
                    )
                )
            elif format_type == "both":
                cbz_path = self.converter.to_cbz(
                    chapter_dir,
                    chapter_dir.with_suffix(".cbz"),
                    delete_images=False,
                )
                pdf_path = self.converter.to_pdf(
                    chapter_dir,
                    chapter_dir.with_suffix(".pdf"),
                    delete_images=False,
                )
                artifacts.extend([cbz_path, pdf_path])
                if self.config.delete_images_after:
                    self.converter._cleanup_images(chapter_dir, list(chapter_dir.iterdir()))
            else:
                raise ValueError(f"Unsupported format: {format_type}")

        return artifacts

    async def _send_generated_files(
        self,
        status_message: Message,
        artifacts: Sequence[Path],
        manga_title: str,
        fmt: str,
    ) -> Tuple[int, int]:
        if fmt == "images":
            return (0, 0)

        max_upload_files = int(self.config.get("bot.max_upload_files", 3) or 3)
        max_upload_size_mb = int(self.config.get("bot.max_upload_size_mb", 45) or 45)
        size_cap = max_upload_size_mb * 1024 * 1024

        sent = 0
        skipped = 0
        for artifact in list(artifacts)[:max_upload_files]:
            if not artifact.exists() or not artifact.is_file():
                skipped += 1
                continue
            if artifact.stat().st_size > size_cap:
                skipped += 1
                continue
            try:
                await status_message.reply_document(
                    str(artifact),
                    caption=f"📦 {self._trim(manga_title, 70)} • {artifact.name}",
                )
                sent += 1
            except Exception as exc:  # pragma: no cover - network/runtime
                logger.warning("Failed to upload %s: %s", artifact, exc)
                skipped += 1
        return (sent, skipped)

    async def _render_provider_list(self, message: Message, page: int):
        provider_ids = sorted(self._enabled_provider_ids(), key=self._provider_display_name)
        if not provider_ids:
            await self._safe_edit_or_reply(message, "❌ No enabled providers found.", self._back_home_keyboard())
            return

        total_pages = max(1, (len(provider_ids) + self.PROVIDERS_PER_PAGE - 1) // self.PROVIDERS_PER_PAGE)
        page = max(1, min(page, total_pages))
        start = (page - 1) * self.PROVIDERS_PER_PAGE
        chunk = provider_ids[start : start + self.PROVIDERS_PER_PAGE]

        text_lines = ["📚 **Enabled Providers**", f"Page {page}/{total_pages}", ""]
        for idx, provider_id in enumerate(chunk, start=1 + start):
            text_lines.append(f"`{idx}.` {self._provider_display_name(provider_id)} (`{provider_id}`)")

        rows: List[List[InlineKeyboardButton]] = []
        nav: List[InlineKeyboardButton] = []
        if page > 1:
            nav.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"provp:{page - 1}"))
        if page < total_pages:
            nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"provp:{page + 1}"))
        if nav:
            rows.append(nav)
        rows.append([InlineKeyboardButton("🏠 Home", callback_data="menu:home")])

        await self._safe_edit_or_reply(message, "\n".join(text_lines), InlineKeyboardMarkup(rows))

    async def _render_settings(self, message: Message):
        text = (
            "⚙️ **Bot Settings**\n\n"
            f"📦 Default format: **{self.config.default_format.upper()}**\n"
            f"🌐 Preferred language: `{self.config.preferred_language or 'none'}`\n"
            f"🧩 Preferred scanlator: `{self.config.preferred_scanlator or 'none'}`\n"
            f"🧵 Chapter workers: `{self.config.max_chapter_workers}`\n"
            f"🖼️ Image workers: `{self.config.max_image_workers}`\n\n"
            "_These settings are stored globally in_ `config/settings.yaml`."
        )
        await self._safe_edit_or_reply(message, text, self._settings_keyboard())

    def _home_keyboard(self) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("🔎 Search Manga", callback_data="menu:search"),
                    InlineKeyboardButton("🔗 Manga by URL", callback_data="menu:url"),
                ],
                [
                    InlineKeyboardButton("⚙️ Settings", callback_data="menu:settings"),
                    InlineKeyboardButton("📚 Providers", callback_data="menu:providers"),
                ],
                [InlineKeyboardButton("❓ Help", callback_data="menu:help")],
            ]
        )

    def _help_keyboard(self) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Back Home", callback_data="menu:home")]])

    def _settings_keyboard(self) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("📦 Change Default Format", callback_data="settings:format")],
                [InlineKeyboardButton("🌐 Preferred Language", callback_data="settings:language")],
                [InlineKeyboardButton("🧩 Preferred Scanlator", callback_data="settings:scanlator")],
                [InlineKeyboardButton("🧵 Chapter Workers", callback_data="settings:chapter_workers")],
                [InlineKeyboardButton("🖼️ Image Workers", callback_data="settings:image_workers")],
                [InlineKeyboardButton("🏠 Home", callback_data="menu:home")],
            ]
        )

    def _settings_back_keyboard(self) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("⚙️ Back to Settings", callback_data="menu:settings")],
                [InlineKeyboardButton("🏠 Home", callback_data="menu:home")],
            ]
        )

    def _format_keyboard(self) -> InlineKeyboardMarkup:
        formats = ["cbz", "pdf", "images", "both"]
        rows = [
            [InlineKeyboardButton(fmt.upper(), callback_data=f"settings:setfmt:{fmt}")]
            for fmt in formats
        ]
        rows.append([InlineKeyboardButton("⚙️ Back to Settings", callback_data="menu:settings")])
        return InlineKeyboardMarkup(rows)

    def _back_home_keyboard(self) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Home", callback_data="menu:home")]])

    def _manga_keyboard(self, session: UserSession) -> InlineKeyboardMarkup:
        rows: List[List[InlineKeyboardButton]] = [
            [
                InlineKeyboardButton("⬇️ Latest 1", callback_data="download:latest1"),
                InlineKeyboardButton("⬇️ Latest 5", callback_data="download:latest5"),
            ],
            [
                InlineKeyboardButton("⬇️ Download All", callback_data="download:all"),
                InlineKeyboardButton("🎯 Custom Range", callback_data="download:range"),
            ],
        ]

        if self._max_chapter_pages(session.chapters) > 1:
            rows.append(
                [
                    InlineKeyboardButton("⬅️ Chapters", callback_data="chapter:prev"),
                    InlineKeyboardButton("Chapters ➡️", callback_data="chapter:next"),
                ]
            )

        rows.append(
            [
                InlineKeyboardButton("🔙 Back Results", callback_data="search:back"),
                InlineKeyboardButton("🏠 Home", callback_data="menu:home"),
            ]
        )
        return InlineKeyboardMarkup(rows)

    async def _safe_edit_or_reply(
        self,
        message: Message,
        text: str,
        keyboard: Optional[InlineKeyboardMarkup] = None,
    ):
        text = text[:4000]
        try:
            await message.edit_text(text, reply_markup=keyboard, disable_web_page_preview=True)
        except Exception:
            await message.reply_text(text, reply_markup=keyboard, disable_web_page_preview=True)

    def _chapters_desc(self, chapters: Sequence[Chapter]) -> str:
        if not chapters:
            return "No chapters"
        return f"{len(chapters)} chapters"

    def _descending_chapters(self, chapters: Sequence[Chapter]) -> List[Chapter]:
        return sorted(chapters, key=lambda ch: ch.sort_key, reverse=True)

    def _max_chapter_pages(self, chapters: Sequence[Chapter]) -> int:
        return max(1, (len(chapters) + self.CHAPTERS_PER_PREVIEW - 1) // self.CHAPTERS_PER_PREVIEW)

    def _chapter_preview(self, chapters: Sequence[Chapter], page: int) -> str:
        if not chapters:
            return "No chapter preview available."
        descending = self._descending_chapters(chapters)
        max_page = self._max_chapter_pages(descending)
        page = max(1, min(page, max_page))
        start = (page - 1) * self.CHAPTERS_PER_PREVIEW
        chunk = descending[start : start + self.CHAPTERS_PER_PREVIEW]
        lines = [f"📄 **Chapter Preview** (latest first, page {page}/{max_page})"]
        for idx, chapter in enumerate(chunk, start=start + 1):
            lines.append(
                f"`{idx}.` Ch. {chapter.chapter_number} — {self._trim(chapter.title or 'Untitled', 45)}"
            )
        return "\n".join(lines)

    @staticmethod
    def _trim(value: str, length: int) -> str:
        value = (value or "").strip()
        if len(value) <= length:
            return value
        return value[: length - 1].rstrip() + "…"

    @staticmethod
    def _safe_path_name(value: str) -> str:
        import re

        clean = re.sub(r'[<>:"/\\|?*]', "_", value)
        clean = re.sub(r"\s+", " ", clean)
        return clean.strip()

    @staticmethod
    def _help_text() -> str:
        return (
            "❓ **How to use MangaForge Bot**\n\n"
            "1. Tap **Search Manga** and send a title.\n"
            "2. Pick a provider, open a result, and choose download mode.\n"
            "3. Configure default format in **Settings**.\n"
            "4. Use **Manga by URL** if you already have a direct manga link.\n\n"
            "Formats supported: `CBZ`, `PDF`, `IMAGES`, `BOTH`.\n"
            "MangaKakalot has been removed."
        )

