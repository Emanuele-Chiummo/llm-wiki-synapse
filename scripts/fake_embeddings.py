#!/usr/bin/env python3
"""
Fake embeddings server for CI E2E tests.

Minimal HTTP server (stdlib-only) that mimics bge-m3/Ollama embeddings endpoint.
Returns fixed-dimension vectors (no actual embedding computation).

Usage:
    python scripts/fake_embeddings.py --port 11435 --dim 1024 &
    # Server listens on http://localhost:11435/api/embeddings
    # Client: POST {"model": "bge-m3", "prompt": "test"}
    # Response: {"embedding": [0.1, 0.2, ..., 0.1]}  (1024 floats)

Environment:
    FAKE_EMBEDDINGS_PORT — port to listen on (default 11435)
    FAKE_EMBEDDINGS_DIM — embedding dimension (default 1024)
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


class FakeEmbeddingsHandler(BaseHTTPRequestHandler):
    """HTTP request handler for fake embeddings endpoint."""

    # Class variable: dimension (set by main() before starting server)
    embedding_dim: int = 1024

    def do_POST(self) -> None:
        """Handle POST /api/embeddings request."""
        # Parse request
        content_length = int(self.headers.get("Content-Length", 0))
        if not content_length:
            self.send_error(400, "Missing Content-Length")
            return

        try:
            body = self.rfile.read(content_length)
            request_data: Any = json.loads(body)
        except json.JSONDecodeError:
            self.send_error(400, "Invalid JSON")
            return

        # Validate request
        if not isinstance(request_data, dict):
            self.send_error(400, "Request body must be a JSON object")
            return

        # Extract model and text (ollama format)
        model = request_data.get("model", "bge-m3")
        prompt = request_data.get("prompt")  # ollama format
        input_text = request_data.get("input")  # openai format

        if not prompt and not input_text:
            self.send_error(400, "Missing 'prompt' or 'input' field")
            return

        # Generate fake embedding: fixed dimension vector with varying seed
        # (deterministic so tests are reproducible)
        text_to_embed = prompt or input_text or ""
        seed_hash = sum(ord(c) for c in text_to_embed) % 100
        embedding = [
            (i + seed_hash) / (self.embedding_dim + 100) for i in range(self.embedding_dim)
        ]

        # Build response (ollama format)
        response = {"embedding": embedding}

        # Send response
        response_json = json.dumps(response).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response_json)))
        self.end_headers()
        self.wfile.write(response_json)

    def do_GET(self) -> None:
        """Handle health check (GET /)."""
        if self.path == "/" or self.path == "/health":
            response = json.dumps({"status": "ok", "dim": self.embedding_dim}).encode(
                "utf-8"
            )
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)
        else:
            self.send_error(404, "Not Found")

    def log_message(self, format: str, *args: Any) -> None:
        """Override to use logging instead of stderr."""
        logger.info(format, *args)


def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Fake embeddings server for CI E2E tests."
    )
    parser.add_argument(
        "--port", type=int, default=11435, help="Port to listen on (default 11435)"
    )
    parser.add_argument(
        "--dim",
        type=int,
        default=1024,
        help="Embedding dimension (default 1024)",
    )
    parser.add_argument(
        "--host",
        type=str,
        default="127.0.0.1",
        help="Host to listen on (default 127.0.0.1)",
    )
    args = parser.parse_args()

    # Set dimension on handler class
    FakeEmbeddingsHandler.embedding_dim = args.dim

    # Create and start server
    server_address = (args.host, args.port)
    server = HTTPServer(server_address, FakeEmbeddingsHandler)

    logger.info(
        "Fake embeddings server listening on http://%s:%d/api/embeddings (dim=%d)",
        args.host,
        args.port,
        args.dim,
    )
    logger.info("Press Ctrl+C to stop.")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down.")
        server.server_close()
        sys.exit(0)


if __name__ == "__main__":
    main()
