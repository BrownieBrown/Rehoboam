.DEFAULT_GOAL := help
.PHONY: help install install-web dev web-build web-start web-check \
	status auto-dry lint format typecheck security check test test-cov clean

WEB  := web
PORT ?= 3000

help:
	@echo "Rehoboam - KICKBASE bot + dashboard"
	@echo ""
	@echo "Dashboard (web/, Next.js):"
	@echo "  dev           Start the dashboard on http://localhost:$(PORT) (PORT=3001 to change)"
	@echo "  web-build     Production build + secret-bundle guard"
	@echo "  web-start     Serve the production build"
	@echo "  web-check     Typecheck, lint and unit-test the dashboard"
	@echo ""
	@echo "Setup:"
	@echo "  install       uv sync --extra dev"
	@echo "  install-web   npm install in web/"
	@echo ""
	@echo "Bot:"
	@echo "  status        Read-only diagnostic (what auto would do)"
	@echo "  auto-dry      Simulate one trading session"
	@echo ""
	@echo "Code quality:"
	@echo "  lint | format | typecheck | security | check"
	@echo "  test | test-cov"
	@echo ""
	@echo "  clean         Remove build artifacts (keeps node_modules and .venv)"

# ============================================================================
# Dashboard
# ============================================================================

# The dashboard reads the REAL store through DATABASE_URL, and an empty
# ALLOWED_EMAILS locks everyone out — so refuse to start without .env.local
# rather than boot into a login page that can never succeed.
$(WEB)/.env.local:
	@echo "Missing $(WEB)/.env.local — copy $(WEB)/.env.example and fill in the four values."
	@exit 1

$(WEB)/node_modules: $(WEB)/package-lock.json
	cd $(WEB) && npm install
	@touch $@

install-web: $(WEB)/node_modules

dev: $(WEB)/.env.local $(WEB)/node_modules
	cd $(WEB) && npm run dev -- --port $(PORT)

web-build: $(WEB)/.env.local $(WEB)/node_modules
	cd $(WEB) && npm run build

web-start: web-build
	cd $(WEB) && npm run start -- --port $(PORT)

web-check: $(WEB)/node_modules
	cd $(WEB) && npm run typecheck && npm run lint && npm test

# ============================================================================
# Bot
# ============================================================================

install:
	uv sync --extra dev

status:
	uv run rehoboam status

auto-dry:
	uv run rehoboam auto --dry-run

# ============================================================================
# Code quality
# ============================================================================

lint:
	uv run ruff check rehoboam/ --fix

format:
	uv run black rehoboam/
	uv run ruff check rehoboam/ --fix

typecheck:
	uv run mypy rehoboam/ --ignore-missing-imports

security:
	uv run bandit -r rehoboam/ -c pyproject.toml

check: lint typecheck security

test:
	uv run pytest

test-cov:
	uv run pytest --cov=rehoboam --cov-report=html --cov-report=term

# ============================================================================
# Utilities
# ============================================================================

clean:
	rm -rf build/ dist/ *.egg-info/ .pytest_cache/ .mypy_cache/ .ruff_cache/ htmlcov/
	rm -rf $(WEB)/.next
	find . -type d -name __pycache__ -not -path "./.venv/*" -exec rm -rf {} + 2>/dev/null || true
