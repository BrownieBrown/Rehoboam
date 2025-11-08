#!/bin/bash
set -e

echo "Starting Rehoboam KICKBASE Trading Bot..."
echo "Trading schedule: Every 4 hours"
echo "Dry run mode: $DRY_RUN"
echo "================================"

# Run initial test to verify credentials
echo "Testing KICKBASE login..."
rehoboam login

echo ""
echo "Login successful! Starting cron scheduler..."
echo "Logs will be written to /var/log/rehoboam/trade.log"
echo ""

# Start cron in foreground
cron && tail -f /var/log/rehoboam/trade.log
