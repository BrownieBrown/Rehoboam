# Development Guide

This guide covers setting up the development environment, running tests, and contributing to Rehoboam.

## Quick Setup

```bash
# 1. Clone the repository
git clone https://github.com/yourusername/rehoboam.git
cd rehoboam

# 2. Create virtual environment
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# 3. Install dependencies (including dev tools)
pip install -e ".[dev]"

# 4. Set up pre-commit hooks
pre-commit install
pre-commit install --hook-type pre-push

# 5. Copy environment file and configure
cp .env.example .env
# Edit .env with your KICKBASE credentials
```

## Pre-commit Hooks

Pre-commit hooks automatically run code quality checks before each commit.

### What Gets Checked

**On Every Commit:**

- ✅ Code formatting (Black)
- ✅ Linting (Ruff)
- ✅ Security checks (Bandit)
- ✅ Trailing whitespace
- ✅ File endings
- ✅ Syntax validation (YAML, JSON, TOML)
- ✅ Large file detection
- ✅ Merge conflict detection
- ✅ Private key detection

### Manual Run

Run hooks on all files:

```bash
pre-commit run --all-files
```

Run specific hook:

```bash
pre-commit run black --all-files
pre-commit run ruff --all-files
```

### Skip Hooks (Emergency Only)

If you absolutely need to skip hooks:

```bash
git commit --no-verify -m "Your message"
```

**⚠️ Warning:** Only use `--no-verify` in emergencies. CI will still run checks!

## Code Quality Tools

### Black (Code Formatting)

Format all Python files:

```bash
black rehoboam/
```

Check without modifying:

```bash
black --check rehoboam/
```

### Ruff (Linting)

Lint and auto-fix:

```bash
ruff check rehoboam/ --fix
```

Lint only:

```bash
ruff check rehoboam/
```

### Bandit (Security)

Security scan:

```bash
bandit -r rehoboam/ -c pyproject.toml
```

### Mypy (Type Checking)

Type check:

```bash
mypy rehoboam/ --ignore-missing-imports
```

## Testing

### Run All Tests

```bash
pytest
```

### Run Specific Tests

```bash
# Run specific file
pytest tests/test_analyzer.py

# Run specific test
pytest tests/test_analyzer.py::test_value_calculation

# Run with verbose output
pytest -v

# Run with coverage
pytest --cov=rehoboam --cov-report=html
```

### Test Markers

```bash
# Skip slow tests
pytest -m "not slow"

# Run only integration tests
pytest -m integration
```

## GitHub Actions CI/CD

Our CI pipeline runs automatically on:

- Push to `main` or `develop` branches
- Pull requests to `main` or `develop`
- Manual trigger (workflow_dispatch)

### CI Jobs

1. **Code Quality** (`lint`)

   - Black formatting check
   - Ruff linting
   - Bandit security scan

1. **Tests** (`test`)

   - Runs on Python 3.10, 3.11, 3.12
   - Full test suite with pytest

1. **Type Checking** (`type-check`)

   - Mypy type validation
   - Currently non-blocking

1. **Security Scan** (`security`)

   - Dependency vulnerability check with Safety
   - Currently non-blocking

1. **Build** (`build`)

   - Package build verification
   - Uploads build artifacts

### View CI Results

- Go to your repository on GitHub
- Click "Actions" tab
- Select a workflow run to see details

### Local CI Simulation

Run the same checks locally:

```bash
# Code quality
black --check rehoboam/
ruff check rehoboam/
bandit -r rehoboam/ -c pyproject.toml

# Tests
pytest

# Type checking
mypy rehoboam/ --ignore-missing-imports

# Security
pip freeze | safety check --stdin

# Build
python -m build
```

## Configuration Files

### `.pre-commit-config.yaml`

Defines all pre-commit hooks and their configuration.

### `.github/workflows/ci.yml`

GitHub Actions CI/CD pipeline definition.

### `pyproject.toml`

Project configuration including:

- Dependencies
- Tool configurations (black, ruff, pytest, bandit, mypy)
- Build settings

## Best Practices

### Commit Messages

Use conventional commits format:

```
feat: Add new trading strategy
fix: Correct peak detection calculation
docs: Update API documentation
test: Add tests for profit trader
refactor: Simplify value calculation logic
chore: Update dependencies
```

### Code Style

- Follow PEP 8 (enforced by Black and Ruff)
- Max line length: 100 characters
- Use type hints where practical
- Write docstrings for public functions
- Keep functions focused and small

### Security

- Never commit `.env` files or credentials
- Use environment variables for sensitive data
- Run `bandit` before committing security-sensitive code
- Keep dependencies updated

### Testing

- Write tests for new features
- Maintain test coverage above 70%
- Use descriptive test names
- Test edge cases and error conditions

## Troubleshooting

### Pre-commit Hook Fails

If a hook fails:

1. Read the error message
1. Fix the issues manually or run `pre-commit run --all-files`
1. Stage the changes: `git add .`
1. Commit again

### Import Errors

```bash
# Reinstall in development mode
pip install -e ".[dev]"
```

### CI Fails But Passes Locally

- Check Python version (CI runs 3.10, 3.11, 3.12)
- Ensure all dependencies are in `pyproject.toml`
- Check for OS-specific issues

## Updating Dependencies

```bash
# Update pre-commit hooks
pre-commit autoupdate

# Update Python dependencies
pip install --upgrade pip
pip install -e ".[dev]" --upgrade
```

## Getting Help

- Check existing issues on GitHub
- Read the documentation in `/docs`
- Run `rehoboam --help` for CLI usage
