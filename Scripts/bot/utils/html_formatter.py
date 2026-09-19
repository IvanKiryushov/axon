import re
import html

def markdown_to_telegram_html(text: str) -> str:
    """
    Безопасно преобразует Markdown и смешанную разметку в валидный HTML для Telegram Bot API.
    Обрабатывает: code blocks, inline code, bold, italic, links.
    Сохраняет уже существующие валидные HTML теги (<b>, <a>, <i>, <code>, <pre>) и экранирует спецсимволы.
    """
    if not text:
        return ""

    # 1. Сохраняем уже существующие валидные HTML теги во временные плейсхолдеры
    valid_tags = []
    def save_valid_tag(match):
        idx = len(valid_tags)
        valid_tags.append(match.group(0))
        return f"@@@VALID_HTML_{idx}@@@"

    text = re.sub(r'</?(?:b|i|u|s|code|pre|a(?:\s+href="[^"]*")?|tg-spoiler)>', save_valid_tag, text, flags=re.IGNORECASE)

    # 2. Сохраняем блоки кода ```...``` во временные плейсхолдеры
    code_blocks = []
    def save_code_block(match):
        code = match.group(1)
        escaped_code = html.escape(code)
        idx = len(code_blocks)
        code_blocks.append(f"<pre><code>{escaped_code}</code></pre>")
        return f"@@@CODE_BLOCK_{idx}@@@"

    text = re.sub(r'```(?:[a-zA-Z0-9_-]+)?\n?([\s\S]*?)```', save_code_block, text)

    # 3. Сохраняем инлайн-код `...` во временные плейсхолдеры
    inline_codes = []
    def save_inline_code(match):
        code = match.group(1)
        escaped_code = html.escape(code)
        idx = len(inline_codes)
        inline_codes.append(f"<code>{escaped_code}</code>")
        return f"@@@INLINE_CODE_{idx}@@@"

    text = re.sub(r'`([^`\n]+)`', save_inline_code, text)

    # 3.1. Защищаем сноски со звездочками вида: "- * - " или "- ** - "
    footnote_tokens = []
    def save_footnote(match):
        idx = len(footnote_tokens)
        footnote_tokens.append(match.group(2))
        return f"{match.group(1)}@@@FOOTNOTE_{idx}@@@{match.group(3)}"

    text = re.sub(r'(?m)^(\s*[-•*]\s*)(\*{1,2})(\s*[-–—]\s*)', save_footnote, text)

    # 3.2. Автоматически защищаем технические маски именования со звездочками и подчеркиваниями
    def protect_mask(match):
        raw = match.group(0).strip()
        # Если первое слово ошибочно обрамлено в **Слово**_ -> исправляем на Слово**_
        clean = re.sub(r'^\*\*([А-Яа-яA-Za-z0-9\s]+)\*\*_', r'\1**_', raw)
        idx = len(inline_codes)
        inline_codes.append(f"<code>{html.escape(clean)}</code>")
        return f"@@@INLINE_CODE_{idx}@@@"

    # Паттерн маски: строка содержит _ и хотя бы одну сноску * или **
    text = re.sub(r'(?m)^(?=[^\n]*_[^\n]*\*)([А-Яа-яA-Za-z0-9\s\*_]+)$', protect_mask, text)

    # 4. Экранируем оставшийся текст (&, <, >)
    text = html.escape(text)

    # 5. Преобразуем ссылки Markdown [текст](url) -> <a href="url">текст</a>
    def replace_link(match):
        label = match.group(1)
        url = match.group(2)
        clean_url = url.replace("&amp;", "&")
        return f'<a href="{clean_url}">{label}</a>'

    text = re.sub(r'\[([^\]]+)\]\((https?://[^\s\)]+)\)', replace_link, text)

    # 6. Жирный текст **текст** или __текст__ (строго в пределах строки без захвата других звездочек)
    text = re.sub(r'\*\*([^\*\n]+?)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'__([^_\n]+?)__', r'<b>\1</b>', text)

    # 7. Курсив *текст* или _текст_ (не затрагивая слова с подчёркиваниями вроде ADSK_Позиция)
    text = re.sub(r'(?<!\w)\*([^\*\n]+?)\*(?!\w)', r'<i>\1</i>', text)
    text = re.sub(r'(?<!\w)_([^_\n]+?)_(?!\w)', r'<i>\1</i>', text)

    # 8. Заголовки Markdown (# Заголовок, ## Заголовок) -> <b>Заголовок</b>
    text = re.sub(r'^(?:#{1,6})\s+(.+)$', r'<b>\1</b>', text, flags=re.MULTILINE)

    # 8.1. Возвращаем защищенные сноски со звездочками
    for idx, token in enumerate(footnote_tokens):
        text = text.replace(f"@@@FOOTNOTE_{idx}@@@", token)

    # 9. Возвращаем блоки кода и инлайн код
    for idx, block in enumerate(code_blocks):
        text = text.replace(f"@@@CODE_BLOCK_{idx}@@@", block)

    for idx, inline in enumerate(inline_codes):
        text = text.replace(f"@@@INLINE_CODE_{idx}@@@", inline)

    # 10. Возвращаем сохраненные валидные HTML теги
    for idx, tag in enumerate(valid_tags):
        text = text.replace(f"@@@VALID_HTML_{idx}@@@", tag)

    return text
