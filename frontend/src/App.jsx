import { useCallback, useEffect, useState } from 'react'
import './App.css'

const API_URL = import.meta.env.VITE_API_URL ?? 'http://localhost:8000'
const SESSION_KEY = 'supportflow_session_id'
const THEME_KEY = 'supportflow_theme'
const MESSAGES_KEY = 'supportflow_messages'

// Same tab/session only (sessionStorage, not localStorage) — matches the
// existing session_id's own lifetime, so a restored chat and the
// session_id it continues always refer to the same backend conversation.
function initialMessages() {
  try {
    const stored = sessionStorage.getItem(MESSAGES_KEY)
    return stored ? JSON.parse(stored) : []
  } catch {
    return []
  }
}

function getSessionId() {
  let sessionId = sessionStorage.getItem(SESSION_KEY)
  if (!sessionId) {
    sessionId = crypto.randomUUID()
    sessionStorage.setItem(SESSION_KEY, sessionId)
  }
  return sessionId
}

// A new session_id, not just a cleared message list — Escalation's
// dedup/cap store is scoped per session_id, so "start over" means the
// backend sees a genuinely new conversation too, not the same one with
// an emptied browser-side view of it.
function resetSessionId() {
  const sessionId = crypto.randomUUID()
  sessionStorage.setItem(SESSION_KEY, sessionId)
  return sessionId
}

