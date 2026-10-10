import { uniqueValues } from '../utils/incidents.js'

function FilterSelect({ label, value, options, onChange }) {
  return (
    <label className="filter-control">
      <span>{label}</span>
      <select value={value} onChange={(event) => onChange(event.target.value)}>
        <option value="">All {label.toLowerCase()}</option>
        {options.map((option) => (
          <option value={option} key={option}>{option.replaceAll('_', ' ')}</option>
        ))}
      </select>
    </label>
  )
}

export default function FilterBar({ incidents, filters, onChange, onClear }) {
  const locations = [...new Set(incidents.flatMap((incident) => incident.locations || [])
    .flatMap((location) => [
      location.village,
      location.taluk,
      location.block,
      location.normalized_name,
      location.text,
      location.district,
    ]).filter(Boolean))].sort((a, b) => a.localeCompare(b))
  const hasFilters = Object.values(filters).some(Boolean)

  return (
    <form className="filter-bar" onSubmit={(event) => event.preventDefault()}>
      <label className="search-control">
        <span className="visually-hidden">Search incidents</span>
        <span className="search-icon" aria-hidden="true">⌕</span>
        <input
          type="search"
          value={filters.search}
          onChange={(event) => onChange('search', event.target.value)}
          placeholder="Search title, source, location, evidence…"
        />
      </label>
      <div className="filter-fields">
        <FilterSelect
          label="Severity"
          value={filters.severity}
          options={uniqueValues(incidents, (incident) => incident.severity)}
          onChange={(value) => onChange('severity', value)}
        />
        <FilterSelect
          label="Priority"
          value={filters.priority}
          options={uniqueValues(incidents, (incident) => incident.priority)}
          onChange={(value) => onChange('priority', value)}
        />
        <FilterSelect
          label="Category"
          value={filters.category}
          options={uniqueValues(incidents, (incident) => incident.category)}
          onChange={(value) => onChange('category', value)}
        />
        <FilterSelect
          label="Status"
          value={filters.status}
          options={uniqueValues(incidents, (incident) => incident.event_status)}
          onChange={(value) => onChange('status', value)}
        />
        <FilterSelect
          label="Location"
          value={filters.location}
          options={locations}
          onChange={(value) => onChange('location', value)}
        />
      </div>
      {incidents.some((incident) => incident.event_time) ? (
        <div className="date-filters">
          <label className="filter-control">
            <span>From</span>
            <input
              type="date"
              value={filters.startDate}
              onChange={(event) => onChange('startDate', event.target.value)}
            />
          </label>
          <label className="filter-control">
            <span>To</span>
            <input
              type="date"
              value={filters.endDate}
              onChange={(event) => onChange('endDate', event.target.value)}
            />
          </label>
        </div>
      ) : null}
      <button
        className="button button-clear"
        type="button"
        disabled={!hasFilters}
        onClick={onClear}
      >
        Clear filters
      </button>
    </form>
  )
}
