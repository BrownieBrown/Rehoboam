.PHONY: help install install-dev install-web dev api web build lint format typecheck test clean

# Default target
help:
	@echo "Rehoboam - KICKBASE Trading Bot"
	@echo ""
	@echo "Usage: make [target]"
	@echo ""
	@echo "Setup:"
	@echo "  install       Install Python dependencies"
	@echo "  install-dev   Install Python dev dependencies"
	@echo "  install-web   Install frontend dependencies"
	@echo "  install-all   Install everything"
	@echo ""
	@echo "Development:"
	@echo "  dev           Run both API and frontend (requires tmux)"
	@echo "  api           Run FastAPI backend only"
	@echo "  web           Run React frontend only"
	@echo "  cli           Run the CLI (rehoboam --help)"
	@echo ""
	@echo "Code Quality:"
	@echo "  lint          Run ruff linter"
	@echo "  format        Format code with black and ruff"
	@echo "  typecheck     Run mypy type checker"
	@echo "  security      Run bandit security scan"
	@echo "  check         Run all checks (lint, typecheck, security)"
	@echo ""
	@echo "Testing:"
	@echo "  test          Run all tests"
	@echo "  test-cov      Run tests with coverage"
	@echo ""
	@echo "Build:"
	@echo "  build         Build frontend for production"
	@echo "  build-api     Build Python package"
	@echo ""
	@echo "Utilities:"
	@echo "  clean         Remove build artifacts"
	@echo "  analyze       Run rehoboam analyze"
	@echo "  trade-dry     Run dry-run trading"

# ============================================================================
# Setup
# ============================================================================

install:
	pip install -e .

install-dev:
	pip install -e ".[dev]"

install-web:
	cd web && npm install

install-all: install-dev install-web
	@echo "All dependencies installed"

# ============================================================================
# Development
# ============================================================================

api:
	uvicorn api.main:app --reload --host 0.0.0.0 --port 8000

web:
	cd web && npm run dev

# Run both API and frontend (requires tmux)
dev:
	@if command -v tmux >/dev/null 2>&1; then \
		tmux new-session -d -s rehoboam 'make api' \; \
			split-window -h 'make web' \; \
			attach; \
	else \
		echo "tmux not installed. Run 'make api' and 'make web' in separate terminals."; \
	fi

cli:
	rehoboam --help

# ============================================================================
# Code Quality
# ============================================================================

lint:
	ruff check rehoboam/ api/ --fix

format:
	black rehoboam/ api/
	ruff check rehoboam/ api/ --fix

typecheck:
	mypy rehoboam/ api/ --ignore-missing-imports

security:
	bandit -r rehoboam/ api/ -c pyproject.toml

check: lint typecheck security
	@echo "All checks passed"

# ============================================================================
# Testing
# ============================================================================

test:
	pytest

test-cov:
	pytest --cov=rehoboam --cov-report=html --cov-report=term

# ============================================================================
# Build
# ============================================================================

build:
	cd web && npm run build

build-api:
	python -m build

# ============================================================================
# Utilities
# ============================================================================

clean:
	rm -rf build/
	rm -rf dist/
	rm -rf *.egg-info/
	rm -rf .pytest_cache/
	rm -rf .mypy_cache/
	rm -rf .ruff_cache/
	rm -rf htmlcov/
	rm -rf web/dist/
	rm -rf web/node_modules/
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

analyze:
	rehoboam analyze

trade-dry:
	rehoboam trade --max 5

# ============================================================================
# Deployment helpers
# ============================================================================

deploy-check:
	@echo "Pre-deployment checklist:"
	@echo "1. Backend (Railway):"
	@echo "   - Set KICKBASE_EMAIL"
	@echo "   - Set KICKBASE_PASSWORD"
	@echo "   - Set JWT_SECRET (generate with: openssl rand -hex 32)"
	@echo "   - Set CORS_ORIGINS to your Vercel URL"
	@echo ""
	@echo "2. Frontend (Vercel):"
	@echo "   - Set VITE_API_URL to your Railway URL"
	@echo ""
	@echo "Generate a JWT secret:"
	@openssl rand -hex 32
