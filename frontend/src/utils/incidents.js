const PRIORITY_ORDER = { critical: 4, high: 3, medium: 2, low: 1 }
const SEVERITY_ORDER = { critical: 4, high: 3, moderate: 2, low: 1, info: 0 }

export function safeHttpUrl(value) {
  if (!value) return null
  try {
    const url = new URL(value)
    return ['http:', 'https:'].includes(url.protocol) ? url.toString() : null
  } catch {
    return null
  }
}

export function formatDateTime(value, options = {}) {
  if (!value) return 'Time not provided'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return String(value)

  return new Intl.DateTimeFormat('en-IN', {
    timeZone: 'Asia/Kolkata',
    dateStyle: 'medium',
    timeStyle: 'short',
    ...options,
  }).format(date)
}

export function formatPrecision(precision) {
  if (!precision) return null
  return String(precision).replaceAll('_', ' ')
}

export function displayValue(value) {
  if (value === null || value === undefined || value === '') return 'Not specified'
  return String(value).replaceAll('_', ' ')
}

export function incidentLocation(incident) {
  const locations = incident.locations || []
  if (!locations.length) return incident.provenance?.district_hint || 'Location not established'

  return locations.map((location) => {
    const names = [
      location.village,
      location.taluk,
      location.block,
      location.normalized_name || location.text,
      location.district,
    ].filter(Boolean)
    return [...new Set(names)].join(', ')
  }).filter(Boolean).join(' · ') || 'Location not established'
}

export function requiresReview(incident) {
  return incident.validation?.state !== 'accepted'
    || (incident.claims || []).some((claim) => claim.review !== 'accepted')
}

export function isHighPriority(incident) {
  return ['critical', 'high'].includes(incident.priority)
    || ['critical', 'high'].includes(incident.severity)
}

export function sortByAttention(incidents) {
  return [...incidents].sort((first, second) => {
    const firstRank = PRIORITY_ORDER[first.priority] ?? SEVERITY_ORDER[first.severity] ?? 0
    const secondRank = PRIORITY_ORDER[second.priority] ?? SEVERITY_ORDER[second.severity] ?? 0
    const priorityDelta = secondRank - firstRank
    if (priorityDelta) return priorityDelta

    const severityDelta = (SEVERITY_ORDER[second.severity] ?? 0)
      - (SEVERITY_ORDER[first.severity] ?? 0)
    if (severityDelta) return severityDelta

    const firstTime = Date.parse(first.event_time || '') || 0
    const secondTime = Date.parse(second.event_time || '') || 0
    return secondTime - firstTime
  })
}

export function isNewsIncident(incident) {
  return incident.provenance?.source_type?.toLowerCase() === 'news'
    || (incident.evidence || []).some((item) => item.source_type?.toLowerCase() === 'news')
}

export function isAgricultureIncident(incident) {
  return incident.category === 'agriculture'
    || incident.provenance?.source_type?.toLowerCase().includes('agriculture')
    || incident.provenance?.record_type?.toLowerCase().includes('market')
    || (incident.evidence || []).some((item) =>
      item.source_type?.toLowerCase().includes('agriculture'))
}

export function isWeatherIncident(incident) {
  const searchable = [
    incident.incident_type,
    incident.category,
    incident.title,
    incident.description,
    incident.provenance?.source_type,
    ...(incident.evidence || []).map((item) => item.quote),
  ].filter(Boolean).join(' ').toLowerCase()

  return /\b(weather|rain|rainfall|flood|flooding|cyclone|storm|drought|heatwave|landslide|lightning)\b/.test(searchable)
}

export function matchesSearch(incident, search) {
  if (!search.trim()) return true
  const query = search.trim().toLocaleLowerCase()
  const searchable = [
    incident.incident_id,
    incident.title,
    incident.description,
    incident.incident_type,
    incident.category,
    incident.department,
    incident.severity,
    incident.priority,
    incident.event_status,
    incident.provenance?.source_id,
    incident.provenance?.source_type,
    incident.provenance?.record_id,
    incidentLocation(incident),
    ...(incident.evidence || []).flatMap((item) => [
      item.quote,
      item.source_id,
      item.record_id,
    ]),
  ].filter(Boolean).join(' ').toLocaleLowerCase()

  return searchable.includes(query)
}

export function getSourceLabel(incident) {
  const source = incident.provenance?.source_id
    || incident.evidence?.[0]?.source_id
  return source || 'Source not specified'
}

export function uniqueValues(incidents, select) {
  return [...new Set(incidents.map(select).filter(Boolean))].sort((a, b) =>
    String(a).localeCompare(String(b)))
}

export function compareIncidentFilters(incident, filters) {
  const eventDay = incident.event_time
    ? new Date(incident.event_time).toISOString().slice(0, 10)
    : ''
  const locations = incident.locations || []
  const hasLocation = filters.location
    ? locations.some((location) => [
      location.text,
      location.normalized_name,
      location.district,
      location.taluk,
      location.village,
      location.block,
    ].some((value) => value?.toLocaleLowerCase() === filters.location.toLocaleLowerCase()))
    : true

  return (!filters.severity || incident.severity === filters.severity)
    && (!filters.priority || incident.priority === filters.priority)
    && (!filters.category || incident.category === filters.category)
    && (!filters.status || incident.event_status === filters.status)
    && hasLocation
    && (!filters.startDate || (eventDay && eventDay >= filters.startDate))
    && (!filters.endDate || (eventDay && eventDay <= filters.endDate))
}
