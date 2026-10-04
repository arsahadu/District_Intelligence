import { useEffect, useState } from 'react'
import Header from '../components/Header.jsx'
import StatusCard from '../components/StatusCard.jsx'
import { API_BASE_URL, getHealth, getRecords } from '../services/api.js'
import '../App.css'

function formatDateTime(value) {
  if (!value) return '—'

  const date = new Date(value)
  if (Number.isNaN(date.getTime())) {
    return value
  }

  return date.toLocaleString([], {
    dateStyle: 'medium',
    timeStyle: 'short',
  })
}

function getRecordPreview(record) {
  const payload = record.data ?? {}
  const content = payload.content ?? payload.forecast ?? payload.commodity ?? ''

  if (typeof content !== 'string') {
    return 'No preview available.'
  }

  return content.length > 160 ? `${content.slice(0, 157)}...` : content
}

function Dashboard() {
  const [connection, setConnection] = useState({
    status: 'checking',
    service: null,
    lastChecked: null,
  })
  const [records, setRecords] = useState([])
  const [recordsStatus, setRecordsStatus] = useState('loading')
  const [refreshKey, setRefreshKey] = useState(0)

  useEffect(() => {
    let isActive = true

    async function checkBackend() {
      try {
        const health = await getHealth()
        if (isActive) {
          setConnection({
            status: 'connected',
            service: health.service,
            lastChecked: new Date().toLocaleTimeString(),
          })
        }
      } catch {
        if (isActive) {
          setConnection({
            status: 'disconnected',
            service: null,
            lastChecked: new Date().toLocaleTimeString(),
          })
        }
      }
    }

    async function fetchRecords() {
      try {
        setRecordsStatus('loading')
        const items = await getRecords()
        if (!isActive) return

        const nextRecords = Array.isArray(items) ? items : []
        setRecords(nextRecords)
        setRecordsStatus(nextRecords.length === 0 ? 'empty' : 'ready')
      } catch {
        if (!isActive) return
        setRecords([])
        setRecordsStatus('error')
      }
    }

    void checkBackend()
    void fetchRecords()
    const intervalId = window.setInterval(() => {
      void checkBackend()
      void fetchRecords()
    }, 10000)

    return () => {
      isActive = false
      window.clearInterval(intervalId)
    }
  }, [refreshKey])

  return (
    <div className="app-shell">
      <Header />
      <main className="dashboard-main">
        <div className="dashboard-heading">
          <div>
            <p className="eyebrow">PLATFORM OVERVIEW</p>
            <h2>System status</h2>
            <p className="dashboard-description">
              Monitor the connection to the District Intelligence API.
            </p>
          </div>
          <div className="environment-label"><span aria-hidden="true" />Development</div>
        </div>

        <StatusCard
          status={connection.status}
          service={connection.service}
          apiUrl={API_BASE_URL}
          lastChecked={connection.lastChecked}
          onRetry={() => setRefreshKey((key) => key + 1)}
        />

        <section className="records-panel">
          <div className="records-header">
            <h3>Stored records</h3>
            <span>{records.length} total</span>
          </div>

          {recordsStatus === 'loading' ? (
            <p className="records-empty">Loading records...</p>
          ) : recordsStatus === 'error' ? (
            <p className="records-empty">Unable to load records. Please check the backend connection.</p>
          ) : recordsStatus === 'empty' ? (
            <p className="records-empty">No records available yet.</p>
          ) : (
            <div className="record-list">
              {records.map((record) => (
                <article key={record.record_id} className="record-card">
                  <div className="record-card-top">
                    <div>
                      <p className="record-type">{record.source_id} · {record.record_type}</p>
                      <h4>{record.title}</h4>
                    </div>
                    {record.source_url ? (
                      <a
                        className="source-link"
                        href={record.source_url}
                        target="_blank"
                        rel="noreferrer"
                      >
                        View Source ↗
                      </a>
                    ) : null}
                  </div>

                  <div className="record-meta-grid">
                    <div>
                      <span className="meta-label">District</span>
                      <strong>{record.location?.district || '—'}</strong>
                    </div>
                    <div>
                      <span className="meta-label">Event time</span>
                      <strong>{formatDateTime(record.event_time)}</strong>
                    </div>
                    <div>
                      <span className="meta-label">Status</span>
                      <strong>{record.status || '—'}</strong>
                    </div>
                    <div>
                      <span className="meta-label">Severity</span>
                      <strong>{record.severity || '—'}</strong>
                    </div>
                  </div>

                  <p className="record-preview">{getRecordPreview(record)}</p>
                </article>
              ))}
            </div>
          )}
        </section>

        <footer className="page-footer">District Intelligence Platform</footer>
      </main>
    </div>
  )
}

export default Dashboard