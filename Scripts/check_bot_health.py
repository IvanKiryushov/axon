import re
import sys
import json
import argparse
from pathlib import Path
from dataclasses import dataclass, field
from datetime import datetime

# Гарантируем UTF-8 вывод в Windows PowerShell
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

LOGS_ROOT = Path(__file__).resolve().parent.parent / "Scripts" / "bot" / "data" / "logs"

@dataclass
class Incident:
    timestamp: str
    severity: str  # CRITICAL | WARNING
    category: str
    message: str
    details: str = ""

@dataclass
class SessionHealthReport:
    session_id: str
    session_dir: Path
    total_requests: int = 0
    successful_requests: int = 0
    durations_ms: list[float] = field(default_factory=list)
    incidents: list[Incident] = field(default_factory=list)
    conversations_count: int = 0
    
    @property
    def avg_latency_sec(self) -> float:
        if not self.durations_ms:
            return 0.0
        return round(sum(self.durations_ms) / len(self.durations_ms) / 1000.0, 2)

    @property
    def max_latency_sec(self) -> float:
        if not self.durations_ms:
            return 0.0
        return round(max(self.durations_ms) / 1000.0, 2)

    @property
    def success_rate(self) -> float:
        if self.total_requests == 0:
            return 100.0
        return round((self.successful_requests / self.total_requests) * 100, 1)

    @property
    def status(self) -> str:
        critical_count = sum(1 for inc in self.incidents if inc.severity == "CRITICAL")
        if critical_count > 0 or self.success_rate < 70.0:
            return "🔴 CRITICAL"
        if len(self.incidents) > 0 or self.success_rate < 95.0 or self.avg_latency_sec > 12.0:
            return "🟡 DEGRADED"
        return "🟢 HEALTHY"

def find_sessions(limit: int = 5) -> list[Path]:
    """Возвращает список последних директорий сессий, отсортированных по времени."""
    if not LOGS_ROOT.exists():
        return []
    
    sessions = [p for p in LOGS_ROOT.iterdir() if p.is_dir() and p.name.startswith("session_")]
    sessions.sort(key=lambda p: p.name, reverse=True)
    return sessions[:limit]

