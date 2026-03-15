# 🤖 MangaForge Pyrofork Bot

MangaForge is now a **Telegram bot** powered by **Python + Pyrofork** with an inline-button UI and a reusable manga scraping/download engine.

## ✨ Highlights

- 🔎 Search manga by title from supported providers
- 🔗 Load manga directly from URL (auto provider detection)
- 📚 Browse chapters with paginated previews
- ⬇️ Download modes:
  - Latest 1
  - Latest 5
  - All chapters
  - Custom range
- 📦 Output formats: **CBZ**, **PDF**, **Images**, **Both**
- ⚙️ In-bot settings UI:
  - Default format
  - Preferred language
  - Preferred scanlator
  - Chapter/image workers
- 🎨 "Cool UI" via Telegram inline keyboards and rich status messages

---

## ❌ Removed

- **MangaKakalot provider has been removed** from this project.

---

## 🚀 Quick Start

### 1) Install dependencies

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2) Set Telegram bot credentials

Create env vars before running:

```bash
export API_ID="123456"
export API_HASH="your_api_hash"
export BOT_TOKEN="123456:your_bot_token"
```

### 3) Run bot

```bash
python main.py
```

---

## 🧠 Bot Commands

- `/start` — open main menu
- `/help` — show help panel

Everything else is handled from inline buttons and guided input prompts.

---

## 🏗️ Core Structure

```text
MangaForge/
├── bot/
│   └── app.py                 # Pyrofork bot UI + handlers
├── core/
│   ├── provider_manager.py    # Auto-load providers
│   ├── downloader.py          # Parallel download engine
│   ├── converter.py           # CBZ/PDF conversion
│   └── config.py              # YAML config manager
├── providers/                 # Provider plugins (MangaKakalot removed)
├── config/settings.yaml       # Runtime settings
└── main.py                    # Bot entrypoint
```

---

## ⚙️ Configuration Notes

Main settings are in `config/settings.yaml`.

Useful keys:

- `output.default_format`: `cbz`, `pdf`, `images`, `both`
- `providers.preferred_language`
- `providers.preferred_scanlator`
- `download.max_chapter_workers`
- `download.max_image_workers`
- `bot.max_upload_files` (optional)
- `bot.max_upload_size_mb` (optional)

---

## 🧪 Basic Verification

```bash
python test_core_system.py
python test_cli_system.py
```

`test_cli_system.py` now validates the bot entry/runtime imports and config integration.

---

## 📄 License

MIT — see `LICENSE`.

