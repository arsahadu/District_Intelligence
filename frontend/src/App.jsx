import { useCallback, useEffect, useRef, useState } from 'react'
import AppLayout from './components/AppLayout.jsx'
import IncidentDetailPanel from './components/IncidentDetailPanel.jsx'
import GovernmentDocuments from './pages/GovernmentDocuments.jsx'
import Dashboard from './pages/Dashboard.jsx'
import GISMapView from './pages/GISMapView.jsx'
import { getIncident, getIncidents, getRecords } from './services/api.js'
import { isIncidentRecord } from './utils/incidents.js'
import './App.css'
import './CommandCentre.css'

const VIEWS = new Set([
  'overview', 'incidents', 'documents', 'news', 'sources', 'weather', 'agriculture', 'map',
])

function viewFromLocation() {
  const view = new URLSearchParams(window.location.search).get('view')
  return VIEWS.has(view) ? view : 'overview'
}

function App() {
  const [view, setView] = useState(viewFromLocation)
  const [incidents, setIncidents] = useState([])
  const [commonRecords, setCommonRecords] = useState([])
  const [commonRecordStatus, setCommonRecordStatus] = useState('loading')
  const [commonRecordError, setCommonRecordError] = useState('')
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

  const navigate = useCallback((nextView, selection = null) => {
    const url = new URL(window.location.href)
    url.searchParams.set('view', nextView)
    if (selection?.recordKind && selection?.recordId) {
      url.searchParams.set('recordKind', selection.recordKind)
      url.searchParams.set('recordId', selection.recordId)
    } else {
      url.searchParams.delete('recordKind')
      url.searchParams.delete('recordId')
    }
    window.history.pushState(null, '', `${url.pathname}${url.search}${url.hash}`)
    setView(nextView)
  }, [])

  useEffect(() => {
    const syncView = () => setView(viewFromLocation())
    window.addEventListener('popstate', syncView)
    return () => window.removeEventListener('popstate', syncView)
  }, [])

  useEffect(() => {
    let active = true
    async function loadIncidents() {
      setStatus('loading')
      setConnection('loading')
      setError('')
      try {
        const data = await getIncidents()
        if (!active) return
        const incidentDocuments = data.filter(isIncidentRecord)
        setIncidents(data)
        setStatus(incidentDocuments.length ? 'ready' : 'empty')
        setConnection('connected')
        setLastUpdated(new Intl.DateTimeFormat('en-IN', {
          timeZone: 'Asia/Kolkata',
          hour: 'numeric',
          minute: '2-digit',
        }).format(new Date()))
      } catch {
        if (!active) return
        setStatus('error')
        setConnection('disconnected')
        setError('Incident information is temporarily unavailable. Please try again.')
      }
    }
    void loadIncidents()
    return () => { active = false }
  }, [refreshKey])

  useEffect(() => {
    let active = true
    async function loadCommonRecords() {
      setCommonRecordStatus('loading')
      setCommonRecordError('')
      try {
        const data = await getRecords()
        if (!active) return
        setCommonRecords(data)
        setCommonRecordStatus('ready')
      } catch {
        if (!active) return
        setCommonRecordStatus('error')
        setCommonRecordError('District information is temporarily unavailable. Please try again.')
      }
    }
    void loadCommonRecords()
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
      } catch {
        if (!active) return
        setDetailStatus('error')
        setDetailError('Incident details are temporarily unavailable. Please try again.')
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
  const selectMapRecord = useCallback((recordKind, recordId) => {
    setSelectedId(null)
    setSelectedIncident(null)
    navigate('map', { recordKind, recordId })
  }, [navigate])

  return (
    <>
      <AppLayout
        activeView={view}
        onNavigate={navigate}
        connection={connection}
        lastUpdated={lastUpdated}
        onRefresh={view === 'map' ? undefined : refresh}
        showConnection={view !== 'map'}
      >
        <div hidden={view === 'documents' || view === 'map'}>
          <Dashboard
            view={view === 'documents' || view === 'map' ? 'overview' : view}
            incidents={incidents}
            commonRecords={commonRecords}
            commonRecordStatus={commonRecordStatus}
            commonRecordError={commonRecordError}
            status={status}
            error={error}
            connection={connection}
            lastUpdated={lastUpdated}
            onRetry={refresh}
            onSelectIncident={selectIncident}
            onSelectMapRecord={selectMapRecord}
            onNavigate={navigate}
          />
        </div>
        {view === 'documents' ? <GovernmentDocuments /> : null}
        {view === 'map' ? <GISMapView onSelectIncident={selectIncident} /> : null}
      </AppLayout>
      {selectedId ? (
        <IncidentDetailPanel
          incident={selectedIncident}
          loading={detailStatus === 'loading'}
          error={detailStatus === 'error' ? detailError : ''}
          onRetry={retryDetails}
          onClose={closeDetails}
          onSelectMapRecord={selectMapRecord}
        />
      ) : null}
    </>
  )
}

export default App
