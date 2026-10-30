"""
Скрипт-наблюдатель за очередью замечаний ревью (Agent Review Watcher).
Отслеживает появление новых задач в Data/review_batches/agent_queue.json.
При появлении задачи со статусом 'pending' выводит детали и завершается с кодом 0,
сигнализируя автономному агенту о необходимости исправления замечаний.
"""

import sys
import time
import json
import argparse
from datetime import datetime
from pathlib import Path

# Настройка UTF-8 для консоли Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
QUEUE_PATH = PROJECT_ROOT / "Data" / "review_batches" / "agent_queue.json"


def load_queue() -> dict:
    if QUEUE_PATH.exists():
        try:
            with open(QUEUE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"tasks": []}


def save_queue(data: dict):
    QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(QUEUE_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def print_task_summary(task: dict):
    t_id = task.get("id")
    b_idx = task.get("batch_idx")
    created = task.get("created_at")
    items = task.get("items", [])
    print("========================================================")
    print(f"[AGENT QUEUE TASK DETECTED]")
    print(f"Task ID:    {t_id}")
    print(f"Batch:      Batch {b_idx:02d}")
    print(f"Created at: {created}")
    print(f"Items:      {len(items)} шт.")
    print("--------------------------------------------------------")
    for it in items:
        idx = it.get("item_idx", 0)
        fname = it.get("file_name", "")
        fpath = it.get("file_path", "")
        itype = it.get("type", "")
        label = it.get("label", "")
        note = it.get("note", "")
        print(f"* [#{idx}] {fname}")
        print(f"  Path:  {fpath}")
        print(f"  Type:  {itype} ({label})")
        if note:
            print(f"  Note:  {note}")
    print("========================================================", flush=True)


def list_tasks():
    q = load_queue()
    tasks = q.get("tasks", [])
    print(f"[AGENT QUEUE] Всего задач: {len(tasks)}")
    for t in tasks:
        t_id = t.get("id")
        b_idx = t.get("batch_idx")
        st = t.get("status")
        cnt = len(t.get("items", []))
        created = t.get("created_at")
        print(f"- [{st.upper()}] {t_id} | Batch {b_idx:02d} | {cnt} items | {created}")


def resolve_task(task_id: str):
    q = load_queue()
    found = False
    for t in q.get("tasks", []):
        if t.get("id") == task_id:
            t["status"] = "resolved"
            t["resolved_at"] = datetime.now().isoformat(timespec="seconds")
            found = True
            break
    if found:
        save_queue(q)
        print(f"[AGENT QUEUE] Задача {task_id} переведена в статус 'resolved'.")
    else:
        print(f"[AGENT QUEUE] Задача {task_id} не найдена.", file=sys.stderr)
        sys.exit(1)


def watch_queue(timeout: int = 3600, poll_interval: float = 2.0):
    start_time = time.time()
    print(f"[AGENT WATCHER] Ожидание задач в {QUEUE_PATH.relative_to(PROJECT_ROOT)} (таймаут: {timeout}с)...", flush=True)

    while True:
        q = load_queue()
        tasks = q.get("tasks", [])
        pending_task = next((t for t in tasks if t.get("status") == "pending"), None)

        if pending_task:
            pending_task["status"] = "in_progress"
            pending_task["started_at"] = datetime.now().isoformat(timespec="seconds")
            save_queue(q)
            print_task_summary(pending_task)
            sys.exit(0)

        if timeout and (time.time() - start_time) > timeout:
            print(f"[AGENT WATCHER] Превышен таймаут ожидания ({timeout}с). Задач не поступило.", flush=True)
            sys.exit(2)

        time.sleep(poll_interval)


def check_queue():
    q = load_queue()
    tasks = q.get("tasks", [])
    pending_task = next((t for t in tasks if t.get("status") == "pending"), None)
    if pending_task:
        print_task_summary(pending_task)
        sys.exit(0)
    else:
        print("[AGENT QUEUE] Задач со статусом 'pending' нет.")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="AxonBot Agent Review Watcher")
    parser.add_argument("--wait", action="store_true", default=True, help="Wait until a pending task appears (default)")
    parser.add_argument("--check", action="store_true", help="Non-blocking check for pending tasks")
    parser.add_argument("--list", action="store_true", help="List all tasks in queue")
    parser.add_argument("--resolve", type=str, help="Mark task ID as resolved")
    parser.add_argument("--timeout", type=int, default=3600, help="Wait timeout in seconds (default: 3600)")
    parser.add_argument("--interval", type=float, default=2.0, help="Poll interval in seconds (default: 2.0)")
    args = parser.parse_args()

    if args.list:
        list_tasks()
        return

    if args.resolve:
        resolve_task(args.resolve)
        return

    if args.check:
        check_queue()
        return

    watch_queue(timeout=args.timeout, poll_interval=args.interval)


if __name__ == "__main__":
    try:
        from Scripts.bot.utils.alert_manager import setup_crash_reporter
        setup_crash_reporter("Watch_Reviews")
    except Exception:
        pass
    main()
