import { useMemo, useState } from 'react'
import FilterBar from '../components/FilterBar.jsx'
import IncidentTable from '../components/IncidentTable.jsx'
import {
  EmptyState,
  ErrorState,
  LoadingState,
  ReviewTag,
  SeverityIndicator,
  SummaryCard,
} from '../components/Shared.jsx'
import {
  compareIncidentFilters,
  formatDateTime,
  getSourceLabel,
  incidentLocation,
  isAgricultureIncident,
  isHighPriority,
  isNewsIncident,
  isWeatherIncident,
  matchesSearch,
  requiresReview,
  safeHttpUrl,
  sortByAttention,
} from '../utils/incidents.js'

const INITIAL_FILTERS = {
  search: '',
  severity: '',
  priority: '',
  category: '',
  status: '',
  location: '',
  startDate: '',
  endDate: '',
}

const PAGE_INFO = {
  overview: ['Madurai District Overview', 'A source-backed view of current district incidents and items requiring attention.'],
  incidents: ['Incidents', 'Search and review structured incidents recorded for Madurai district.'],
  news: ['News Intelligence', 'District incidents derived from news sources, with direct links to the supporting evidence.'],
  weather: ['Weather', 'Weather-related incident records and alerts available in the district intelligence feed.'],
  agriculture: ['Agriculture & Markets', 'Agriculture incidents and market observations from available source records.'],
  map: ['Map View', 'Location references from the incident register.'],
  sources: ['Sources', 'Source records and references cited by available incidents.'],
}

function PageIntro({ view, count, onRefresh }) {
  const [title, description] = PAGE_INFO[view]
  return (
    <div className="page-intro">
      <div>
        <p className="eyebrow">DISTRICT INTELLIGENCE / {title.toUpperCase()}</p>
        <h1>{title}</h1>
        <p>{description}</p>
      </div>
      <div className="intro-actions">
        {Number.isInteger(count) ? <span className="record-count">{count} {count === 1 ? 'incident' : 'incidents'}</span> : null}
        <button className="button button-secondary refresh-inline" type="button" onClick={onRefresh}>
          <span aria-hidden="true">↻</span> Refresh data
        </button>
      </div>
    </div>
  )
}

function IncidentRow({ incident, onSelect, index }) {
  return (
    <button
      className="attention-row"
      type="button"
      onClick={() => onSelect(incident.incident_id)}
    >
      <span className="attention-index">{String(index + 1).padStart(2, '0')}</span>
      <span className="attention-copy">
        <strong>{incident.title || 'Untitled incident'}</strong>
        <span>{incidentLocation(incident)} · {getSourceLabel(incident)}</span>
      </span>
      <span className="attention-tags">
        <SeverityIndicator value={incident.severity} />
        {requiresReview(incident) ? <ReviewTag required /> : null}
      </span>
      <time className="attention-time" dateTime={incident.event_time || undefined}>
        {formatDateTime(incident.event_time)}
      </time>
      <span className="row-chevron" aria-hidden="true">›</span>
    </button>
  )
}

