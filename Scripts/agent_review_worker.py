"""
AxonBot Agent Review Worker
Автономный фоновый демон/воркер обработки очереди замечаний модерации (Rapid Review).
Выполняет независимую калибровку боксов, снятие ложных срабатываний (FP),
покадровый блюр (включая анимированные GIF), обновление batch_meta.json,
перегенерацию HTML-отчетов и перевод задач в статус resolved.
"""

import os
import sys
import subprocess
from pathlib import Path

# Автоматический перезапуск через виртуальное окружение .venv при запуске из системного python
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_VENV_PYTHON = _PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"
if _VENV_PYTHON.exists() and Path(sys.executable).resolve() != _VENV_PYTHON.resolve():
    try:
        import PIL
    except ImportError:
        res = subprocess.run([str(_VENV_PYTHON)] + sys.argv, cwd=str(_PROJECT_ROOT))
        sys.exit(res.returncode)

import time
import json
import shutil
import signal
import ctypes
import logging
import argparse
import threading
from datetime import datetime
from logging.handlers import RotatingFileHandler

from PIL import Image
from Scripts.bot.utils.alert_manager import setup_crash_reporter, send_sync_alert, send_sync_critical

# Настройка UTF-8 для консоли Windows
if sys.platform == "win32":
    try:
        if sys.stdout is not None and hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        if sys.stderr is not None and hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

BATCH_BASE = PROJECT_ROOT / "Data" / "review_batches"
AGENT_QUEUE_PATH = BATCH_BASE / "agent_queue.json"
AUDIT_LOG_PATH = BATCH_BASE / "audit_log.json"
WORKER_LOG_PATH = BATCH_BASE / "worker.log"
PID_FILE_PATH = BATCH_BASE / "worker.pid"

_QUEUE_LOCK = threading.Lock()
_STOP_EVENT = threading.Event()

# =====================================================================
# Настройка логирования (ротируемый файл Data/review_batches/worker.log)
# =====================================================================
def get_logger():
    logger = logging.getLogger("agent_review_worker")
    if not logger.handlers:
        logger.setLevel(logging.INFO)
        BATCH_BASE.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            str(WORKER_LOG_PATH),
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8"
        )
        formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

        if sys.stdout is not None and hasattr(sys.stdout, "write"):
            try:
                console_handler = logging.StreamHandler(sys.stdout)
                console_handler.setFormatter(formatter)
                logger.addHandler(console_handler)
            except Exception:
                pass
    return logger

logger = get_logger()

# =====================================================================
# Управление процессом демона (PID, Detached, Status, Stop)
# =====================================================================
def get_python_executable() -> str:
    venv_py = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"
    if venv_py.exists():
        return str(venv_py)
    return sys.executable


def is_pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        kernel32 = ctypes.windll.kernel32
        kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
        kernel32.GetExitCodeProcess.restype = ctypes.c_int
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.c_int

        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        exit_code = ctypes.c_ulong()
        success = kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
        kernel32.CloseHandle(handle)
        STILL_ACTIVE = 259
        return bool(success and exit_code.value == STILL_ACTIVE)
    else:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False


def read_pid_file() -> int | None:
    if PID_FILE_PATH.exists():
        try:
            with open(PID_FILE_PATH, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if content:
                    pid = int(content)
                    if is_pid_alive(pid):
                        return pid
                    else:
                        remove_pid_file()
        except Exception:
            pass
    return None


def write_pid_file(pid: int):
    BATCH_BASE.mkdir(parents=True, exist_ok=True)
    with open(PID_FILE_PATH, "w", encoding="utf-8") as f:
        f.write(str(pid))


def remove_pid_file():
    if PID_FILE_PATH.exists():
        try:
            PID_FILE_PATH.unlink()
        except Exception:
            pass


def is_daemon_running() -> bool:
    return read_pid_file() is not None


def kill_process_tree(pid: int):
    if pid <= 0:
        return
    if sys.platform == "win32":
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False
            )
        except Exception:
            pass
    else:
        try:
            os.kill(pid, signal.SIGTERM)
        except Exception:
            pass


