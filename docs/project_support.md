# Система підтримки клієнтів

## Опис

Мультиагентна система для обробки запитів клієнтів. Router класифікує запит і направляє до спеціалізованого агента: один працює з внутрішньою документацією, інший шукає в інтернеті, третій ескалює на людину через месенджер.

## Архітектурний патерн

**Основний патерн:** Routing (Anthropic) — Router класифікує запит і направляє до спеціалізованого агента. Кожен агент має свій набір інструментів, оптимізований під тип запиту.

**Тип взаємодії:** ієрархічна — Router як supervisor приймає рішення, спеціалізовані агенти виконують. Ключовий виклик: якість класифікації визначає якість всієї системи.

## Агенти та мінімальні інструменти

**Router**
- Роль: класифікує запит за категорією та терміновістю, направляє до відповідного агента
- Інструменти: LLM structured output (ClassificationOutput)

**Docs Agent**
- Роль: обробляє запити по продукту (тарифи, функціонал, політика повернень, FAQ), відповідає на основі внутрішньої бази знань
- Інструменти: RAG (семантичний пошук по KB)

**Web Search Agent**
- Роль: обробляє загальні/технічні запити, відповіді на які відсутні в KB (інтеграції, сумісність, зовнішні сервіси)
- Інструменти: DuckDuckGo Search (пошук у вебі)

**Escalation Agent**
- Роль: обробляє критичні запити та випадки, де автоматична відповідь неможлива, передає контекст людині-оператору
- Інструменти: Slack/Telegram API (нотифікація оператора), File System tool (збереження escalation report)

### Навіщо кожен інструмент (real-world motivation)

1. **LLM structured output (Router)**: Router класифікує запит за категорією (`product`, `general`, `critical`) та терміновістю (`low`, `medium`, `critical`). `category` як `Literal` — Router не може вигадати нову категорію, тільки обрати з визначених.
2. **RAG (Docs Agent)**: Docs Agent шукає по внутрішній базі знань: FAQ, документація продукту, тарифи, політика повернень. RAG дозволяє семантичний пошук — клієнт може запитати «чому мені списали гроші?», а система знайде FAQ про автоматичне продовження підписки, навіть якщо точних слів немає.
3. **DuckDuckGo Search (Web Search Agent)**: Для запитів, відповіді на які немає в KB — наприклад, «чи сумісний ваш API з Zapier?» або «як налаштувати інтеграцію з Google Sheets?». Окремий агент від Docs Agent, бо інструмент та стратегія пошуку принципово різні.
4. **Slack/Telegram API (Escalation Agent)**: Реальна інтеграція з месенджером — Escalation Agent надсилає повідомлення в канал підтримки або напряму оператору з контекстом запиту. Не mock, а працююча нотифікація (хоча б у тестовий канал).
5. **File System tool (Escalation Agent)**: Зберігає escalation report як файл — аудит-трейл: що запитав клієнт, яку категорію визначив Router, чому не вдалося відповісти автоматично. Використовується для аналізу gaps у KB.

## Structured Output контракти (Pydantic)

- **ClassificationOutput:** `category: Literal["product","general","critical"], urgency: Literal["low","medium","critical"], language: str`
- **DocsResponse:** `answer: str, sources: list[str], confidence: float`
- **WebSearchResponse:** `answer: str, sources: list[str], confidence: float`
- **EscalationOutput:** `summary: str, category: str, customer_message: str, attempted_resolution: str`

## Підготовка даних для RAG

Студент самостійно шукає/створює базу знань для вигаданої компанії:

- 15–25 FAQ ("питання — відповідь") у .md/.txt
- Опис продукту: тарифи, функціонал, політика повернень (2–5 сторінок)
- 5–10 прикладів звернень клієнтів з очікуваними відповідями

*Компанія може бути вигаданою (SaaS, e-commerce, банк — на вибір).*

