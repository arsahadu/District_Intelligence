function StatusCard({ status, service, apiUrl, lastChecked, onRetry }) {
  const isConnected = status === 'connected'
  const isChecking = status === 'checking'
  const badgeText = isChecking
    ? 'Checking'
    : isConnected
      ? 'Connected'
      : 'Disconnected'

  return (
    <section className="status-card" aria-labelledby="backend-status-title">
      <div className="status-card-header">
        <h3 id="backend-status-title">Backend Status</h3>
        <span className={`connection-badge ${status}`} aria-live="polite">
          {badgeText}
        </span>
      </div>

      <div className="status-card-content" aria-live="polite">
        {isChecking ? (
          <>
            <p className="status-summary">Checking backend connection</p>
            <p className="status-message">Waiting for a response from FastAPI.</p>
          </>
        ) : isConnected ? (
          <>
            <p className="status-summary">Backend: Online</p>
            <dl className="service-details">
              <div>
                <dt>API</dt>
                <dd><a href={apiUrl}>{apiUrl}</a></dd>
              </div>
              <div>
                <dt>Service</dt>
                <dd>{service}</dd>
              </div>
            </dl>
          </>
        ) : (
          <>
            <p className="status-summary">Backend: Offline</p>
            <p className="status-message">
              Unable to connect to FastAPI. Check that the backend is running.
            </p>
          </>
        )}
      </div>

      <div className="status-card-footer">
        <span className="last-checked">
          {lastChecked ? `Last checked ${lastChecked}` : 'Connection check in progress'}
        </span>
        <button
          className="retry-button"
          type="button"
          onClick={onRetry}
          disabled={isChecking}
        >
          Check now
        </button>
      </div>
    </section>
  )
}

export default StatusCard