// Stored choice wins; otherwise follow the OS. localStorage throws in some
// privacy modes, so every access is guarded — a themed page must still render.
function initialTheme() {
  try {
    const stored = localStorage.getItem(THEME_KEY)
    if (stored === 'light' || stored === 'dark') return stored
  } catch {
    /* fall through to the system preference */
  }
  return window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

// Internal knowledge-base file paths (`internal_policy/*.md`) are an
// implementation detail, not something a customer recognises — DocsResponse
// still carries them (grounding/evaluation need every source), the chat UI
// just doesn't render them. Duplicate refs (the retriever often surfaces
// the same chunk more than once) collapse into one "ref (×N)" entry.
// A web-search ref is a full URL — often long enough to overflow the
// bubble — so it's kept as `{ref, isLink}` and rendered as a short "джерело"
// anchor instead of the raw address; a non-URL ref (a Silpo product name)
// renders as plain text.
function groupSources(sources) {
  const counts = new Map()
  for (const source of sources) {
    if (source.ref.endsWith('.md')) continue
    counts.set(source.ref, (counts.get(source.ref) ?? 0) + 1)
  }
  return [...counts.entries()].map(([ref, count]) => ({
    ref,
    count,
    isLink: ref.startsWith('http://') || ref.startsWith('https://'),
  }))
}

// Two decimals, or an em dash where the statistic genuinely does not
// exist — a single score has no spread, and an empty sample has nothing
// at all. Printing 0.00 there would read as a measured zero.
function formatScore(value) {
  return typeof value === 'number' ? value.toFixed(2) : '—'
}

// Local time, no seconds — the reader needs "was this before or after the
// last batch of chatting", not a precise instant.
function formatMoment(iso) {
  const parsed = new Date(iso)
  return Number.isNaN(parsed.getTime())
    ? iso
    : parsed.toLocaleString('uk-UA', { dateStyle: 'short', timeStyle: 'short' })
}

const UNAVAILABLE_REASONS = {
  no_batch_run_yet:
    'Пакетний прогін ще не виконувався. Запустіть ' +
    'scripts/eval_live_batch.py, щоб оцінити збережені живі запити.',
  no_experiment_configured:
    'EXPERIMENT не заданий у .env, тому живі відповіді неможливо ' +
    'відокремити від офлайн-прогонів скриптів.',
  tracing_disabled: 'Трасування вимкнено — оцінки не збираються.',
  fetch_failed: 'Не вдалося прочитати оцінки з Langfuse.',
  baseline_unavailable: 'Файл еталонного прогону недоступний.',
}

// Two metrics can share the word "relevance" and still be different
// instruments: DeepEval's ratio of statements judged relevant clusters
// near its ceiling, while a single holistic 0–1 rating spreads much
// wider. Naming the method next to the number is what stops the two
// being read as the same measurement.
const METRIC_METHODS = {
  'supportflow-answer-relevance':
    'цілісна оцінка 0–1 одним викликом LLM: наскільки відповідь ' +
    'Docs/Web Search стосується запиту',
  'supportflow-escalation-quality':
    'цілісна оцінка 0–1: наскільки якісно Escalation передає справу ' +
    'оператору',
  'Answer Relevancy': 'частка тверджень у відповіді, визнаних релевантними',
  Faithfulness: 'частка тверджень, підтверджених наданими джерелами',
  'Support Resolution Quality [GEval]':
    'авторська рубрика (3 критерії): вирішує звернення або чесно каже, ' +
    'що не може; не вигадує фактів; пропонує конкретний наступний крок. ' +
    'Застосовна до будь-якої відповіді, включно з ескалацією',
  'Route Correctness': 'збіг обраного маршруту з очікуваним (0 або 1)',
  'Privacy Safety': 'відсутність персональних даних у відповіді (0 або 1)',
  'Tool Correctness': 'збіг викликаних інструментів з очікуваними',
}

function ScoreCard({
  title,
  judge,
  source,
  selected,
  onSelect,
  note,
  action,
  onRefresh,
  refreshing,
}) {
  // `onRefresh` is no longer passed by any caller (PR #23 — the shared
  // "⏱ Авто" toggle in `sidebar-head` replaced per-card refresh for both
  // live cards), so this always renders null today. Left as a prop rather
  // than deleted: `ScoreCard` is a reusable component and a future card
  // needing its own refresh can still pass one.
  const refreshButton = onRefresh && (
    <button
      type="button"
      className="pill refresh"
      onClick={onRefresh}
      disabled={refreshing}
      title="Перечитати цю картку"
    >
      🔄 Оновити
    </button>
  )

  if (!source?.available) {
    return (
      <div className="score-card">
        <div className="score-card-head">
          <h3>{title}</h3>
          {refreshButton}
        </div>
        <p className="score-note">
          {UNAVAILABLE_REASONS[source?.reason] ?? 'Дані недоступні.'}
        </p>
        {action}
      </div>
    )
  }

  const names = Object.keys(source.metrics ?? {})
  const active = names.includes(selected) ? selected : names[0]
  const stats = source.metrics?.[active]

  return (
    <div className="score-card">
      <div className="score-card-head">
        <h3>{title}</h3>
        {refreshButton}
      </div>
      <p className="score-judge">Суддя: {judge}</p>
      <select
        className="metric-select"
        value={active ?? ''}
        onChange={(event) => onSelect(event.target.value)}
        aria-label={`Метрика — ${title}`}
      >
        {names.map((name) => (
          <option key={name} value={name}>
            {name}
          </option>
        ))}
      </select>
      {!stats?.n ? (
        <p className="score-note">
          {/* `stats` is undefined whenever `names` is empty — right after
              a prompt promotion, before any fresh traffic under the new
              version has been scored, `live_deepeval`'s filter can leave
              every metric bucket empty even though the file itself has
              cases in it. Same message either way: there is nothing to
              show yet, not a broken card. */}
          Ще немає оцінок. Поставте запитання в чаті — з увімкненим «⏱ Авто»
          картка оновиться сама; DeepEval-картку рахуйте кнопкою «▶ Оцінити».
        </p>
      ) : (
        <>
          <p className="score-mean">{formatScore(stats?.mean)}</p>
          <dl className="score-rows">
            <div>
              <dt>Запитів</dt>
              <dd>
                {source.n_cases && stats?.covers != null
                  ? `${stats.covers} з ${source.n_cases}`
                  : (stats?.n ?? 0)}
              </dd>
            </div>
            <div>
              <dt>σ (розкид)</dt>
              <dd>{formatScore(stats?.std_dev)}</dd>
            </div>
            <div>
              <dt>95% довірчий</dt>
              <dd>
                {stats?.ci
                  ? `${formatScore(stats.ci[0])} – ${formatScore(stats.ci[1])}`
                  : '—'}
              </dd>
            </div>
          </dl>
        </>
      )}
      {METRIC_METHODS[active] && (
        <p className="score-note">Як міряється: {METRIC_METHODS[active]}.</p>
      )}
      {note && <p className="score-note">{note}</p>}
      {action}
    </div>
  )
}

function App() {
  const [messages, setMessages] = useState(initialMessages)
  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)
  const [allowRealSend, setAllowRealSend] = useState(null)
  const [theme, setTheme] = useState(initialTheme)
  const [quality, setQuality] = useState(null)
  const [qualityState, setQualityState] = useState('loading')
  const [liveMetric, setLiveMetric] = useState('supportflow-answer-relevance')
  // One selector drives both DeepEval cards on purpose: they are the same
  // instrument on two populations, so showing different metrics in each
  // would invite exactly the invalid comparison the panel warns against.
  const [deepevalMetric, setDeepevalMetric] = useState('Answer Relevancy')

  useEffect(() => {
    try {
      sessionStorage.setItem(MESSAGES_KEY, JSON.stringify(messages))
    } catch {
      /* history just won't survive a reload this time — chat still works */
    }
  }, [messages])

  useEffect(() => {
    document.documentElement.dataset.theme = theme
    try {
      localStorage.setItem(THEME_KEY, theme)
    } catch {
      /* a session-only theme is still a working theme */
    }
  }, [theme])

  useEffect(() => {
    fetch(`${API_URL}/health`)
      .then((r) => r.json())
      .then((data) => setAllowRealSend(data.allow_real_send))
      .catch(() => setAllowRealSend(null))
  }, [])

  // Not refetched after each answer: the judge scores a trace
  // asynchronously, well after /chat has replied, so an automatic
  // refresh here would reliably miss the answer that triggered it. The
  // manual button is the honest control.
  const loadQuality = useCallback(async () => {
    setQualityState('loading')
    try {
      const response = await fetch(`${API_URL}/stats/quality`)
      setQuality(await response.json())
      setQualityState('ok')
    } catch {
      setQualityState('error')
    }
  }, [])

  useEffect(() => {
    loadQuality()
  }, [loadQuality])

  // Grading costs money per new case, so a single run is never automatic
  // on its own — the button says how many are pending and goes inert at
  // zero. `autoEval` below is an opt-in exception: re-running on an
  // interval is cheap when there is nothing new (the endpoint only pays
  // for genuinely unscored cases, see `eval_live_batch.py`'s own
  // trace-id matching), so a periodic call just keeps this card's count
  // from drifting far behind the always-live Langfuse card, at the
  // author's own request (2026-08-29) rather than by default.
  const [evalState, setEvalState] = useState('idle')
  const pendingCases = quality?.live_deepeval?.pending ?? 0

  const runLiveEval = useCallback(async () => {
    setEvalState('running')
    try {
      const response = await fetch(`${API_URL}/stats/eval-live`, { method: 'POST' })
      const data = await response.json()
      setQuality((prev) => ({ ...prev, live_deepeval: data.stats }))
      setEvalState(data.ok ? 'idle' : 'error')
    } catch {
      setEvalState('error')
    }
  }, [])

  const AUTO_EVAL_INTERVAL_MS = 30 * 1000
  const [autoEval, setAutoEval] = useState(false)

  // Shared with the Langfuse card, not DeepEval-only: `loadQuality` is
  // a plain re-read (cheap, no scoring), `runLiveEval` is the one that
  // actually pays for new judge calls — running both together keeps
  // both cards moving together instead of leaving Langfuse's stuck on
  // whatever it showed when the page loaded (2026-08-29, at the
  // author's own request — this replaced two separate controls,
  // Langfuse's own "🔄 Оновити" and DeepEval's auto toggle).
  const refreshBoth = useCallback(() => {
    loadQuality()
    runLiveEval()
  }, [loadQuality, runLiveEval])

  useEffect(() => {
    if (!autoEval) return undefined
    // Fire once immediately on enabling — otherwise turning this on gives
    // no visible feedback for up to a full interval, and reads as "did
    // nothing" (observed live, 2026-08-29).
    refreshBoth()
    const id = setInterval(refreshBoth, AUTO_EVAL_INTERVAL_MS)
    return () => clearInterval(id)
  }, [autoEval, refreshBoth])

  function clearChat() {
    setMessages([])
    try {
      sessionStorage.removeItem(MESSAGES_KEY)
    } catch {
      /* messages state is already cleared; storage just won't persist that */
    }
    resetSessionId()
  }

  async function toggleRealSend() {
    const next = !allowRealSend
    try {
      const response = await fetch(`${API_URL}/admin/real-send`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: next }),
      })
      const data = await response.json()
      setAllowRealSend(data.allow_real_send)
    } catch {
      // leave the toggle at its last known state on failure
    }
  }

  async function sendMessage(event) {
    event.preventDefault()
    const text = input.trim()
    if (!text || sending) return

    setMessages((prev) => [...prev, { role: 'customer', text }])
    setInput('')
    setSending(true)

    try {
      const response = await fetch(`${API_URL}/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text, session_id: getSessionId() }),
      })
      if (!response.ok) {
        throw new Error('request failed')
      }
      const data = await response.json()
      setMessages((prev) => [
        ...prev,
        {
          role: 'assistant',
          text: data.answer,
          sources: data.sources,
          escalated: data.escalated,
          reportWritten: data.report_written,
          telegramSent: data.telegram_sent,
          escalationCapped: data.escalation_capped,
          category: data.category,
          elapsedMs: data.elapsed_ms,
        },
      ])
      // The answer just became a recorded case, so the "N pending"
      // button would otherwise stay inert until someone pressed refresh
      // — the count it shows would be one request out of date.
      loadQuality()
      // Safe to score right here, unlike a Langfuse-card refresh (see
      // `loadQuality`'s own comment above): `live_case_log.append_case`
      // already wrote this case to disk before `/chat` responded, so
      // there is no async lag to race against — the case is there to be
      // scored the instant this line runs. Gated on `autoEval` so this
      // still costs money only when the author opted in, never by
      // default (2026-08-29, at the author's own request).
      if (autoEval) {
        runLiveEval()
      }
    } catch {
      setMessages((prev) => [
        ...prev,
        {
          role: 'error',
          text:
            'Не вдалося надіслати повідомлення. Якщо попередній запит ще ' +
            'обробляється, дочекайтеся відповіді — агенти обробляють по ' +
            'одному запиту за раз. Деталі помилки — у терміналі API.',
        },
      ])
    } finally {
      setSending(false)
    }
  }

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark">Сільпо</span>
          <span className="brand-sub">SupportFlow</span>
        </div>
        <div className="topbar-actions">
          <button
            type="button"
            className="pill"
            onClick={clearChat}
            disabled={messages.length === 0}
            title="Очистити історію розмови і почати заново"
          >
            🗑️ Нова розмова
          </button>
          {allowRealSend !== null && (
            <button
              type="button"
              className={`pill ${allowRealSend ? 'on' : ''}`}
              onClick={toggleRealSend}
              title="Реальна відправка ескалацій у Telegram"
            >
              {allowRealSend ? '📨 Telegram: реально' : '📪 Telegram: симуляція'}
            </button>
          )}
          <button
            type="button"
            className="pill"
            onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
            title="Перемкнути світлу/темну тему"
          >
            {theme === 'dark' ? '☀️ Світла' : '🌙 Темна'}
          </button>
        </div>
      </header>

      <div className="workspace">
        <aside className="sidebar">
          <div className="sidebar-head">
            <h2>Оцінка якості</h2>
            <span
              className={`status-dot ${qualityState}`}
              title={
                qualityState === 'loading'
                  ? 'Завантаження…'
                  : qualityState === 'ok'
                    ? 'Дані завантажено'
                    : 'Помилка завантаження'
              }
            >
              {qualityState === 'loading' ? '◐' : qualityState === 'ok' ? '✓' : '✕'}
            </span>
            <button
              type="button"
              className={`pill auto-eval${autoEval ? ' on' : ''}`}
              onClick={() => setAutoEval((prev) => !prev)}
              title={
                autoEval
                  ? 'Автооновлення увімкнено: обидві картки — після кожного повідомлення й кожні 30с. Натисніть, щоб вимкнути'
                  : 'Автоматично оновлювати обидві картки: після кожного повідомлення й кожні 30 секунд, поки сторінка відкрита'
              }
            >
              {autoEval ? '⏱ Авто: увімк' : '⏱ Авто: вимк'}
            </button>
          </div>

          <ScoreCard
            title="Реальні запити — Langfuse"
            judge={quality?.live?.judge}
            source={quality?.live}
            selected={liveMetric}
            onSelect={setLiveMetric}
            // No separate "🔄 Оновити" — the shared "⏱ Авто" toggle in
            // sidebar-head now covers both this card and DeepEval's, so
            // a lone refresh button here would duplicate it.
            note={
              quality?.live?.experiment
                ? `Трейси з міткою «${quality.live.experiment}».`
                : null
            }
          />

          <ScoreCard
            title="Реальні запити — DeepEval"
            judge={quality?.live_deepeval?.judge}
            source={quality?.live_deepeval}
            selected={deepevalMetric}
            onSelect={setDeepevalMetric}
            note={
              quality?.live_deepeval?.measured_at
                ? `Останній прогін: ${formatMoment(
                    quality.live_deepeval.measured_at,
                  )}, оцінено ${quality.live_deepeval.n_cases} запитів.`
                : null
            }
            action={
              <button
                type="button"
                className="pill run-eval"
                onClick={runLiveEval}
                disabled={evalState === 'running' || pendingCases === 0}
                title={
                  pendingCases === 0
                    ? 'Немає нових запитів — оцінювати нічого'
                    : 'Оцінює лише нові запити; уже оцінені не перераховуються'
                }
              >
                {evalState === 'running'
                  ? '⏳ Оцінюю…'
                  : pendingCases === 0
                    ? '✓ Усі оцінені'
                    : `▶ Оцінити ${pendingCases} нових`}
              </button>
            }
          />

          <ScoreCard
            title="Еталонний набір — DeepEval"
            judge={quality?.baseline?.judge}
            source={quality?.baseline}
            selected={deepevalMetric}
            onSelect={setDeepevalMetric}
            note="Незмінний прогін golden dataset."
          />

          <ul className="sidebar-note boxed compare">
            <li>
              ✅ <strong>Дві нижні картки можна порівнювати</strong> — одна
              метрика, один суддя, дві вибірки: реальні запити проти еталонного
              набору.
            </li>
            <li>
              У нижній картці є маршрут і інструменти, у середній немає — для
              живих запитів не існує «очікуваного» маршруту.
            </li>
          </ul>

          <ul className="sidebar-note boxed caution">
            <li>
              ⚠️ Верхню картку <strong>не порівнюйте з нижніми</strong>: там інший
              спосіб обчислення, тому різниця показує метод вимірювання, а не
              якість відповідей.
            </li>
            <li>
              Жоден суддя не звірявся з людськими оцінками — це орієнтир, а не
              істина.
            </li>
            <li>
              Коректно порівнювати: одну метрику в часі або дві нижні картки між
              собою.
            </li>
            <li>
              Бали з’являються із затримкою — суддя оцінює вже після відповіді.
              Увімкніть «⏱ Авто» або оцініть DeepEval-картку вручну.
            </li>
          </ul>
        </aside>

        <main className="chat">
        <div className="messages">
          {messages.length === 0 && (
            <div className="empty">
              <h2>Чим можемо допомогти?</h2>
              <p>
                Запитайте про товари, доставку, повернення чи бонуси — відповідь
                спирається на базу знань «Сільпо» та каталог товарів.
              </p>
            </div>
          )}
          {messages.map((message, index) => (
            <div key={index} className={`message ${message.role}`}>
              <p>{message.text}</p>
              {message.escalated && (
                <div className="ops">
                  <p className="escalated">Передано людині-оператору.</p>
                  <p className="ops-badges">
                    <span className={`badge ${message.reportWritten ? 'ok' : ''}`}>
                      {message.reportWritten ? '✓ Звіт записано' : '✕ Звіт не записано'}
                    </span>
                    <span className={`badge ${message.telegramSent ? 'ok' : ''}`}>
                      {message.telegramSent
                        ? '✓ Надіслано в Telegram'
                        : message.escalationCapped
                          ? '⚠ Ліміт відправок вичерпано'
                          : '— Telegram не надсилався'}
                    </span>
                  </p>
                </div>
              )}
              {message.sources?.length > 0 && groupSources(message.sources).length > 0 && (
                <p className="sources">
                  Джерела:{' '}
                  {groupSources(message.sources).map((s, i, arr) => (
                    <span key={s.ref}>
                      {s.isLink ? (
                        <a href={s.ref} target="_blank" rel="noreferrer">
                          джерело{s.count > 1 ? ` (×${s.count})` : ''}
                        </a>
                      ) : (
                        <>
                          {s.ref}
                          {s.count > 1 ? ` (×${s.count})` : ''}
                        </>
                      )}
                      {i < arr.length - 1 ? ', ' : ''}
                    </span>
                  ))}
                </p>
              )}
              {message.role === 'assistant' && (
                <p className="meta">
                  {message.category && <>Категорія: {message.category} · </>}
                  {Number.isFinite(message.elapsedMs) && (
                    <>Час обробки: {(message.elapsedMs / 1000).toFixed(1)} с</>
                  )}
                </p>
              )}
            </div>
          ))}
          {sending && <div className="message assistant pending">…</div>}
        </div>

        <form onSubmit={sendMessage} className="composer">
          <input
            value={input}
            onChange={(event) => setInput(event.target.value)}
            placeholder="Напишіть ваше запитання…"
            disabled={sending}
          />
          <button type="submit" disabled={sending || !input.trim()}>
            Надіслати
          </button>
        </form>
        </main>
      </div>
    </div>
  )
}

export default App
