import { useEffect, useMemo, useState } from 'react'
import {
  CircleMarker,
  GeoJSON,
  MapContainer,
  Popup,
  TileLayer,
  useMap,
} from 'react-leaflet'
import { EmptyState, LoadingState } from '../components/Shared.jsx'
import {
  getGISHealth,
  getGISMapGeoJSON,
  getGISMapRecords,
  getGISMapUnmappedRecords,
  getGISStats,
  getGISTalukas,
  reloadGISData,
} from '../services/gisApi.js'
import { filterMapRecords } from '../utils/mapRecords.js'
import 'leaflet/dist/leaflet.css'
import './GISMapView.css'

const MAP_CENTER = [9.9252, 78.1198]
const EMPTY_RECORDS = []

const MAP_CATEGORIES = [
  { id: 'all', label: 'All district information' },
  { id: 'incidents', label: 'Incidents' },
  { id: 'news', label: 'News' },
  { id: 'weather', label: 'Weather' },
  { id: 'agriculture', label: 'Agriculture & markets' },
]

const RECORD_COLORS = {
  incident: '#b94b43',
  news: '#2f65b0',
  weather: '#258b91',
  agriculture: '#438464',
}

function safeHttpUrl(value) {
  if (typeof value !== 'string') return null
  try {
    const url = new URL(value)
    return url.protocol === 'http:' || url.protocol === 'https:' ? url.href : null
  } catch {
    return null
  }
}

function labelForRecord(record) {
  if (record.record_kind === 'incident') return 'Incident'
  if (record.source_type === 'agriculture') return 'Agriculture & market'
  if (record.source_type === 'weather') return 'Weather'
  if (record.source_type === 'news') return 'News'
  return 'District information'
}

function displayFact(key, value, unit) {
  if (value === null || value === undefined || value === '') return null
  if (Array.isArray(value)) return value.map(String).join(', ')
  if (typeof value === 'object') return Object.values(value).map(String).join(', ')
  if (key.endsWith('_temp_c') || key.endsWith('_c')) return `${value} °C`
  if (key.startsWith('relative_humidity_')) return `${value}%`
  if (key === 'min_price' || key === 'max_price' || key === 'modal_price') {
    return `₹${value}${unit ? ` / ${unit}` : ''}`
  }
  return String(value)
}

function displayFactLabel(key) {
  const labels = {
    commodity: 'Commodity',
    market: 'Market',
    unit: 'Unit',
    min_price: 'Minimum price',
    max_price: 'Maximum price',
    modal_price: 'Modal price',
    quantity: 'Quantity reported',
    forecast: 'Forecast',
    warning: 'Weather warning',
    condition: 'Condition',
    station_id: 'Weather station',
    max_temp_c: 'Maximum temperature',
    min_temp_c: 'Minimum temperature',
  }
  return labels[key] || key.replaceAll('_', ' ').replace(/\b\w/g, (letter) => letter.toUpperCase())
}

