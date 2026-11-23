"""Наблюдатель за отвязанным прогоном factcheck.py --pilot --force (задача 029).

Раз в WATCH_STEP секунд пишет строку в data/factcheck_watch.log: сколько свежих json
пилота готово, сколько строк в data/factcheck_run.log и хвост лога. Заканчивается,
когда прогон завершился (в run-логе есть строка «проверка заняла» или процесс
factcheck.py исчез) либо когда истекли MAX_WAIT секунд (45 минут).

Запуск (после старта прогона): python tasks/029_watch.py
"""
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "factcheck"
RUN = ROOT / "data" / "factcheck_run.log"
ERR = ROOT / "data" / "factcheck_err.log"
WATCH = ROOT / "data" / "factcheck_watch.log"

WATCH_STEP = 120          # период проверки, сек
MAX_WAIT = 45 * 60        # максимум наблюдения, сек
GRACE = 90                # до первого alive-проверки (процесс должен успеть появиться)


def factcheck_alive():
    """Есть ли живой python-процесс с factcheck.py в командной строке."""
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-CimInstance Win32_Process -Filter \"Name='python.exe'\")"
             ".CommandLine"],
            capture_output=True, text=True, timeout=60).stdout
    except Exception:
        return True   # не смогли спросить — считаем, что жив
    return "factcheck.py" in (out or "")


def tail(path, n=3):
    try:
        lines = [l.rstrip() for l in path.read_text(encoding="utf-8", errors="replace").splitlines()]
        return len(lines), " ⏎ ".join(lines[-n:])
    except Exception:
        return 0, ""


def main():
    t0 = time.time()
    seen_alive = False
    WATCH.write_text("", encoding="utf-8")   # свежий лог наблюдения

    def note(s):
        with WATCH.open("a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%H:%M:%S')}] {s}\n")
        print(s, flush=True)

    note(f"наблюдение старт; шаг {WATCH_STEP} с, максимум {MAX_WAIT // 60} мин")
    while time.time() - t0 < MAX_WAIT:
        time.sleep(WATCH_STEP)
        fresh = [f.name for f in sorted(OUT.glob("*.json")) if f.stat().st_mtime >= t0 - GRACE]
        n_run, last = tail(RUN)
        n_err, last_err = tail(ERR, 1)
        done = "проверка заняла" in RUN.read_text(encoding="utf-8", errors="replace") if RUN.exists() else False
        alive = factcheck_alive()
        seen_alive = seen_alive or alive
        note(f"готово {len(fresh)} свежих json ({', '.join(f[:30] for f in fresh[-3:])}); "
             f"run.log {n_run} стр.; последний: {last[:220]}")
        if n_err and last_err:
            note(f"!! err.log ({n_err} стр.): {last_err[:220]}")
        if done:
            note("прогон завершился (финальная строка в run.log) — наблюдаю ещё один шаг и выходим")
            time.sleep(WATCH_STEP)
            n_run, last = tail(RUN, 5)
            note(f"финал: run.log {n_run} стр.; последние: {last[:400]}")
            return
        if seen_alive and not alive:
            note("процесс factcheck.py исчез (не отработав до финальной строки) — завершаем наблюдение")
            n_run, last = tail(RUN, 5)
            n_err, last_err = tail(ERR, 3)
            note(f"run.log {n_run} стр.; последние: {last[:400]}")
            if last_err:
                note(f"err.log: {last_err[:400]}")
            return
    note(f"истекли {MAX_WAIT // 60} минут наблюдения — прогон ещё идёт; "
         f"готово {len([f for f in OUT.glob('*.json') if f.stat().st_mtime >= t0 - GRACE])} свежих json")


if __name__ == "__main__":
    main()
