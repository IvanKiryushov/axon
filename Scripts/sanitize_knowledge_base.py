import os
import sys
import re
import glob
import difflib
import argparse
from pathlib import Path

# Настройка UTF-8 для Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

PROJECT_ROOT = Path(__file__).resolve().parent.parent
KB_DIR = PROJECT_ROOT / "Data" / "Sanitized_KR"
OBSOLETE_FILE = KB_DIR / "Перевод проекта на DOGMA_КлассБетона в ДЕ_7442936.md"

# Словарь детерминированных замен (Surgical Matrix)
REPLACEMENTS = [
    # 1. PII: ФИО авторов и разработчиков
    (re.compile(r'\bКаменцев\s+Виталий\b', re.IGNORECASE), "Ведущий BIM-разработчик"),
    (re.compile(r'Login:levan\.kanashyan@gmail\.comPassword:852654levan', re.IGNORECASE), "[Доступ по корпоративной учетной записи]"),
    (re.compile(r'\blevan\.kanashyan@gmail\.com\b', re.IGNORECASE), "engineer@corp.internal"),

    # 2. Реальные шифры проектов и объектов
    (re.compile(r'\b02_КР#1001_П_ЖД_УБ01_С01_КР\b', re.IGNORECASE), "00_КР#0000_Р_ЖД_ТИПОВОЙ_ОБЪЕКТ"),
    (re.compile(r'\b0[12]_[АК]Р#1001_П_ЖД_УБ0[16]_С0[01]_[АК]Р\b', re.IGNORECASE), "00_КР#0000_Р_ЖД_ТИПОВОЙ_ОБЪЕКТ"),
    (re.compile(r'\b1001_ПУШ-2\b', re.IGNORECASE), "ТИПОВОЙ_ОБЪЕКТ"),
    (re.compile(r'\b1001_П_ЖД_УБ0[16]_С0[01]_[АК]Р\b', re.IGNORECASE), "0000_Р_ЖД_ОБЪЕКТ"),

    # 3. Корпоративные сетевые пути и домены
    (re.compile(r'\\\\dp\.1BIM_CORP\.ru\\projects_dp\\', re.IGNORECASE), r"\\\\corp.internal\\share\\"),
    (re.compile(r'\\\[dp\.1BIM_CORP\.ru\\\]\(http://dp\.1BIM_CORP\.ru\)\\projects_dp\\', re.IGNORECASE), r"\\\\corp.internal\\share\\"),
    (re.compile(r'https?://apilc\.1BIM_CORP\.ru', re.IGNORECASE), "https://confluence.corp.internal"),
    (re.compile(r'https?://sd\.BIM_CORP\.ru', re.IGNORECASE), "https://support.corp.internal"),

    # 4. Jira тикеты техподдержки
    (re.compile(r'BIMSUP-250_Перевод КлассБетона в ДЕ', re.IGNORECASE), "TASK-101_Перевод КлассБетона в ДЕ"),
    (re.compile(r'BIMSUP-988_261_Распределения', re.IGNORECASE), "TASK-102_261_Распределения"),
    (re.compile(r'\bBIMSUP-(\d+)\b', re.IGNORECASE), r"TASK-\1"),
]

def sanitize_content(content: str) -> tuple[str, int]:
    """Применяет замены к содержимому и возвращает (новый_текст, число_замен)"""
    total_replacements = 0
    for pattern, repl in REPLACEMENTS:
        new_content, count = pattern.subn(repl, content)
        if count > 0:
            total_replacements += count
            content = new_content
    return content, total_replacements

def audit_kb() -> dict:
    """Глубокий аудит всей директории KB на остаточные токены"""
    findings = {}
    md_files = sorted(list(KB_DIR.glob("*.md")))

    for fpath in md_files:
        with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()

        file_issues = []
        for pat, _ in REPLACEMENTS:
            matches = pat.findall(content)
            if matches:
                file_issues.extend(matches)

        if "DOGMA" in fpath.name or "DOGMA" in content:
            file_issues.append("DOGMA mention")

        if file_issues:
            findings[fpath.name] = file_issues

    return findings

def print_diff(original: str, modified: str, filename: str):
    """Выводит цветной unified diff"""
    orig_lines = original.splitlines(keepends=True)
    mod_lines = modified.splitlines(keepends=True)
    diff = list(difflib.unified_diff(orig_lines, mod_lines, fromfile=f"a/{filename}", tofile=f"b/{filename}"))
    if not diff:
        print(f"No changes needed for: {filename}")
        return

    for line in diff:
        if line.startswith('+') and not line.startswith('+++'):
            print(f"\033[92m{line.rstrip()}\033[0m")
        elif line.startswith('-') and not line.startswith('---'):
            print(f"\033[91m{line.rstrip()}\033[0m")
        elif line.startswith('^') or line.startswith('@'):
            print(f"\033[96m{line.rstrip()}\033[0m")
        else:
            print(line.rstrip())

def main():
    parser = argparse.ArgumentParser(description="AxonBot Knowledge Base Text Sanitizer")
    parser.add_argument("--audit", action="store_true", help="Audit all KB files for sensitive entities")
    parser.add_argument("--file", type=str, help="Process a single file")
    parser.add_argument("--dry-run", action="store_true", help="Show diff without writing to disk")
    parser.add_argument("--apply", action="store_true", help="Apply replacements to files")
    parser.add_argument("--all", action="store_true", help="Apply to all files in Data/Sanitized_KR")
    args = parser.parse_args()

    if args.audit:
        print("=== AUDIT: Scanning Data/Sanitized_KR/ for Sensitive Entities ===")
        findings = audit_kb()
        if not findings:
            print("✅ All files clean! 0 corporate entities found.")
        else:
            print(f"⚠️ Found {len(findings)} files with sensitive entities:")
            for fname, issues in findings.items():
                print(f"  [{fname}] -> {len(issues)} matches: {issues[:3]}")
        return

    if args.file:
        fpath = Path(args.file)
        if not fpath.is_absolute():
            fpath = PROJECT_ROOT / fpath
        if not fpath.exists():
            print(f"Error: File not found: {fpath}")
            return

        with open(fpath, "r", encoding="utf-8") as f:
            orig = f.read()

        new_text, count = sanitize_content(orig)
        print(f"\nProcessing: {fpath.name} ({count} replacements found)")
        print_diff(orig, new_text, fpath.name)

        if args.apply and count > 0:
            with open(fpath, "w", encoding="utf-8") as f:
                f.write(new_text)
            print(f"💾 Applied changes to: {fpath.name}")
        return

    if args.apply and args.all:
        print("=== APPLY: Batch sanitizing all files in Data/Sanitized_KR/ ===")
        md_files = sorted(list(KB_DIR.glob("*.md")))
        total_modified = 0

        for fpath in md_files:
            if fpath == OBSOLETE_FILE:
                continue
            with open(fpath, "r", encoding="utf-8") as f:
                orig = f.read()
            new_text, count = sanitize_content(orig)
            if count > 0:
                with open(fpath, "w", encoding="utf-8") as f:
                    f.write(new_text)
                total_modified += 1
                print(f"  [Modified] {fpath.name} ({count} replacements)")

        # Удаляем устаревший дубликат
        if OBSOLETE_FILE.exists():
            OBSOLETE_FILE.unlink()
            print(f"🗑️ [Deleted] Obsolete un-sanitized duplicate: {OBSOLETE_FILE.name}")

        print(f"\nDone! Modified {total_modified} files.")
        return

    parser.print_help()

if __name__ == "__main__":
    main()
