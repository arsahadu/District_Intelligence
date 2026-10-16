/**
 * @typedef {Object} MapFilters
 * @property {string} recordType
 * @property {string} [search]
 * @property {string} [category]
 * @property {string} [severity]
 * @property {string} [status]
 * @property {string} [taluk]
 * @property {string} [startDate]
 * @property {string} [endDate]
 * @property {string} [alert]
 * @property {string} [commodity]
 */

/**
 * Apply the same map filters to mapped and unmapped records.
 * @param {Array<{
 *   record_kind?: string, source_type?: string, search_string?: string,
 *   category?: string, severity?: string, status?: string, taluk?: string,
 *   date_only?: string, details?: Record<string, unknown>
 * }>} records
 * @param {MapFilters} filters
 */
export function filterMapRecords(records, filters) {
  return records.filter((record) => {
    const recordType = record.record_kind === 'incident' ? 'incidents' : record.source_type
    if (filters.recordType !== 'all' && recordType !== filters.recordType) return false
    if (filters.search && !String(record.search_string || '')
      .includes(filters.search.toLocaleLowerCase())) return false
    if (filters.category && record.record_kind === 'incident'
      && record.category !== filters.category) return false
    if (filters.severity && record.record_kind === 'incident'
      && record.severity !== filters.severity) return false
    if (filters.status && record.record_kind === 'incident'
      && record.status !== filters.status) return false
    if (filters.taluk && record.taluk !== filters.taluk) return false
    if (filters.startDate && (!record.date_only || record.date_only < filters.startDate)) return false
    if (filters.endDate && (!record.date_only || record.date_only > filters.endDate)) return false
    if (filters.alert === 'warning'
      && (record.source_type !== 'weather' || !record.details?.warning)) return false
    if (filters.alert === 'none'
      && (record.source_type !== 'weather' || record.details?.warning)) return false
    if (filters.commodity && record.details?.commodity !== filters.commodity) return false
    return true
  })
}
