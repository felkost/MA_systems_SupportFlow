import { useEffect, useState } from 'react'
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

function App() {
  const [messages, setMessages] = useState(initialMessages)
  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)
  const [allowRealSend, setAllowRealSend] = useState(null)
  const [theme, setTheme] = useState(initialTheme)

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
          category: data.category,
          elapsedMs: data.elapsed_ms,
        },
      ])
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
  )
}

export default App