**Приклади баз знань як можливий референс:**
- [Stripe Docs](https://docs.stripe.com/) — приклад відмінної продуктової документації
- [Notion Help Center](https://www.notion.com/help) — FAQ-формат
- [Zendesk Help Center](https://support.zendesk.com/hc/en-us) — структура support-бази

## Взаємодія між агентами

*LangGraph: StateGraph з conditional edges від Router. Router — центральний хаб (патерн Routing від Anthropic). Кожен маршрут веде до агента з принципово різним набором інструментів.*

| Від | Кому | Що передається (structured output) |
|-----|------|-----------------------------------|
| User | Router | Повідомлення клієнта (текст) |
| Router | Docs Agent | ClassificationOutput (category: product) |
| Router | Web Search Agent | ClassificationOutput (category: general) |
| Router | Escalation Agent | ClassificationOutput (category: critical) |
| Docs Agent | User | DocsResponse (confidence > threshold) |
| Docs Agent | Escalation Agent | DocsResponse (confidence < threshold) → fallback |
| Web Search Agent | User | WebSearchResponse (confidence > threshold) |
| Web Search Agent | Escalation Agent | WebSearchResponse (confidence < threshold) → fallback |
| Escalation Agent | Human (Slack/Telegram) | EscalationOutput + нотифікація в месенджер |

## Workflow (LangGraph)

1. **START → Router:** повідомлення клієнта.
2. **Router** класифікує (`with_structured_output` → ClassificationOutput).
3. **Conditional edge по category:**
   - `product` → **Docs Agent** шукає в KB через RAG.
   - `general` → **Web Search Agent** шукає в інтернеті.
   - `critical` → **Escalation Agent** одразу ескалює.
4. **Docs Agent / Web Search Agent** формують відповідь.
5. **Conditional edge:** `confidence < threshold` → **Escalation Agent** (fallback, якщо агент не впевнений у відповіді).
6. **Escalation Agent** формує EscalationOutput, зберігає report, надсилає нотифікацію в Slack/Telegram.
7. **END:** відповідь клієнту або підтвердження ескалації.

## Опціональні функції (бонус)

Список не вичерпний — можна запропонувати власні розширення:

**Додаткові інтеграції (MCP-сервери та API)**
- [Slack MCP](https://github.com/modelcontextprotocol/servers/tree/main/src/slack) — як альтернатива прямому Slack SDK.
- [Notion MCP](https://github.com/modelcontextprotocol/servers/tree/main/src/notion) — KB у Notion замість локальних файлів.
- [Google Drive MCP](https://github.com/modelcontextprotocol/servers/tree/main/src/gdrive) — збереження escalation reports у Google Docs.
- Будь-які інші існуючі MCP-сервери на власний розсуд.

## Моніторинг та тестування (обов'язково)

### Langfuse або LangSmith

Підключити один з сервісів моніторингу до всього pipeline. Кожен виклик LLM (Router, Docs Agent, Web Search Agent, Escalation Agent) має логуватися з:
- Input/output кожного агента
- Latency та кількість токенів
- Metadata: назва агента, category та urgency від Router, confidence scores, session ID

**Навіщо:** Без моніторингу неможливо зрозуміти, де система працює добре, а де — ні. Якщо 80% product-запитів ескалюються — це сигнал, що KB не покриває тему. Trace-и дозволяють побачити весь шлях запиту і знайти bottleneck.

### Тести через LLM-as-a-Judge

Написати автоматизовані тести, які оцінюють якість роботи компонентів системи. Кожен тест — це окремий LLM-виклик з критеріями оцінки та тестовими даними.

**Мінімальні тести:**

| Що тестується | Критерій | Приклад тестового сценарію |
|--------------|----------|--------------------------|
| **Router** | Правильна класифікація category | Набір з 10+ тестових запитів різних типів → Judge порівнює класифікацію Router з очікуваною (ground truth) |
| **Docs Agent** | Знаходить релевантну відповідь з KB, не hallucinate | Запит про тарифи (є в KB) → Judge перевіряє, що відповідь відповідає документації; запит поза KB → confidence має бути низьким |
| **Web Search Agent** | Знаходить релевантну інформацію, відповідь базується на знайдених джерелах | Запит про інтеграцію → Judge перевіряє, що відповідь підкріплена джерелами з пошуку |
| **Escalation Agent** | Summary повне, нотифікація містить достатньо контексту для оператора | Ескальований запит → Judge перевіряє, що EscalationOutput дає оператору контекст для продовження |

**Навіщо:** Помилка = незадоволений клієнт. Router, що класифікує product-запит як general, відправить його в веб замість KB. LLM-as-a-Judge дозволяє тестувати кожен компонент ізольовано та виявляти регресії.

## Що здавати

- Вихідний код у Git-репозиторії.
- Записане демо роботи системи (відео або GIF).
- README з діаграмою архітектури, інструкцією запуску та прикладами використання.
- Скріншоти або посилання на dashboard Langfuse/LangSmith з прикладами traces.
- Тести (LLM-as-a-Judge) з результатами запуску.
