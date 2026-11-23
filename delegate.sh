#!/usr/bin/env bash
# Hand a coding task to an API model through opencode (Claude only writes the task and reviews the result).
# Usage: ./delegate.sh tasks/NNN_name.md [model]     model default: glm-5.3 (reformboss)
#        models: glm-5.3 | deepseek-v4.1-flash | kimi-k3.1-pro (slow)
# opencode sometimes hangs at startup with no output: if the log is still empty after STARTUP_WAIT
# seconds, the run is killed and retried (up to 3 attempts). Only one opencode run at a time.
set -u
task="$1"
model="ocam-fc180f9dd00f/${2:-glm-5.3}"
cli="/c/Users/fl3xx/AppData/Local/Programs/ai.opencode.desktop/resources/opencode-cli.exe"
log="${task%.md}.log"
STARTUP_WAIT=${STARTUP_WAIT:-180}
cd "$(dirname "$0")"
export PYTHONUTF8=1
code=1
for attempt in 1 2 3; do
  "$cli" run -m "$model" --auto --title "mxw $(basename "$task" .md)" \
    "Выполни задачу из файла $task. Работай только в этом проекте, не читай .llm_env и .openrouter_key. В конце кратко отчитайся: какие файлы создал или изменил и как проверил." \
    > "$log" 2>&1 &
  pid=$!
  waited=0
  while kill -0 $pid 2>/dev/null && [ ! -s "$log" ] && [ $waited -lt $STARTUP_WAIT ]; do
    sleep 5; waited=$((waited + 5))
  done
  if kill -0 $pid 2>/dev/null && [ ! -s "$log" ]; then
    echo "attempt $attempt: no output after ${STARTUP_WAIT}s, restarting"
    kill $pid 2>/dev/null
    taskkill //F //IM opencode-cli.exe >/dev/null 2>&1
    sleep 3
    continue
  fi
  wait $pid
  code=$?
  break
done
echo "exit=$code model=$model log=$log"
tail -15 "$log"
