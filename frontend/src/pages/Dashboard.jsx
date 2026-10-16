import { useMemo, useState } from 'react'
import FilterBar from '../components/FilterBar.jsx'
import IncidentTable from '../components/IncidentTable.jsx'
import CommonRecordList from '../components/CommonRecordList.jsx'
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
  isHighPriority,
  isIncidentRecord,
  isNewsIncident,
  matchesSearch,
  requiresReview,
  safeHttpUrl,
  sortByAttention,
} from '../utils/incidents.js'
import { commonRecordsForSource } from '../utils/commonRecords.js'

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
  weather: ['Weather', 'Weather forecasts and observations returned as CommonRecords by the platform API.'],
  agriculture: ['Agriculture & Markets', 'Market prices and agriculture observations returned as CommonRecords by the platform API.'],
  map: ['Map View', 'Location references from the incident register.'],
  sources: ['Sources', 'Source records and references cited by available incidents.'],
}

function PageIntro({ view, count, onRefresh }) {
  const [title, description] = PAGE_INFO[view]
  const countLabel = view === 'weather' || view === 'agriculture' ? 'record' : 'incident'
  return (
    <div className="page-intro">
      <div>
        <p className="eyebrow">DISTRICT INTELLIGENCE / {title.toUpperCase()}</p>
        <h1>{title}</h1>
        <p>{description}</p>
      </div>
      <div className="intro-actions">
        {Number.isInteger(count) ? (
          <span className="record-count">
            {count} {countLabel}{count === 1 ? '' : 's'}
          </span>
        ) : null}
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

function Overview({ incidents, connection, lastUpdated, onSelect, onNavigate }) {
  const incidentRecords = incidents.filter(isIncidentRecord)
  const highPriority = incidentRecords.filter(isHighPriority).length
  const needsReview = incidentRecords.filter(requiresReview).length
  const sources = new Set(incidentRecords.flatMap((incident) => [
    incident.provenance?.source_id,
    ...(incident.evidence || []).map((item) => item.source_id),
  ]).filter(Boolean))
  const attentionRecords = sortByAttention(incidentRecords.filter((incident) =>
    isHighPriority(incident) || requiresReview(incident)))
  const attention = attentionRecords.slice(0, 6)
  const recent = [...incidentRecords]
    .sort((a, b) => (Date.parse(b.event_time || '') || 0) - (Date.parse(a.event_time || '') || 0))
    .slice(0, 5)

  const categories = Object.entries(incidentRecords.reduce((totals, incident) => {
    const category = incident.category || 'unresolved'
    totals[category] = (totals[category] || 0) + 1
    return totals
  }, {})).sort((a, b) => b[1] - a[1])
  const statuses = Object.entries(incidentRecords.reduce((totals, incident) => {
    const status = incident.event_status || 'unknown'
    totals[status] = (totals[status] || 0) + 1
    return totals
  }, {})).sort((a, b) => b[1] - a[1])

  return (
    <>
      <div className="overview-statusline" role="status">
        <span className={`statusline-indicator statusline-${connection}`} aria-hidden="true" />
        <span className="statusline-label">Incident feed</span>
        <strong>
          {connection === 'connected'
            ? 'Connected'
            : connection === 'loading'
              ? 'Connecting'
              : 'Unavailable'}
        </strong>
        <span className="statusline-divider" aria-hidden="true" />
        <span>{lastUpdated ? `Last refreshed ${lastUpdated}` : 'No successful refresh recorded'}</span>
      </div>

      <div className="summary-grid summary-grid-overview">
        <SummaryCard
          label="Incident register"
          value={incidentRecords.length}
          note="Structured district records"
          featured
        />
        <SummaryCard label="High / critical" value={highPriority} note="Priority or severity" tone="red" />
        <SummaryCard label="Review required" value={needsReview} note="Validation or claim state" tone="amber" />
        <SummaryCard label="Cited sources" value={sources.size} note="Distinct source identifiers" tone="green" />
      </div>

      <div className="command-overview-grid">
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
                View register <span aria-hidden="true">→</span>
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

        <section className="panel activity-panel">
          <div className="panel-heading">
            <div><span className="eyebrow">CURRENT REGISTER</span><h2>District activity</h2></div>
          </div>
          <p className="activity-summary">
            {incidentRecords.length} incident {incidentRecords.length === 1 ? 'record' : 'records'} across {categories.length} reported {categories.length === 1 ? 'category' : 'categories'}.
          </p>
          <h3>By category</h3>
          {categories.length ? (
            <div className="breakdown-list">
              {categories.map(([category, amount]) => (
                <div className="breakdown-row" key={category}>
                  <span>{category.replaceAll('_', ' ')}</span>
                  <span
                    className="breakdown-track"
                    role="img"
                    aria-label={`${amount} of ${incidentRecords.length} incidents`}
                  >
                    <span style={{ width: `${incidentRecords.length ? (amount / incidentRecords.length) * 100 : 0}%` }} />
                  </span>
                  <strong>{amount}</strong>
                </div>
              ))}
            </div>
          ) : <p className="activity-empty">Category totals will appear when incident records are available.</p>}
          <h3>Reported status</h3>
          {statuses.length ? (
            <div className="status-summary-list">
              {statuses.map(([status, amount]) => (
                <span key={status}>{status.replaceAll('_', ' ')} <strong>{amount}</strong></span>
              ))}
            </div>
          ) : <p className="activity-empty">No status values have been supplied.</p>}
          <div className="overview-shortcuts" aria-label="District modules">
            <button type="button" onClick={() => onNavigate('documents')}>Government documents <span aria-hidden="true">→</span></button>
            <button type="button" onClick={() => onNavigate('map')}>Open district map <span aria-hidden="true">→</span></button>
          </div>
          {sources.size ? <p className="panel-footnote">Source IDs cited: {[...sources].join(', ')}</p> : null}
        </section>

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

function WeatherView({ records }) {
  return (
    <>
      <div className="information-note information-warning">
        <strong>Weather source records</strong>
        <p>Forecast conditions and observations are shown from CommonRecords, separately from assessed incidents.</p>
      </div>
      <CommonRecordList
        records={records}
        sourceLabel="Weather records"
        emptyTitle="No weather records available"
        emptyText="No weather CommonRecords are present in the platform API."
      />
    </>
  )
}

function AgricultureView({ records }) {
  return (
    <>
      <div className="information-note information-green">
        <strong>Market records and incidents are distinct</strong>
        <p>Market prices and agriculture observations are shown from CommonRecords and are not incident assessments.</p>
      </div>
      <CommonRecordList
        records={records}
        sourceLabel="Agriculture and market records"
        emptyTitle="No agriculture or market records available"
        emptyText="No agriculture CommonRecords are present in the platform API."
      />
    </>
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
  commonRecords,
  commonRecordStatus,
  commonRecordError,
  status,
  error,
  connection,
  lastUpdated,
  onRetry,
  onSelectIncident,
  onNavigate,
}) {
  const [filters, setFilters] = useState(INITIAL_FILTERS)
  const incidentRecords = useMemo(() => incidents.filter(isIncidentRecord), [incidents])
  const newsIncidents = useMemo(
    () => incidentRecords.filter(isNewsIncident),
    [incidentRecords],
  )
  const weatherRecords = useMemo(
    () => commonRecordsForSource(commonRecords, 'weather'),
    [commonRecords],
  )
  const agricultureRecords = useMemo(
    () => commonRecordsForSource(commonRecords, 'agriculture'),
    [commonRecords],
  )
  const filteredIncidents = useMemo(() => incidentRecords.filter((incident) =>
    matchesSearch(incident, filters.search) && compareIncidentFilters(incident, filters)),
  [incidentRecords, filters])
  const pageCount = view === 'weather'
    ? commonRecordStatus === 'ready' ? weatherRecords.length : undefined
    : view === 'agriculture'
      ? commonRecordStatus === 'ready' ? agricultureRecords.length : undefined
      : status !== 'ready'
        ? undefined
        : view === 'overview' || view === 'incidents'
          ? incidentRecords.length
          : view === 'news'
            ? newsIncidents.length
            : undefined
  const changeFilter = (key, value) => setFilters((current) => ({ ...current, [key]: value }))
  const clearFilters = () => setFilters(INITIAL_FILTERS)
  const incidentView = view !== 'weather' && view !== 'agriculture'

  return (
    <div className="dashboard-page">
      <PageIntro view={view} count={pageCount} onRefresh={onRetry} />
      {incidentView && status === 'loading' ? <LoadingState /> : null}
      {incidentView && status === 'error' ? <ErrorState message={error} onRetry={onRetry} /> : null}
      {incidentView && status === 'empty' ? (
        <EmptyState title="No incidents recorded">
          The API is connected, but the incident register currently contains no records.
        </EmptyState>
      ) : null}
      {status === 'ready' && view === 'overview' ? (
        <Overview
          incidents={incidentRecords}
          connection={connection}
          lastUpdated={lastUpdated}
          onSelect={onSelectIncident}
          onNavigate={onNavigate}
        />
      ) : null}
      {status === 'ready' && view === 'incidents' ? (
        <>
          <FilterBar incidents={incidentRecords} filters={filters} onChange={changeFilter} onClear={clearFilters} />
          <IncidentTable incidents={filteredIncidents} total={incidentRecords.length} onSelect={onSelectIncident} />
        </>
      ) : null}
      {status === 'ready' && view === 'news' ? (
        <NewsView incidents={newsIncidents} onSelect={onSelectIncident} />
      ) : null}
      {view === 'weather' && commonRecordStatus === 'loading' ? (
        <LoadingState label="Loading weather CommonRecords" />
      ) : null}
      {view === 'weather' && commonRecordStatus === 'error' ? (
        <ErrorState
          title="Weather CommonRecords could not be loaded"
          message={commonRecordError}
          onRetry={onRetry}
        />
      ) : null}
      {view === 'weather' && commonRecordStatus === 'ready' ? (
        <WeatherView records={weatherRecords} />
      ) : null}
      {view === 'agriculture' && commonRecordStatus === 'loading' ? (
        <LoadingState label="Loading agriculture CommonRecords" />
      ) : null}
      {view === 'agriculture' && commonRecordStatus === 'error' ? (
        <ErrorState
          title="Agriculture CommonRecords could not be loaded"
          message={commonRecordError}
          onRetry={onRetry}
        />
      ) : null}
      {view === 'agriculture' && commonRecordStatus === 'ready' ? (
        <AgricultureView records={agricultureRecords} />
      ) : null}
      {status === 'ready' && view === 'sources' ? <SourcesView incidents={incidentRecords} /> : null}
    </div>
  )
}
