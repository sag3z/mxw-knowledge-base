#!/usr/bin/env bash
# Параллельный запуск Devin по промптам из каталога.
# usage: tasks/devin/run_devin.sh <dir с *.md и *.model> <out dir> <permission-mode> [timeout_s]
cd "$(dirname "$0")/../.."
dir="$1"; out="$2"; mode="$3"; tmo="${4:-3000}"
D=/c/Users/fl3xx/AppData/Local/devin/cli/bin/devin
CFG="$(cygpath -w tasks/devin/devin_cfg.json)"
mkdir -p "$out"
for p in "$dir"/*.md; do
  k=$(basename "$p" .md)
  m=$(cat "$dir/$k.model" 2>/dev/null || echo claude-opus-5-5-high)
  (
    for a in 1 2; do
      echo "$(date +%H:%M:%S) start $k ($m) attempt $a"
      timeout "$tmo" "$D" --config "$CFG" --respect-workspace-trust false --permission-mode "$mode" \
        --process-model in-process --model "$m" --prompt-file "$(cygpath -w "$p")" -p > "$out/$k.md" 2> "$out/$k.err"
      rc=$?
      if [ $rc -eq 0 ] && [ -s "$out/$k.md" ]; then break; fi
      echo "$(date +%H:%M:%S) $k rc=$rc, retry"; sleep 20
    done
    echo "$(date +%H:%M:%S) done $k rc=$rc size=$(wc -c < "$out/$k.md")"
  ) &
  sleep 3
done
wait
echo ALL_DONE
