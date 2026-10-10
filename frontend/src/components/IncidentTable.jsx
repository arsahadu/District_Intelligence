import { useMemo, useState } from 'react'
import {
  formatDateTime,
  formatPrecision,
  getSourceLabel,
  incidentLocation,
  requiresReview,
  sortByAttention,
} from '../utils/incidents.js'
import { EmptyState, ReviewTag, SeverityIndicator } from './Shared.jsx'

const PAGE_SIZE = 10

function sortIncidents(incidents, sort) {
  const ordered = sortByAttention(incidents)
  if (sort === 'oldest') {
    return [...ordered].sort((a, b) =>
      (Date.parse(a.event_time || '') || 0) - (Date.parse(b.event_time || '') || 0))
  }
  if (sort === 'recent') {
    return [...ordered].sort((a, b) =>
      (Date.parse(b.event_time || '') || 0) - (Date.parse(a.event_time || '') || 0))
  }
  return ordered
}

export default function IncidentTable({ incidents, total, onSelect }) {
  const [sort, setSort] = useState('priority')
  const [page, setPage] = useState(1)
  const sorted = useMemo(() => sortIncidents(incidents, sort), [incidents, sort])
  const pageCount = Math.max(1, Math.ceil(sorted.length / PAGE_SIZE))
  const currentPage = Math.min(page, pageCount)
  const pageItems = sorted.slice((currentPage - 1) * PAGE_SIZE, currentPage * PAGE_SIZE)

  if (!incidents.length) {
    return (
      <EmptyState title="No incidents match these filters">
        Try a different search or clear the filters to see all incidents.
      </EmptyState>
    )
  }

  return (
    <section className="table-card" aria-label="Incident results">
      <div className="table-heading">
        <div>
          <h2>Incident register</h2>
          <p>{incidents.length} of {total} incidents shown</p>
        </div>
        <label className="sort-control">
          <span>Sort by</span>
          <select value={sort} onChange={(event) => setSort(event.target.value)}>
            <option value="priority">Priority & severity</option>
            <option value="recent">Most recent event</option>
            <option value="oldest">Oldest event</option>
          </select>
        </label>
      </div>
      <div className="table-scroll">
        <table className="incident-table">
          <thead>
            <tr>
              <th scope="col">Incident</th>
              <th scope="col">Category · type</th>
              <th scope="col">Severity</th>
              <th scope="col">Priority</th>
              <th scope="col">Status</th>
              <th scope="col">Location</th>
              <th scope="col">Event time</th>
              <th scope="col">Source</th>
              <th scope="col">Review</th>
            </tr>
          </thead>
          <tbody>
            {pageItems.map((incident) => (
              <tr key={incident.incident_id}>
                <td>
                  <button
                    className="incident-title-button"
                    type="button"
                    onClick={() => onSelect(incident.incident_id)}
                  >
                    <strong>{incident.title || 'Untitled incident'}</strong>
                    <span>{incident.incident_id}</span>
                  </button>
                </td>
                <td>
                  <span className="table-primary-value">{incident.category?.replaceAll('_', ' ') || 'Unresolved'}</span>
                  <span className="table-secondary-value">{incident.incident_type?.replaceAll('_', ' ') || 'Not specified'}</span>
                </td>
                <td><SeverityIndicator value={incident.severity} /></td>
                <td><span className="priority-value">{incident.priority?.replaceAll('_', ' ') || '—'}</span></td>
                <td><span className="status-value">{incident.event_status?.replaceAll('_', ' ') || 'Unknown'}</span></td>
                <td><span className="location-cell">{incidentLocation(incident)}</span></td>
                <td>
                  <span className="table-primary-value">{formatDateTime(incident.event_time)}</span>
                  {formatPrecision(incident.event_time_precision) ? (
                    <span className="table-secondary-value">{formatPrecision(incident.event_time_precision)} precision</span>
                  ) : null}
                </td>
                <td><span className="source-cell">{getSourceLabel(incident)}</span></td>
                <td><ReviewTag required={requiresReview(incident)} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {sorted.length > PAGE_SIZE ? (
        <div className="pagination">
          <span>
            Showing {(currentPage - 1) * PAGE_SIZE + 1}–{Math.min(currentPage * PAGE_SIZE, sorted.length)}
            {' '}of {sorted.length}
          </span>
          <div className="pagination-actions">
            <button
              className="button button-secondary"
              type="button"
              onClick={() => setPage((value) => Math.max(1, value - 1))}
              disabled={currentPage === 1}
            >
              Previous
            </button>
            <span>Page {currentPage} of {pageCount}</span>
            <button
              className="button button-secondary"
              type="button"
              onClick={() => setPage((value) => Math.min(pageCount, value + 1))}
              disabled={currentPage === pageCount}
            >
              Next
            </button>
          </div>
        </div>
      ) : null}
    </section>
  )
}
