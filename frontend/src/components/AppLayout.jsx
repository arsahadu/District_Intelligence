import { useCallback, useEffect, useRef, useState } from 'react'

const NAV_ITEMS = [
  { id: 'overview', label: 'Overview', icon: '◫' },
  { id: 'incidents', label: 'Incidents', icon: '≡' },
  { id: 'news', label: 'News Intelligence', icon: '▤' },
  { id: 'weather', label: 'Weather', icon: '◌' },
  { id: 'agriculture', label: 'Agriculture & Markets', icon: '⌁' },
  { id: 'map', label: 'Map View', icon: '⌖' },
  { id: 'sources', label: 'Sources', icon: '↗' },
]

function Sidebar({ activeView, onNavigate, open, onClose, sidebarRef }) {
  return (
    <>
      {open ? (
        <button
          className="sidebar-scrim"
          aria-label="Close navigation"
          type="button"
          onClick={onClose}
        />
      ) : null}
      <aside ref={sidebarRef} className={`sidebar ${open ? 'sidebar-open' : ''}`}>
        <div className="brand">
          <div className="brand-mark" aria-hidden="true">DI</div>
          <div className="brand-copy">
            <span>District</span>
            <strong>Intelligence</strong>
          </div>
          <button
            className="icon-button sidebar-close"
            type="button"
            aria-label="Close navigation"
            onClick={onClose}
          >
            ×
          </button>
        </div>

        <p className="nav-label">WORKSPACE</p>
        <nav className="primary-nav" id="primary-navigation" aria-label="Main navigation">
          {NAV_ITEMS.map((item) => (
            <button
              className={`nav-item ${activeView === item.id ? 'nav-item-active' : ''}`}
              type="button"
              key={item.id}
              aria-current={activeView === item.id ? 'page' : undefined}
              onClick={() => {
                onNavigate(item.id)
                onClose()
              }}
            >
              <span className="nav-icon" aria-hidden="true">{item.icon}</span>
              <span>{item.label}</span>
            </button>
          ))}
        </nav>

        <div className="sidebar-footer">
          <span className="sidebar-footer-mark" aria-hidden="true">MD</span>
          <span><strong>Madurai District</strong><small>Tamil Nadu, India</small></span>
        </div>
      </aside>
    </>
  )
}

export default function AppLayout({
  activeView,
  onNavigate,
  connection,
  lastUpdated,
  onRefresh,
  children,
}) {
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const sidebarRef = useRef(null)
  const menuButton = useRef(null)
  const closeSidebar = useCallback(() => {
    const wasOpen = sidebarOpen
    setSidebarOpen(false)
    if (wasOpen) menuButton.current?.focus()
  }, [sidebarOpen])
  useEffect(() => {
    if (!sidebarOpen) return undefined
    sidebarRef.current?.querySelector('.nav-item')?.focus()
    function handleEscape(event) {
      if (event.key === 'Escape') closeSidebar()
    }
    window.addEventListener('keydown', handleEscape)
    return () => window.removeEventListener('keydown', handleEscape)
  }, [sidebarOpen, closeSidebar])
  const date = new Intl.DateTimeFormat('en-IN', {
    timeZone: 'Asia/Kolkata',
    weekday: 'short',
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  }).format(new Date())

  return (
    <div className="app-layout">
      <Sidebar
        activeView={activeView}
        onNavigate={onNavigate}
        open={sidebarOpen}
        onClose={closeSidebar}
        sidebarRef={sidebarRef}
      />
      <div className="main-column">
        <header className="topbar">
          <button
            ref={menuButton}
            className="icon-button menu-toggle"
            type="button"
            aria-label="Open navigation"
            aria-expanded={sidebarOpen}
            aria-controls="primary-navigation"
            onClick={() => setSidebarOpen(true)}
          >
            <span aria-hidden="true">☰</span>
          </button>
          <div className="district-context">
            <strong>Madurai District</strong>
            <span>Tamil Nadu</span>
          </div>
          <div className="topbar-tools">
            <time className="today-date" dateTime={new Date().toISOString()}>
              {date}
            </time>
            <span className={`api-status api-${connection}`} role="status">
              <span aria-hidden="true" />
              {connection === 'connected'
                ? 'API connected'
                : connection === 'loading'
                  ? 'Connecting'
                  : 'API unavailable'}
            </span>
            <button
              className="button button-refresh"
              type="button"
              onClick={onRefresh}
              disabled={connection === 'loading'}
              title={lastUpdated ? `Last refreshed ${lastUpdated}` : 'Refresh incidents'}
            >
              <span aria-hidden="true">↻</span>
              <span className="refresh-label">Refresh</span>
            </button>
          </div>
        </header>
        <main className="main-content">
          {children}
          <footer className="page-footer">
            <span>District Intelligence · Madurai</span>
            {lastUpdated ? <span>Data refreshed {lastUpdated}</span> : null}
          </footer>
        </main>
      </div>
    </div>
  )
}
