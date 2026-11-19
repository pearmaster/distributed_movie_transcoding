# Distributed Video Transcoding System

A scalable, distributed video transcoding system designed to process MPEG-2 Transport Stream (`.ts`) video files in parallel across multiple worker nodes, encoding them into H.264 Matroska (`.mkv`) files using HandBrake, and merging them back into seamless output files.

---

## Architecture Overview

```
                      +-----------------------------+
                      |       Input Directory       |
                      |          (/input)           |
                      +--------------+--------------+
                                     |
                                     | Discovers *.ts files
                                     v
+---------------------------------------------------------------------------------+
|                               Coordinator Service                               |
|                                                                                 |
|  1. SQLite3 Database:                                                           |
|     - Tracks files, segments, workers, speeds, and transcoding reports.         |
|  2. Lossless Segmenter:                                                         |
|     - Splits MPEG-2 .ts files into 5-minute (300s) chunks via FFmpeg stream copy|
|  3. Smart Segment Scheduler:                                                    |
|     - Tracks worker speed stats (video_seconds / transcode_seconds).            |
|     - Assigns EARLIER segments to FASTER workers.                               |
|     - Assigns LATER segments to SLOWER workers.                                 |
|  4. Lossless Merger:                                                            |
|     - Joins completed .mkv segments via mkvmerge / ffmpeg concat.               |
|     - Writes final movie file to /output/<basename>.mkv                         |
+------------------------------------+--------------------------------------------+
                                     |
                    REST API / Signals / Transcode Tasks
                                     |
         +---------------------------+---------------------------+
         |                                                       |
         v                                                       v
+-----------------------------+                         +-----------------------------+
|      Worker 1 (Fast)        |                         |       Worker 2 (Slow)       |
|                             |                         |                             |
| - Registers with identifier |                         | - Registers with identifier |
| - Waits for work signal     |                         | - Waits for work signal     |
| - Downloads earlier segment |                         | - Downloads later segment   |
| - Encodes H.264 via         |                         | - Encodes H.264 via         |
|   HandBrakeCLI              |                         |   HandBrakeCLI              |
| - Sends segment + report    |                         | - Sends segment + report    |
|   (duration, transcode_sec) |                         |   (duration, transcode_sec) |
+-----------------------------+                         +-----------------------------+
```

---

## Key Features

1. **Dual Docker Container Architecture**:
   - `coordinator`: Monitors `/input`, manages SQLite state, splits incoming MPEG-2 TS files, assigns work, receives transcoded chunks, and joins them into `/output`.
   - `worker`: Registers a unique worker ID, waits for work signals, downloads assigned segments, transcodes to H.264 MKV using `HandBrakeCLI`, measures video duration & encoding time, and submits reports back.

2. **Smart Speed-Based Segment Scheduling**:
   - The coordinator tracks each worker's cumulative speed ratio ($\text{speed} = \frac{\text{video\_duration\_seconds}}{\text{transcode\_time\_seconds}}$).
   - Higher speed ratios represent faster workers.
   - When pending segments are assigned:
     - **Faster workers** are assigned **earlier segments** (lower segment indices, e.g. 0, 1, 2...).
     - **Slower workers** are assigned **later segments** (higher segment indices, e.g. $N-2, N-1$).
     - This ensures sequential playback readiness and prevents slow workers from gating the front of the queue.

3. **Lossless MPEG-2 TS Splitting**:
   - Segments are sliced every 5 minutes (300 seconds) using `ffmpeg -c copy`, completing in seconds with zero transcoding loss before workers begin processing.

4. **HandBrake H.264 Encoding**:
   - Each worker runs `HandBrakeCLI` with `-e x264` to convert MPEG-2 video to high-efficiency H.264 video with AAC audio in an MKV container.

5. **Accurate Transcoding Reports**:
   - Workers report:
     - `duration_seconds`: exact video duration of the segment.
     - `transcode_time_seconds`: wall-clock time required to convert the video.
   - Coordinator updates the SQLite database and calculates running averages for each worker.

6. **Automatic Lossless Joining**:
   - Once all segments for a file are reported complete, the coordinator concatenates all `.mkv` segments into `/output/<original_basename>.mkv` and updates the file status to `completed`.

7. **Multi-Architecture Support (x86_64 & aarch64)**:
   - Automated GitHub Actions workflow builds multi-platform Docker container images for both `linux/amd64` and `linux/arm64` (Apple Silicon / Raspberry Pi / AWS Graviton / Ampere).

---

## Directory Structure