function Overview({ incidents, onSelect, onNavigate }) {
  const highPriority = incidents.filter(isHighPriority).length
  const needsReview = incidents.filter(requiresReview).length
  const sources = new Set(incidents.flatMap((incident) => [
    incident.provenance?.source_id,
    ...(incident.evidence || []).map((item) => item.source_id),
  ]).filter(Boolean))
  const attentionRecords = sortByAttention(incidents.filter((incident) =>
    isHighPriority(incident) || requiresReview(incident)))
  const attention = attentionRecords.slice(0, 6)
  const recent = [...incidents]
    .sort((a, b) => (Date.parse(b.event_time || '') || 0) - (Date.parse(a.event_time || '') || 0))
    .slice(0, 5)

  const categories = Object.entries(incidents.reduce((totals, incident) => {
    const category = incident.category || 'unresolved'
    totals[category] = (totals[category] || 0) + 1
    return totals
  }, {})).sort((a, b) => b[1] - a[1])
  const statuses = Object.entries(incidents.reduce((totals, incident) => {
    const status = incident.event_status || 'unknown'
    totals[status] = (totals[status] || 0) + 1
    return totals
  }, {})).sort((a, b) => b[1] - a[1])

  return (
    <>
      <div className="summary-grid">
        <SummaryCard label="Total incidents" value={incidents.length} note="Incident records" />
        <SummaryCard label="High / critical" value={highPriority} note="Priority or severity" tone="red" />
        <SummaryCard label="Review required" value={needsReview} note="Validation or claim state" tone="amber" />
        <SummaryCard label="Reporting sources" value={sources.size} note="Cited by these incidents" tone="green" />
      </div>

      <section className="panel attention-panel">
        <div className="panel-heading">
          <div>
            <span className="eyebrow">PRIORITY QUEUE</span>
            <h2>Attention required</h2>
          </div>
          <div className="queue-actions">
            {attentionRecords.length > attention.length ? (
              <span className="queue-count">Top {attention.length} of {attentionRecords.length}</span>
            ) : null}
            <button className="text-button" type="button" onClick={() => onNavigate('incidents')}>
              View all incidents <span aria-hidden="true">→</span>
            </button>
          </div>
        </div>
        {attention.length ? (
          <div className="attention-list">
            {attention.map((incident, index) => (
              <IncidentRow incident={incident} index={index} key={incident.incident_id} onSelect={onSelect} />
            ))}
          </div>
        ) : (
          <EmptyState title="No incidents currently require attention">
            No high-priority or review-required incident records are available.
          </EmptyState>
        )}
      </section>

      <div className="overview-grid">
        <section className="panel recent-panel">
          <div className="panel-heading">
            <div><span className="eyebrow">EVENT TIMELINE</span><h2>Recent incidents</h2></div>
            <button className="text-button" type="button" onClick={() => onNavigate('incidents')}>Incident register →</button>
          </div>
          {recent.length ? (
            <div className="recent-list">
              {recent.map((incident) => (
                <button
                  type="button"
                  className="recent-row"
                  key={incident.incident_id}
                  onClick={() => onSelect(incident.incident_id)}
                >
                  <span className="recent-dot" aria-hidden="true" />
                  <span className="recent-copy">
                    <strong>{incident.title || 'Untitled incident'}</strong>
                    <small>{formatDateTime(incident.event_time)} · {incidentLocation(incident)}</small>
                  </span>
                  <SeverityIndicator value={incident.severity} />
                </button>
              ))}
            </div>
          ) : <EmptyState title="No incidents available" />}
          <p className="panel-footnote">Ordered by event time. The Incident v2.0 response does not expose created or updated timestamps.</p>
        </section>

        <section className="panel activity-panel">
          <div className="panel-heading">
            <div><span className="eyebrow">CURRENT REGISTER</span><h2>District activity</h2></div>
          </div>
          <p className="activity-summary">
            {incidents.length} incident {incidents.length === 1 ? 'record' : 'records'} across {categories.length} reported {categories.length === 1 ? 'category' : 'categories'}.
          </p>
          <h3>By category</h3>
          <div className="breakdown-list">
            {categories.map(([category, amount]) => (
              <div className="breakdown-row" key={category}>
                <span>{category.replaceAll('_', ' ')}</span>
                <span className="breakdown-track"><span style={{ width: `${incidents.length ? (amount / incidents.length) * 100 : 0}%` }} /></span>
                <strong>{amount}</strong>
              </div>
            ))}
          </div>
          <h3>Reported status</h3>
          <div className="status-summary-list">
            {statuses.map(([status, amount]) => (
              <span key={status}>{status.replaceAll('_', ' ')} <strong>{amount}</strong></span>
            ))}
          </div>
          {sources.size ? <p className="panel-footnote">Sources cited: {[...sources].join(', ')}</p> : null}
        </section>
      </div>
    </>
  )
}

function NewsView({ incidents, onSelect }) {
  const news = incidents.filter(isNewsIncident)
  return (
    <>
      <div className="information-note">
        <strong>Source-backed intelligence</strong>
        <p>These are incident records derived from news sources. A report and its extracted claims are not, by themselves, independent confirmation that an event occurred.</p>
      </div>
      <IncidentTable incidents={news} total={news.length} onSelect={onSelect} />
    </>
  )
}

