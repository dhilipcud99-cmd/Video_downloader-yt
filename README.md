# Clipbay

Paste a video URL, preview it, mark an in/out point (and an optional crop
box), pick a format and quality, and download the exact clip. Built with
FastAPI, `yt-dlp`, and FFmpeg on the backend, and a dependency-free HTML/CSS/JS
frontend served by the same app.

> **Use responsibly.** Only download videos you own or have explicit
> permission to download. Clipbay does not bypass DRM, paywalls, or private
> content, and the UI requires the user to confirm they have the right to
> download before a job can run.

## How it works

```
frontend (static/)  →  FastAPI (backend/app)  →  yt-dlp (download)  →  ffmpeg (trim/crop/encode)  →  file download
```

1. **`POST /api/info`** — probes the URL with `yt-dlp` (no download) and
   returns title, duration, thumbnail, and the resolutions actually
   available for that video.
2. **`POST /api/process`** — validates the trim window and crop box, then
   starts a background thread that:
   - downloads the best-matching source stream with `yt-dlp`, reporting
     byte-progress through a hook,
   - runs `ffmpeg -ss … -t … [-vf crop=...] -c:v … -c:a …` to cut, crop, and
     transcode into the requested container, reporting time-progress by
     parsing `ffmpeg -progress pipe:1`.
   Progress from both stages is merged into a single 0–100 percentage.
3. **`GET /api/status/{job_id}`** — the frontend polls this every ~1.2s.
4. **`GET /api/download/{job_id}`** — streams the finished file once the job
   is `completed`.

Jobs live in memory (see `backend/app/jobs.py`) and files live under
`/tmp/clipbay/{job_id}/`. That's enough for a single-process deployment,
which is what Render/Railway free & hobby tiers give you. See **Scaling
beyond one worker** below if you outgrow it.

## Project layout

```
backend/
  app/
    main.py          FastAPI app, routes, request/response models
    jobs.py           In-memory job store (thread-safe)
    video_info.py      yt-dlp probing → frontend-friendly metadata
    pipeline.py         download + ffmpeg pipeline with progress tracking
  static/               Frontend (index.html, style.css, app.js) — no build step
  requirements.txt
Procfile                 process command for Railway/Render
render.yaml               Render Blueprint
railway.json               Railway config
.gitignore
```

## Running locally

Requirements: Python 3.11+, `ffmpeg` on PATH.

```bash
# 1. install ffmpeg (Debian/Ubuntu example)
sudo apt-get update && sudo apt-get install -y ffmpeg

# 2. install python deps
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 3. run
uvicorn app.main:app --reload --port 8000
```

Open `http://localhost:8000`.

## REST API reference

### `POST /api/info`
```json
{ "url": "https://example.com/watch?v=..." }
```
→
```json
{
  "title": "…",
  "duration": 812.0,
  "thumbnail": "https://…",
  "uploader": "…",
  "extractor": "Youtube",
  "width": 1920,
  "height": 1080,
  "resolutions": [{ "label": "1080p", "value": "1080", "fps": 30, "ext": "mp4" }],
  "has_audio_only": true
}
```

### `POST /api/process`
```json
{
  "url": "https://example.com/watch?v=...",
  "start_time": 12.5,
  "end_time": 42.0,
  "crop": { "x": 100, "y": 40, "width": 800, "height": 450 },
  "container": "mp4",
  "quality": "1080",
  "audio_only": false,
  "confirm_permission": true
}
```
→ `{ "job_id": "a1b2c3d4e5f6" }`

`crop` is optional. `container` is one of `mp4 | webm | mkv | mp3 | m4a`.
`quality` is `best`, a height like `"720"`, or `"audio"`.

### `GET /api/status/{job_id}`
```json
{
  "job_id": "a1b2c3d4e5f6",
  "status": "processing",
  "progress": 68.2,
  "message": "Encoding your clip…",
  "error": null,
  "output_name": null,
  "output_size_bytes": null
}
```
`status` is one of `queued | fetching_info | downloading | processing |
completed | failed`.

### `GET /api/download/{job_id}`
Streams the finished file (415/409 if not ready, 410 if it already expired).

### `DELETE /api/jobs/{job_id}`
Cancels bookkeeping and deletes the job's working directory.

## Error handling

- Bad/unreachable URLs return `422` from `/api/info` with a plain-language
  message (private video, unsupported site, typo, etc).
- `/api/process` rejects missing permission confirmation (`400`), invalid
  trim windows (`422`), and clips over 3 hours (`422`).
- Failures during download/encode set the job to `failed` with `error`
  populated instead of crashing the server; the frontend surfaces that
  message directly.
- The frontend never assumes success: every fetch checks `res.ok` and shows
  the server's `detail` message.

## Deployment

Both `render.yaml` and `railway.json` run the same single web process:

```
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

FFmpeg needs to be present in the container. Both configs install it via
apt during the build step (`render.yaml`'s `buildCommand`, and
`railway.json`'s Nixpacks `aptPkgs`).

### Render

1. Push this repo to GitHub.
2. In Render: **New → Blueprint**, point it at the repo — `render.yaml` is
   picked up automatically and provisions the web service.
3. Or manually: **New → Web Service**, root directory `backend`,
   build command `apt-get update && apt-get install -y ffmpeg && pip install -r requirements.txt`,
   start command `uvicorn app.main:app --host 0.0.0.0 --port $PORT`.

### Railway

1. Push this repo to GitHub.
2. In Railway: **New Project → Deploy from GitHub repo**. `railway.json` +
   `nixpacks.toml` tell Nixpacks to install `ffmpeg` and run the same
   start command.
3. Set the service root directory to `backend` if Railway doesn't infer it.

### GitHub integration

Both platforms redeploy automatically on push once connected to the repo —
no extra webhook config needed. A minimal CI check is included at
`.github/workflows/ci.yml` (installs deps, byte-compiles the app, and runs
`uvicorn --help`. as a smoke test) so broken builds are caught before merge.

## Scaling beyond one worker

The job store (`backend/app/jobs.py`) and file storage are both in-process.
If you deploy multiple workers/replicas, swap:
- `JobStore` for a Redis-backed version (same `create/get/update/delete`
  interface),
- `/tmp/clipbay` for shared object storage (S3/R2) with `/api/download`
  issuing a redirect to a signed URL instead of `FileResponse`.

## Limitations & notes

- Clipbay relies on `yt-dlp`'s extractor support — whatever site it can
  read, this app can read. Sites requiring login/cookies aren't wired up.
- Long source videos are downloaded in full before trimming (simplest,
  most reliable approach). For very long videos this costs bandwidth/time;
  an optimization would be to pass `-ss` to `yt-dlp`'s HLS/DASH downloader
  directly where supported.
- Files are deleted from disk 1:1 with job cleanup; there's no scheduled
  janitor process. Add a cron hitting `DELETE /api/jobs/{id}` (or a simple
  age-based sweep of `/tmp/clipbay`) for long-running deployments.
