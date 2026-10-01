import { useEffect, useState } from 'react'
import Header from '../components/Header.jsx'
import StatusCard from '../components/StatusCard.jsx'
import { API_BASE_URL, getHealth } from '../services/api.js'
import '../App.css'

function Dashboard() {
  const [connection, setConnection] = useState({
    status: 'checking',
    service: null,
    lastChecked: null,
  })
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

    void checkBackend()
    const intervalId = window.setInterval(checkBackend, 10000)

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

        <footer className="page-footer">District Intelligence Platform</footer>
      </main>
    </div>
  )
}

export default Dashboard