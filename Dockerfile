# Use Python 3.14 slim image
FROM python:3.14-slim

# Set working directory
WORKDIR /app

# Install cron and other dependencies
RUN apt-get update && \
    apt-get install -y cron && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Copy project files
COPY pyproject.toml ./
COPY README.md ./
COPY rehoboam/ ./rehoboam/

# Install the bot
RUN pip install --no-cache-dir -e .

# Create log directory
RUN mkdir -p /var/log/rehoboam

# Copy cron configuration
COPY docker/crontab /etc/cron.d/rehoboam-cron

# Give execution rights on the cron job
RUN chmod 0644 /etc/cron.d/rehoboam-cron

# Apply cron job
RUN crontab /etc/cron.d/rehoboam-cron

# Create the log file to be able to run tail
RUN touch /var/log/rehoboam/trade.log

# Copy entrypoint script
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Run the command on container startup
ENTRYPOINT ["/entrypoint.sh"]
