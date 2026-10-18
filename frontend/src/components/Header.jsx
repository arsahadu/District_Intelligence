function Header() {
  return (
    <header className="site-header">
      <div className="header-inner">
        <div className="brand-mark" aria-hidden="true">
          <img src="/logo.png" alt="Tamilnadu District Intelligence" className="brand-logo-img" />
        </div>
        <div>
          <div className="header-badge-row">
            <p className="eyebrow">TAMILNADU DISTRICT INTELLIGENCE</p>
            <span className="brand-badge">TNDI</span>
          </div>
          <h1>Tamilnadu District Intelligence Platform</h1>
          <p className="header-subtitle">Office of the District Collector · Government of Tamil Nadu</p>
        </div>
      </div>
    </header>
  )
}

export default Header