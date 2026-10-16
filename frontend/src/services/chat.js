export const CHAT_BASE_URL = (
  import.meta.env.VITE_CHAT_BASE_URL || 'http://127.0.0.1:8010'
).replace(/\/+$/, '')

const HEALTH_TIMEOUT_MS = 8000
const QUESTION_TIMEOUT_MS = 60000

function errorFrom(payload, status) {
  const detail = payload && typeof payload.detail === 'string'
    ? payload.detail
    : `The chat service replied with status ${status}.`
  const error = new Error(detail)
  error.status = status
  error.code = (payload && payload.error) || ''
  error.reply = payload || null
  return error
}

async function requestJson(path, init, timeoutMs) {
  let response
  try {
    response = await fetch(`${CHAT_BASE_URL}${path}`, {
      ...init,
      signal: AbortSignal.timeout(timeoutMs),
    })
  } catch (cause) {
    if (cause && cause.name === 'TimeoutError') {
      throw new Error(
        `No answer came back within ${Math.round(timeoutMs / 1000)}s from the chat service at ${CHAT_BASE_URL}.`,
        { cause },
      )
    }
    throw new Error(`No answer came back: the chat service at ${CHAT_BASE_URL} did not answer.`, { cause })
  }

  let payload
  try {
    payload = await response.json()
  } catch {
    payload = null
  }

  if (!response.ok) throw errorFrom(payload, response.status)
  return payload
}

export function getChatHealth() {
  return requestJson('/health', { method: 'GET', headers: { Accept: 'application/json' } }, HEALTH_TIMEOUT_MS)
}

export function askChat(question, sessionId) {
  const body = { question }
  if (sessionId) body.session_id = sessionId
  return requestJson(
    '/chat',
    {
      method: 'POST',
      headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    },
    QUESTION_TIMEOUT_MS,
  )
}
