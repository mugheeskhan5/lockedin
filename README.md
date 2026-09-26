# Reels Digest- LOCKEDIN

`Version: V1` · `Platform: Windows & Linux (desktop)`

### Automate the scroll. Curate the highlights. Deliver to Discord

Reels Digest is a desktop automation tool for Instagram Reels discovery, local content analysis, and scheduled Discord sharing. Start a timed scrolling session, and it captures the reels that appear, extracts available text, groups related content, and selects a small digest for delivery with your own message attached.

It connects browsing and sharing into one workflow: **automated scrolling, background capture, local AI curation, and scheduled delivery**. You choose when a browsing session starts, how long it runs, and where the digest goes.

**V1 · Browser automation · Local AI · Scheduled delivery · Windows & Linux**

[Get started](#windows-installation) · [How it works](#how-it-works) · [Full documentation](DOCS.md)

## What it automates

| Capability | What happens automatically |
| --- | --- |
| **Reels scrolling** | Advances through the visible Reels viewer at a fixed interval during a session you start from the extension popup. |
| **Capture and deduplication** | Records viewed reels with their links, available captions, and capture times, without creating another row for a repeated shortcode. |
| **Local text analysis** | Reads available caption and frame text, builds embeddings, and groups related reels using models on your computer. |
| **Digest selection** | Chooses 3–6 eligible reels per batch, defaulting to 4, favoring variety and avoiding previously shared picks. |
| **Discord sharing** | Sends each selected reel to your configured channel or DM, with your custom message and a thumbnail when available. |
| **Daily execution** | Runs processing and delivery through Windows Task Scheduler, systemd, or cron at your configured time. |
| **Queue and delivery tracking** | Retains pending captures and delivery state so interrupted work can resume while previously delivered picks remain recorded. |

## From a scrolling session to a daily digest

1. **Set it up once.** Connect the Chrome extension to the local backend, configure your Discord destination, write your message, and choose a delivery schedule.
2. **Start a session.** Open Instagram's Reels viewer, choose a scroll interval and session duration in the extension popup, and press **Start**.
3. **Let it collect.** While the Reels tab stays visible, the extension advances through the feed and the backend processes captured content in the background.
4. **Receive the picks.** At the scheduled time, the tool selects eligible reels and sends them to Discord. You can also request an additional batch manually.

**Browsing sessions are user-started.** Sign into Instagram yourself and keep the local backend running while collecting. Auto-scroll stops when the tab is hidden, you interact with the page, the session expires, or its failure checks require it to stop. Capture also works when you prefer to scroll manually.

After queued captures reach the backend, Instagram can be closed. The scheduled digest runs on saved content; your computer must be available at delivery time, with internet access for Discord and Ollama running for local cluster labeling.

> **V1 controls:** Start and stop scrolling from the Chrome extension; configure and manage processing through the terminal; receive digests in Discord. A local dashboard is on the roadmap.

## How it works

| Stage | What happens |
| --- | --- |
| Scrolling | A user-started extension session advances the visible Reels viewer at the configured interval. Capture also works during manual browsing. |
| Capture | The extension observes visible reels and records the shortcode, permalink, caption, capture time, and an optional canvas frame/poster. Instagram DOM selectors live in `extension/selectors.js`. |
| Ingest | A durable extension outbox sends batches to `http://127.0.0.1:8000/ingest`, normally about every 10 seconds while browsing. SQLite deduplicates shortcodes. |
| Understanding | A background worker runs Tesseract OCR when a usable frame exists, then computes a local MiniLM text embedding. Missing/tainted frames fall back to caption text. Rows with no usable text are skipped. |
| Clustering | HDBSCAN groups a capture day's embeddings. Local Ollama produces short cluster labels and summaries; failures are logged with fallbacks. |
| Selection | The selector chooses 3–6 eligible, diverse, previously unshared reels, defaulting to 4. It avoids very similar picks. Fewer than 3 eligible candidates means no new digest. |
| Delivery | A Discord bot sends the configured message and link, with a thumbnail if available. A persistent delivery ledger records progress and prevents automatic re-sharing. |

Content analysis uses **captions and readable on-screen text**. There is no audio transcription or whole-video analysis. Missing captions and failed canvas capture can leave nothing to analyze. Cluster labels can be inaccurate, and some reels legitimately remain unclustered noise.

## Requirements

The Windows walkthrough uses **64-bit Python 3.11**, desktop Chrome 120 or newer, Tesseract with English language data, and Ollama. Python 3.11 is the recommended starting point for the setup commands below.

| Component | Purpose / installation |
| --- | --- |
| Python | Install from [python.org](https://www.python.org/downloads/windows/), including the Python launcher. |
| Chrome | Load the included Manifest V3 extension in Developer Mode. |
| Tesseract | Install the OCR engine separately using the [Tesseract installation documentation](https://tesseract-ocr.github.io/tessdoc/Installation.html). On Windows, the docs link to Windows builds. `pytesseract` alone does not install the engine. |
| Ollama | Install from the [official Windows instructions](https://docs.ollama.com/windows). Use `llama3.2:1b` for the small local labeling model. |
| Discord | A bot account and access to the intended channel or DM recipient. |
| Internet | Needed for initial package/model downloads, Instagram browsing, and Discord delivery. OCR and embeddings run locally after installation; default LLM calls use local Ollama. |
| Git | Only needed to publish or manage the source repository. |

**A GPU is not required.** Embeddings run on CPU. The default CPU thread setting is 2, with a supported setting range of 1–4. For a 16 GB RAM laptop, start with the defaults and the 1B Ollama model. Processing can be slow, and thread limits do not impose a hard RAM limit. Leave memory available for Chrome and other applications, and run one processing job at a time.

Allow several GB of disk space for Python packages, both model downloads, and future captured data. Dependencies and model weights are downloaded during installation and are not bundled with the source. Ollama's model and the embedding model are separate downloads.

## Windows installation

Run all PowerShell commands below from the extracted **`reels-digest` project root**, the folder containing this README. Do not run them from inside `backend`.

### 1. Create the Python environment

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\python -m pip install torch --index-url https://download.pytorch.org/whl/cpu
.\.venv\Scripts\python -m pip install -r requirements.txt
```

The explicit PyTorch CPU wheel installation avoids installing a GPU build for this CPU-oriented setup. Consult the [official PyTorch installer](https://pytorch.org/get-started/locally/) if your platform needs a different command. No virtual-environment activation is necessary: every command invokes its Python directly.

### 2. Create local runtime settings

For a **fresh install**:

```powershell
Copy-Item .\backend\runtime.example.json .\backend\runtime.local.json
```

Keep these defaults initially:

```json
{
  "DIGEST_TIMEZONE": "Asia/Karachi",
  "OLLAMA_MODEL": "llama3.2:1b",
  "OLLAMA_URL": "http://127.0.0.1:11434",
  "CLUSTER_MIN_SIZE": "3",
  "CLUSTER_MIN_SAMPLES": "2",
  "OCR_LANG": "eng",
  "DIGEST_CPU_THREADS": "2"
}
```

Change `DIGEST_TIMEZONE` to your IANA timezone if needed. Do not overwrite an existing local config when updating.

Install the Tesseract executable and English language data. The backend looks for Tesseract on PATH and in the usual Windows installation location. If needed, add this setting to the JSON, using escaped backslashes:

```json
"TESSERACT_CMD": "C:\\Program Files\\Tesseract-OCR\\tesseract.exe"
```

Add a comma after the preceding property. JSON does not permit comments or trailing commas.

### 3. Download the embedding model

```powershell
.\.venv\Scripts\python -m backend.prepare_model
```

Wait for **`Model ready. You can now start the backend.`** The first run needs an internet connection and can take several minutes. Keep `backend/data/model-cache/`; normal understanding uses this local cache.

### 4. Install and prepare Ollama

After installing Ollama, open a new PowerShell window:

```powershell
ollama pull llama3.2:1b
```

Keep Ollama running when clustering. The Windows app normally provides its local server in the background. If it is not running, start `ollama serve` in a separate terminal. If that reports that port 11434 is already in use, check the existing Ollama instance before starting another.

Ollama provides cluster labels. It does **not** provide the embedding model downloaded in step 3, and it does not generate the outgoing Discord message in this version.

### 5. Start the local backend

```powershell
.\.venv\Scripts\python -m uvicorn backend.app:app --host 127.0.0.1 --port 8000
```

Leave this terminal open while collecting reels. Use one server process; do not add `--reload` or multiple workers for routine use.

Open [http://127.0.0.1:8000/health](http://127.0.0.1:8000/health). A healthy fresh installation shows `"status":"ok"`, reel/embedding counts, and understanding job counts. An empty database is expected initially. SQLite is initialized automatically.

Keep the bind address as `127.0.0.1`. This is a local service, not an authenticated public or LAN API.

### 6. Load the Chrome extension

1. Open `chrome://extensions` and enable **Developer mode**.
2. Choose **Load unpacked** and select the project's **`extension`** folder.
3. Pin Reels Digest using Chrome's Extensions menu.
4. Open Instagram, sign in yourself, and **refresh any Instagram tabs that were open before loading/reloading the extension**.
5. Open the Reels viewer and browse normally. After several reels, allow roughly 10–15 seconds for a batch, then refresh `/health`. The `reels` count should increase for previously unseen shortcodes.

The backend reads captures from your browser; it does not discover a feed by itself. Already-seen shortcodes do not increase the unique count. Queued captures can wait in extension storage while the backend is unavailable, so allow them to flush before closing Chrome.

### 7. Configure Discord delivery

Create an application and bot in the [Discord Developer Portal](https://discord.com/developers/applications). See the [discord.py bot setup guide](https://discordpy.readthedocs.io/en/stable/discord.html) for the portal flow.

- Use a **bot token**, never your personal Discord account token.
- Install the bot into the intended server using the bot OAuth2 scope.
- For a channel, grant **View Channel**, **Send Messages**, **Embed Links**, and **Attach Files** there. Threads may also need **Send Messages in Threads**. Administrator access is unnecessary.
- Enable Developer Mode in Discord settings, then copy the target channel ID. For a DM, copy the recipient's user ID instead; their privacy settings and access to the bot must allow DMs. Sharing a server with the bot is the straightforward setup.
- This sender does not need privileged message-content or member intents.

In a second PowerShell terminal, from the project root:

```powershell
.\.venv\Scripts\python -m backend.discord_setup --configure
```

Paste the token into the hidden prompt, choose `channel` or `user`, and provide the matching ID. Read the displayed bot and destination identities. Type `yes` only when they identify your intended destination. This saves `backend/discord.local.json` and **sends no messages**.

Check the effective settings without sending:

```powershell
.\.venv\Scripts\python -m backend.discord_setup
```

The local JSON is excluded by `.gitignore`. Do not copy its contents into issues, screenshots, examples, or this README. Shell environment overrides can supersede saved settings; see the [Configuration section in DOCS.md](DOCS.md#configuration).

### 8. Set your outgoing message

Edit `backend/message_template.py`:

```python
REEL_MESSAGE = "Here's a reel for you 👀"
```

Use a nonempty string of at most 500 characters and save as UTF-8. This same text accompanies every reel. No caption, OCR text, or cluster summary is interpolated into it. Links and thumbnails are added separately; thumbnails may naturally contain text visible in the original reel. Mentions are disabled by the sender.

The next delivery process reads the edited template, including for pending prepared messages. Already-sent Discord messages are not changed. **This file is public source if you publish it**, so keep private names or sensitive text out of the copy you upload to GitHub.

## Important things to know

- **This is a single-user desktop tool**, not a hosted multi-user service. There's no authentication layer for a public backend, no mobile capture app, and no automatic data-retention UI in V1.
- **No Instagram password is ever collected.** Your browser stays signed into Instagram normally; the extension only reads what's visible on the page.
- **The local backend is not a public API.** Keep it bound to `127.0.0.1`. It's a local service for your own machine, not an authenticated LAN or public endpoint.
- **Only your custom message, the reel link, and a thumbnail (when available) go to Discord.** Captions and OCR text are used locally for clustering and selection — they are never sent as the outgoing message.
- **Duplicate protection is intentional and persistent.** The delivery ledger (`backend/data/reels.db`) remembers what's already been sent. Never delete it or reset `shared` flags just to force a resend.
- **Back up before updating or moving the install.** Stop the backend and any scheduled jobs first, then back up the whole `backend/data/` directory plus both local JSON config files (`discord.local.json`, `runtime.local.json`).
- **Keep `backend/discord.local.json` and `runtime.local.json` private.** They're excluded from Git by `.gitignore`, but don't paste their contents into issues, screenshots, or chat either.

## Full documentation

This README covers the overview, requirements, and Windows installation — everything needed for a first working setup. Everything past initial setup lives in **[`DOCS.md`](/docs/DOCS.md)**, including:

- Daily usage (auto-scroll, manual capture, previewing and sending a digest)
- Dates, `yesterday` vs `today`, and sending additional batches
- Automatic scheduling (Windows Task Scheduler, systemd, cron)
- Full configuration reference (runtime settings, Discord settings, environment overrides)
- Troubleshooting and recovery steps
- Linux installation and scheduling
- Storage, privacy, backups, and updates in detail
- Roadmap — planned for future versions
- Publishing a clean copy to GitHub
- Project map
- A step-by-step checklist to confirm your setup is working end to end