function WeatherView({ incidents, onSelect }) {
  const weather = sortByAttention(incidents.filter(isWeatherIncident))
  if (!weather.length) {
    return (
      <EmptyState title="No weather-related incidents recorded">
        No weather alerts are present in the incident feed. This dashboard does not provide a live forecast.
      </EmptyState>
    )
  }
  return (
    <>
      <div className="information-note information-warning">
        <strong>Incident records only</strong>
        <p>These alerts reflect stored intelligence records and source evidence; they are not a live weather forecast.</p>
      </div>
      <IncidentTable incidents={weather} total={weather.length} onSelect={onSelect} />
    </>
  )
}

function AgricultureView({ incidents, onSelect }) {
  const agriculture = incidents.filter(isAgricultureIncident)
  const marketObservations = agriculture.filter((incident) =>
    incident.provenance?.record_type?.toLowerCase().includes('market'))

  return (
    <>
      <div className="information-note information-green">
        <strong>Incidents and observations are distinct</strong>
        <p>Market-source records may describe an observation or announcement rather than a verified incident. Only values present in the incident fields or cited evidence are shown.</p>
      </div>
      {marketObservations.length ? (
        <section className="market-observations">
          <div className="panel-heading">
            <div><span className="eyebrow">SOURCE RECORDS</span><h2>Market observations</h2></div>
            <span className="record-count">{marketObservations.length}</span>
          </div>
          <div className="market-list">
            {marketObservations.map((incident) => (
              <article className="market-observation" key={incident.incident_id}>
                <div className="market-observation-main">
                  <span className="observation-label">Observation · {incident.validation?.state?.replaceAll('_', ' ') || 'validation not specified'}</span>
                  <button type="button" className="market-title" onClick={() => onSelect(incident.incident_id)}>
                    {incident.title || 'Untitled market record'}
                  </button>
                  <span>{incidentLocation(incident)} · {formatDateTime(incident.event_time)}</span>
                  {incident.evidence?.length ? (
                    <ul className="market-evidence">
                      {incident.evidence.slice(0, 3).map((item) => (
                        <li key={item.evidence_id}>
                          <span>{item.field || 'Source excerpt'}</span>
                          {item.quote || 'No excerpt supplied'}
                        </li>
                      ))}
                    </ul>
                  ) : null}
                </div>
                <SeverityIndicator value={incident.severity} />
              </article>
            ))}
          </div>
        </section>
      ) : null}
      <IncidentTable incidents={agriculture} total={agriculture.length} onSelect={onSelect} />
    </>
  )
}

function MapView({ incidents }) {
  const mapped = incidents.flatMap((incident) => (incident.locations || [])
    .filter((location) => Number.isFinite(location.latitude) && Number.isFinite(location.longitude))
    .map((location) => ({ ...location, incident })))

  if (!mapped.length) {
    return (
      <EmptyState title="Map data unavailable">
        No incident locations currently include resolved GIS coordinates. Place names are shown in incident records; no map pins or coordinates have been inferred.
      </EmptyState>
    )
  }

  return (
    <section className="panel mapped-locations">
      <div className="panel-heading">
        <div><span className="eyebrow">RESOLVED GIS LOCATIONS</span><h2>Incident coordinates</h2></div>
        <span className="record-count">{mapped.length} locations</span>
      </div>
      <ul>
        {mapped.map(({ incident, ...location }, index) => (
          <li key={`${incident.incident_id}-${location.location_id}-${index}`}>
            <span><strong>{location.text}</strong><small>{incident.title || incident.incident_id}</small></span>
            <code>{location.latitude}, {location.longitude}</code>
          </li>
        ))}
      </ul>
    </section>
  )
}

