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
        <div><dt>Source</dt><dd>{(evidence.source_id || 'Source not specified').replaceAll('_', ' ')}</dd></div>
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
