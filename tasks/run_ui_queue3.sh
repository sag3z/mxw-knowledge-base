#!/usr/bin/env bash
cd "$(dirname "$0")/.."
# wait for the in-flight 021 opencode run to finish
while tasklist //FI "IMAGENAME eq opencode-cli.exe" //V 2>/dev/null | grep -q "opencode-cli"; do
  powershell -NoProfile -Command "if (Get-CimInstance Win32_Process -Filter \"Name='opencode-cli.exe'\" | Where-Object { \$_.CommandLine -match ' run ' }) { exit 0 } else { exit 1 }" || break
  sleep 30
done
echo "   022 finished $(date +%H:%M)"
cp tasks/022_ui_topics_articles.log tasks/022_ui_topics_articles_a1.log
if tail -3 tasks/022_ui_topics_articles.log | grep -q -E "Error: |ECONNRESET|stream ended"; then
  for a in 2 3 4; do
    echo "== 022 retry $a $(date +%H:%M)"; timeout 3600 ./delegate.sh tasks/022_ui_topics_articles.md glm-5.3 > /dev/null 2>&1
    cp tasks/022_ui_topics_articles.log tasks/022_ui_topics_articles_a$a.log
    tail -3 tasks/022_ui_topics_articles.log | grep -q -E "Error: |ECONNRESET|stream ended" || break; sleep 60
  done
fi
for t in 021_ui_people 024_local_search 023_export_from_web; do
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
