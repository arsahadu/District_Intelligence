import { safeHttpUrl } from '../utils/incidents.js'

function EvidenceCard({ evidence }) {
  const url = safeHttpUrl(evidence.source_url)
  return (
    <article className="evidence-item">
      {evidence.quote ? (
        <blockquote>“{evidence.quote}”</blockquote>
      ) : (
        <p className="evidence-no-quote">No source excerpt was provided for this evidence item.</p>
      )}
      <dl className="evidence-meta">
        <div><dt>Source</dt><dd>{evidence.source_id || 'Not specified'} · {evidence.source_type || 'type not specified'}</dd></div>
        <div><dt>Source record</dt><dd>{evidence.record_id || 'Not specified'}</dd></div>
        {evidence.field ? <div><dt>Source field</dt><dd>{evidence.field}</dd></div> : null}
        {Number.isInteger(evidence.char_start) || Number.isInteger(evidence.char_end) ? (
          <div><dt>Character offsets</dt><dd>{evidence.char_start ?? '—'}–{evidence.char_end ?? '—'}</dd></div>
        ) : null}
        {evidence.span_validation ? <div><dt>Span validation</dt><dd>{evidence.span_validation.replaceAll('_', ' ')}</dd></div> : null}
        {evidence.method ? <div><dt>Extraction method</dt><dd>{evidence.method.replaceAll('_', ' ')}</dd></div> : null}
        {Number.isFinite(evidence.confidence) ? (
          <div><dt>Evidence confidence</dt><dd>{Math.round(evidence.confidence * 100)}%</dd></div>
        ) : null}
      </dl>
      {url ? (
        <a className="evidence-link" href={url} target="_blank" rel="noopener noreferrer">
          Open source record <span aria-hidden="true">↗</span>
        </a>
      ) : evidence.source_url ? (
        <span className="unsafe-url-note">Source link is not a valid HTTP(S) URL.</span>
      ) : null}
    </article>
  )
}

export default function EvidenceList({ evidence = [] }) {
  if (!evidence.length) {
    return <p className="muted-note">No source evidence is attached to this incident.</p>
  }

  return (
    <div className="evidence-list">
      {evidence.map((item) => <EvidenceCard evidence={item} key={item.evidence_id} />)}
    </div>
  )
}
