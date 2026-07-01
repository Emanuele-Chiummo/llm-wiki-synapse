.PHONY: help up down dev dev-down migrate logs test lint typecheck fmt er openapi clean

# Default target
help:
	@echo "Synapse Makefile targets:"
	@echo ""
	@echo "Local FULL STACK on your Mac (one command — frontend + backend + postgres + qdrant):"
	@echo "  make dev        - Start the full dev stack (needs Ollama at localhost:11434)"
	@echo "                    → UI http://localhost:5173 · API http://localhost:8000"
	@echo "  make dev-down   - Stop the full dev stack"
	@echo ""
	@echo "Production compose (TrueNAS: backend + postgres only; Qdrant/Ollama/SearXNG external):"
	@echo "  make up         - Start backend + postgres (no frontend container)"
	@echo "  make down       - Stop all services"
	@echo "  make migrate    - Run Alembic migrations (alembic upgrade head)"
	@echo "  make logs       - Tail logs from all services"
	@echo ""
	@echo "Testing & Linting (service-free, use local venv):"
	@echo "  make test       - Run pytest (backend unit tests; no live services)"
	@echo "  make lint       - Run ruff + black --check"
	@echo "  make typecheck  - Run mypy (strict mode)"
	@echo "  make fmt        - Auto-format code (black + ruff --fix)"
	@echo ""
	@echo "Documentation (service-free):"
	@echo "  make er         - Generate docs/er/schema.mmd from SQLAlchemy models"
	@echo "  make openapi    - Generate docs/api/openapi.json from FastAPI app"
	@echo ""
	@echo "Cleanup:"
	@echo "  make clean      - Remove generated files and cache"
	@echo ""

# ──────────────────────────────────────────────────────────────────────────
# Docker Compose targets (require docker, docker-compose)
# ──────────────────────────────────────────────────────────────────────────

up:
	docker compose up -d

down:
	docker compose down

# Full local stack (prod compose + dev override): frontend + backend(reload) + postgres + qdrant.
# Only external dependency is Ollama at localhost:11434 (Ollama.app). This is the
# closest thing to "just launch the app" — one command brings up the whole client/server stack.
dev:
	docker compose -f docker-compose.yml -f docker-compose.dev.yml up

dev-down:
	docker compose -f docker-compose.yml -f docker-compose.dev.yml down

migrate:
	docker compose exec synapse-backend alembic upgrade head

logs:
	docker compose logs -f

# ──────────────────────────────────────────────────────────────────────────
# Testing & Linting (service-free — use local venv)
# ──────────────────────────────────────────────────────────────────────────

test:
	cd backend && python -m pytest -v

lint:
	cd backend && ruff check app tests

typecheck:
	cd backend && mypy app

fmt:
	cd backend && black app tests && ruff check --fix app tests

# ──────────────────────────────────────────────────────────────────────────
# Documentation generation (service-free)
# ──────────────────────────────────────────────────────────────────────────

er:
	cd backend && python scripts/generate_er.py

openapi:
	cd backend && python scripts/generate_openapi.py

# ──────────────────────────────────────────────────────────────────────────
# Cleanup
# ──────────────────────────────────────────────────────────────────────────

clean:
	cd backend && rm -rf .mypy_cache .pytest_cache .ruff_cache htmlcov .coverage __pycache__
	find backend -type d -name __pycache__ -exec rm -rf {} +
	find backend -type f -name "*.py[cod]" -delete
	rm -f docs/er/schema.mmd docs/api/openapi.json
	rm -rf docs/screens/*.png
