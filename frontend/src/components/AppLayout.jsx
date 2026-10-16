import { useCallback, useEffect, useRef, useState } from 'react'

const NAV_ITEMS = [
  { id: 'overview', label: 'Overview', icon: 'overview', group: 'Command' },
  { id: 'incidents', label: 'Incidents', icon: 'incidents', group: 'Intelligence' },
  { id: 'documents', label: 'Government Documents', icon: 'documents', group: 'Intelligence' },
  { id: 'news', label: 'News Intelligence', icon: 'news', group: 'Intelligence' },
  { id: 'sources', label: 'Sources', icon: 'sources', group: 'Intelligence' },
  { id: 'weather', label: 'Weather', icon: 'weather', group: 'District systems' },
  { id: 'agriculture', label: 'Agriculture & Markets', icon: 'agriculture', group: 'District systems' },
  { id: 'map', label: 'Map View', icon: 'map', group: 'District systems' },
]

function NavigationIcon({ name }) {
  const common = {
    fill: 'none',
    stroke: 'currentColor',
    strokeLinecap: 'round',
    strokeLinejoin: 'round',
    strokeWidth: 1.7,
    viewBox: '0 0 24 24',
    'aria-hidden': true,
  }

  const icons = {
    overview: <><rect x="3.5" y="3.5" width="7" height="7" rx="1" /><rect x="13.5" y="3.5" width="7" height="7" rx="1" /><rect x="3.5" y="13.5" width="7" height="7" rx="1" /><path d="M14 14h6.5M14 17.5h6.5M14 21h4" /></>,
    incidents: <><path d="M8 5h12M8 12h12M8 19h12" /><circle cx="4" cy="5" r=".8" /><circle cx="4" cy="12" r=".8" /><circle cx="4" cy="19" r=".8" /></>,
    documents: <><path d="M6 3.5h8l4 4V20a.5.5 0 0 1-.5.5h-11A.5.5 0 0 1 6 20z" /><path d="M14 3.5v4h4M9 12h6M9 16h6" /></>,
    news: <><path d="M5 5.5h14v13H5zM8 9h8M8 12h8M8 15h5" /><path d="M3 8v11a1.5 1.5 0 0 0 1.5 1.5H17" /></>,
    sources: <><circle cx="12" cy="12" r="8.5" /><path d="M9.5 14.5 15 9m-4.5 0H15v4.5" /></>,
    weather: <><path d="M7 17.5h10a3.5 3.5 0 0 0 .4-7A5.5 5.5 0 0 0 7 8.5a4.5 4.5 0 0 0 0 9Z" /><path d="M8 21h.01M12 21h.01M16 21h.01" /></>,
    agriculture: <><path d="M20 4c-8 0-13 3.5-13 9a5 5 0 0 0 5 5c5.5 0 8-6 8-14Z" /><path d="M4 21c2-5 5.5-8 11-11" /></>,
    map: <><path d="m3.5 6 5-2.5 7 2.5 5-2.5v14l-5 2.5-7-2.5-5 2.5zM8.5 3.5v14M15.5 6v14" /><circle cx="12" cy="10" r="1.5" /></>,
  }

  return <svg {...common}>{icons[name]}</svg>
}

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
          <div className="brand-mark" aria-hidden="true">
            <svg viewBox="0 0 40 40" fill="none">
              <path d="M20 5.5 31 11.8v12.6L20 30.7 9 24.4V11.8L20 5.5Z" stroke="currentColor" strokeWidth="1.5" />
              <path d="M20 12v16M12.8 16.1l14.4 7.8M27.2 16.1l-14.4 7.8" stroke="currentColor" strokeWidth="1.2" opacity=".76" />
              <circle cx="20" cy="20" r="3" fill="currentColor" />
            </svg>
          </div>
          <div className="brand-copy">
            <span>District Intelligence</span>
            <strong>Collectorate Platform</strong>
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

        <nav className="primary-nav" id="primary-navigation" aria-label="Main navigation">
          {NAV_ITEMS.map((item, index) => (
            <div className="nav-entry" key={item.id}>
              {index === 0 || NAV_ITEMS[index - 1].group !== item.group
                ? <p className="nav-label">{item.group}</p>
                : null}
              <button
                className={`nav-item ${activeView === item.id ? 'nav-item-active' : ''}`}
                type="button"
                aria-current={activeView === item.id ? 'page' : undefined}
                onClick={() => {
                  onNavigate(item.id)
                  onClose()
                }}
              >
                <span className="nav-icon"><NavigationIcon name={item.icon} /></span>
                <span>{item.label}</span>
              </button>
            </div>
          ))}
        </nav>

        <div className="sidebar-footer">
          <span className="sidebar-footer-mark" aria-hidden="true">TN</span>
          <span><strong>Madurai District</strong><small>Government of Tamil Nadu</small></span>
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
  showConnection = true,
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
            <span className="menu-glyph" aria-hidden="true"><i /><i /><i /></span>
          </button>
          <div className="district-context">
            <strong>Madurai District Administration</strong>
            <span>Government of Tamil Nadu</span>
          </div>
          <div className="topbar-tools">
            <time className="today-date" dateTime={new Date().toISOString()}>
              {date}
            </time>
            {showConnection ? (
              <span className={`api-status api-${connection}`} role="status">
                <span aria-hidden="true" />
                {connection === 'connected'
                  ? 'API connected'
                  : connection === 'loading'
                    ? 'Connecting'
                    : 'API unavailable'}
              </span>
            ) : null}
            {onRefresh ? (
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
            ) : null}
          </div>
        </header>
        <main className="main-content">
          {children}
          <footer className="page-footer">
            <span>District Intelligence Platform · Office of the District Collector, Madurai</span>
            {lastUpdated ? <span>Incident feed refreshed {lastUpdated}</span> : null}
          </footer>
        </main>
      </div>
    </div>
  )
}
