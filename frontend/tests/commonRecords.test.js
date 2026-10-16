import assert from 'node:assert/strict'
import test from 'node:test'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { createServer } from 'vite'
import { getRecords } from '../src/services/api.js'
import { isIncidentRecord } from '../src/utils/incidents.js'
import {
  classifyCommonRecordSource,
  commonRecordDataEntries,
  commonRecordsForSource,
  commonRecordLocation,
  searchCommonRecords,
  sortCommonRecords,
} from '../src/utils/commonRecords.js'

const records = [
  {
    record_id: 'NEWS-1',
    source_id: 'dinamalar',
    source_type: 'news',
    record_type: 'article',
    title: 'News item',
    event_time: '2026-10-02T12:00:00',
    location: { raw_text: 'Madurai', district: 'Madurai', state: 'Tamil Nadu' },
    data: { content: 'news body' },
  },
  {
    record_id: 'AGRI-1',
    source_id: 'tn_agri_marketing',
    source_type: 'agriculture',
    record_type: 'market_price',
    title: 'Tomato price - Anna nagar',
    event_time: '2026-10-03T00:00:00',
    location: { raw_text: 'Anna nagar Uzhavar Santhai, Madurai', district: 'Madurai' },
    source_url: 'https://agrimark.tn.gov.in/price',
    data: { commodity: 'Tomato', min_price: 46, max_price: 50, unit: 'kg' },
  },
  {
    record_id: 'WEATHER-1',
    source_id: 'imd',
    source_type: 'weather',
    record_type: 'forecast',
    title: 'Madurai Weather Forecast',
    event_time: '2026-10-04T00:00:00',
    location: { district: 'Madurai' },
    source_url: 'https://mausam.imd.gov.in/',
    data: { min_temp_c: 24.5, relative_humidity_0830: 71, forecast: 'Cloudy' },
  },
]

test('getRecords requests and returns actual CommonRecords from the backend API', async () => {
  const originalFetch = globalThis.fetch
  let requestedUrl
  globalThis.fetch = async (url) => {
    requestedUrl = url
    return { ok: true, json: async () => records }
  }
  try {
    assert.deepEqual(await getRecords(), records)
    assert.equal(requestedUrl, 'http://localhost:8000/records')
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('weather and agriculture filters deterministically select their actual CommonRecords', () => {
  assert.deepEqual(
    commonRecordsForSource(records, 'agriculture').map((record) => record.record_id),
    ['AGRI-1'],
  )
  assert.deepEqual(
    commonRecordsForSource(records, 'weather').map((record) => record.record_id),
    ['WEATHER-1'],
  )
  assert.equal(classifyCommonRecordSource({ source_type: 'irrigation_board' }), 'other')
  assert.equal(classifyCommonRecordSource({
    source_id: 'dinamalar',
    source_type: '',
    record_type: 'article',
  }), 'news')
})

test('incident totals exclude routine agriculture and weather documents', () => {
  const documents = [
    { incident_id: 'INC-NEWS-1', record_kind: 'incident', provenance: { source_type: 'news' } },
    { incident_id: 'INC-AGRI-1', record_kind: 'context', provenance: { source_type: 'agriculture' } },
    { incident_id: 'INC-WX-1', record_kind: 'forecast', provenance: { source_type: 'weather' } },
  ]

  assert.deepEqual(documents.filter(isIncidentRecord).map((record) => record.incident_id), [
    'INC-NEWS-1',
  ])
})

test('CommonRecord search, sorting, location and source fields use returned record values', () => {
  const agriculture = commonRecordsForSource(records, 'agriculture')
  assert.deepEqual(searchCommonRecords(agriculture, 'Tomato'), agriculture)
  assert.deepEqual(sortCommonRecords(records, 'recent').map((record) => record.record_id), [
    'WEATHER-1', 'AGRI-1', 'NEWS-1',
  ])
  assert.equal(commonRecordLocation(agriculture[0]), 'Anna nagar Uzhavar Santhai, Madurai')
  assert.deepEqual(commonRecordDataEntries(agriculture[0]), [
    ['commodity', 'Tomato'],
    ['min price', '46 / kg'],
    ['max price', '50 / kg'],
    ['unit', 'kg'],
  ])
  assert.deepEqual(commonRecordDataEntries(records[2]).slice(0, 2), [
    ['min temp c', '24.5 °C'],
    ['relative humidity 0830', '71%'],
  ])
})

test('weather and agriculture record views render real backend values and source links', async () => {
  const root = fileURLToPath(new URL('..', import.meta.url))
  const vite = await createServer({
    root,
    configFile: path.join(root, 'vite.config.js'),
    server: { middlewareMode: true },
    appType: 'custom',
  })
  try {
    const { default: CommonRecordList } = await vite.ssrLoadModule(
      '/src/components/CommonRecordList.jsx',
    )
    const agricultureHtml = renderToStaticMarkup(React.createElement(CommonRecordList, {
      records: commonRecordsForSource(records, 'agriculture'),
      sourceLabel: 'Agriculture and market records',
      emptyTitle: 'No agriculture records',
      emptyText: 'No records.',
    }))
    const weatherHtml = renderToStaticMarkup(React.createElement(CommonRecordList, {
      records: commonRecordsForSource(records, 'weather'),
      sourceLabel: 'Weather records',
      emptyTitle: 'No weather records',
      emptyText: 'No records.',
    }))

    assert.match(agricultureHtml, /Tomato price - Anna nagar/)
    assert.match(agricultureHtml, /46 \/ kg/)
    assert.match(agricultureHtml, /https:\/\/agrimark\.tn\.gov\.in\/price/)
    assert.match(weatherHtml, /Madurai Weather Forecast/)
    assert.match(weatherHtml, /24\.5 °C/)
    assert.match(weatherHtml, /71%/)
    assert.match(weatherHtml, /https:\/\/mausam\.imd\.gov\.in\//)
  } finally {
    await vite.close()
  }
})
