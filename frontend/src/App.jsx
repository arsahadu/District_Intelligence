import { useCallback, useEffect, useRef, useState } from 'react'
import AppLayout from './components/AppLayout.jsx'
import IncidentDetailPanel from './components/IncidentDetailPanel.jsx'
import Dashboard from './pages/Dashboard.jsx'
import { getIncident, getIncidents } from './services/api.js'
import './App.css'

function App() {
  const [view, setView] = useState('overview')
  const [incidents, setIncidents] = useState([])
  const [status, setStatus] = useState('loading')
  const [connection, setConnection] = useState('loading')
  const [error, setError] = useState('')
  const [lastUpdated, setLastUpdated] = useState('')
  const [refreshKey, setRefreshKey] = useState(0)
  const [selectedId, setSelectedId] = useState(null)
  const [selectedIncident, setSelectedIncident] = useState(null)
  const [detailStatus, setDetailStatus] = useState('loading')
  const [detailError, setDetailError] = useState('')
  const [detailRetryKey, setDetailRetryKey] = useState(0)
  const returnFocus = useRef(null)

  useEffect(() => {
    let active = true
    async function loadIncidents() {
      setStatus('loading')
      setConnection('loading')
      setError('')
      try {
        const data = await getIncidents()
        if (!active) return
        setIncidents(data)
        setStatus(data.length ? 'ready' : 'empty')
        setConnection('connected')
        setLastUpdated(new Intl.DateTimeFormat('en-IN', {
          timeZone: 'Asia/Kolkata',
          hour: 'numeric',
          minute: '2-digit',
        }).format(new Date()))
      } catch (requestError) {
        if (!active) return
        setStatus('error')
        setConnection('disconnected')
        setError(requestError instanceof Error
          ? requestError.message
          : 'An unexpected error occurred while requesting incidents.')
      }
    }
    void loadIncidents()
    return () => { active = false }
  }, [refreshKey])

  useEffect(() => {
    if (!selectedId) return undefined
    let active = true
    async function loadIncident() {
      setDetailStatus('loading')
      setSelectedIncident(null)
      setDetailError('')
      try {
        const incident = await getIncident(selectedId)
        if (!active) return
        setSelectedIncident(incident)
        setDetailStatus('ready')
      } catch (requestError) {
        if (!active) return
        setDetailStatus('error')
        setDetailError(requestError instanceof Error
          ? requestError.message
          : 'Unable to retrieve incident details.')
      }
    }
    void loadIncident()
    return () => { active = false }
  }, [selectedId, detailRetryKey])

  const closeDetails = useCallback(() => {
    setSelectedId(null)
    setSelectedIncident(null)
    window.requestAnimationFrame(() => returnFocus.current?.focus())
  }, [])
  const retryDetails = useCallback(() => {
    setDetailRetryKey((current) => current + 1)
  }, [])
  const refresh = useCallback(() => {
    setRefreshKey((current) => current + 1)
  }, [])
  const selectIncident = useCallback((incidentId) => {
    returnFocus.current = document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null
    setSelectedId(incidentId)
  }, [])

  return (
    <>
      <AppLayout
        activeView={view}
        onNavigate={setView}
        connection={connection}
        lastUpdated={lastUpdated}
        onRefresh={refresh}
      >
        <Dashboard
          view={view}
          incidents={incidents}
          status={status}
          error={error}
          onRetry={refresh}
          onSelectIncident={selectIncident}
          onNavigate={setView}
        />
      </AppLayout>
      {selectedId ? (
        <IncidentDetailPanel
          incident={selectedIncident}
          loading={detailStatus === 'loading'}
          error={detailStatus === 'error' ? detailError : ''}
          onRetry={retryDetails}
          onClose={closeDetails}
        />
      ) : null}
    </>
  )
}

export default App
