# Whisper AV Transcription Service

Host-side microservice for audio/video transcription (R8-3, F12). The Synapse backend
container never imports Whisper directly — it calls this service over HTTP, keeping heavy
GPU/ML deps out of the backend image (same pattern as R8-1 Marker service, ADR-0051).

## Quick Start

### 1. Create the venv (host only, not inside Docker)

```bash
cd tools/whisper-service
python3.11 -m venv .venv
./.venv/bin/pip install -r requirements.txt
```

### 2. Pre-download the model (optional, avoids first-request delay)

```bash
# faster-whisper (CPU/CUDA path, default on non-macOS):
./.venv/bin/python -c "from faster_whisper import WhisperModel; WhisperModel('large-v3')"

# mlx-whisper (Apple Silicon / MPS path, macOS only):
./.venv/bin/python -c "import mlx_whisper; mlx_whisper.transcribe('/dev/null', path_or_hf_repo='mlx-community/whisper-large-v3')" 2>/dev/null || true
```

### 3. Start the service

```bash
./.venv/bin/python service.py --port 8666
# Or with a smaller/faster model for development:
./.venv/bin/python service.py --port 8666 --model whisper-small
```

The service binds to `0.0.0.0:8666` by default so the Synapse backend container can
reach it via `http://host.docker.internal:8666` (Docker on Mac/Windows) or the host IP
on Linux.

## Enable in Synapse

Set these environment variables in your `.env` or `docker-compose.override.yml`:

```env
AV_TRANSCRIPTION_ENABLED=true
WHISPER_SERVICE_URL=http://host.docker.internal:8666
WHISPER_TIMEOUT_SECONDS=300
AV_MAX_FILES_PER_RUN=3
```

When `AV_TRANSCRIPTION_ENABLED=false` (the default), the backend never contacts this
service — zero behaviour change.

## Engine Selection

The service auto-detects the best available Whisper engine at startup (in order):

| Priority | Engine | Best for | Notes |
|---|---|---|---|
| 1 | **mlx-whisper** | Apple Silicon (M1/M2/M3+) | Fastest on macOS; uses Metal GPU |
| 2 | **faster-whisper** | CPU / CUDA | ~4x faster than openai-whisper on CPU |
| 3 | **openai-whisper** | CPU-only fallback | Original; slowest but most compatible |

If none is installed, the service starts but returns HTTP 503 on every transcription
request (with a clear error message). The Synapse backend treats 503 as a failure and
falls back to the text placeholder.

## API

```
POST /transcribe
Content-Type: multipart/form-data
  file: <AV bytes>  (field name must be "file")

200 OK
Content-Type: application/json
{"text": "<transcript>", "language": "<ISO code>", "duration_seconds": <float>}

Non-200 responses:
  413  Upload exceeds size limit (default 200 MB)
  429  Transcription already in progress (one-at-a-time gate)
  503  No Whisper engine available
  500  Transcription failed
```

The backend considers any non-200 or network error a fallback trigger — it logs a WARNING
and uses the existing AV placeholder text.

## Apple Silicon (MPS) Setup

```bash
# Requires macOS 13+ (Ventura) with Metal 3
pip install "mlx-whisper"
# Model is downloaded on first use (~3 GB from Hugging Face)
```

Models are cached at `~/.cache/huggingface/`. To use a smaller model (faster, less
accurate), pass `--model whisper-small` to the service.

## CPU / CUDA Setup

```bash
pip install faster-whisper
# For CUDA acceleration also install:
pip install nvidia-cublas-cu12 nvidia-cudnn-cu12
```

## Testing

Real transcription tests require a GPU or MPS host and are skipped in CI:

```python
import pytest
WHISPER_AVAILABLE = ...  # set based on engine detection
@pytest.mark.skipif(not WHISPER_AVAILABLE, reason="Whisper engine not available")
def test_real_transcription():
    ...
```

The health endpoint is always testable without a GPU:

```bash
./.venv/bin/python -m pytest tests/test_whisper_service.py -v
```

## Configuration Options

| Flag | Default | Description |
|---|---|---|
| `--port` | 8666 | TCP port |
| `--host` | 0.0.0.0 | Bind address |
| `--max-upload-mb` | 200 | Max AV file size in MB |
| `--model` | whisper-large-v3 | Whisper model name |

## MV Permissions

| Env var (Synapse) | Default | Description |
|---|---|---|
| `AV_TRANSCRIPTION_ENABLED` | `false` | Master gate (off by default) |
| `WHISPER_SERVICE_URL` | `http://host.docker.internal:8666` | Service base URL |
| `WHISPER_TIMEOUT_SECONDS` | `300.0` | Per-call HTTP timeout |
| `AV_MAX_FILES_PER_RUN` | `3` | Files per ingest run cap |
