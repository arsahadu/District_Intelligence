const COMMON_RECORD_SOURCES = new Set(['news', 'agriculture', 'weather'])
const NEWS_SOURCE_IDS = new Set(['dinamalar'])

export function classifyCommonRecordSource(record) {
  const sourceType = String(record?.source_type || '').trim().toLowerCase()
  const recordType = String(record?.record_type || '').trim().toLowerCase()
  const sourceId = String(record?.source_id || '').trim().toLowerCase()
  if (sourceType === 'agriculture' || sourceType === 'weather') return sourceType
  if (recordType === 'market_price' || recordType === 'forecast') return 'other'
  if (sourceType === 'news'
    || (!sourceType && NEWS_SOURCE_IDS.has(sourceId) && recordType === 'article')) {
    return 'news'
  }
  return COMMON_RECORD_SOURCES.has(sourceType) ? sourceType : 'other'
}

export function commonRecordsForSource(records, sourceType) {
  return records.filter((record) => classifyCommonRecordSource(record) === sourceType)
}

export function searchCommonRecords(records, query) {
  const normalizedQuery = query.trim().toLocaleLowerCase()
  if (!normalizedQuery) return records

  return records.filter((record) => [
    record.record_id,
    record.source_id,
    record.source_type,
    record.record_type,
    record.title,
    record.location?.raw_text,
    record.location?.district,
    record.location?.state,
    JSON.stringify(record.data || {}),
  ].filter(Boolean).join(' ').toLocaleLowerCase().includes(normalizedQuery))
}

export function sortCommonRecords(records, sort) {
  const timestamp = (record) => Date.parse(record.event_time || '') || 0
  return [...records].sort((first, second) => {
    if (sort === 'oldest') return timestamp(first) - timestamp(second)
    if (sort === 'source') {
      return String(first.source_id || '').localeCompare(String(second.source_id || ''))
    }
    return timestamp(second) - timestamp(first)
  })
}

export function commonRecordLocation(record) {
  const location = record.location || {}
  const values = [
    location.raw_text,
    location.district,
    location.state,
  ].filter(Boolean).filter((value, index, all) => all.indexOf(value) === index)
  const parts = values.filter((value, index) => !values.slice(0, index).some((earlier) =>
    earlier.toLocaleLowerCase().includes(value.toLocaleLowerCase())))
  return parts.join(', ')
    || 'Location unavailable'
}

export function adaptCommonRecordForDisplay(record) {
  const data = record?.data && typeof record.data === 'object' && !Array.isArray(record.data)
    ? record.data
    : {}
  const sourceLabels = {
    dinamalar: 'Dinamalar',
    imd_city_madurai: 'India Meteorological Department',
    tn_agri_marketing: 'Tamil Nadu Agriculture Marketing',
  }
  const sourceId = String(record?.source_id || record?.source_type || 'Source not specified')
  const location = record?.location || {}
  const rawLocation = String(location.raw_text || '').trim()
  const normalizedRawLocation = rawLocation.toLocaleLowerCase()
  const stationId = String(data.station_id || '').trim()
  const category = classifyCommonRecordSource(record)
  const isRegisteredMaduraiWeatherStation = category === 'weather'
    && sourceId.toLocaleLowerCase() === 'imd_city_madurai'
    && stationId === '43360'
  const isDistrictOnly = Boolean(rawLocation && location.district
    && normalizedRawLocation === String(location.district).toLocaleLowerCase())
    || ['madurai', 'மதுரை'].includes(normalizedRawLocation)
  return {
    category,
    recordKind: 'context',
    id: String(record?.record_id || ''),
    title: String(record?.title || 'Untitled record'),
    summary: String(data.summary || data.content || '').trim(),
    source: sourceLabels[sourceId] || sourceId.replaceAll('_', ' '),
    sourceUrl: record?.source_url || null,
    timestamp: record?.event_time || null,
    location: commonRecordLocation(record),
    locationPrecision: isRegisteredMaduraiWeatherStation
      ? 'Approximate weather-station location'
      : isDistrictOnly ? 'District-level location' : null,
    commodity: data.commodity || null,
    market: data.market || null,
    priceUnit: data.unit || null,
    minPrice: data.min_price ?? null,
    maxPrice: data.max_price ?? null,
    modalPrice: data.modal_price ?? null,
    weatherType: String(record?.record_type || '').toLowerCase() === 'forecast'
      ? 'Forecast'
      : 'Observation',
    weatherCondition: data.warning || data.forecast || data.condition || null,
    minTemperature: data.min_temp_c ?? null,
    maxTemperature: data.max_temp_c ?? null,
    morningHumidity: data.relative_humidity_0830 ?? null,
    eveningHumidity: data.relative_humidity_1730 ?? null,
    stationId: data.station_id || null,
    warning: data.warning || null,
    original: record,
  }
}

export function commonRecordDataEntries(record) {
  const data = record.data && typeof record.data === 'object' && !Array.isArray(record.data)
    ? record.data
    : {}
  const unit = data.unit
  return Object.entries(data)
    .filter(([field, value]) => field !== 'language' && value !== null && value !== '')
    .map(([field, value]) => {
      let display = typeof value === 'object' ? JSON.stringify(value) : String(value)
      if (field.endsWith('_c')) display += ' °C'
      else if (field.startsWith('relative_humidity_')) display += '%'
      else if ((field === 'min_price' || field === 'max_price') && unit) display += ` / ${unit}`
      return [field.replaceAll('_', ' '), display]
    })
}
