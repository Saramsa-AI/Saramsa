#!/bin/bash
# Performance monitoring script for testing the gc.collect() fixes
# Usage: ./monitor-performance.sh

echo "=================================="
echo "PERFORMANCE MONITOR - PR #73 Test"
echo "=================================="
echo ""
echo "Watching for new uploads..."
echo "Expected: 10-15 min processing (vs 40 min before)"
echo "Expected: 25-30 sec/batch (vs 600 sec before)"
echo ""

TEMP_DIR=$(mktemp -d)
LAST_TASK=""

while true; do
    # Download latest logs
    az webapp log download --name saramsa-celery-prod-2 --resource-group saramsa \
        --log-file "$TEMP_DIR/logs.zip" 2>&1 | grep -v "UserWarning" > /dev/null

    unzip -o -q "$TEMP_DIR/logs.zip" -d "$TEMP_DIR/logs" 2>/dev/null

    # Find latest task
    LATEST=$(tail -500 "$TEMP_DIR/logs/LogFiles/2026_06_03_ln0mdlwk0001FY_default_docker.log" 2>/dev/null | \
        grep "Background task started" | tail -1 | grep -oP 'task_id=\K[a-f0-9-]+' || echo "")

    if [ -n "$LATEST" ] && [ "$LATEST" != "$LAST_TASK" ]; then
        echo ""
        echo "🆕 NEW TASK DETECTED: $LATEST"
        echo "Started: $(date '+%Y-%m-%d %H:%M:%S')"
        LAST_TASK="$LATEST"
        START_TIME=$(date +%s)
    fi

    if [ -n "$LAST_TASK" ]; then
        # Get latest batch info
        BATCH_INFO=$(tail -1000 "$TEMP_DIR/logs/LogFiles/2026_06_03_ln0mdlwk0001FY_default_docker.log" 2>/dev/null | \
            grep "DIAG.*Batch" | grep "$LAST_TASK" | tail -1)

        if [ -n "$BATCH_INFO" ]; then
            # Extract batch number and timing
            BATCH_NUM=$(echo "$BATCH_INFO" | grep -oP 'Batch \K\d+/\d+')
            BATCH_TIME=$(echo "$BATCH_INFO" | grep -oP 'in \K[\d.]+s')
            PAIRS_SEC=$(echo "$BATCH_INFO" | grep -oP '\(\K\d+ pairs/s')

            echo "📊 $BATCH_NUM | ${BATCH_TIME}s | ${PAIRS_SEC}"

            # Check for completion
            if echo "$BATCH_INFO" | grep -q "PHASE END\|SUCCESS"; then
                ELAPSED=$(($(date +%s) - START_TIME))
                echo ""
                echo "✅ COMPLETED in ${ELAPSED}s ($(($ELAPSED / 60))m $(($ELAPSED % 60))s)"
                echo ""
            fi
        fi
    fi

    sleep 5
done
