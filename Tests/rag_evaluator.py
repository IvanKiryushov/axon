import os
import re
import json
import asyncio
from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if (PROJECT_ROOT / ".env").exists():
    load_dotenv(PROJECT_ROOT / ".env")
else:
    load_dotenv()

@dataclass
class JudgeVerdict:
    is_correct: bool
    confidence: float
    verdict_reason: str

JUDGE_SYSTEM_PROMPT = """Ты — независимый ведущий BIM-эксперт и арбитр качества RAG-систем по разделу КР (Конструктивные решения) в Autodesk Revit.
Твоя задача: объективно оценить, ответил ли RAG-бот на вопрос пользователя в соответствии с инженерным смыслом и заданной рубрикой.

Правила судейства:
1. Оценивай СМЫСЛ и ФАКТИЧЕСКУЮ ТОЧНОСТЬ, а не точные формулировки слов.
2. База знаний ограничена регламентами компании по разделу КР (BIM-стандарт), а не всеми функциями Autodesk Revit. Не штрафуй бота за отказ, если запрашиваемый функционал объективно отсутствует в базе знаний или не предусмотрен рубрикой. Не придумывай внешних требований сверх предоставленной рубрики: если в стандарте компании или рубрике зафиксировано конкретное правило или допущение, оценивай строго по соответствию этому правилу.
3. Если бот дает корректное проектное решение (например, имя типоразмера "В25_200" по стандарту компании "Материал_Толщина" для плиты 200 мм В25), это СЧИТАЕТСЯ ВЕРНЫМ, даже если рубрика описывает общую маску.
4. Разрешай естественные синонимы и морфологические формы (подрезка торцов = торец, сечение = видимость тела арматуры, 30_КР = 40_КЖ для планов опалубки).
5. Регламентные запреты: Если рубрика описывает запрещенное действие (например, запрет редактирования профиля монолитной стены или запрет создания уклона через субэлементы), и бот явно сообщает о запрете и указывает корректное проектное решение/альтернативу, это СЧИТАЕТСЯ ВЕРНЫМ (is_correct=true).
6. Если вопрос тестовый на Out-of-Scope (АР, ВК, ЭОМ, варез) и бот выдал вежливый регламентный отказ ("В базе регламентов КР нет информации..."), это ВЕРНЫЙ ответ (is_correct=true).
7. Если бот выдал регламентный отказ на вопрос, на который в базе КР ЕСТЬ ответ согласно рубрике, это НЕВЕРНЫЙ ответ (is_correct=false).
8. Противоречивый ответ (Dual Output): Если бот дал содержательную правильную инженерную инструкцию по рубрике, но в самом конце приписал дежурную фразу "В базе знаний регламентов КР нет информации по данному вопросу" (дефект дублирования вывода):
   - Оценивай содержательную техническую часть ответа: если содержательная часть верна, ставь is_correct=true с confidence=0.75 и обязательно укажи в verdict_reason замечание о противоречивом дежурном отказе в конце.
9. Двуязычный режим (Cross-Lingual): Если вопрос задан на английском языке, бот обязан отвечать на английском языке, сохраняя имена параметров (`ADSK_...`, `!BIM_CORP_...`, `срубаемаяЧасть`), шифры семейств (`222_Дефшов`, `205_Лобовая балка`), шаблоны вида и рабочие наборы строго на кириллице (Strict Cyrillic Verbatim). Оценивай смысловую точность английского ответа по отношению к рубрике так же строго, как и русского.

Формат ответа — строго JSON:
{
  "is_correct": true | false,
  "confidence": 0.0-1.0,
  "verdict_reason": "Краткое (1-2 предложения) инженерное обоснование вердикта"
}
"""

