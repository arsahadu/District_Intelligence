import { API_BASE_URL } from './api.js'

const DOCUMENTS_PATH = '/document-intelligence'

function isRecord(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

function errorMessage(status, payload) {
  if (status === 400 && typeof payload?.detail === 'string') {
    return payload.detail.replace(/\s+/g, ' ').slice(0, 240)
  }
  if (status === 404) return 'This document job could not be found.'
  if (status === 409) return 'OCR is still processing. Wait for it to complete, then try again.'
  if (status === 502) return 'The document processing service is temporarily unavailable. Please retry later.'
  if (status >= 500) return 'The server could not complete this document request.'
  return `The document request failed (HTTP ${status}).`
}

async function requestJson(url, options) {
  let response
  try {
    response = await fetch(url, {
      ...options,
      headers: { Accept: 'application/json', ...options?.headers },
    })
  } catch (error) {
    if (error instanceof Error && error.name === 'AbortError') throw error
    throw new Error('Could not reach the document service. Check the connection and retry.', { cause: error })
  }

  let responseText
  try {
    responseText = await response.text()
  } catch (error) {
    if (error instanceof Error && error.name === 'AbortError') throw error
    throw new Error('The connection ended before the document service returned a response.', { cause: error })
  }
  let payload
  if (responseText) {
    try {
      payload = JSON.parse(responseText)
    } catch {
      if (response.ok) {
        throw new Error('The document service returned an unreadable response.')
      }
    }
  }

  if (!response.ok) throw new Error(errorMessage(response.status, payload))
  if (!isRecord(payload)) {
    throw new Error('The document service returned an unexpected response.')
  }
  return payload
}

function validateJobId(jobId) {
  if (typeof jobId !== 'string' || !jobId.trim()) {
    throw new Error('The document service did not return a valid job ID.')
  }
  return jobId.trim()
}

export async function uploadDocument(file, { signal } = {}) {
  const form = new FormData()
  form.append('file', file)

  const result = await requestJson(`${API_BASE_URL}${DOCUMENTS_PATH}/documents`, {
    method: 'POST',
    body: form,
    signal,
  })
  validateJobId(result.job_id)
  return result
}

function pollingUrl(jobId, pollUrl) {
  const expected = new URL(
    `${DOCUMENTS_PATH}/jobs/${encodeURIComponent(jobId)}`,
    API_BASE_URL,
  )
  if (typeof pollUrl !== 'string' || !pollUrl.trim()) return expected.href

  try {
    const candidate = new URL(pollUrl, `${API_BASE_URL}/`)
    if (
      candidate.origin === expected.origin
      && candidate.pathname === expected.pathname
      && !candidate.search
      && !candidate.hash
    ) {
      return candidate.href
    }
  } catch {
    // An invalid server-provided URL falls back to the documented endpoint.
  }
  return expected.href
}

export async function getDocumentJob(jobId, pollUrl, { signal } = {}) {
  const validJobId = validateJobId(jobId)
  const result = await requestJson(pollingUrl(validJobId, pollUrl), { signal })
  if (typeof result.status !== 'string') {
    throw new Error('The document service returned an invalid job status.')
  }
  if (typeof result.job_id === 'string' && result.job_id !== validJobId) {
    throw new Error('The document service returned a different job than requested.')
  }
  return result
}

export async function generateDocumentInsights(jobId, { signal } = {}) {
  const validJobId = validateJobId(jobId)
  return requestJson(
    `${API_BASE_URL}${DOCUMENTS_PATH}/jobs/${encodeURIComponent(validJobId)}/insights`,
    { method: 'POST', signal },
  )
}
