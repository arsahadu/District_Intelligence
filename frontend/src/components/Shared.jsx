export function SeverityIndicator({ value }) {
  const severity = value || 'unresolved'
  return (
    <span className={`severity-tag severity-${severity}`} aria-label={`Severity: ${severity}`}>
      <span aria-hidden="true" />
      {severity.replaceAll('_', ' ')}
    </span>
  )
}

export function SummaryCard({ label, value, note, tone = 'navy' }) {
  return (
    <article className={`summary-card summary-${tone}`}>
      <span className="summary-label">{label}</span>
      <strong className="summary-value">{value}</strong>
      {note ? <span className="summary-note">{note}</span> : null}
    </article>
  )
}

export function LoadingState({ label = 'Loading incidents' }) {
  return (
    <div className="state-panel" role="status">
      <span className="loading-mark" aria-hidden="true" />
      <p>{label}</p>
    </div>
  )
}

export function EmptyState({ title, children }) {
  return (
    <div className="state-panel empty-state">
      <span className="empty-mark" aria-hidden="true">—</span>
      <h3>{title}</h3>
      {children ? <p>{children}</p> : null}
    </div>
  )
}

export function ErrorState({ message, onRetry }) {
  return (
    <div className="state-panel error-state" role="alert">
      <span className="error-mark" aria-hidden="true">!</span>
      <h3>Incidents could not be loaded</h3>
      <p>{message}</p>
      <button className="button button-secondary" type="button" onClick={onRetry}>
        Retry connection
      </button>
    </div>
  )
}

export function ReviewTag({ required }) {
  return (
    <span className={`review-tag ${required ? 'review-needed' : 'review-complete'}`}>
      {required ? 'Review required' : 'No review pending'}
    </span>
  )
}