function SourcesView({ incidents }) {
  const sources = useMemo(() => {
    const index = new Map()
    incidents.forEach((incident) => {
      const references = [
        ...(incident.provenance ? [{
          source_id: incident.provenance.source_id,
          source_type: incident.provenance.source_type,
          source_url: incident.provenance.source_url,
          record_id: incident.provenance.record_id,
        }] : []),
        ...(incident.evidence || []),
      ]
      references.forEach((reference) => {
        if (!reference.source_id) return
        const source = index.get(reference.source_id) || {
          source_id: reference.source_id,
          source_types: new Set(),
          incidents: new Map(),
          urls: new Set(),
          record_ids: new Set(),
        }
        if (reference.source_type) source.source_types.add(reference.source_type)
        if (reference.source_url) source.urls.add(reference.source_url)
        if (reference.record_id) source.record_ids.add(reference.record_id)
        source.incidents.set(incident.incident_id, incident)
        index.set(reference.source_id, source)
      })
    })
    return [...index.values()].sort((a, b) => a.source_id.localeCompare(b.source_id))
  }, [incidents])

  if (!sources.length) {
    return <EmptyState title="No cited sources available">Sources will appear here when an incident includes source provenance or evidence.</EmptyState>
  }
  return (
    <div className="source-grid">
      {sources.map((source) => (
        <article className="source-card" key={source.source_id}>
          <span className="source-icon" aria-hidden="true">↗</span>
          <h2>{source.source_id}</h2>
          <p>{[...source.source_types].join(', ') || 'Source type not specified'}</p>
          <dl>
            <div><dt>Incident records</dt><dd>{source.incidents.size}</dd></div>
            <div><dt>Source records cited</dt><dd>{source.record_ids.size}</dd></div>
          </dl>
          {[...source.urls].map((url) => {
            const safeUrl = safeHttpUrl(url)
            return safeUrl ? (
              <a className="evidence-link" href={safeUrl} key={url} target="_blank" rel="noopener noreferrer">
                Open cited source <span aria-hidden="true">↗</span>
              </a>
            ) : (
              <span className="unsafe-url-note" key={url}>Source link is not a valid HTTP(S) URL.</span>
            )
          })}
        </article>
      ))}
    </div>
  )
}

export default function Dashboard({
  view,
  incidents,
  status,
  error,
  onRetry,
  onSelectIncident,
  onNavigate,
}) {
  const [filters, setFilters] = useState(INITIAL_FILTERS)
  const filteredIncidents = useMemo(() => incidents.filter((incident) =>
    matchesSearch(incident, filters.search) && compareIncidentFilters(incident, filters)),
  [incidents, filters])
  const pageCount = status !== 'ready'
    ? undefined
    : view === 'overview' || view === 'incidents'
      ? incidents.length
      : view === 'news'
        ? incidents.filter(isNewsIncident).length
        : view === 'weather'
          ? incidents.filter(isWeatherIncident).length
          : view === 'agriculture'
            ? incidents.filter(isAgricultureIncident).length
            : undefined
  const changeFilter = (key, value) => setFilters((current) => ({ ...current, [key]: value }))
  const clearFilters = () => setFilters(INITIAL_FILTERS)

  return (
    <div className="dashboard-page">
      <PageIntro view={view} count={pageCount} onRefresh={onRetry} />
      {status === 'loading' ? <LoadingState /> : null}
      {status === 'error' ? <ErrorState message={error} onRetry={onRetry} /> : null}
      {status === 'empty' ? (
        <EmptyState title="No incidents recorded">
          The API is connected, but the incident register currently contains no records.
        </EmptyState>
      ) : null}
      {status === 'ready' && view === 'overview' ? (
        <Overview incidents={incidents} onSelect={onSelectIncident} onNavigate={onNavigate} />
      ) : null}
      {status === 'ready' && view === 'incidents' ? (
        <>
          <FilterBar incidents={incidents} filters={filters} onChange={changeFilter} onClear={clearFilters} />
          <IncidentTable incidents={filteredIncidents} total={incidents.length} onSelect={onSelectIncident} />
        </>
      ) : null}
      {status === 'ready' && view === 'news' ? (
        <NewsView incidents={incidents} onSelect={onSelectIncident} />
      ) : null}
      {status === 'ready' && view === 'weather' ? (
        <WeatherView incidents={incidents} onSelect={onSelectIncident} />
      ) : null}
      {status === 'ready' && view === 'agriculture' ? (
        <AgricultureView incidents={incidents} onSelect={onSelectIncident} />
      ) : null}
      {status === 'ready' && view === 'map' ? <MapView incidents={incidents} /> : null}
      {status === 'ready' && view === 'sources' ? <SourcesView incidents={incidents} /> : null}
    </div>
  )
}