def analyze_session(session_dir: Path) -> SessionHealthReport:
    """Детальный парсинг и аудит логов одной сессии."""
    report = SessionHealthReport(
        session_id=session_dir.name,
        session_dir=session_dir
    )

    system_log = session_dir / "system.log"
    conversations_jsonl = session_dir / "conversations.jsonl"

    if system_log.exists():
        with open(system_log, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                # 1. Подсчет запросов
                if "Отправка запроса в n8n:" in line:
                    report.total_requests += 1

                # 2. Замеры времени
                dur_match = re.search(r"Duration\s+(\d+(?:\.\d+)?)\s*ms", line)
                if dur_match:
                    report.durations_ms.append(float(dur_match.group(1)))

                # 3. Парсинг таймштампа
                ts_match = re.match(r"^(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})", line)
                ts = ts_match.group(1) if ts_match else "Unknown time"

                # 4. Классификация ошибок
                if "ERROR" in line:
                    if "Сервер n8n ответил кодом 500" in line or "кодом 50" in line:
                        report.incidents.append(Incident(
                            timestamp=ts,
                            severity="CRITICAL",
                            category="N8N_HTTP_500",
                            message="n8n вернул Internal Server Error (500)",
                            details=line.strip()
                        ))
                    elif "NoneType" in line:
                        report.incidents.append(Incident(
                            timestamp=ts,
                            severity="CRITICAL",
                            category="PYTHON_NONETYPE_CRASH",
                            message="Ошибка 'NoneType' object has no attribute 'get' при чтении ответа n8n",
                            details=line.strip()
                        ))
                    elif "TelegramNetworkError" in line:
                        report.incidents.append(Incident(
                            timestamp=ts,
                            severity="WARNING",
                            category="TELEGRAM_TIMEOUT",
                            message="Таймаут соединения с серверами Telegram API",
                            details=line.strip()
                        ))
                    elif "re.PatternError" in line:
                        report.incidents.append(Incident(
                            timestamp=ts,
                            severity="CRITICAL",
                            category="REGEX_CRASH",
                            message="Ошибка компиляции регулярного выражения",
                            details=line.strip()
                        ))
                    else:
                        report.incidents.append(Incident(
                            timestamp=ts,
                            severity="CRITICAL",
                            category="UNHANDLED_ERROR",
                            message=line.strip()
                        ))

                elif "WARNING" in line:
                    if "Не удалось загрузить медиа" in line:
                        report.incidents.append(Incident(
                            timestamp=ts,
                            severity="WARNING",
                            category="MEDIA_DOWNLOAD_FAIL",
                            message="Не удалось скачать вложение из Confluence",
                            details=line.strip()
                        ))
                    elif "Ошибка форматирования/парсинга HTML" in line:
                        report.incidents.append(Incident(
                            timestamp=ts,
                            severity="WARNING",
                            category="HTML_PARSE_FALLBACK",
                            message="Сбой парсинга HTML в Telegram, отправлен fallback чистый текст",
                            details=line.strip()
                        ))

    # Расчет успешно обработанных запросов
    critical_errors = sum(1 for inc in report.incidents if inc.severity == "CRITICAL")
    report.successful_requests = max(0, report.total_requests - critical_errors)

    # 5. Проверка conversations.jsonl
    if conversations_jsonl.exists():
        with open(conversations_jsonl, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                    report.conversations_count += 1
                    answer = data.get("answer", "")
                    if "🛑" in answer or "📵" in answer:
                        report.incidents.append(Incident(
                            timestamp=data.get("timestamp", "Unknown"),
                            severity="WARNING",
                            category="STUB_ANSWER_RETURNED",
                            message="Пользователю возвращена системная заглушка вместо ответа ИИ",
                            details=answer
                        ))
                except Exception:
                    pass

    return report

def print_health_report(report: SessionHealthReport):
    """Выводит красивый консольный отчет о состоянии сессии."""
    print("\n" + "=" * 75)
    print(f"🩺 ОТЧЕТ О ЗДОРОВЬЕ БОТА: {report.session_id}")
    print(f"📁 Путь: {report.session_dir}")
    print(f"Статус системы: {report.status}")
    print("-" * 75)
    print(f"📊 Запросов всего:       {report.total_requests}")
    print(f"✅ Успешных ответов:     {report.successful_requests} ({report.success_rate}%)")
    print(f"💬 Записей в истории:    {report.conversations_count}")
    print(f"⏱ Среднее время ответа: {report.avg_latency_sec} сек (макс: {report.max_latency_sec} сек)")
    print(f"⚠️ Всего инцидентов:     {len(report.incidents)}")
    print("=" * 75)

    if report.incidents:
        print("\n📋 СПИСОК ОБНАРУЖЕННЫХ ИНЦИДЕНТОВ:")
        for idx, inc in enumerate(report.incidents, 1):
            icon = "🔴" if inc.severity == "CRITICAL" else "🟡"
            print(f"\n{idx}. {icon} [{inc.severity}] [{inc.category}] {inc.timestamp}")
            print(f"   Сообщение: {inc.message}")
            if inc.details and inc.details != inc.message:
                print(f"   Детали:    {inc.details[:120]}...")
    else:
        print("\n✨ Критических ошибок и предупреждений не обнаружено. Система работает стабильно.")
    print("=" * 75 + "\n")

def main():
    parser = argparse.ArgumentParser(description="Анализатор логов и монитор здоровья AxonBot")
    parser.add_argument("--all", action="store_true", help="Проанализировать последние 5 сессий")
    args = parser.parse_args()

    sessions = find_sessions(limit=5 if args.all else 1)
    if not sessions:
        print("❌ Сессии логов не найдены в", LOGS_ROOT)
        return

    for session_dir in sessions:
        report = analyze_session(session_dir)
        print_health_report(report)

if __name__ == "__main__":
    main()
