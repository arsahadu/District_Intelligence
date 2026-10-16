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
  getGISGeoJSON,
  getGISHealth,
  getGISIncidents,
  getGISStats,
  getGISTalukas,
  getGISUnmappedRecords,
  reloadGISData,
} from '../services/gisApi.js'
import 'leaflet/dist/leaflet.css'
import './GISMapView.css'

const MAP_CENTER = [9.9252, 78.1198]
const EMPTY_RECORDS = []

const INCIDENT_LAYER = { id: 'incidents', label: 'Saved incidents', color: '#b94b43' }

const INCIDENT_COLORS = {
  critical: '#a32e36',
  high: '#bb5148',
  medium: '#bd8735',
  low: '#438464',
  unresolved: '#788693',
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
  return INCIDENT_COLORS[record.severity] || INCIDENT_COLORS.unresolved
}

function mapMatches(record, filters, layers) {
  if (!layers.incidents) return false
  if (filters.search && !String(record.search_string || '').includes(filters.search.toLocaleLowerCase())) {
    return false
  }
  if (filters.category && record.category !== filters.category) return false
  if (filters.severity && record.severity !== filters.severity) return false
  if (filters.status && record.status !== filters.status) return false
  if (filters.taluk && record.taluk !== filters.taluk) return false
  if (filters.startDate && (!record.date_only || record.date_only < filters.startDate)) return false
  if (filters.endDate && (!record.date_only || record.date_only > filters.endDate)) return false
  return true
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
        aria-label="Interactive map of Madurai District intelligence records"
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
                  <span className="gis-popup-type">
                    Saved incident
                  </span>
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

function GISRecordDetails({ record }) {
  if (!record) {
    return (
      <div className="gis-details-empty">
        <span aria-hidden="true">⌖</span>
        <strong>Select a mapped record</strong>
        <p>Record provenance, location resolution and supporting evidence appear here.</p>
      </div>
    )
  }

  const evidence = Array.isArray(record.evidence) ? record.evidence : []
  const sources = Array.isArray(record.source_records) ? record.source_records : []
  const summary = String(record.summary || '').trim()

  return (
    <article className="gis-details">
      <div className="gis-details-heading">
        <div>
          <span className="gis-record-kind is-incident">Saved incident</span>
          <h3>{record.title}</h3>
        </div>
      </div>
      <div className="gis-detail-tags">
        {record.category ? <span>{record.category.replaceAll('_', ' ')}</span> : null}
        {record.incident_type ? <span>{record.incident_type.replaceAll('_', ' ')}</span> : null}
        {record.severity ? (
          <span className={`gis-severity gis-severity-${record.severity}`}>
            {record.severity}
          </span>
        ) : null}
        {record.status ? <span>{String(record.status).replaceAll('_', ' ')}</span> : null}
        {record.review_required === true ? <span className="gis-review-tag">Review required</span> : null}
      </div>
      <dl className="gis-detail-fields">
        <div><dt>Event time</dt><dd>{formatDate(record.event_time)}</dd></div>
        {record.retrieved_at ? <div><dt>Retrieved</dt><dd>{formatDate(record.retrieved_at)}</dd></div> : null}
        <div><dt>Location</dt><dd>{record.location_name || record.raw_location_text || 'Not supplied'}</dd></div>
        {record.taluk && record.taluk !== 'Unassigned' ? (
          <div><dt>Taluk</dt><dd>{record.taluk}</dd></div>
        ) : null}
        {record.resolution_status ? (
          <div><dt>Location resolution</dt><dd>{record.resolution_status.replaceAll('_', ' ').toLowerCase()}</dd></div>
        ) : null}
        {record.review_state ? (
          <div><dt>Validation</dt><dd>{record.review_state.replaceAll('_', ' ')}</dd></div>
        ) : null}
        {record.base_lat !== null && record.base_lng !== null ? (
          <div>
            <dt>{record.coordinate_origin === 'registry_approximate' ? 'Approximate registry point' : 'Source coordinates'}</dt>
            <dd>{Number(record.base_lat).toFixed(5)}, {Number(record.base_lng).toFixed(5)}</dd>
          </div>
        ) : null}
        {record.coordinate_origin === null && record.source_coordinates ? (
          <div>
            <dt>Unvalidated source coordinates</dt>
            <dd>{String(record.source_coordinates.latitude ?? '—')}, {String(record.source_coordinates.longitude ?? '—')}</dd>
          </div>
        ) : null}
      </dl>
      {record.resolution_reason ? <p className="gis-resolution-note">{record.resolution_reason}</p> : null}
      {summary ? (
        <div className="gis-detail-summary">
          <h4>Description</h4>
          <p>{summary.length > 650 ? `${summary.slice(0, 650)}…` : summary}</p>
        </div>
      ) : null}
      <div className="gis-detail-links">
        {record.source_url ? (
          safeHttpUrl(record.source_url)
            ? <a href={safeHttpUrl(record.source_url)} target="_blank" rel="noopener noreferrer">Open source record ↗</a>
            : <span>Source URL is not a valid HTTP(S) link.</span>
        ) : null}
        {record.provenance?.record_id ? (
          <span>CommonRecord: {record.provenance.record_id}</span>
        ) : null}
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
          <h4>Evidence returned by the intelligence API</h4>
          {evidence.map((item) => (
            <blockquote key={item.evidence_id || `${item.record_id}-${item.field}-${item.char_start}`}>
              {item.quote ? <p>“{item.quote}”</p> : <p>No quote supplied.</p>}
              <cite>{[item.source_id, item.source_field || item.field, item.record_id].filter(Boolean).join(' · ')}</cite>
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
          <h3>Unmapped incidents</h3>
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

function GISStatus({ health, stats, warning }) {
  const sourceType = health?.dataSourceType || stats?.dataSourceType || 'unknown'
  const unavailable = sourceType === 'unknown' || sourceType === 'unavailable'
  const label = unavailable ? 'Backend unavailable' : 'Live backend data'
  const lastRefreshed = health?.lastRefreshed || stats?.lastRefreshed
  return (
    <div className={`gis-status ${unavailable || warning ? 'gis-status-partial' : 'gis-status-live'}`} role="status">
      <span className="gis-status-dot" aria-hidden="true" />
      <strong>{label}</strong>
      {lastRefreshed ? <span>Updated {formatDate(lastRefreshed)}</span> : null}
      {warning ? <span className="gis-status-warning">{warning}</span> : null}
      {health?.sourceEndpoints?.incidents?.status === 'unavailable' ? <span>Incidents API unavailable</span> : null}
    </div>
  )
}

export default function GISMapView() {
  const [refreshKey, setRefreshKey] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [snapshot, setSnapshot] = useState(null)
  const [optionalWarning, setOptionalWarning] = useState('')
  const [selectedId, setSelectedId] = useState(null)
  const [layers, setLayers] = useState({ incidents: true })
  const [filters, setFilters] = useState({
    search: '',
    category: '',
    severity: '',
    status: '',
    taluk: '',
    startDate: '',
    endDate: '',
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
          getGISIncidents(controller.signal),
          getGISGeoJSON(controller.signal),
          getGISStats(controller.signal),
          getGISUnmappedRecords(controller.signal),
          getGISHealth(controller.signal),
          getGISTalukas(controller.signal),
        ])
        const failure = results.slice(0, 3).find((result) => result.status === 'rejected')
        if (failure) throw failure.reason
        const [records, geojson, stats] = results.slice(0, 3).map((result) => result.value)
        if (!active) return
        const optionalErrors = results.slice(3).filter((result) => result.status === 'rejected')
        const warning = optionalErrors.length
          ? 'Some GIS status or boundary details could not be loaded.'
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
    () => [...new Set(records.map((record) => record.category).filter(Boolean))].sort(),
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
  const visibleRecords = useMemo(
    () => records.filter((record) => mapMatches(record, filters, layers)),
    [records, filters, layers],
  )
  const visibleUnmapped = useMemo(
    () => unmapped.filter((record) => mapMatches(record, filters, layers)),
    [unmapped, filters, layers],
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
  const selectedRecord = records.find((record) => getRecordKey(record) === selectedId)
    || unmapped.find((record) => getRecordKey(record) === selectedId)
    || null
  const stats = snapshot?.stats

  const setFilter = (field, value) => setFilters((current) => ({ ...current, [field]: value }))
  const toggleLayer = (layer) => setLayers((current) => ({ ...current, [layer]: !current[layer] }))
  const clearFilters = () => {
    setFilters({
      search: '',
      category: '',
      severity: '',
      status: '',
      taluk: '',
      startDate: '',
      endDate: '',
    })
    setLayers({ incidents: true })
  }

  return (
    <div className="gis-page">
      <div className="gis-page-header">
        <div>
          <p className="gis-eyebrow">DISTRICT INTELLIGENCE / SPATIAL VIEW</p>
          <h1>Madurai GIS map</h1>
          <p>Persisted intelligence incidents, mapped only where supported by source coordinates or the GIS place registry.</p>
        </div>
        <button
          type="button"
          className="button button-secondary gis-refresh-button"
          onClick={() => setRefreshKey((current) => current + 1)}
          disabled={loading}
        >
          <span aria-hidden="true">↻</span> Refresh GIS data
        </button>
      </div>

      {loading && !snapshot ? <LoadingState label="Connecting to the GIS data service" /> : null}
      {error ? (
        <section className="gis-error-state" role="alert">
          <strong>GIS data could not be loaded</strong>
          <p>{error}</p>
          <button className="button button-secondary" type="button" onClick={() => setRefreshKey((current) => current + 1)}>
            Retry GIS connection
          </button>
        </section>
      ) : null}
      {snapshot ? (
        <>
          <GISStatus health={snapshot.health} stats={stats} warning={optionalWarning} />
          <section className="gis-summary-grid" aria-label="GIS record totals">
            <article><span>Persisted incidents</span><strong>{stats?.incidentCount ?? 0}</strong><small>Incident records returned by the backend</small></article>
            <article><span>Mapped incidents</span><strong>{stats?.mappedCount ?? 0}</strong><small>Located incidents shown on the map</small></article>
            <article><span>Unmapped incidents</span><strong>{stats?.unmappedCount ?? 0}</strong><small>Kept in the location review list</small></article>
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
                    <span>Category</span>
                    <select value={filters.category} onChange={(event) => setFilter('category', event.target.value)}>
                      <option value="">All categories</option>
                      {categories.map((category) => <option key={category} value={category}>{category.replaceAll('_', ' ')}</option>)}
                    </select>
                  </label>
                  {severities.length ? (
                    <label>
                      <span>Severity</span>
                      <select value={filters.severity} onChange={(event) => setFilter('severity', event.target.value)}>
                        <option value="">All severities</option>
                        {severities.map((severity) => <option key={severity} value={severity}>{severity}</option>)}
                      </select>
                    </label>
                  ) : null}
                  {statuses.length ? (
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
                  <button type="button" className="gis-clear-filters" onClick={clearFilters}>Clear filters</button>
                </div>
                <div className="gis-layer-controls" aria-label="Map layers">
                  <span>Layers</span>
                  <label>
                    <input
                      type="checkbox"
                      checked={layers.incidents}
                      onChange={() => toggleLayer(INCIDENT_LAYER.id)}
                    />
                    <i style={{ backgroundColor: INCIDENT_LAYER.color }} aria-hidden="true" />
                    {INCIDENT_LAYER.label}
                  </label>
                </div>
              </div>
              <div className="gis-map-count-row">
                <span><strong>{mappedRecords.length}</strong> mapped incidents match the current filters</span>
                <span>{visibleUnmapped.length} unmapped match</span>
                {!records.length && snapshot.boundaries?.features?.length ? (
                  <span className="gis-map-context-note">
                    Administrative boundaries are shown; no incident records are currently available.
                  </span>
                ) : null}
              </div>
              {mappedRecords.length || snapshot.boundaries?.features?.length ? (
                <GISMapCanvas
                  records={mappedRecords}
                  featureById={featureById}
                  boundaries={snapshot.boundaries}
                  selected={selectedRecord}
                  onSelect={(record) => setSelectedId(getRecordKey(record))}
                  onReset={() => setSelectedId(null)}
                />
              ) : (
                <div className="gis-map-empty">
                  <EmptyState title={records.length ? 'No mapped records match these filters' : 'No GIS map data available'}>
                    {records.length
                      ? 'Clear the active filters to view mapped records.'
                      : 'The GIS service did not return incident records or administrative boundaries.'}
                  </EmptyState>
                </div>
              )}
              <div className="gis-legend">
                <span><i style={{ backgroundColor: INCIDENT_LAYER.color }} aria-hidden="true" />{INCIDENT_LAYER.label}</span>
                <span className="gis-boundary-legend"><i aria-hidden="true" />Taluk boundary</span>
              </div>
            </div>
            <aside className="gis-side-panel">
              <GISRecordDetails record={selectedRecord} />
              <GISUnmappedPanel
                records={visibleUnmapped}
                onSelect={(record) => setSelectedId(getRecordKey(record))}
              />
            </aside>
          </section>
        </>
      ) : null}
    </div>
  )
}
