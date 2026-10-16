import { useMemo, useState } from 'react'
import { formatDateTime, safeHttpUrl } from '../utils/incidents.js'
import {
  commonRecordDataEntries,
  commonRecordLocation,
  searchCommonRecords,
  sortCommonRecords,
} from '../utils/commonRecords.js'
import { EmptyState, SeverityIndicator } from './Shared.jsx'

export default function CommonRecordList({ records, sourceLabel, emptyTitle, emptyText }) {
  const [search, setSearch] = useState('')
  const [sort, setSort] = useState('recent')
  const visibleRecords = useMemo(
    () => sortCommonRecords(searchCommonRecords(records, search), sort),
    [records, search, sort],
  )

  if (!records.length) {
    return <EmptyState title={emptyTitle}>{emptyText}</EmptyState>
  }

  return (
    <section className="market-observations">
      <div className="panel-heading">
        <div>
          <span className="eyebrow">SOURCE RECORDS</span>
          <h2>{sourceLabel}</h2>
        </div>
        <span className="record-count">{visibleRecords.length}</span>
      </div>
      <div className="common-record-controls">
        <label className="search-control">
          <span className="visually-hidden">Search {sourceLabel.toLowerCase()}</span>
          <span className="search-icon" aria-hidden="true">⌕</span>
          <input
            type="search"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder={`Search ${sourceLabel.toLowerCase()}…`}
          />
        </label>
        <label className="sort-control">
          <span>Sort by</span>
          <select value={sort} onChange={(event) => setSort(event.target.value)}>
            <option value="recent">Most recent record</option>
            <option value="oldest">Oldest record</option>
            <option value="source">Source</option>
          </select>
        </label>
      </div>
      {visibleRecords.length ? (
        <div className="market-list">
          {visibleRecords.map((record) => {
            const sourceUrl = safeHttpUrl(record.source_url)
            const details = commonRecordDataEntries(record)
            return (
              <article className="market-observation" key={record.record_id}>
                <div className="market-observation-main">
                  <span className="observation-label">
                    {String(record.record_type || 'source record').replaceAll('_', ' ')}
                  </span>
                  <strong className="market-title">{record.title || 'Untitled source record'}</strong>
                  <span>
                    {commonRecordLocation(record)} · {formatDateTime(record.event_time)}
                  </span>
                  <span>
                    {record.source_id || record.source_type || 'Source not specified'}
                    {' · '}{record.record_id}
                  </span>
                  {details.length ? (
                    <ul className="market-evidence">
                      {details.map(([field, value]) => (
                        <li key={field}><span>{field}</span>{value}</li>
                      ))}
                    </ul>
                  ) : null}
                  {sourceUrl ? (
                    <a className="market-title" href={sourceUrl} target="_blank" rel="noopener noreferrer">
                      Open source record
                    </a>
                  ) : null}
                </div>
                {record.severity ? <SeverityIndicator value={record.severity} /> : null}
              </article>
            )
          })}
        </div>
      ) : (
        <EmptyState title={`No ${sourceLabel.toLowerCase()} match this search`}>
          Clear the search to see all source records.
        </EmptyState>
      )}
    </section>
  )
}