def start_daemon():
    existing_pid = read_pid_file()
    if existing_pid:
        print(f"[WORKER] Daemon is already running (PID: {existing_pid}).")
        return

    py_exe = get_python_executable()
    worker_script = str(Path(__file__).resolve())
    cmd = [py_exe, worker_script, "--run-loop"]

    if sys.platform == "win32":
        CREATE_BREAKAWAY_FROM_JOB = 0x01000000
        CREATE_NO_WINDOW = 0x08000000
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        flags = CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
        try:
            subprocess.Popen(
                cmd,
                creationflags=flags | CREATE_BREAKAWAY_FROM_JOB,
                close_fds=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                cwd=str(PROJECT_ROOT)
            )
        except OSError:
            subprocess.Popen(
                cmd,
                creationflags=flags,
                close_fds=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                cwd=str(PROJECT_ROOT)
            )
    else:
        subprocess.Popen(
            cmd,
            start_new_session=True,
            close_fds=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            cwd=str(PROJECT_ROOT)
        )

    # Ожидаем появления реального PID в файле от дочернего процесса
    real_pid = None
    for _ in range(20):
        time.sleep(0.1)
        real_pid = read_pid_file()
        if real_pid:
            break

    if real_pid:
        print(f"[WORKER] Daemon successfully started in background (PID: {real_pid}).")
        logger.info(f"Daemon process confirmed active with PID {real_pid}")
    else:
        print("[WORKER] Error: failed to verify daemon startup. Check Data/review_batches/worker.log.")


def stop_daemon():
    pid = read_pid_file()
    if not pid:
        print("[WORKER] Daemon is not running.")
        remove_pid_file()
        return

    kill_process_tree(pid)
    remove_pid_file()
    print(f"[WORKER] Daemon (PID: {pid}) stopped successfully.")
    logger.info(f"Daemon (PID: {pid}) stopped by request.")


def get_queue_statistics() -> dict:
    q = load_queue_safe()
    tasks = q.get("tasks", [])
    stats = {
        "total": len(tasks),
        "pending": 0,
        "in_progress": 0,
        "resolved": 0,
        "error": 0
    }
    for t in tasks:
        st = t.get("status", "pending")
        if st in stats:
            stats[st] += 1
        else:
            stats[st] = 1
    return stats


def print_status():
    pid = read_pid_file()
    if pid:
        print(f"[WORKER] Status: ACTIVE (PID: {pid})")
    else:
        print("[WORKER] Status: STOPPED (No active background daemon)")

    stats = get_queue_statistics()
    print(f"[WORKER] Queue metrics:")
    print(f"   Total:       {stats.get('total', 0)}")
    print(f"   Pending:     {stats.get('pending', 0)}")
    print(f"   InProgress:  {stats.get('in_progress', 0)}")
    print(f"   Resolved:    {stats.get('resolved', 0)}")
    print(f"   Error:       {stats.get('error', 0)}")

    q = load_queue_safe()
    pending_tasks = [t for t in q.get("tasks", []) if t.get("status") in ("pending", "in_progress")]
    if pending_tasks:
        print("[WORKER] Active / Waiting tasks in queue:")
        for t in pending_tasks:
            t_id = t.get("id")
            b_idx = t.get("batch_idx")
            cnt = len(t.get("items", []))
            st = t.get("status")
            print(f"   * [{st.upper()}] {t_id} | Batch {b_idx:02d} | {cnt} items")