```
distributed_movie_transcoding/
├── coordinator/
│   ├── Dockerfile
│   ├── requirements.txt
│   └── coordinator_app/
│       ├── __init__.py
│       ├── config.py           # Configuration and environment variables
│       ├── database.py         # SQLite3 database schema, models, and queries
│       ├── splitter.py         # 5-minute MPEG-2 TS segmenter using FFmpeg
│       ├── merger.py           # Matroska joiner using mkvmerge / ffmpeg
│       ├── scheduler.py        # Speed-ranked segment scheduler
│       ├── watcher.py          # Input directory monitor thread
│       └── main.py             # FastAPI REST and signal server
├── worker/
│   ├── Dockerfile
│   ├── requirements.txt
│   └── worker_app/
│       ├── __init__.py
│       ├── config.py           # Worker environment configuration
│       ├── transcoder.py       # HandBrakeCLI execution and timing wrapper
│       └── worker.py           # Worker registration, loop, and reporting
├── docker-compose.yml          # Multi-container orchestration (1 coordinator + 3 workers)
├── input/                      # Monitored directory for input .ts files
├── output/                     # Output directory for finished .mkv files
├── data/                       # Coordinator SQLite database and working segment storage
├── scripts/
│   ├── generate_sample_video.py # Synthetic MPEG-2 TS generator for testing
│   └── test_e2e.py             # Automated end-to-end test script
├── tests/
│   └── test_components.py      # Unit tests for database and scheduler
└── README.md
```

---

## SQLite Database Schema

The coordinator maintains SQLite in WAL mode (`transcode.db`):

- **`files`**: Tracks overall movie files, status (`discovered` -> `splitting` -> `transcoding` -> `joining` -> `completed`), segment counts, and final output paths.
- **`segments`**: Tracks individual segments, index, duration, status (`pending`, `assigned`, `completed`), assigned worker ID, transcode time, and output path.
- **`workers`**: Tracks workers by unique identifier, last heartbeat, total jobs completed, total video seconds processed, total transcode seconds taken, and cumulative `avg_speed_ratio`.
- **`transcode_reports`**: Historical log of every completed segment transcode with worker ID, video duration, transcode time, and speed ratio.

---

## Quick Start

### 1. Build and Start the Cluster

```bash
docker compose up --build -d
```

This starts:
- `transcode-coordinator` on port `8000`
- `transcode-worker-fast`
- `transcode-worker-slow`
- `transcode-worker-standard`

Check container statuses:
```bash
docker compose ps
```

### 2. Drop a Video File into `input/`

Copy any MPEG-2 Transport Stream file (`*.ts`) into the `./input` directory:
```bash
cp /path/to/my_movie.ts ./input/
```

Or generate a synthetic test video using the included script:
```bash
python3 scripts/generate_sample_video.py -o input/test_movie.ts -d 900
```
*(900 seconds = 15 minutes, will be sliced into three 5-minute segments)*.

### 3. Monitor Progress

- Check coordinator logs:
  ```bash
  docker compose logs -f coordinator
  ```
- Check worker logs:
  ```bash
  docker compose logs -f worker-fast
  ```
- Query coordinator API:
  ```bash
  curl http://localhost:8000/api/status | jq .
  curl http://localhost:8000/api/workers | jq .
  curl http://localhost:8000/api/files | jq .
  curl http://localhost:8000/api/reports | jq .
  ```

### 4. Collect the Converted File

When processing completes, the converted `.mkv` file appears in `./output/`:
```bash
ls -lh output/
```

---

## Coordinator API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/health` | Healthcheck and storage paths |
| `GET` | `/api/status` | High-level overview: active files, queue depth, workers, reports |
| `GET` | `/api/files` | List all discovered files and their transcoding statuses |
| `GET` | `/api/files/{id}` | Detailed file status and individual segment progress |
| `GET` | `/api/workers` | List all registered workers and their speed stats |
| `POST` | `/api/workers/register` | Worker registration with worker identifier |
| `POST` | `/api/workers/heartbeat` | Worker heartbeat ping |
| `GET` | `/api/tasks/next` | Fetches next segment assignment based on worker speed ranking |
| `GET` | `/api/segments/{file_id}/{segment_id}/download` | Download `.ts` segment for processing |
| `POST` | `/api/segments/{file_id}/{segment_id}/upload` | Upload transcoded `.mkv` segment with report data |
| `POST` | `/api/signal` | Explicit signal to notify and wake idle workers |
| `GET` | `/api/reports` | List transcoding reports |

---

## Scaling Workers

To scale up worker instances dynamically:

```bash
docker compose up -d --scale worker-standard=5
```

Each worker will automatically register with its unique container hostname, report heartbeats, receive segments assigned according to its benchmarked speed, and transcode in parallel.
