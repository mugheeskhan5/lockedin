# Reels Digest — Full Documentation

This file covers everything beyond the overview and Windows installation in the main [README](README.md): day-to-day usage, scheduling, configuration, troubleshooting, the Linux setup, storage and privacy details, the roadmap, publishing to GitHub, the project map, and a setup checklist.

## Contents

- [Daily usage](#daily-usage)
  - [Optional auto-scroll](#optional-auto-scroll)
  - [Capture while scrolling manually](#capture-while-scrolling-manually)
  - [Inspect and prepare a digest](#inspect-and-prepare-a-digest)
  - [What must stay open?](#what-must-stay-open)
- [Dates and additional batches](#dates-and-additional-batches)
- [Automatic scheduling](#automatic-scheduling)
- [Configuration](#configuration)
- [Troubleshooting](#troubleshooting)
- [Linux installation and scheduling](#linux-installation-and-scheduling)
- [Storage, privacy, backups, and updates](#storage-privacy-backups-and-updates)
- [Roadmap — planned for future versions](#roadmap--planned-for-future-versions)
- [Publish this clean copy to GitHub](#publish-this-clean-copy-to-github)
- [Project map](#project-map)
- [Confirm your setup](#confirm-your-setup)

## Daily usage

Start the local backend and open Instagram in Chrome. For an automated browsing session, use the extension controls below. Each new shortcode is recorded once; captions or frames captured later can improve an existing row without creating a duplicate.

### Optional auto-scroll

Open a visible Instagram Reels viewer with the backend online, then open the extension popup. Set the interval and session duration, and press **Start**. Defaults are 10 seconds between advances and a 10-minute session. Supported ranges are 5–120 seconds and 1–60 minutes.

Scrolling uses a fixed timer. It does not automatically start when Chrome opens. It stops when you stop it, the session ends, the tab becomes hidden, you leave the Reels route, the backend becomes unavailable at a health check, or repeated capture/advance failures occur. Manual wheel, touch, or pointer interaction and relevant navigation keys also stop it.

Close the popup using the extension toolbar button if you want the session to continue: clicking the Instagram page may count as manual interaction and stop it. Keep the Instagram tab visible; a separate visible Chrome window can work alongside another window. Switching that tab into the background or minimizing it stops the session. Reloading the page does not resume a previous session automatically.

### Capture while scrolling manually

Leave auto-scroll stopped and browse normally. The same capture and processing workflow runs in the background, and saved reels remain eligible for your scheduled digest.

### Inspect and prepare a digest

Capture enough reels with usable text. Ten captured reels do not guarantee ten eligible selections: no-text rows, near duplicates, previously shared reels, and pending reservations are excluded.

```powershell
.\.venv\Scripts\python -m backend.inspect_rows
.\.venv\Scripts\python -m backend.clustering --date today
.\.venv\Scripts\python -m backend.clustering --date today --show
.\.venv\Scripts\python -m backend.sharing --date today --preview
```

`inspect_rows` prints a sample of captions, OCR, and understanding state. `--show` prints saved cluster results. **Preview prepares/reserves picks in the local database but sends nothing.** It does not run outstanding understanding jobs itself; let the backend finish those first.

Send the prepared selection:

```powershell
.\.venv\Scripts\python -m backend.sharing --date today --send
.\.venv\Scripts\python -m backend.sharing --date today --status
```

For a single command that processes queued work, clusters, and **actually sends**:

```powershell
.\.venv\Scripts\python -m backend.nightly --date today
```

### What must stay open?

| Activity | Needed |
| --- | --- |
| Collect new reels | Chrome with Instagram open; backend running for prompt delivery/processing. Auto-scroll additionally requires a visible Reels tab. |
| Process already saved captures | The Python command and its local data/model files. Instagram need not stay open. |
| Generate cluster labels | Ollama running with the configured model installed. |
| Deliver a saved digest | Computer on, internet available, valid Discord settings. The nightly command starts its own processing; the HTTP backend does not need to stay open solely for this. |
| Run the supplied Windows task | The configured Windows user signed in; a locked screen is fine. Sleep/shutdown can prevent the scheduled time from being met. |

## Dates and additional batches

**`backend.nightly` defaults to `yesterday`.** Standalone clustering/sharing commands default to `today`; the examples specify the date explicitly to avoid confusion. Dates are interpreted in `DIGEST_TIMEZONE`.

At **00:10 on September 25**, a job with `--date yesterday` selects reels captured on **September 24**. September 25's reels normally go out at 00:10 on September 26. Running the same scheduled task manually keeps its configured argument; clicking Run does not change `yesterday` to `today`.

Use an explicit date when needed:

```powershell
.\.venv\Scripts\python -m backend.nightly --date 2026-09-24
```

A completed day normally returns **`already_sent`** on another run. To send another batch from **today's still-unshared eligible reels**, explicitly use:

```powershell
.\.venv\Scripts\python -m backend.nightly --date today --new-batch
```

This can send another batch each time enough eligible candidates exist. It is not a "send only the most recently captured reels" switch: selection still prioritizes diversity and novelty within that capture day.

To preview an additional batch after understanding/clustering are ready:

```powershell
.\.venv\Scripts\python -m backend.sharing --date today --new-batch --preview
.\.venv\Scripts\python -m backend.sharing --date today --send
```

Unfinished deliveries are resumed before creating a new batch. Leave `--new-batch` out of your routine daily schedule unless repeated additional deliveries are intentional. Never reset `shared` flags or delete the ledger to force sending; that undermines duplicate protection.

## Automatic scheduling

### Windows Task Scheduler — recommended on Windows

Complete a manual delivery first. From the project root:

```powershell
powershell.exe -NoProfile -File .\scheduling\install-windows-task.ps1 -At '00:10'
```

The installer creates **`ReelsDigest-Nightly`**, running:

```text
-u -m backend.nightly --date yesterday
```

It records absolute Python/project paths, adds a daily trigger and a sign-in catch-up trigger, and runs as the current user while signed in. Temporary `$env:` values from a terminal are not saved as the task's Discord credentials; use the local JSON setup. The installer creates runtime settings only if they are missing.

**The trigger uses Windows' system timezone.** Match that timezone to `DIGEST_TIMEZONE`, or deliberately account for the difference. For a wake request, add `-WakeToRun`; Windows power settings and hardware still determine whether waking succeeds. An off computer cannot run the task. Catch-up targets yesterday relative to the eventual run date; it does not automatically send every missed day.

To inspect or manually trigger the installed task:

```powershell
Get-ScheduledTask -TaskName ReelsDigest-Nightly
Get-ScheduledTaskInfo -TaskName ReelsDigest-Nightly
Start-ScheduledTask -TaskName ReelsDigest-Nightly
```

**Starting this task can send Discord messages for yesterday.** For today's reels, run the command with `--date today` shown above instead.

To schedule an evening digest for the current day, install with your preferred time, open Task Scheduler → ReelsDigest-Nightly → Properties → Actions → Edit, and replace only `--date yesterday` with `--date today`. Preserve the executable and **Start in** directory. The sign-in trigger also uses this action; remove that trigger if evening-only delivery is required. Re-running the installer resets the action to its previous-day default.

If downloaded scripts are blocked, review the script and use Windows' file Properties → Unblock where applicable. Organization execution policies still apply. If task registration is denied, use PowerShell elevated as the same intended Windows user if your machine's policy permits it.

If the default execution policy still blocks the script after unblocking the file, run it with a one-time policy bypass scoped to that single process only:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scheduling\install-windows-task.ps1 -At '00:10'
```

`-ExecutionPolicy Bypass` here applies only to this one `powershell.exe` invocation — it does not change your system's saved execution policy, and the scheduled task itself still runs under whatever policy your system normally enforces. Read the script first if you did not already do so above. A stricter organization-managed policy (e.g., via Group Policy) can still block execution regardless of this flag; if so, use your organization's approved process instead of trying to force it through.

Inspect the latest job result and log:

```powershell
Get-Content .\backend\data\nightly-last.json
Get-Content .\backend\data\logs\nightly.log -Tail 30
```

Look at `stages.share.newly_sent` for messages newly sent in this invocation; cumulative sent totals can include earlier batches. `already_sent` and `no_digest` are valid outcomes. A successful task exit alone does not prove new messages were delivered.

The installer does not launch Chrome, the ingest server, or Ollama for you. After moving the project, re-run it to update paths. To remove this task while preserving all local data:

```powershell
powershell.exe -NoProfile -File .\scheduling\remove-windows-task.ps1
```

## Configuration

**There is no automatic `.env` loader.** Use the local JSON files or real process environment variables. `.env` patterns are ignored by Git to reduce accidental publication, not because the app loads them.

### Runtime settings

`backend/runtime.local.json` accepts the following keys. Environment variables with the same names take precedence; restart the relevant process after changing settings.

| Key | Default / purpose |
| --- | --- |
| `DIGEST_TIMEZONE` | `Asia/Karachi`; capture-day boundaries. |
| `OLLAMA_MODEL` | `llama3.2:1b`; local cluster-label model. |
| `OLLAMA_URL` | `http://127.0.0.1:11434`; keep local for local inference. |
| `CLUSTER_MIN_SIZE` | `3`; minimum HDBSCAN group size. |
| `CLUSTER_MIN_SAMPLES` | `2`; density requirement. |
| `OCR_LANG` | `eng`; additional languages require installed Tesseract language data. |
| `DIGEST_CPU_THREADS` | `2`; supported range 1–4, not a RAM cap. |
| `TESSERACT_CMD` | Optional absolute path to the Tesseract executable. |

For a small dataset, `CLUSTER_MIN_SIZE=2` and `CLUSTER_MIN_SAMPLES=1` can allow smaller groups, but cannot create meaningful topics from missing text. MiniLM is primarily suited to English here; installing OCR languages does not add translation or guarantee multilingual clustering quality.

### Discord settings

The wizard writes `backend/discord.local.json`. The tracked `discord.example.json` contains only placeholders:

```json
{
  "bot_token": "PASTE_YOUR_DISCORD_BOT_TOKEN_HERE",
  "channel_id": 0,
  "user_id": 0,
  "picks_per_day": 4
}
```

Supply exactly one positive destination ID; omit or set the other to zero. `picks_per_day` is 3–6 and applies to each requested batch, so explicit extra batches can exceed that number in one day.

| Environment variable | Override |
| --- | --- |
| `DISCORD_BOT_TOKEN` | Bot token. |
| `DISCORD_CHANNEL_ID` | Target channel ID. |
| `DISCORD_USER_ID` | Target user ID for a DM. |
| `DIGEST_PICKS` | Number of picks, 3–6. |

If **either target environment variable is present**, target selection uses the environment target values instead of the file. Remove stale variables when changing between channels and DMs. Do not set both to positive IDs. Run `backend.discord_setup` without `--configure` to inspect the effective identity.

## Troubleshooting

| Symptom | What to do |
| --- | --- |
| Capture count stays zero | Check `/health`, refresh Instagram after loading the extension, open the Reels viewer, and browse previously unseen reels. Check extension errors under `chrome://extensions`. Instagram markup changes may require updating `extension/selectors.js`. |
| Captions look wrong or empty | Use `backend.inspect_rows`. Instagram's DOM can change; punctuation/emoji-only content is not sufficient text. Improve the selector adapter if it captured interface text. |
| OCR is empty | A reel may have no readable on-screen text; canvas access can be tainted or unavailable. Check Tesseract installation/path and language data. Caption-only processing is an expected fallback. |
| `clusters=0` and noise is nonzero | HDBSCAN did not find stable groups. Collect more varied text-bearing reels and inspect captured text before changing density settings. This is not an Ollama failure. |
| Labels hallucinate or say no text supplied | Inspect source rows first. Reels with no usable text should be skipped. A small LLM can still mislabel weak text. Fix inputs, then use `backend.clustering --date today --force` to regenerate labels. |
| Ollama connection/model failure | Keep Ollama running, pull the exact configured model, and check `OLLAMA_URL` and persistent settings. Cluster labeling can fall back; inspect `llm_failures` and logs. |
| `no_digest` | At least 3 eligible, sufficiently distinct, unshared/unreserved reels are needed. A large capture count alone does not meet this requirement. |
| `already_sent` | The selected date's digest is complete. Use `--date today --new-batch` only if you intentionally want another batch. |
| Sends yesterday's reels | The installed task uses `--date yesterday`. Running it manually does not alter its date argument. |
| Invalid token / forbidden / failed DM | Run the read-only identity check; verify bot token, destination ID, channel permissions, and recipient privacy. Fix access before retrying the same date. |
| Link appears without thumbnail | A frame/poster may be unavailable or an Instagram CDN URL may have expired. Text/link fallback is expected. |
| Task works manually but not on schedule | Check the signed-in account, absolute action paths, Start in directory, local configs, timezone, sleep/power state, and logs. Temporary terminal environment variables are not a persistent setup. |
| Another run is busy/deferred | Let the active job finish and inspect logs. Do not start several instances or remove lock/data files to force overlap. |
| A delivery is `unknown` | A network interruption may have occurred after Discord accepted the message. Inspect the destination and ledger manually; automatic retry is intentionally blocked to avoid duplicates. |

### Recover a deleted or missing embedding model

The embedding model lives under `backend/data/model-cache/`. Ollama's separately managed cache cannot replace it.

1. Stop the backend with Ctrl+C and allow any scheduled job to finish.
2. Restore the model while connected to the internet:

   ```powershell
   .\.venv\Scripts\python -m backend.prepare_model
   ```

3. Requeue only jobs that failed during embedding:

   ```powershell
   @'
   from backend.db import connect
   with connect() as db:
       result = db.execute("UPDATE understanding_jobs SET status='pending', attempts=0, last_error=NULL WHERE status='error' AND last_error LIKE 'Embedding %'")
       print(f"Queued {result.rowcount} failed jobs.")
   '@ | .\.venv\Scripts\python -
   ```

4. Restart the backend with the installation command. Watch `/health` for the embedded count to increase. A zero requeue count means there were no matching failed jobs; it does not mean the download failed.

Do not delete the database to repair a model cache. It holds your capture and delivery history.

### Logs and recovery behavior

Nightly stages run sequentially with time limits and per-row error handling. Understanding retries eligible failed jobs up to its attempt limit; invalid/no-text rows are skipped until new useful input arrives. Stage failures are recorded without discarding saved state.

`backend/data/logs/nightly.log` rotates, and detailed stage logs/results are retained for approximately 30 days. `backend/data/nightly-last.json` summarizes the last run. A normal invocation handles up to 500 understanding jobs; `--max-jobs` can adjust that budget for a backlog.

Nightly exit codes: `0` for complete/already sent/no digest, `1` for degraded or failed work, and `2` for deferred/busy work. Look at the detailed result when diagnosing an exit code.

Prepared or failed messages can resume. An interrupted message with an uncertain outcome is not automatically resent. This prioritizes duplicate avoidance over a claim of guaranteed exactly-once network delivery.

## Linux installation and scheduling

The primary walkthrough is Windows. Linux templates are included; package names and service permissions vary by distribution.

On Debian/Ubuntu-style systems, install Python with venv support and Tesseract plus English data using your distribution packages. Install Ollama following its [official documentation](https://docs.ollama.com/linux), then from the project root:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install torch --index-url https://download.pytorch.org/whl/cpu
.venv/bin/python -m pip install -r requirements.txt
cp backend/runtime.example.json backend/runtime.local.json
.venv/bin/python -m backend.prepare_model
ollama pull llama3.2:1b
.venv/bin/python -m backend.discord_setup --configure
.venv/bin/python -m uvicorn backend.app:app --host 127.0.0.1 --port 8000
```

Use a Python version with wheels available for these packages; Python 3.11 is the suggested baseline. Follow the same Chrome, settings, preview, and message instructions, replacing `.\.venv\Scripts\python` with `.venv/bin/python`. Do not overwrite existing runtime settings on an update.

### systemd user timer

1. Edit **both** absolute project paths in `scheduling/reels-digest.service`.
2. Edit `OnCalendar` in `scheduling/reels-digest.timer` if needed. Its default is 00:10 **Asia/Karachi**, independent of a changed runtime JSON timezone.
3. Install and enable:

   ```bash
   mkdir -p ~/.config/systemd/user
   cp scheduling/reels-digest.service scheduling/reels-digest.timer ~/.config/systemd/user/
   systemctl --user daemon-reload
   systemctl --user enable --now reels-digest.timer
   systemctl --user list-timers reels-digest.timer
   ```

`Persistent=true` can cause a missed run to start when the timer is enabled. **It is a real delivery job.** To run it manually and inspect logs:

```bash
systemctl --user start reels-digest.service
journalctl --user -u reels-digest.service -n 50
```

The user manager must remain available; running while logged out may require lingering, subject to your system's policy. Ollama must also be available for labeling. Disable with `systemctl --user disable --now reels-digest.timer`.

### cron alternative

Choose cron **or** systemd, not both. Edit `scheduling/cron.example` with the absolute project path and add its entry through `crontab -e`:

```cron
10 0 * * * /bin/sh '/absolute/path/reels-digest/scheduling/run-nightly.sh'
```

The wrapper defaults to yesterday. Cron uses its configured scheduler timezone and does not inherently recover runs missed while the computer is off. Launcher output goes to `backend/data/logs/cron-last.log`; Python also writes the regular logs.

## Storage, privacy, backups, and updates

| Location | Contents |
| --- | --- |
| Chrome extension local storage | Pending captures and popup settings. |
| `backend/data/reels.db` | Reels, captions, OCR, embeddings, clusters, delivery records, and supporting job tables. SQLite may also create companion WAL/SHM files. |
| `backend/data/model-cache/` | Downloaded MiniLM embedding model. |
| `backend/data/logs/` | Local processing and delivery diagnostics. |
| `backend/discord.local.json` | Private Discord token and destination. |
| `backend/runtime.local.json` | Per-machine runtime preferences. |
| Ollama's own model directory | Downloaded cluster-label model, managed separately by Ollama. |

No Instagram password is collected by this backend. Your browser remains signed into Instagram normally. Captures and analysis stay local with the default settings; **selected reel links, your message, and available thumbnails are sent to Discord**. Downloads contact package/model providers. Pointing Ollama at a remote server changes where cluster text is processed.

This V1 is a single-user desktop tool, not a hosted multi-user service. It does not include authentication for a public backend, a mobile capture app, or an automatic data-retention UI. Disk usage grows with captures and thumbnails.

Before updating or moving an existing installation, stop the backend and scheduled jobs and privately back up the **whole `backend/data/` directory plus both local JSON files**. Preserve that data when installing new source. Recreate the virtual environment on a new machine rather than copying it. Reconfigure scheduler paths and reload the unpacked extension afterward.

Do not publish backups or delete the delivery ledger during routine cleanup. It is what remembers already shared reels. Legacy Telegram history is preserved by migration; an old migrated sent flag is historical state, not evidence that Discord sent that message.

## Roadmap — planned for future versions

V1 is a working, single-user, Windows/Linux desktop tool focused on Instagram capture and Discord delivery. The following are **not implemented yet** and are being considered for future releases. None of this affects how V1 currently behaves.

### A local dashboard, not just a CLI

- A lightweight local web UI to browse captured reels, preview clusters, and approve/send picks without the `inspect_rows` / `--preview` / `--status` command sequence.
- A readable `/health` status page in place of raw JSON.

### Smarter curation

- Feedback on past digest picks (👍/👎) feeding back into future selection scoring.
- Cross-day topic tracking to surface recurring interests over a week or month, not just a single capture day.
- Local semantic search across your own capture history.

### Broader capture and delivery

- Support for additional short-video platforms (e.g., TikTok, YouTube Shorts) using the same capture → understand → cluster → select pipeline.
- Additional delivery targets beyond Discord — email digest and/or RSS feed.
- Perceptual-hash thumbnail comparison to catch reposted content that has a different shortcode but identical media.

### Easier setup

- A Docker Compose option bundling the backend and Ollama, as an alternative to the manual venv/Tesseract/Ollama install.
- One-shot setup scripts for Windows and Linux.
- macOS installation instructions.

### Quality and safety nets

- An automated test suite and CI covering selection logic, deduplication, and date handling.
- A `--dry-run` mode showing what a digest would contain without reserving or sending anything.
- Configurable auto-purge of old thumbnails and logs.

V1 already rotates its main nightly log and retains detailed stage logs for approximately 30 days. The planned retention controls would make cleanup configurable and extend it to thumbnails. Likewise, today's `--preview` sends nothing but reserves picks; the proposed `--dry-run` would leave selection state unchanged.

**Contributions and suggestions toward any of these are welcome.** Open an issue describing your use case before starting large changes, since priorities above are tentative.

## Publish this clean copy to GitHub

**Start from this freshly extracted release folder**, not your existing working installation. Do not copy your private configs, database, model cache, logs, or `.venv` into it before publishing. The archive has no Git history and no installed runtime data.

The supplied `.gitignore` excludes local JSON settings, `.env` variants, databases, logs, model weights, virtual environments, common backups, and generated archives. The `*.example.json` files remain trackable and contain placeholders only. Process environment variables are not automatically uploaded by Git; the risk is copying their values into files or terminal transcripts.

1. Create an **empty** GitHub repository. Do not initialize it with a README or license if you intend to use the commands below unchanged.
2. Open PowerShell in this clean project root and stage the source:

   ```powershell
   git init
   git add README.md DOCS.md requirements.txt .gitignore .gitattributes backend extension scheduling
   git diff --cached --name-only
   git diff --cached
   ```

3. Review the staged filenames and contents before committing. The two `*.example.json` files should contain placeholders. There should be **no** `discord.local.json`, `runtime.local.json`, `.env`, `backend/data/`, logs, database, model files, or virtual environment. Check the custom message for anything you do not want public. Press `q` to leave Git's pager.
4. Commit and push, replacing the URL with your own repository:

   ```powershell
   git commit -m "Initial Reels Digest V1"
   git branch -M main
   git remote add origin https://github.com/YOUR_USERNAME/YOUR_REPOSITORY.git
   git push -u origin main
   ```

Use your normal GitHub authentication flow; do not place an access token in the repository URL or a source file. Enable GitHub secret scanning/push protection where available as an additional safeguard.

`.gitignore` does not erase files already tracked in an old repository, and it cannot recognize every secret pasted into ordinary source files. This is why the fresh-folder workflow and staged review matter. If a real bot token was previously published, reset it in Discord and update your private local settings; deleting the latest file alone does not revoke the token.

No license has been selected for you. If you want others to reuse the project as open-source software, choose an appropriate license and add its `LICENSE` file before advertising it that way. Third-party dependencies and models retain their own licenses.

## Project map

| Path | Responsibility |
| --- | --- |
| `extension/manifest.json` | Chrome extension registration and permissions. |
| `extension/selectors.js` | Instagram DOM adapter; fix markup changes here. |
| `extension/content.js`, `frame.js` | Visible-reel capture and optional canvas frame. |
| `extension/background.js` | Durable outbox and local ingest batching. |
| `extension/autoscroll.js`, `popup.*` | User-started scroll session and controls. |
| `backend/app.py`, `db.py`, `schema.sql` | Local API, SQLite setup, and schema. |
| `backend/understanding.py`, `text_quality.py` | OCR, usable-text filtering, and embeddings. |
| `backend/prepare_model.py`, `inspect_rows.py` | Model download and manual row inspection. |
| `backend/clustering.py`, `cluster_labels.py` | HDBSCAN and local LLM labels. |
| `backend/sharing.py` | Selection, prepared payloads, and durable delivery state. |
| `backend/discord_*.py` | Discord setup, settings, and transport. |
| `backend/message_template.py` | Editable hardcoded outgoing text. |
| `backend/nightly.py`, `nightly_stages.py`, `process_lock.py` | Scheduled orchestration, logging, and concurrency limits. |
| `backend/runtime.py`, `*.example.json` | Runtime configuration and public templates. |
| `scheduling/` | Windows Task Scheduler, systemd, and cron integration. |

## Confirm your setup

1. **Capture:** With the backend running, view several new reels, wait for a batch, and refresh `/health`. Pass: `status` is `ok` and the unique reel count increases.
2. **Understanding:** Run `backend.inspect_rows` using the virtual-environment Python. Pass: available captions/OCR match remembered reels, and no-text rows are skipped rather than assigned invented content.
3. **Delivery:** With at least 3 eligible reels, preview today's digest and then send it using the commands above. Pass: the intended Discord destination receives the displayed picks with your custom message and links; no OCR paragraphs are substituted for that message.
4. **Duplicate protection:** Run the same ordinary send command again. Pass: previously delivered picks are not sent again. Do not add `--new-batch` for this check.
5. **Schedule:** Over the next 3–4 scheduled runs, inspect `nightly-last.json` and the destination. Pass: the requested capture date is correct, results are complete/already sent/no digest as appropriate, and days with enough eligible captures deliver without manual intervention.
