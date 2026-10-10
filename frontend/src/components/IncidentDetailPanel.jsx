import { useEffect, useRef } from 'react'
import EvidenceList from './EvidenceList.jsx'
import { LoadingState } from './Shared.jsx'
import {
  displayValue,
  formatDateTime,
  formatPrecision,
  incidentLocation,
  requiresReview,
  safeHttpUrl,
} from '../utils/incidents.js'
import { ReviewTag, SeverityIndicator } from './Shared.jsx'

function DetailField({ label, children }) {
  return (
    <div className="detail-field">
      <dt>{label}</dt>
      <dd>{children || 'Not specified'}</dd>
    </div>
  )
}

export default function IncidentDetailPanel({ incident, loading, error, onRetry, onClose }) {
  const panel = useRef(null)
  const closeButton = useRef(null)

  useEffect(() => {
    closeButton.current?.focus()
    function handleKeyDown(event) {
      if (event.key === 'Escape') onClose()
      if (event.key === 'Tab') {
        const focusable = panel.current?.querySelectorAll(
          'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
        )
        if (!focusable?.length) return
        const first = focusable[0]
        const last = focusable[focusable.length - 1]
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault()
          last.focus()
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault()
          first.focus()
        }
      }
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [onClose])

  const reviewRequired = incident ? requiresReview(incident) : false
  const referenceLabels = new Map([
    ...(incident?.entities || []).map((entity) => [entity.entity_id, entity.text]),
    ...(incident?.locations || []).map((location) => [
      location.location_id,
      location.normalized_name || location.text,
    ]),
  ])
  const provenanceUrl = safeHttpUrl(incident?.provenance?.source_url)

  return (
    <div className="detail-backdrop" onMouseDown={(event) => {
      if (event.target === event.currentTarget) onClose()
    }}>
      <aside
        className="detail-panel"
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-labelledby="incident-detail-title"
      >
        <header className="detail-header">
          <span className="detail-eyebrow">INCIDENT RECORD</span>
          <button
            ref={closeButton}
            className="icon-button detail-close"
            type="button"
            aria-label="Close incident details"
            onClick={onClose}
          >
            ×
          </button>
          {incident ? (
            <>
              <h2 id="incident-detail-title">{incident.title || 'Untitled incident'}</h2>
              <span className="detail-id">{incident.incident_id}</span>
              <div className="detail-head-tags">
                <SeverityIndicator value={incident.severity} />
                <ReviewTag required={reviewRequired} />
              </div>
            </>
          ) : <h2 id="incident-detail-title">Incident details</h2>}
        </header>

        {loading ? <LoadingState label="Loading incident details" /> : null}
        {error ? (
          <div className="detail-error" role="alert">
            <p>{error}</p>
            <button className="button button-secondary" type="button" onClick={onRetry}>Retry</button>
          </div>
        ) : null}
        {incident && !loading ? (
          <div className="detail-scroll">
            {reviewRequired ? (
              <div className="review-notice">
                <strong>Human review required</strong>
                <p>This record is unresolved or has claims awaiting review. Treat its assessment as provisional.</p>
              </div>
            ) : null}

            {incident.description ? (
              <section className="detail-section">
                <h3>Description</h3>
                <p className="detail-description">{incident.description}</p>
              </section>
            ) : null}

            <section className="detail-section">
              <h3>Incident summary</h3>
              <dl className="detail-grid">
                <DetailField label="Category">{displayValue(incident.category)}</DetailField>
                <DetailField label="Type">{displayValue(incident.incident_type)}</DetailField>
                <DetailField label="Priority">{displayValue(incident.priority)}</DetailField>
                <DetailField label="Status">{displayValue(incident.event_status)}</DetailField>
                <DetailField label="Event time">{formatDateTime(incident.event_time)}</DetailField>
                <DetailField label="Time precision">{formatPrecision(incident.event_time_precision)}</DetailField>
                <DetailField label="Department">{displayValue(incident.department)}</DetailField>
                <DetailField label="Location">{incidentLocation(incident)}</DetailField>
              </dl>
            </section>

            <section className="detail-section evidence-section">
              <div className="section-heading">
                <div>
                  <h3>Source evidence</h3>
                  <p>Verbatim material attached to this incident</p>
                </div>
                <span className="section-count">{incident.evidence?.length || 0}</span>
              </div>
              <EvidenceList evidence={incident.evidence} />
            </section>

            {incident.claims?.length ? (
              <section className="detail-section">
                <h3>Intelligence assessment</h3>
                <p className="assessment-disclaimer">
                  Structured assessments are pipeline output, not source quotations or independent confirmation.
                </p>
                <div className="claim-list">
                  {incident.claims.map((claim, index) => (
                    <article className="claim-item" key={`${claim.field}-${index}`}>
                      <div className="claim-heading">
                        <strong>{displayValue(claim.field)}</strong>
                        <span className={`claim-review claim-${claim.review || 'unresolved'}`}>
                          {displayValue(claim.review)}
                        </span>
                      </div>
                      <p>{displayValue(claim.value)}</p>
                      <span className="claim-method">
                        {displayValue(claim.method)}
                        {claim.confidence !== null && claim.confidence !== undefined
                          ? ` · ${Math.round(claim.confidence * 100)}% confidence`
                          : ''}
                      </span>
                      {claim.notes ? <p className="claim-notes">{claim.notes}</p> : null}
                    </article>
                  ))}
                </div>
              </section>
            ) : null}

            {(incident.entities?.length || incident.relationships?.length) ? (
              <section className="detail-section">
                <h3>Entities & relationships</h3>
                {incident.entities?.length ? (
                  <ul className="entity-list">
                    {incident.entities.map((entity) => (
                      <li key={entity.entity_id}>
                        <strong>{entity.text}</strong>
                        <span>{[entity.entity_type, entity.review].filter(Boolean).join(' · ')}</span>
                      </li>
                    ))}
                  </ul>
                ) : null}
                {incident.relationships?.length ? (
                  <ul className="relationship-list">
                    {incident.relationships.map((relationship) => (
                      <li key={relationship.relationship_id}>
                        {referenceLabels.get(relationship.from_ref) || relationship.from_ref}
                        {' → '}
                        {referenceLabels.get(relationship.to_ref) || relationship.to_ref}
                        <span>{relationship.kind}</span>
                      </li>
                    ))}
                  </ul>
                ) : null}
              </section>
            ) : null}

            <section className="detail-section">
              <h3>Validation & provenance</h3>
              <dl className="detail-grid">
                <DetailField label="Validation state">{displayValue(incident.validation?.state)}</DetailField>
                <DetailField label="Validator">{incident.validation?.validator}</DetailField>
                <DetailField label="Checked at">{formatDateTime(incident.validation?.checked_at)}</DetailField>
                <DetailField label="Source">{incident.provenance?.source_id}</DetailField>
                <DetailField label="Source type">{incident.provenance?.source_type}</DetailField>
                <DetailField label="Source record">{incident.provenance?.record_id}</DetailField>
                <DetailField label="Retrieved at">{formatDateTime(incident.provenance?.retrieved_at)}</DetailField>
                <DetailField label="Schema version">{incident.schema_version}</DetailField>
                {provenanceUrl ? (
                  <DetailField label="Source URL">
                    <a href={provenanceUrl} target="_blank" rel="noopener noreferrer">Open source</a>
                  </DetailField>
                ) : null}
              </dl>
              {incident.validation?.issues?.length ? (
                <ul className="validation-issues">
                  {incident.validation.issues.map((issue, index) => (
                    <li key={`${issue.code}-${index}`}>
                      <strong>{displayValue(issue.code)}</strong>
                      {issue.detail ? <span>{issue.detail}</span> : null}
                    </li>
                  ))}
                </ul>
              ) : null}
            </section>

            {incident.generation ? (
              <section className="detail-section">
                <h3>Generation metadata</h3>
                <dl className="detail-grid">
                  <DetailField label="Provider">{incident.generation.provider}</DetailField>
                  <DetailField label="Model">{incident.generation.model}</DetailField>
                  <DetailField label="Prompt version">{incident.generation.prompt_version}</DetailField>
                  <DetailField label="Generated at">{formatDateTime(incident.generation.generated_at)}</DetailField>
                </dl>
                {incident.generation.warnings?.length ? (
                  <ul className="validation-issues">
                    {incident.generation.warnings.map((warning, index) => <li key={index}>{warning}</li>)}
                  </ul>
                ) : null}
              </section>
            ) : null}
          </div>
        ) : null}
      </aside>
    </div>
  )
}