class RAGEvaluator:
    def __init__(self, model: str = "gemini-3.5-flash-lite"):
        self.model = model
        self.api_key = os.getenv("GEMINI_API_KEY")
        self._client = None
        # Рабочий пул ТОЛЬКО из Lite-моделей (лимит 500 RPD и 15 RPM у каждой)
        self._models_pool = ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite"]
        if self.model not in self._models_pool:
            self._models_pool.insert(0, self.model)
        self._model_idx = 0
        self._lock = asyncio.Lock()
        if self.api_key:
            try:
                from google import genai
                self._client = genai.Client(api_key=self.api_key)
            except Exception as e:
                print(f"[RAGEvaluator] Ошибка инициализации google.genai: {e}")

    @property
    def is_available(self) -> bool:
        return self._client is not None

    async def evaluate(
        self,
        question: str,
        response_text: str,
        expected_rubric: str,
        is_refusal_test: bool = False
    ) -> JudgeVerdict:
        if not self.is_available:
            return JudgeVerdict(
                is_correct=False,
                confidence=0.0,
                verdict_reason="LLM-судья недоступен (отсутствует GEMINI_API_KEY или SDK google.genai)"
            )

        if not response_text or not response_text.strip():
            return JudgeVerdict(
                is_correct=False,
                confidence=1.0,
                verdict_reason="Ответ бота пустой"
            )

        # Не тратим квоту судьи на Out-of-Scope тесты отказов (они проверяются детерминированно)
        if is_refusal_test:
            refusal_markers = [
                "нет информации", "не относится", "не входит", "обратитесь к bim", "уточните запрос",
                "no information", "no guideline", "not covered", "not described", "bim department",
                "out of scope", "there is no"
            ]
            resp_lower = response_text.lower()
            has_refusal = any(m in resp_lower for m in refusal_markers)
            return JudgeVerdict(
                is_correct=has_refusal,
                confidence=1.0,
                verdict_reason="Детерминированная проверка регламентного отказа" if has_refusal else "Отказ не обнаружен в ответе на Out-of-Scope вопрос"
            )

        user_prompt = f"""Вопрос пользователя:
«{question}»

Инженерная рубрика / ожидаемое решение:
«{expected_rubric}»

Фактический ответ бота:
«{response_text}»

Оцени фактическую корректность ответа бота по отношению к вопросу и рубрике. Верни строго JSON.
"""

        # Round-robin ротация для балансировки нагрузки между изолированными счетчиками RPM
        async with self._lock:
            start_idx = self._model_idx
            self._model_idx = (self._model_idx + 1) % len(self._models_pool)

        models_to_try = self._models_pool[start_idx:] + self._models_pool[:start_idx]
        last_error = ""

        from google.genai import types
        config = types.GenerateContentConfig(
            system_instruction=JUDGE_SYSTEM_PROMPT,
            response_mime_type="application/json",
            temperature=0.0,
        )

        for attempt, model_name in enumerate(models_to_try):
            try:
                resp = await self._client.aio.models.generate_content(
                    model=model_name,
                    contents=user_prompt,
                    config=config,
                )
                raw_json = resp.text.strip()
                data = json.loads(raw_json)
                return JudgeVerdict(
                    is_correct=bool(data.get("is_correct", False)),
                    confidence=float(data.get("confidence", 1.0)),
                    verdict_reason=str(data.get("verdict_reason", "")),
                )
            except Exception as e:
                last_error = str(e)
                if "429" in last_error or "RESOURCE_EXHAUSTED" in last_error:
                    # Мгновенный failover на вторую модель (у нее отдельный счетчик 15 RPM)
                    if attempt < len(models_to_try) - 1:
                        continue
                    else:
                        # Если обе модели исчерпали минутное окно, ждем 25 секунд для очистки окна
                        await asyncio.sleep(25.0)
                        try:
                            resp = await self._client.aio.models.generate_content(
                                model=models_to_try[0],
                                contents=user_prompt,
                                config=config,
                            )
                            raw_json = resp.text.strip()
                            data = json.loads(raw_json)
                            return JudgeVerdict(
                                is_correct=bool(data.get("is_correct", False)),
                                confidence=float(data.get("confidence", 1.0)),
                                verdict_reason=str(data.get("verdict_reason", "")),
                            )
                        except Exception as e2:
                            last_error = str(e2)
                            break
                else:
                    break

        return JudgeVerdict(
            is_correct=None,
            confidence=0.0,
            verdict_reason=f"API_FALLBACK: {last_error[:100]}"
        )
