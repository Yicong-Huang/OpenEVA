#!/bin/bash
# Eva hook: forwards agent events to the server

INPUT=$(cat)

# Debug log
echo "$(date): event fired" >> /tmp/eva-hook-fired.log
echo "TMUX=$TMUX" >> /tmp/eva-hook-fired.log
echo "INPUT=$(echo "$INPUT" | head -c 300)" >> /tmp/eva-hook-fired.log

# Get tmux session name
SESSION=""
if [ -n "$TMUX" ]; then
  SESSION=$(tmux display-message -p '#S' 2>/dev/null)
fi
# Fallback: walk parent PID chain
if [ -z "$SESSION" ]; then
  PID=$$
  for i in 1 2 3 4 5 6 7 8; do
    PID=$(ps -o ppid= -p "$PID" 2>/dev/null | tr -d ' ')
    [ -z "$PID" ] || [ "$PID" = "1" ] && break
    MATCH=$(tmux list-panes -a -F '#{pane_pid} #{session_name}' 2>/dev/null | awk -v p="$PID" '$1==p{print $2}')
    if [ -n "$MATCH" ]; then
      SESSION="$MATCH"
      break
    fi
  done
fi

echo "SESSION=$SESSION" >> /tmp/eva-hook-fired.log
echo "---" >> /tmp/eva-hook-fired.log

[ -z "$SESSION" ] && exit 0

EVENT=$(echo "$INPUT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('hook_event_name',''))" 2>/dev/null)

# Hook target -- defaults to localhost:8021 to match Eva's default
# port. OSS users running on a different port (via EVA_PORT) can
# override the URL via EVA_HOOK_URL or EVA_PORT in their agent
# launcher's environment.
HOOK_URL="${EVA_HOOK_URL:-http://localhost:${EVA_PORT:-8021}/api/hook}"

curl -s -X POST "$HOOK_URL" \
  -H "Content-Type: application/json" \
  -d "{\"session\":\"$SESSION\",\"event\":\"$EVENT\",\"data\":$INPUT}" \
  --max-time 2 > /dev/null 2>&1 &

exit 0
