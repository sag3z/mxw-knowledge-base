#!/usr/bin/env bash
# Sequential UI tasks; a run that dies from a provider error is retried (up to 4 attempts per task).
cd "$(dirname "$0")/.."
for t in 019_ui_shell 020_ui_chat 021_ui_people 022_ui_topics_articles 023_export_from_web; do
  for a in 1 2 3 4; do
    echo "== $t attempt $a $(date +%H:%M)"
    timeout 3600 ./delegate.sh tasks/$t.md glm-5.3 > /dev/null 2>&1
    cp tasks/$t.log tasks/${t}_a$a.log
    if tail -3 tasks/$t.log | grep -q -E "Error: |ECONNRESET|stream ended"; then echo "   provider error, retry"; sleep 60; continue; fi
    break
  done
  echo "   done $t $(date +%H:%M)"
done
echo UI_DONE
