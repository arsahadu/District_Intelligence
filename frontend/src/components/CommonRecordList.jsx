import { useMemo, useState } from 'react'
import { formatDateTime, safeHttpUrl } from '../utils/incidents.js'
import {
  adaptCommonRecordForDisplay,
  searchCommonRecords,
  sortCommonRecords,
} from '../utils/commonRecords.js'
import { EmptyState } from './Shared.jsx'

const PAGE_SIZE = 12

function uniqueRecordValues(records, selector) {
  return [...new Set(records.map(selector).filter(Boolean))]
    .sort((first, second) => String(first).localeCompare(String(second)))
}

function recordLocationFilterValue(record) {
  const location = record.location?.raw_text || record.location?.district || ''
  const stationId = record.data?.station_id
  return stationId ? `${location || 'Weather station'} (${stationId})` : location
}

function newsOutcome(record, intelligenceRecords) {
  const recordId = String(record.record_id || '')
  const associated = intelligenceRecords.filter((item) => {
    const provenanceId = item.provenance?.record_id
    const evidenceIds = (item.evidence || []).map((evidence) => evidence.record_id)
    return provenanceId === recordId || evidenceIds.includes(recordId)
  })
  const accepted = associated.find((item) => (
    item.record_kind === 'incident' && item.validation?.state === 'accepted'
  ))
  if (accepted) return { label: 'Accepted incident', assessment: accepted }
  const needsReview = associated.find((item) => (
    ['incident', 'unresolved'].includes(item.record_kind)
    && item.validation?.state
    && item.validation.state !== 'accepted'
  ))
  if (needsReview) return { label: 'Needs review', assessment: needsReview }
  if (associated.some((item) => item.record_kind === 'context')) {
    return { label: 'Information only', assessment: null }
  }
  return { label: 'News', assessment: null }
}

function PriceFacts({ record }) {
  const unit = record.priceUnit ? ` / ${record.priceUnit}` : ''
  const hasMinimum = record.minPrice !== null
  const hasMaximum = record.maxPrice !== null
  const hasModal = record.modalPrice !== null
  if (!hasMinimum && !hasMaximum && !hasModal) return null
  return (
    <dl className="record-facts">
      {record.commodity ? <div><dt>Commodity</dt><dd>{record.commodity}</dd></div> : null}
      {record.market ? <div><dt>Market</dt><dd>{record.market}</dd></div> : null}
      {hasMinimum || hasMaximum ? (
        <div>
          <dt>{hasMinimum && hasMaximum ? 'Price range' : 'Price reported'}</dt>
          <dd>
            {hasMinimum && hasMaximum
              ? `₹${record.minPrice}–₹${record.maxPrice}${unit}`
              : `₹${record.minPrice ?? record.maxPrice}${unit}`}
          </dd>
        </div>
      ) : null}
      {hasModal ? <div><dt>Modal price</dt><dd>₹{record.modalPrice}{unit}</dd></div> : null}
    </dl>
  )
}

function WeatherFacts({ record }) {
  return (
    <>
      {record.warning ? <p className="record-warning">{record.warning}</p> : null}
      {!record.warning && record.weatherCondition ? (
        <p className="record-weather-condition">{record.weatherCondition}</p>
      ) : null}
      {(
        record.minTemperature !== null
        || record.maxTemperature !== null
        || record.morningHumidity !== null
        || record.eveningHumidity !== null
        || record.stationId
      ) ? (
        <dl className="record-facts">
          {record.minTemperature !== null ? (
            <div><dt>Minimum temperature</dt><dd>{record.minTemperature} °C</dd></div>
          ) : null}
          {record.maxTemperature !== null ? (
            <div><dt>Maximum temperature</dt><dd>{record.maxTemperature} °C</dd></div>
          ) : null}
          {record.morningHumidity !== null ? (
            <div><dt>Relative humidity (08:30)</dt><dd>{record.morningHumidity}%</dd></div>
          ) : null}
          {record.eveningHumidity !== null ? (
            <div><dt>Relative humidity (17:30)</dt><dd>{record.eveningHumidity}%</dd></div>
          ) : null}
          {record.stationId ? <div><dt>Weather station</dt><dd>{record.stationId}</dd></div> : null}
        </dl>
      ) : null}
    </>
  )
}

