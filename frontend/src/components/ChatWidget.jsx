import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { askChat, getChatHealth } from '../services/chat.js'
import { formatDateTime, safeHttpUrl } from '../utils/incidents.js'

const STATE_LABELS = {
  grounded: 'From the records',
  qualified: 'From the records, with limits',
  not_answerable: 'Not answered from the records',
}
const GENERAL_LABEL = 'General background, not from the records'

function newSessionId() {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID()
  return `collector-${Date.now()}-${Math.random().toString(16).slice(2)}`
}

function Citation({ citation }) {
  const url = safeHttpUrl(citation.source_url)
  return (
    <li className="chat-citation">
      <span className="chat-citation-field">{citation.field}</span>
      {citation.quote ? <blockquote>{citation.quote}</blockquote> : null}
      <span className="chat-citation-meta">
        {citation.record_kind}
        {' · '}
        {citation.method}
        {citation.when ? ` · ${formatDateTime(citation.when, { timeStyle: undefined })}` : ''}
      </span>
      <span className="chat-citation-source">
        {url ? <a href={url} target="_blank" rel="noopener noreferrer">Open source</a> : null}
        {url ? ' · ' : null}
        {citation.record_id}
      </span>
    </li>
  )
}

function ReplyBody({ reply }) {
  const general = reply.path === 'general'
  const lines = reply.statements.filter((statement) => statement.text !== reply.headline)
  const chip = general ? GENERAL_LABEL : STATE_LABELS[reply.state] || reply.state
  return (
    <div className={`chat-reply${general ? ' chat-reply-general' : ''}`}>
      <span className={`chat-state ${general ? 'chat-state-general' : `chat-state-${reply.state}`}`}>{chip}</span>
      {reply.headline ? <p className="chat-headline">{reply.headline}</p> : null}
      <ul className="chat-statements">
        {lines.map((statement, index) => (
          <li className="chat-statement" key={`${reply.reply_id}-${index}`}>
            <p>{statement.text}</p>
          </li>
        ))}
      </ul>
      {reply.citations.length ? (
        <details className="chat-citations">
          <summary>Source evidence ({reply.citations.length})</summary>
          <ul>
            {reply.citations.map((citation) => (
              <Citation citation={citation} key={citation.evidence_id} />
            ))}
          </ul>
        </details>
      ) : null}
      {reply.missing ? <p className="chat-missing">{reply.missing}</p> : null}
    </div>
  )
}

function ChatGlyph() {
  return (
    <svg className="chat-launcher-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <path
        d="M16.5 11.5a2.5 2.5 0 0 1-2.5 2.5H8.8L5.5 17.2v-3.2A2.5 2.5 0 0 1 3 11.5V6.5A2.5 2.5 0 0 1 5.5 4h8.5a2.5 2.5 0 0 1 2.5 2.5z"
        fill="none"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="1.6"
      />
      <path
        d="M6.9 11.7l1.85-4.7 1.85 4.7M7.7 10.1h2.1M13.6 7v4.7"
        fill="none"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="1.3"
      />
      <path d="M19.7 2.6l.8 2.1 2.1.8-2.1.8-.8 2.1-.8-2.1-2.1-.8 2.1-.8z" fill="currentColor" />
    </svg>
  )
}

function StatusLine({ health, notice }) {
  if (notice) return <p className="chat-notice">{notice}</p>
  if (!health) return <p className="chat-notice">Connecting to the answer service.</p>
  return null
}

export default function ChatWidget() {
  const [open, setOpen] = useState(false)
  const [health, setHealth] = useState(null)
  const [notice, setNotice] = useState('')
  const [entries, setEntries] = useState([])
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)
  const session = useMemo(() => newSessionId(), [])
  const logRef = useRef(null)
  const inputRef = useRef(null)
  const launcherRef = useRef(null)
  const giveFocusBack = useRef(false)

  const close = useCallback(() => {
    giveFocusBack.current = true
    setOpen(false)
  }, [])

  useEffect(() => {
    if (!open || health || notice) return
    let active = true
    getChatHealth()
      .then((payload) => { if (active) setHealth(payload) })
      .catch((error) => { if (active) setNotice(error.message) })
    return () => { active = false }
  }, [open, health, notice])

  useEffect(() => {
    if (!open) return undefined
    inputRef.current?.focus()
    function handleKeyDown(event) {
      if (event.key === 'Escape') close()
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [open, close])

  useEffect(() => {
    const log = logRef.current
    if (log) log.scrollTop = log.scrollHeight
  }, [entries])

  useEffect(() => {
    if (!open && giveFocusBack.current) {
      giveFocusBack.current = false
      launcherRef.current?.focus()
    }
  }, [open])

  const append = useCallback((entry) => {
    setEntries((current) => [...current, { id: `${Date.now()}-${current.length}`, ...entry }])
  }, [])

  const ask = useCallback(() => {
    const question = draft.trim()
    if (!question || busy) return
    setDraft('')
    append({ kind: 'question', text: question })
    setBusy(true)
    askChat(question, session)
      .then((reply) => append({ kind: 'reply', reply }))
      .catch((error) => append({
        kind: 'error',
        text: error.reply && error.reply.state ? '' : error.message,
        reply: error.reply && error.reply.state ? error.reply : null,
      }))
      .finally(() => setBusy(false))
  }, [append, busy, draft, session])

  return (
    <>
      <button
        ref={launcherRef}
        className="chat-launcher"
        type="button"
        aria-label={open ? 'Close district chat' : 'Open district chat'}
        aria-expanded={open}
        aria-controls="district-chat"
        title={open ? 'Close district chat' : 'Open district chat'}
        onClick={() => (open ? close() : setOpen(true))}
      >
        <ChatGlyph />
      </button>
      {open ? (
        <section
          id="district-chat"
          className="chat-panel"
          role="dialog"
          aria-modal="false"
          aria-labelledby="chat-title"
        >
          <header className="chat-header">
            <div>
              <span className="chat-eyebrow">COLLECTOR ASSISTANT</span>
              <h2 id="chat-title">Ask about the records, or anything else</h2>
            </div>
            <button className="icon-button chat-close" type="button" aria-label="Close district chat" onClick={close}>
              ×
            </button>
          </header>
          <StatusLine health={health} notice={notice} />
          <div className="chat-log" ref={logRef}>
            {entries.length === 0 ? (
              <p className="chat-empty">Ask in Tamil or English.</p>
            ) : null}
            {entries.map((entry) => (
              <article key={entry.id} className={`chat-entry chat-entry-${entry.kind}`}>
                {entry.kind === 'question' ? <p className="chat-question">{entry.text}</p> : null}
                {entry.kind === 'error' ? (
                  <p className="chat-error" role="status">{entry.text || 'The chat refused this question.'}</p>
                ) : null}
                {entry.reply ? <ReplyBody reply={entry.reply} /> : null}
              </article>
            ))}
          </div>
          <form
            className="chat-composer"
            onSubmit={(event) => {
              event.preventDefault()
              ask()
            }}
          >
            <textarea
              ref={inputRef}
              className="chat-input"
              rows={2}
              maxLength={400}
              value={draft}
              placeholder="Ask in Tamil or English"
              aria-label="Your question"
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' && !event.shiftKey) {
                  event.preventDefault()
                  ask()
                }
              }}
            />
            <div className="chat-actions">
              <span className="chat-count">{draft.length} / 400</span>
              <button className="button button-secondary" type="submit" disabled={busy || !draft.trim()}>
                {busy ? 'Asking' : 'Ask'}
              </button>
            </div>
          </form>
        </section>
      ) : null}
    </>
  )
}
