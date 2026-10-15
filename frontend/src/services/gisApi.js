export const GIS_API_BASE_URL = (
  import.meta.env.VITE_GIS_API_BASE_URL || 'http://127.0.0.1:8080'
).replace(/\/+$/, '')

async function requestJson(path, options = {}) {
  const response = await fetch(`${GIS_API_BASE_URL}${path}`, {
    ...options,
    headers: {
      Accept: 'application/json',
      ...options.headers,
    },
  })
  const payload = await response.json().catch(() => null)
  if (!response.ok) {
    const detail = typeof payload?.detail === 'string'
      ? payload.detail
      : `GIS service returned HTTP ${response.status}.`
    throw new Error(detail)
  }
  return payload
}

function requireObject(payload, name) {
  if (!payload || typeof payload !== 'object' || Array.isArray(payload)) {
    throw new Error(`The GIS ${name} endpoint returned an unexpected response.`)
  }
  return payload
}

export async function getGISIncidents(signal) {
  const payload = await requestJson('/api/incidents', { signal })
  if (!Array.isArray(payload)) {
    throw new Error('The GIS incidents endpoint did not return a JSON list.')
  }
  return payload
}

export async function getGISUnmappedRecords(signal) {
  const payload = await requestJson('/api/unmapped', { signal })
  if (!Array.isArray(payload)) {
    throw new Error('The GIS unmapped endpoint did not return a JSON list.')
  }
  return payload
}

export async function getGISGeoJSON(signal) {
  const payload = requireObject(await requestJson('/api/geojson', { signal }), 'GeoJSON')
  if (payload.type !== 'FeatureCollection' || !Array.isArray(payload.features)) {
    throw new Error('The GIS GeoJSON endpoint returned an invalid FeatureCollection.')
  }
  return payload
}

export async function getGISStats(signal) {
  return requireObject(await requestJson('/api/stats', { signal }), 'statistics')
}

export async function getGISHealth(signal) {
  return requireObject(await requestJson('/api/health', { signal }), 'health')
}

export async function getGISTalukas(signal) {
  return requireObject(
    await requestJson('/api/talukas?mode=aggregated', { signal }),
    'taluka boundary',
  )
}

export async function reloadGISData(signal) {
  return requireObject(await requestJson('/api/reload', {
    method: 'POST',
    signal,
  }), 'reload')
}
