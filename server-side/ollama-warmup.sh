#!/usr/bin/env bash
set -euo pipefail

# This file goes to /use/local/bin/ollama-warmup.sh

URL="http://192.168.10.161:11434/api/chat"
PAYLOAD='{"model":"coglet:latest","keep_alive":"45m","messages":[{"role":"user","content":"Hi. who are you?"}],"stream":false}'

# max. 10 attempts, 20s timeout each, 5s pause → Worst Case ~250s
for i in $(seq 1 10); do
    echo "ollama-warmup: attempt $i..." >&2
    # if curl -sS -X POST "$URL" -d "$PAYLOAD" --max-time 20 >/dev/null; then
    if curl -sS -X POST "$URL" -d "$PAYLOAD" --max-time 20; then
        echo "ollama-warmup: sucessfull." >&2
        exit 0
    fi
    sleep 5
done

echo "ollama-warmup: all attempts failed." >&2
exit 1
