# Use Python 3.14 slim image
FROM python:3.14-slim

# Set working directory
WORKDIR /app

# Copy project files
COPY pyproject.toml ./
COPY README.md ./
COPY rehoboam/ ./rehoboam/

# Install the bot
RUN pip install --no-cache-dir -e .

# Create log directory (daemon writes to ~/.rehoboam/logs)
RUN mkdir -p /root/.rehoboam/logs

# Set the entrypoint to rehoboam CLI
ENTRYPOINT ["/usr/local/bin/rehoboam"]

# Default command (can be overridden in docker-compose.yml)
CMD ["daemon", "--help"]