# =====================================================================
# Вспомогательные функции для очереди и аудит-лога
# =====================================================================
def load_queue_safe() -> dict:
    with _QUEUE_LOCK:
        if AGENT_QUEUE_PATH.exists():
            try:
                with open(AGENT_QUEUE_PATH, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {"tasks": []}


def save_queue_safe(data: dict):
    with _QUEUE_LOCK:
        AGENT_QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(AGENT_QUEUE_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)


def load_audit_log_safe() -> dict:
    if AUDIT_LOG_PATH.exists():
        try:
            with open(AUDIT_LOG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"batches": {}, "applied_batches": []}


def save_audit_log_safe(data: dict):
    AUDIT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(AUDIT_LOG_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# =====================================================================
# Ядро калибровки и исправления замечаний (Calibration & Redaction Engine)
# =====================================================================
def resolve_single_item(task: dict, item: dict, client=None) -> dict:
    """
    Выполняет полную автономную калибровку одного элемента из замечания:
    - считывает тип (fp, missed, offset, flagged, manual_box);
    - берет чистый оригинал из originals_backup (или бэкапит его);
    - перекалибрует детекции и накладывает снайперский блюр (включая покадровый GIF);
    - синхронизирует batch_meta.json, audit_log.json и отчет report.html.
    """
    from Scripts.media_sanitizer import (
        apply_blur_to_image,
        generate_batch_html_report,
        get_gemini_client,
        process_single_file,
        process_animated_media_smart
    )

    batch_idx = int(item.get("batch_idx", task.get("batch_idx", 1)))
    item_idx = int(item.get("item_idx", 1))
    file_name = item.get("file_name", "")
    file_path = item.get("file_path", "")
    fb_type = item.get("type", "flagged").lower()
    label = item.get("label", "")
    note = item.get("note", "")
    detections = item.get("detections", [])

    batch_dir = BATCH_BASE / f"batch_{batch_idx:02d}"
    meta_path = batch_dir / "batch_meta.json"
    html_report_path = batch_dir / "report.html"
    previews_dir = batch_dir / "previews"
    backup_dir = batch_dir / "originals_backup"

    if not meta_path.exists():
        raise FileNotFoundError(f"batch_meta.json not found for batch {batch_idx}")

    with open(meta_path, "r", encoding="utf-8") as f:
        meta_data = json.load(f)

    target_item = None
    for it in meta_data.get("results", []):
        if it.get("file_name") == file_name:
            target_item = it
            break

    if not target_item:
        raise ValueError(f"File {file_name} not found in results of batch {batch_idx}")

    # Путь к живому файлу в attachments
    orig_rel = target_item.get("file_path") or file_path
    live_path = PROJECT_ROOT / orig_rel if orig_rel else None

    # Обеспечиваем наличие эталонного оригинала в originals_backup
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / file_name

    if not backup_path.exists() and live_path and live_path.exists():
        shutil.copy2(live_path, backup_path)

    src_img = backup_path if backup_path.exists() else live_path
    if not src_img or not src_img.exists():
        raise FileNotFoundError(f"Source image not found for {file_name} (neither backup nor attachments)")

    audit_data = load_audit_log_safe()
    is_batch_applied = batch_idx in audit_data.get("applied_batches", [])

    previews_dir.mkdir(parents=True, exist_ok=True)
    preview_file = previews_dir / file_name

    logger.info(f"Processing item [{file_name}] in batch {batch_idx} (type: {fb_type}, note: '{note}')")

    # СЦЕНАРИЙ 1: Ложное срабатывание (FP)
    if fb_type == "fp":
        target_item["status"] = "clean"
        target_item["detections"] = []
        target_item["preview_rel_to_batch"] = None

        if preview_file.exists():
            try:
                preview_file.unlink()
            except Exception:
                pass

        # Если батч уже был применен, возвращаем чистый оригинал из бэкапа
        if is_batch_applied and backup_path.exists() and live_path and live_path.exists():
            shutil.copy2(backup_path, live_path)
            logger.info(f"Restored clean original to {orig_rel} from backup")

        b_key = str(batch_idx)
        if b_key not in audit_data["batches"]:
            audit_data["batches"][b_key] = {"batch_idx": batch_idx, "applied": is_batch_applied, "items": {}}
        items_map = audit_data["batches"][b_key].setdefault("items", {})
        items_map[str(item_idx)] = {
            "item_idx": item_idx,
            "file_name": file_name,
            "file_path": orig_rel,
            "type": "recheck",
            "is_recheck": True,
            "is_approved": False,
            "is_rejected": False,
            "label": "На проверку (снято ложное срабатывание)",
            "note": note,
            "detections": [],
            "updated_at": datetime.now().isoformat(timespec="seconds")
        }
        save_audit_log_safe(audit_data)

    # СЦЕНАРИЙ 2: Пропуск (Missed), Смещение (Offset), Флаг (Flagged), Ручная рамка (manual_box)
    else:
        new_dets = []
        # Готовые детекции берем ТОЛЬКО если это явное ручное выделение рамкой (manual_box)
        if fb_type == "manual_box" and detections and len(detections) > 0:
            new_dets = detections
        else:
            # Для замечаний offset, missed, flagged требуется перекалибровка через VLM
            if client is None:
                client = get_gemini_client()

            ext = src_img.suffix.lower()
            with Image.open(src_img) as img:
                is_animated = getattr(img, "is_animated", False) or getattr(img, "n_frames", 1) > 1
                is_gif = (ext == ".gif") or is_animated

            if is_gif:
                gif_passport = process_animated_media_smart(src_img, previews_dir, batch_dir, client)
                new_dets = gif_passport.get("detections", [])
            else:
                passport = process_single_file(src_img, preview=True, client=client, route_complex_to_review=False)
                new_dets = passport.get("detections", [])
                temp_preview = src_img.with_name(f"{src_img.stem}_preview{src_img.suffix}")
                if temp_preview.exists() and temp_preview != preview_file:
                    try:
                        temp_preview.unlink()
                    except Exception:
                        pass

        if new_dets:
            apply_blur_to_image(src_img, new_dets, preview_file, blur_radius=22)
            target_item["status"] = "blurred"
            target_item["detections"] = new_dets
            target_item["preview_rel_to_batch"] = f"previews/{file_name}"

            if is_batch_applied and live_path and live_path.exists():
                shutil.copy2(preview_file, live_path)
                logger.info(f"Applied newly calibrated preview to {orig_rel}")
        else:
            target_item["status"] = "clean"
            target_item["detections"] = []
            target_item["preview_rel_to_batch"] = None
            if preview_file.exists():
                try:
                    preview_file.unlink()
                except Exception:
                    pass

            if is_batch_applied and backup_path.exists() and live_path and live_path.exists():
                shutil.copy2(backup_path, live_path)

        b_key = str(batch_idx)
        if b_key not in audit_data["batches"]:
            audit_data["batches"][b_key] = {"batch_idx": batch_idx, "applied": is_batch_applied, "items": {}}
        items_map = audit_data["batches"][b_key].setdefault("items", {})
        items_map[str(item_idx)] = {
            "item_idx": item_idx,
            "file_name": file_name,
            "file_path": orig_rel,
            "type": "recheck",
            "is_recheck": True,
            "is_approved": False,
            "is_rejected": False,
            "label": f"На проверку (откалибровано: {label or fb_type})",
            "note": note,
            "detections": new_dets,
            "updated_at": datetime.now().isoformat(timespec="seconds")
        }
        save_audit_log_safe(audit_data)

    # Сохраняем обновленные метаданные батча
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta_data, f, ensure_ascii=False, indent=2)

    # Перегенерируем HTML-отчет батча
    generate_batch_html_report(batch_idx, meta_data.get("results", []), html_report_path)
    logger.info(f"Batch {batch_idx:02d} metadata and report.html successfully refreshed for {file_name}")

    return target_item


def process_task(task: dict, client=None) -> bool:
    """Обрабатывает одну задачу из очереди, переводя ее в resolved или error"""
    task_id = task.get("id", "unknown")
    logger.info(f"Starting processing task {task_id} (Batch {task.get('batch_idx')}, {len(task.get('items', []))} items)")

    items = task.get("items", [])
    success = True
    error_msg = ""

    for item in items:
        try:
            resolve_single_item(task, item, client=client)
        except Exception as e:
            logger.exception(f"Error processing item {item.get('file_name')} in task {task_id}: {e}")
            success = False
            error_msg = str(e)
            break

    q = load_queue_safe()
    for t in q.get("tasks", []):
        if t.get("id") == task_id:
            if success:
                t["status"] = "resolved"
                t["resolved_at"] = datetime.now().isoformat(timespec="seconds")
                logger.info(f"Task {task_id} resolved successfully.")
                try:
                    send_sync_alert(
                        f"✅ <b>[Review Worker] Задача <code>{task_id}</code> выполнена</b>\n"
                        f"• <b>Батч:</b> <code>Batch {task.get('batch_idx', 0):02d}</code>\n"
                        f"• <b>Откалибровано элементов:</b> <code>{len(items)}</code>",
                        topic_id="AXONBOT",
                        silent=True
                    )
                except Exception:
                    pass
            else:
                t["status"] = "error"
                t["error"] = error_msg
                t["error_details"] = error_msg
                t["error_at"] = datetime.now().isoformat(timespec="seconds")
                logger.error(f"Task {task_id} marked as error: {error_msg}")
                try:
                    send_sync_critical(
                        title=f"Ошибка калибровки задачи {task_id} в Review Worker",
                        details=(
                            f"• <b>Батч:</b> <code>Batch {task.get('batch_idx', 0):02d}</code>\n"
                            f"• <b>Ошибка:</b> <code>{error_msg}</code>"
                        )
                    )
                except Exception:
                    pass
            break
    save_queue_safe(q)
    return success


def run_worker_iteration(client=None) -> int:
    """Проверяет очередь и обрабатывает одну задачу со статусом pending. Возвращает количество обработанных задач."""
    q = load_queue_safe()
    tasks = q.get("tasks", [])
    pending_task = next((t for t in tasks if t.get("status") == "pending"), None)

    if not pending_task:
        return 0

    task_id = pending_task.get("id")
    pending_task["status"] = "in_progress"
    pending_task["started_at"] = datetime.now().isoformat(timespec="seconds")
    save_queue_safe(q)

    process_task(pending_task, client=client)
    return 1


def run_worker_loop(poll_interval: float = 2.0):
    """Бесконечный цикл опроса очереди для демона/фонового потока"""
    logger.info(f"Worker loop started (PID: {os.getpid()}, poll interval: {poll_interval}s)")

    # Переиспользуем клиент Gemini
    client = None
    try:
        from Scripts.media_sanitizer import get_gemini_client
        client = get_gemini_client()
    except Exception as e:
        logger.warning(f"Could not pre-initialize Gemini client: {e}")

    while not _STOP_EVENT.is_set():
        try:
            run_worker_iteration(client=client)
        except Exception as e:
            logger.exception(f"Unexpected worker loop iteration error: {e}")

        time.sleep(poll_interval)

    logger.info("Worker loop stopped cleanly.")


def start_worker_thread(poll_interval: float = 2.0) -> threading.Thread:
    """Запускает воркер в отдельном фоновом демоническом потоке (для интеграции с media_sanitizer --serve)"""
    t = threading.Thread(
        target=run_worker_loop,
        args=(poll_interval,),
        daemon=True,
        name="ReviewWorkerThread"
    )
    t.start()
    return t


get_daemon_pid = read_pid_file


def process_all_pending() -> int:
    """Обрабатывает все накопившиеся задачи со статусом pending и завершается"""
    count = 0
    client = None
    try:
        from Scripts.media_sanitizer import get_gemini_client
        client = get_gemini_client()
    except Exception:
        pass

    while True:
        handled = run_worker_iteration(client=client)
        if handled == 0:
            break
        count += handled
    return count


# =====================================================================
# CLI интерфейс
# =====================================================================
def main():
    parser = argparse.ArgumentParser(description="AxonBot Autonomous Review Worker Daemon")
    parser.add_argument("--daemon-start", "--daemon_start", action="store_true", help="Start background detached worker daemon")
    parser.add_argument("--status", action="store_true", help="Check worker status and review queue stats")
    parser.add_argument("--stop", action="store_true", help="Stop background worker daemon")
    parser.add_argument("--run-once", "--run_once", action="store_true", help="Process all current pending tasks once and exit")
    parser.add_argument("--run-loop", "--run_loop", "--run", action="store_true", help="Run polling loop directly in current process")
    parser.add_argument("--interval", type=float, default=2.0, help="Polling interval in seconds (default: 2.0)")

    args = parser.parse_args()

    if args.daemon_start:
        start_daemon()
        return

    if args.status:
        print_status()
        return

    if args.stop:
        stop_daemon()
        return

    if args.run_once:
        print("[WORKER] Processing all pending review tasks...")
        n = process_all_pending()
        print(f"[WORKER] Successfully processed {n} tasks.")
        return

    if args.run_loop:
        # Регистрируем обработчики завершения
        def _handle_exit(sig, frame):
            logger.info(f"Received exit signal {sig}, terminating worker loop...")
            _STOP_EVENT.set()
            remove_pid_file()
            sys.exit(0)

        signal.signal(signal.SIGINT, _handle_exit)
        signal.signal(signal.SIGTERM, _handle_exit)

        # Записываем PID текущего процесса
        write_pid_file(os.getpid())
        try:
            run_worker_loop(poll_interval=args.interval)
        finally:
            remove_pid_file()
        return

    # По умолчанию если аргументов нет - выводим статус
    print_status()


if __name__ == "__main__":
    setup_crash_reporter("Agent_Review_Worker")
    main()
