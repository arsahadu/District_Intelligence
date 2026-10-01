import { useEffect, useState } from 'react'
import Header from '../components/Header.jsx'
import StatusCard from '../components/StatusCard.jsx'
import { API_BASE_URL, getHealth, getRecords } from '../services/api.js'
import '../App.css'

function Dashboard() {
  const [connection, setConnection] = useState({
    status: 'checking',
    service: null,
    lastChecked: null,
  })
  const [records, setRecords] = useState([])
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
        const items = await getRecords()
        if (isActive) {
          setRecords(Array.isArray(items) ? items : [])
        }
      } catch {
        if (isActive) {
          setRecords([])
        }
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

          {records.length === 0 ? (
            <p className="records-empty">No CommonRecords have been stored yet.</p>
          ) : (
            <div className="records-table-wrap">
              <table className="records-table">
                <thead>
                  <tr>
                    <th>Record ID</th>
                    <th>Source</th>
                    <th>Type</th>
                    <th>District</th>
                    <th>Title</th>
                  </tr>
                </thead>
                <tbody>
                  {records.map((record) => (
                    <tr key={record.record_id}>
                      <td>{record.record_id}</td>
                      <td>{record.source_id}</td>
                      <td>{record.record_type}</td>
                      <td>{record.location?.district || '—'}</td>
                      <td>{record.title}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>

        <footer className="page-footer">District Intelligence Platform</footer>
      </main>
    </div>
  )
}

export default Dashboard