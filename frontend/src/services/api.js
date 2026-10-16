const ENV = import.meta.env || {}

export const API_BASE_URL = (
  ENV.VITE_API_BASE_URL || 'http://localhost:8000'
).replace(/\/+$/, '')

async function requestJson(path) {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    headers: { Accept: 'application/json' },
  })

  if (!response.ok) {
    throw new Error(`Request failed with status ${response.status}`)
  }

  return response.json()
}

export async function getIncidents() {
  const incidents = await requestJson('/incidents')
  if (!Array.isArray(incidents)) {
    throw new Error('The incidents endpoint returned an unexpected response.')
  }
  return incidents
}

export async function getRecords() {
  const records = await requestJson('/records')
  if (!Array.isArray(records)
    || records.some((record) => !record || typeof record !== 'object' || Array.isArray(record))) {
    throw new Error('The CommonRecords endpoint returned an unexpected response.')
  }
  return records
}

export async function getIncident(incidentId) {
  const incident = await requestJson(`/incidents/${encodeURIComponent(incidentId)}`)
  if (!incident || typeof incident !== 'object' || Array.isArray(incident)) {
    throw new Error('The incident endpoint returned an unexpected response.')
  }
  return incident
}