export default function CommonRecordList({
  records,
  sourceLabel,
  emptyTitle,
  emptyText,
  category,
  incidents = [],
  onSelectMap,
  onSelectIncident,
}) {
  const [search, setSearch] = useState('')
  const [commodity, setCommodity] = useState('')
  const [market, setMarket] = useState('')
  const [location, setLocation] = useState('')
  const [date, setDate] = useState('')
  const [alert, setAlert] = useState('all')
  const [sort, setSort] = useState('recent')
  const [page, setPage] = useState(1)
  const commodities = useMemo(() => uniqueRecordValues(records, (record) => record.data?.commodity), [records])
  const markets = useMemo(() => uniqueRecordValues(records, (record) =>
    record.data?.market || record.location?.raw_text), [records])
  const locations = useMemo(() => uniqueRecordValues(records, (record) =>
    recordLocationFilterValue(record)), [records])
  const hasWeatherAlerts = records.some((record) => record.data?.warning)

  const visibleRecords = useMemo(() => {
    const searched = searchCommonRecords(records, search)
    const selected = searched.filter((record) => {
      const recordDate = record.event_time ? String(record.event_time).slice(0, 10) : ''
      const recordMarket = record.data?.market || record.location?.raw_text || ''
      const hasAlert = Boolean(record.data?.warning)
      return (!commodity || record.data?.commodity === commodity)
        && (!market || recordMarket === market)
        && (!location || recordLocationFilterValue(record) === location)
        && (!date || recordDate === date)
        && (alert === 'all' || (alert === 'warning' ? hasAlert : !hasAlert))
    })
    return sortCommonRecords(selected, sort)
  }, [records, search, commodity, market, location, date, alert, sort])
  const pageCount = Math.max(1, Math.ceil(visibleRecords.length / PAGE_SIZE))
  const currentPage = Math.min(page, pageCount)
  const pageRecords = visibleRecords.slice(
    (currentPage - 1) * PAGE_SIZE,
    currentPage * PAGE_SIZE,
  )

  if (!records.length) {
    return <EmptyState title={emptyTitle}>{emptyText}</EmptyState>
  }

  return (
    <section className="market-observations">
      <div className="panel-heading">
        <div>
          <span className="eyebrow">DISTRICT INFORMATION</span>
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
            onChange={(event) => {
              setSearch(event.target.value)
              setPage(1)
            }}
            placeholder="Search by title, source, or location…"
          />
        </label>
        {(category === 'agriculture' && commodities.length) ? (
          <label className="record-filter-control">
            <span>Commodity</span>
            <select value={commodity} onChange={(event) => { setCommodity(event.target.value); setPage(1) }}>
              <option value="">All commodities</option>
              {commodities.map((value) => <option key={value} value={value}>{value}</option>)}
            </select>
          </label>
        ) : null}
        {(category === 'agriculture' && markets.length) ? (
          <label className="record-filter-control">
            <span>Market</span>
            <select value={market} onChange={(event) => { setMarket(event.target.value); setPage(1) }}>
              <option value="">All markets</option>
              {markets.map((value) => <option key={value} value={value}>{value}</option>)}
            </select>
          </label>
        ) : null}
        {(['news', 'weather'].includes(category) && locations.length) ? (
          <label className="record-filter-control">
            <span>{category === 'weather' ? 'Station or location' : 'Location'}</span>
            <select value={location} onChange={(event) => { setLocation(event.target.value); setPage(1) }}>
              <option value="">All locations</option>
              {locations.map((value) => <option key={value} value={value}>{value}</option>)}
            </select>
          </label>
        ) : null}
        {category === 'weather' && hasWeatherAlerts ? (
          <label className="record-filter-control">
            <span>Alert status</span>
            <select value={alert} onChange={(event) => { setAlert(event.target.value); setPage(1) }}>
              <option value="all">All forecasts</option>
              <option value="warning">Weather warning</option>
              <option value="none">No warning</option>
            </select>
          </label>
        ) : null}
        <label className="record-filter-control">
          <span>{category === 'weather' ? 'Forecast date' : 'Date'}</span>
          <input type="date" value={date} onChange={(event) => { setDate(event.target.value); setPage(1) }} />
        </label>
        <label className="record-filter-control">
          <span>Order</span>
          <select value={sort} onChange={(event) => { setSort(event.target.value); setPage(1) }}>
            <option value="recent">Most recent</option>
            <option value="oldest">Oldest</option>
          </select>
        </label>
      </div>
      {pageRecords.length ? (
        <div className="record-card-grid">
          {pageRecords.map((sourceRecord) => {
            const record = adaptCommonRecordForDisplay(sourceRecord)
            const sourceUrl = safeHttpUrl(record.sourceUrl)
            const intelligenceStatus = category === 'news'
              ? newsOutcome(sourceRecord, incidents)
              : null
            return (
              <article className={`district-record-card record-card-${category}`} key={record.id}>
                <div className="district-record-card-heading">
                  <span className="observation-label">
                    {category === 'weather'
                      ? record.weatherType
                      : category === 'agriculture'
                        ? 'Market price'
                        : 'District news'}
                  </span>
                  {intelligenceStatus ? (
                    <span className={`news-outcome news-outcome-${intelligenceStatus.label.toLowerCase().replaceAll(' ', '-')}`}>
                      {intelligenceStatus.label}
                    </span>
                  ) : null}
                </div>
                <h3>{record.title}</h3>
                {category === 'agriculture' ? <PriceFacts record={record} /> : null}
                {category === 'weather' ? <WeatherFacts record={record} /> : null}
                {category === 'news' && record.summary ? (
                  <p className="record-summary">
                    {record.summary.length > 280 ? `${record.summary.slice(0, 280)}…` : record.summary}
                  </p>
                ) : null}
                <div className="record-meta">
                  <span>{record.location}</span>
                  {record.locationPrecision ? <small>{record.locationPrecision}</small> : null}
                  <time dateTime={record.timestamp || undefined}>
                    {category === 'weather' && record.weatherType === 'Forecast' ? 'Forecast for ' : ''}
                    {formatDateTime(record.timestamp)}
                  </time>
                  <span className="record-source">{record.source}</span>
                </div>
                <div className="district-record-actions">
                  <button
                    className="button button-secondary"
                    type="button"
                    onClick={() => onSelectMap?.(record.recordKind, record.id)}
                  >
                    View on map
                  </button>
                  {intelligenceStatus?.assessment?.incident_id ? (
                    <button
                      className="text-button"
                      type="button"
                      onClick={() => onSelectIncident?.(intelligenceStatus.assessment.incident_id)}
                    >
                      {intelligenceStatus.label === 'Needs review' ? 'View review details' : 'View assessment'}
                    </button>
                  ) : null}
                  {sourceUrl ? (
                    <a href={sourceUrl} target="_blank" rel="noopener noreferrer">
                      {category === 'news' ? 'View original article ↗' : 'View source ↗'}
                    </a>
                  ) : <span className="record-source-missing">Source link unavailable</span>}
                </div>
              </article>
            )
          })}
        </div>
      ) : (
        <EmptyState title={`No ${sourceLabel.toLowerCase()} match these filters`}>
          Adjust or clear the search and filters to see available records.
        </EmptyState>
      )}
      {visibleRecords.length > PAGE_SIZE ? (
        <div className="pagination">
          <span>
            Showing {(currentPage - 1) * PAGE_SIZE + 1}–{Math.min(currentPage * PAGE_SIZE, visibleRecords.length)}
            {' '}of {visibleRecords.length}
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