function formatDate(value) {
  if (!value) return 'Time not supplied'
  const date = new Date(value)
  if (!Number.isFinite(date.getTime())) return String(value)
  return new Intl.DateTimeFormat('en-IN', {
    timeZone: 'Asia/Kolkata',
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(date)
}

function markerColor(record) {
  return RECORD_COLORS[record.record_kind === 'incident' ? 'incident' : record.source_type]
    || '#788693'
}

function getFeatureId(feature) {
  const properties = feature?.properties || {}
  if (properties.id !== undefined && properties.record_kind) {
    return `${properties.record_kind}:${properties.id}`
  }
  return feature?.id ?? properties.id
}

function getRecordKey(record) {
  return `${record.record_kind}:${record.id}`
}

function MapSelection({ selected, onReset }) {
  const map = useMap()

  useEffect(() => {
    if (!selected?.is_geocoded) return
    map.flyTo([selected.display_lat, selected.display_lng], Math.max(map.getZoom(), 13), {
      duration: 0.45,
    })
  }, [map, selected?.id, selected?.is_geocoded, selected?.display_lat, selected?.display_lng])

  return (
    <button
      type="button"
      className="gis-map-reset"
      onClick={() => {
        map.flyTo(MAP_CENTER, 10, { duration: 0.45 })
        onReset()
      }}
    >
      Reset map view
    </button>
  )
}

function GISMapCanvas({
  records,
  featureById,
  boundaries,
  selected,
  onSelect,
  onReset,
}) {
  return (
    <div className="gis-map-frame">
      <MapContainer
        center={MAP_CENTER}
        zoom={10}
        minZoom={7}
        maxZoom={18}
        scrollWheelZoom
        className="gis-map"
        aria-label="Interactive map of Tamilnadu District Intelligence (TNDI) records"
      >
        <TileLayer
          attribution="&copy; OpenStreetMap contributors"
          url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
        />
        {boundaries?.features?.length ? (
          <GeoJSON
            key={boundaries.metadata?.display_mode || boundaries.features.length}
            data={boundaries}
            style={(feature) => {
              const properties = feature?.properties || {}
              const severityColors = {
                high: '#d17b73',
                medium: '#d3a462',
                low: '#75a58a',
                unknown: '#8292a0',
              }
              return {
                color: severityColors[properties.severity] || '#7890a1',
                weight: 1.2,
                opacity: 0.78,
                fillColor: severityColors[properties.severity] || '#7890a1',
                fillOpacity: properties.severity && properties.severity !== 'unknown' ? 0.13 : 0.025,
              }
            }}
            onEachFeature={(feature, layer) => {
              const name = feature?.properties?.taluk_name
              if (name) layer.bindTooltip(`${name} Taluk`)
            }}
          />
        ) : null}
        {records.map((record) => {
          const feature = featureById.get(`${record.record_kind}:${record.id}`)
          const coordinates = feature?.geometry?.coordinates
          if (!Array.isArray(coordinates) || coordinates.length < 2) return null
          const selectedRecord = selected && getRecordKey(selected) === getRecordKey(record)
          const color = markerColor(record)
          return (
            <CircleMarker
              key={`${record.record_kind}-${record.id}`}
              center={[coordinates[1], coordinates[0]]}
              radius={8}
              pathOptions={{
                color: selectedRecord ? '#1f405a' : '#fff',
                weight: selectedRecord ? 3 : 1.5,
                fillColor: color,
                fillOpacity: 0.9,
              }}
              eventHandlers={{ click: () => onSelect(record) }}
            >
              <Popup>
                <div className="gis-popup">
                  <span className="gis-popup-type">{labelForRecord(record)}</span>
                  <strong>{record.title}</strong>
                  <span>{record.location_name || record.raw_location_text || 'Unresolved location'}</span>
                  <button type="button" onClick={() => onSelect(record)}>View details</button>
                </div>
              </Popup>
            </CircleMarker>
          )
        })}
        <MapSelection selected={selected} onReset={onReset} />
      </MapContainer>
      <div className="gis-map-attribution-note">
        Taluk outlines are administrative boundaries. Offset pins are visual only.
      </div>
    </div>
  )
}

function GISRecordDetails({ record, onSelectIncident }) {
  if (!record) {
    return (
      <div className="gis-details-empty">
        <span aria-hidden="true">⌖</span>
        <strong>Select a record or location</strong>
        <p>Source, location precision, and relevant details appear here.</p>
      </div>
    )
  }

  const evidence = record.record_kind === 'incident' && Array.isArray(record.evidence)
    ? record.evidence
    : []
  const sources = Array.isArray(record.source_records) ? record.source_records : []
  const outcomes = Array.isArray(record.intelligence_outcomes) ? record.intelligence_outcomes : []
  const summary = String(record.summary || '').trim()
  const unit = record.details?.unit
  const details = Object.entries(record.details || {}).filter(([field, value]) => (
    !['language', 'summary', 'content', 'context_type', 'context_facts', 'district_id', 'market_id']
      .includes(field)
    && displayFact(field, value, unit) !== null
  ))
  const recordUrl = safeHttpUrl(record.source_url)
  const incidentNeedsReview = record.record_kind === 'incident' && record.review_required === true
  const sourceNeedsReview = outcomes.some((outcome) => outcome.status === 'needs_review')
  const precision = record.location_precision?.replaceAll('_', ' ')
    || record.administrative_level?.toLowerCase()
    || 'Unavailable'

  return (
    <article className="gis-details">
      <div className="gis-details-heading">
        <div>
          <span className={`gis-record-kind ${record.record_kind === 'incident' ? 'is-incident' : ''}`}>
            {labelForRecord(record)}
          </span>
          <h3>{record.title}</h3>
        </div>
      </div>
      <div className="gis-detail-tags">
        {record.record_kind === 'incident' && record.category ? <span>{record.category.replaceAll('_', ' ')}</span> : null}
        {record.incident_type ? <span>{record.incident_type.replaceAll('_', ' ')}</span> : null}
        {record.record_kind === 'incident' && record.severity ? (
          <span className={`gis-severity gis-severity-${record.severity}`}>
            {record.severity}
          </span>
        ) : null}
        {record.record_kind === 'incident' && record.status ? <span>{String(record.status).replaceAll('_', ' ')}</span> : null}
        {incidentNeedsReview || sourceNeedsReview ? <span className="gis-review-tag">Needs review</span> : null}
        {(record.record_kind === 'incident' && record.review_state === 'accepted')
          || outcomes.some((outcome) => outcome.status === 'accepted')
          ? <span>Accepted incident</span>
          : null}
        {outcomes.some((outcome) => outcome.status === 'information_only')
          ? <span>Information only</span>
          : null}
        {record.source_type === 'weather' && record.details?.warning
          ? <span className="gis-review-tag">Weather warning</span>
          : null}
      </div>
      <dl className="gis-detail-fields">
        <div>
          <dt>{record.record_type === 'forecast' ? 'Forecast date' : 'Observation / event time'}</dt>
          <dd>{formatDate(record.event_time)}</dd>
        </div>
        {record.retrieved_at ? <div><dt>Data received</dt><dd>{formatDate(record.retrieved_at)}</dd></div> : null}
        <div>
          <dt>Location</dt>
          <dd>{record.location_name || record.raw_location_text || 'Location unavailable'}</dd>
        </div>
        <div><dt>Location precision</dt><dd>{precision}</dd></div>
        {record.taluk && record.taluk !== 'Unassigned' ? (
          <div><dt>Taluk</dt><dd>{record.taluk}</dd></div>
        ) : null}
        {record.base_lat !== null && record.base_lng !== null && record.coordinate_origin === 'source' ? (
          <div>
            <dt>Source coordinates</dt>
            <dd>{Number(record.base_lat).toFixed(5)}, {Number(record.base_lng).toFixed(5)}</dd>
          </div>
        ) : null}
        {record.coordinate_origin === null && record.source_coordinates ? (
          <div>
            <dt>Unvalidated source coordinates</dt>
            <dd>{String(record.source_coordinates.latitude ?? '—')}, {String(record.source_coordinates.longitude ?? '—')}</dd>
          </div>
        ) : null}
        <div><dt>Source</dt><dd>{record.source_name || record.source_id || 'Source not specified'}</dd></div>
      </dl>
      {record.resolution_reason ? <p className="gis-resolution-note">{record.resolution_reason}</p> : null}
      {record.co_location_count > 1 ? (
        <p className="gis-resolution-note">This marker is offset slightly from the registered point so nearby records can be selected. The location shown above is the registered location.</p>
      ) : null}
      {summary ? (
        <div className="gis-detail-summary">
          <h4>Description</h4>
          <p>{summary.length > 650 ? `${summary.slice(0, 650)}…` : summary}</p>
        </div>
      ) : null}
      {details.length ? (
        <dl className="gis-detail-fields gis-source-facts">
          {details.map(([field, value]) => (
            <div key={field}>
              <dt>{displayFactLabel(field)}</dt>
              <dd>{displayFact(field, value, unit)}</dd>
            </div>
          ))}
        </dl>
      ) : null}
      {outcomes.length ? (
        <section className="gis-evidence-section">
          <h4>Related intelligence</h4>
          {outcomes.map((outcome, index) => (
            <div className="gis-source-reference" key={`${outcome.incident_id || outcome.status}-${index}`}>
              <strong>{outcome.title || labelForRecord(record)}</strong>
              <small>
                {outcome.status === 'accepted'
                  ? 'Accepted incident'
                  : outcome.status === 'needs_review'
                    ? 'Needs review'
                    : 'Information only'}
              </small>
              {outcome.incident_id ? (
                <button
                  className="gis-assessment-link"
                  type="button"
                  onClick={() => onSelectIncident?.(outcome.incident_id)}
                >
                  View assessment
                </button>
              ) : null}
            </div>
          ))}
        </section>
      ) : null}
      <div className="gis-detail-links">
        {recordUrl ? (
          <a href={recordUrl} target="_blank" rel="noopener noreferrer">
            {record.source_type === 'news' ? 'View original article ↗' : 'View source ↗'}
          </a>
        ) : null}
        {!recordUrl ? <span>Source link unavailable</span> : null}
      </div>
      {sources.length ? (
        <section className="gis-evidence-section">
          <h4>Linked source records</h4>
          {sources.map((source) => (
            <div className="gis-source-reference" key={source.record_id}>
              <strong>{source.title || source.record_id}</strong>
              <small>{source.source_id || source.source_type || 'Source'} · {source.record_id}</small>
              {safeHttpUrl(source.source_url) ? (
                <a href={safeHttpUrl(source.source_url)} target="_blank" rel="noopener noreferrer">Open source ↗</a>
              ) : null}
            </div>
          ))}
        </section>
      ) : null}
      {evidence.length ? (
        <section className="gis-evidence-section">
          <h4>Supporting evidence</h4>
          {evidence.map((item) => (
            <blockquote key={item.evidence_id || `${item.record_id}-${item.field}-${item.char_start}`}>
              {item.quote ? <p>“{item.quote}”</p> : <p>No quote supplied.</p>}
              <cite>{String(item.source_id || 'Source not specified').replaceAll('_', ' ')}</cite>
              {safeHttpUrl(item.source_url) ? (
                <a href={safeHttpUrl(item.source_url)} target="_blank" rel="noopener noreferrer">Open evidence source ↗</a>
              ) : null}
            </blockquote>
          ))}
        </section>
      ) : null}
    </article>
  )
}

function GISUnmappedPanel({ records, onSelect }) {
  return (
    <section className="gis-unmapped-panel">
      <div className="gis-section-heading">
        <div>
          <span className="gis-eyebrow">LOCATION REVIEW</span>
          <h3>Unmapped records</h3>
        </div>
        <span className="gis-count-pill">{records.length}</span>
      </div>
      {records.length ? (
        <ul className="gis-unmapped-list">
          {records.map((record) => (
            <li key={`${record.record_kind}-${record.id}`}>
              <button type="button" onClick={() => onSelect(record)}>
                <strong>{record.title}</strong>
                <span>{record.raw_location_text || 'No location text supplied'}</span>
                <small>{record.resolution_reason || 'No resolution reason supplied.'}</small>
              </button>
            </li>
          ))}
        </ul>
      ) : <p className="gis-no-unmapped">No unresolved locations in the current data.</p>}
    </section>
  )
}

function GISResultList({ records, onSelect }) {
  const [showAll, setShowAll] = useState(false)
  const shown = showAll ? records : records.slice(0, 12)
  return (
    <section className="gis-unmapped-panel gis-result-panel">
      <div className="gis-section-heading">
        <div>
          <span className="gis-eyebrow">FILTERED RESULTS</span>
          <h3>District records</h3>
        </div>
        <span className="gis-count-pill">{records.length}</span>
      </div>
      {shown.length ? (
        <ul className="gis-unmapped-list">
          {shown.map((record) => (
            <li key={getRecordKey(record)}>
              <button type="button" onClick={() => onSelect(record)}>
                <span className={`gis-result-category gis-result-${record.record_kind === 'incident' ? 'incident' : record.source_type}`}>
                  {labelForRecord(record)}
                </span>
                <strong>{record.title}</strong>
                <span>{record.location_name || record.raw_location_text || 'Location unavailable'}</span>
              </button>
            </li>
          ))}
        </ul>
      ) : <p className="gis-no-unmapped">No records match these filters.</p>}
      {shown.length < records.length ? (
        <button className="gis-load-more" type="button" onClick={() => setShowAll(true)}>
          Show all {records.length} records
        </button>
      ) : null}
    </section>
  )
}

function GISStatus({ health, stats, warning }) {
  const sourceType = health?.dataSourceType || stats?.dataSourceType || 'unknown'
  const unavailable = sourceType === 'unknown' || sourceType === 'unavailable'
  const label = unavailable ? 'Map information unavailable' : 'District information available'
  const lastRefreshed = health?.lastRefreshed || stats?.lastRefreshed
  return (
    <div className={`gis-status ${unavailable || warning ? 'gis-status-partial' : 'gis-status-live'}`} role="status">
      <span className="gis-status-dot" aria-hidden="true" />
      <strong>{label}</strong>
      {lastRefreshed ? <span>Updated {formatDate(lastRefreshed)}</span> : null}
      {warning ? <span className="gis-status-warning">{warning}</span> : null}
      {health?.sourceEndpoints?.records?.status === 'unavailable'
        ? <span>Some news, weather, or market information is temporarily unavailable.</span>
        : null}
    </div>
  )
}

export default function GISMapView({ onSelectIncident }) {
  const [refreshKey, setRefreshKey] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [snapshot, setSnapshot] = useState(null)
  const [optionalWarning, setOptionalWarning] = useState('')
  const [selectedId, setSelectedId] = useState(null)
  const [filters, setFilters] = useState({
    search: '',
    recordType: 'all',
    category: '',
    severity: '',
    status: '',
    taluk: '',
    startDate: '',
    endDate: '',
    alert: 'all',
    commodity: '',
  })

  useEffect(() => {
    const controller = new AbortController()
    let active = true
    async function load() {
      setLoading(true)
      setError('')
      setOptionalWarning('')
      try {
        if (refreshKey > 0) await reloadGISData(controller.signal)
        const results = await Promise.allSettled([
          getGISMapRecords(controller.signal),
          getGISMapGeoJSON(controller.signal),
          getGISStats(controller.signal),
          getGISMapUnmappedRecords(controller.signal),
          getGISHealth(controller.signal),
          getGISTalukas(controller.signal),
        ])
        const failure = results.slice(0, 3).find((result) => result.status === 'rejected')
        if (failure) throw failure.reason
        const [records, geojson, stats] = results.slice(0, 3).map((result) => result.value)
        if (!active) return
        const optionalErrors = results.slice(3).filter((result) => result.status === 'rejected')
        const warning = optionalErrors.length
          ? 'Some location or status information could not be loaded.'
          : ''
        setSnapshot({
          records,
          geojson,
          stats,
          unmapped: results[3].status === 'fulfilled' ? results[3].value : [],
          health: results[4].status === 'fulfilled' ? results[4].value : null,
          boundaries: results[5].status === 'fulfilled' ? results[5].value : null,
        })
        setOptionalWarning(warning)
        setSelectedId(null)
      } catch (requestError) {
        if (!active || requestError?.name === 'AbortError') return
        setError(requestError instanceof Error ? requestError.message : 'Unable to load GIS data.')
      } finally {
        if (active) setLoading(false)
      }
    }
    void load()
    return () => {
      active = false
      controller.abort()
    }
  }, [refreshKey])

  const records = snapshot?.records ?? EMPTY_RECORDS
  const unmapped = snapshot?.unmapped ?? EMPTY_RECORDS
  const categories = useMemo(
    () => [...new Set(records.filter((record) => record.record_kind === 'incident')
      .map((record) => record.category).filter(Boolean))].sort(),
    [records],
  )
  const statuses = useMemo(
    () => [...new Set(records.filter((record) => record.record_kind === 'incident')
      .map((record) => record.status).filter(Boolean))].sort(),
    [records],
  )
  const severities = useMemo(
    () => [...new Set(records.filter((record) => record.record_kind === 'incident')
      .map((record) => record.severity).filter(Boolean))].sort(),
    [records],
  )
  const taluks = useMemo(
    () => [...new Set(records.map((record) => record.taluk)
      .filter((taluk) => taluk && taluk !== 'Unassigned'))].sort(),
    [records],
  )
  const commodities = useMemo(
    () => [...new Set(records.filter((record) => record.source_type === 'agriculture')
      .map((record) => record.details?.commodity).filter(Boolean))].sort(),
    [records],
  )
  const hasWeatherWarnings = records.some((record) =>
    record.source_type === 'weather' && record.details?.warning)
  const visibleRecords = useMemo(
    () => filterMapRecords(records, filters),
    [records, filters],
  )
  const visibleUnmapped = useMemo(
    () => filterMapRecords(unmapped, filters),
    [unmapped, filters],
  )
  const featureById = useMemo(() => {
    const index = new Map()
    for (const feature of snapshot?.geojson?.features || []) {
      const id = getFeatureId(feature)
      if (id !== undefined && id !== null) index.set(String(id), feature)
    }
    return index
  }, [snapshot?.geojson])
  const mappedRecords = visibleRecords.filter((record) => (
    record.is_geocoded && record.base_lat !== null && record.base_lng !== null
      && featureById.has(`${record.record_kind}:${record.id}`)
  ))
  const selectedRecordFromUrl = useMemo(() => {
    if (selectedId) return null
    const params = new URLSearchParams(window.location.search)
    const recordKind = params.get('recordKind')
    const recordId = params.get('recordId')
    if (!recordKind || !recordId) return null
    return [...records, ...unmapped].find(
      (record) => record.record_kind === recordKind && String(record.id) === recordId,
    ) || null
  }, [records, unmapped, selectedId])
  const selectedRecord = records.find((record) => getRecordKey(record) === selectedId)
    || unmapped.find((record) => getRecordKey(record) === selectedId)
    || selectedRecordFromUrl
  const stats = snapshot?.stats

  const setFilter = (field, value) => setFilters((current) => ({ ...current, [field]: value }))
  const selectRecord = (record) => {
    setSelectedId(getRecordKey(record))
    const url = new URL(window.location.href)
    url.searchParams.set('view', 'map')
    url.searchParams.set('recordKind', record.record_kind)
    url.searchParams.set('recordId', String(record.id))
    window.history.replaceState(null, '', `${url.pathname}${url.search}${url.hash}`)
  }
  const clearSelection = () => {
    setSelectedId(null)
    const url = new URL(window.location.href)
    url.searchParams.delete('recordKind')
    url.searchParams.delete('recordId')
    window.history.replaceState(null, '', `${url.pathname}${url.search}${url.hash}`)
  }
  const clearFilters = () => {
    setFilters({
      search: '',
      recordType: 'all',
      category: '',
      severity: '',
      status: '',
      taluk: '',
      startDate: '',
      endDate: '',
      alert: 'all',
      commodity: '',
    })
  }

  return (
    <div className="gis-page">
      <div className="gis-page-header">
        <div>
          <p className="gis-eyebrow">TAMILNADU DISTRICT INTELLIGENCE (TNDI) / SPATIAL VIEW</p>
          <h1>District map</h1>
          <p>Explore incidents, news, weather, and market information. Locations without a reliable point remain available in the record list.</p>
        </div>
        <button
          type="button"
          className="button button-secondary gis-refresh-button"
          onClick={() => setRefreshKey((current) => current + 1)}
          disabled={loading}
        >
          <span aria-hidden="true">↻</span> Refresh district information
        </button>
      </div>

      {loading && !snapshot ? <LoadingState label="Loading district map information" /> : null}
      {error ? (
        <section className="gis-error-state" role="alert">
          <strong>Map information is temporarily unavailable</strong>
          <p>Please try again in a moment.</p>
          <button className="button button-secondary" type="button" onClick={() => setRefreshKey((current) => current + 1)}>
            Try again
          </button>
        </section>
      ) : null}
      {snapshot ? (
        <>
          <GISStatus health={snapshot.health} stats={stats} warning={optionalWarning} />
          <section className="gis-summary-grid" aria-label="District information totals">
            <article><span>Incidents</span><strong>{stats?.incidentCount ?? 0}</strong><small>Validated incident records</small></article>
            <article><span>News, weather & market</span><strong>{stats?.commonRecordCount ?? 0}</strong><small>Source information records</small></article>
            <article><span>Location unavailable</span><strong>{stats?.mapUnmappedCount ?? 0}</strong><small>Still available in the record list</small></article>
          </section>

          <section className="gis-workspace">
            <div className="gis-main-panel">
              <div className="gis-toolbar">
                <label className="gis-search-field">
                  <span>Search records</span>
                  <input
                    type="search"
                    value={filters.search}
                    onChange={(event) => setFilter('search', event.target.value)}
                    placeholder="Title, source, location, taluk…"
                  />
                </label>
                <div className="gis-filter-row">
                  <label>
                    <span>Show</span>
                    <select value={filters.recordType} onChange={(event) => setFilter('recordType', event.target.value)}>
                      {MAP_CATEGORIES.map(({ id, label }) => <option key={id} value={id}>{label}</option>)}
                    </select>
                  </label>
                  {categories.length && filters.recordType !== 'weather'
                    && filters.recordType !== 'agriculture' && filters.recordType !== 'news' ? (
                    <label>
                      <span>Incident category</span>
                      <select value={filters.category} onChange={(event) => setFilter('category', event.target.value)}>
                        <option value="">All categories</option>
                        {categories.map((category) => <option key={category} value={category}>{category.replaceAll('_', ' ')}</option>)}
                      </select>
                    </label>
                  ) : null}
                  {severities.length && filters.recordType !== 'weather'
                    && filters.recordType !== 'agriculture' && filters.recordType !== 'news' ? (
                    <label>
                      <span>Severity</span>
                      <select value={filters.severity} onChange={(event) => setFilter('severity', event.target.value)}>
                        <option value="">All severities</option>
                        {severities.map((severity) => <option key={severity} value={severity}>{severity}</option>)}
                      </select>
                    </label>
                  ) : null}
                  {statuses.length && filters.recordType !== 'weather'
                    && filters.recordType !== 'agriculture' && filters.recordType !== 'news' ? (
                    <label>
                      <span>Incident status</span>
                      <select value={filters.status} onChange={(event) => setFilter('status', event.target.value)}>
                        <option value="">All statuses</option>
                        {statuses.map((status) => <option key={status} value={status}>{status.replaceAll('_', ' ')}</option>)}
                      </select>
                    </label>
                  ) : null}
                  {taluks.length ? (
                    <label>
                      <span>Taluk</span>
                      <select value={filters.taluk} onChange={(event) => setFilter('taluk', event.target.value)}>
                        <option value="">All taluks</option>
                        {taluks.map((taluk) => <option key={taluk} value={taluk}>{taluk}</option>)}
                      </select>
                    </label>
                  ) : null}
                  {records.some((record) => record.date_only) ? (
                    <>
                      <label>
                        <span>From</span>
                        <input type="date" value={filters.startDate} onChange={(event) => setFilter('startDate', event.target.value)} />
                      </label>
                      <label>
                        <span>To</span>
                        <input type="date" value={filters.endDate} onChange={(event) => setFilter('endDate', event.target.value)} />
                      </label>
                    </>
                  ) : null}
                  {hasWeatherWarnings
                    && (filters.recordType === 'all' || filters.recordType === 'weather') ? (
                    <label>
                      <span>Weather alerts</span>
                      <select value={filters.alert} onChange={(event) => setFilter('alert', event.target.value)}>
                        <option value="all">All weather records</option>
                        <option value="warning">With warning</option>
                        <option value="none">No warning</option>
                      </select>
                    </label>
                  ) : null}
                  {commodities.length
                    && (filters.recordType === 'all' || filters.recordType === 'agriculture') ? (
                    <label>
                      <span>Commodity</span>
                      <select value={filters.commodity} onChange={(event) => setFilter('commodity', event.target.value)}>
                        <option value="">All commodities</option>
                        {commodities.map((commodity) => <option key={commodity} value={commodity}>{commodity}</option>)}
                      </select>
                    </label>
                  ) : null}
                  <button type="button" className="gis-clear-filters" onClick={clearFilters}>Clear filters</button>
                </div>
              </div>
              <div className="gis-map-count-row">
                <span><strong>{mappedRecords.length}</strong> of {visibleRecords.length} matching records have mappable locations</span>
                <span>{visibleUnmapped.length} location{visibleUnmapped.length === 1 ? '' : 's'} unavailable</span>
                {!records.length && snapshot.boundaries?.features?.length ? (
                  <span className="gis-map-context-note">
                    Administrative boundaries are shown; no district records are currently available.
                  </span>
                ) : null}
              </div>
              {mappedRecords.length || snapshot.boundaries?.features?.length ? (
                <GISMapCanvas
                  records={mappedRecords}
                  featureById={featureById}
                  boundaries={snapshot.boundaries}
                  selected={selectedRecord}
                  onSelect={selectRecord}
                  onReset={clearSelection}
                />
              ) : (
                <div className="gis-map-empty">
                  <EmptyState title={records.length ? 'No mapped records match these filters' : 'No map information available'}>
                    {records.length
                      ? 'Clear the active filters to view mapped records.'
                      : 'No district records or administrative boundaries are available right now.'}
                  </EmptyState>
                </div>
              )}
              <div className="gis-legend">
                {MAP_CATEGORIES.slice(1).map(({ id, label }) => (
                  <span key={id}><i style={{ backgroundColor: RECORD_COLORS[id === 'incidents' ? 'incident' : id] }} aria-hidden="true" />{label}</span>
                ))}
                <span className="gis-boundary-legend"><i aria-hidden="true" />Taluk boundary</span>
              </div>
            </div>
            <aside className="gis-side-panel">
              <GISRecordDetails record={selectedRecord} onSelectIncident={onSelectIncident} />
              <GISResultList records={visibleRecords} onSelect={selectRecord} />
              <GISUnmappedPanel
                records={visibleUnmapped}
                onSelect={selectRecord}
              />
            </aside>
          </section>
        </>
      ) : null}
    </div>
  )
}
