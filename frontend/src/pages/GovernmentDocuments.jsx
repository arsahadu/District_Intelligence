import { useEffect, useRef, useState } from 'react'
import {
  generateDocumentInsights,
  getDocumentJob,
  uploadDocument,
} from '../services/documentIntelligence.js'

const MAX_DOCUMENT_BYTES = 20 * 1024 * 1024
const POLL_INTERVAL_MS = 4000
const TERMINAL_STATUSES = new Set(['SUCCEEDED', 'FAILED', 'PARTIAL_SUCCESS'])

function isRecord(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

function displayValue(value) {
  if (typeof value === 'string') return value.trim() || null
  if (typeof value === 'number' && Number.isFinite(value)) return String(value)
  return null
}

function formatDate(value) {
  const text = typeof value === 'string' ? displayValue(value) : null
  if (!text) return null
  const date = new Date(text)
  return Number.isNaN(date.getTime())
    ? text
    : new Intl.DateTimeFormat('en-IN', { dateStyle: 'medium', timeZone: 'Asia/Kolkata' }).format(date)
}

function formatDateTime(value) {
  if (typeof value !== 'string') return null
  const date = new Date(value)
  return Number.isNaN(date.getTime())
    ? value
    : new Intl.DateTimeFormat('en-IN', {
      dateStyle: 'medium',
      timeStyle: 'short',
      timeZone: 'Asia/Kolkata',
    }).format(date)
}

function formatBytes(bytes) {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function validateFile(file) {
  if (!file) return 'Choose a PDF document to upload.'
  if (!file.name.toLowerCase().endsWith('.pdf') || (file.type && file.type !== 'application/pdf')) {
    return 'Only PDF documents can be uploaded.'
  }
  if (file.size <= 0) return 'The selected file is empty.'
  if (file.size > MAX_DOCUMENT_BYTES) return 'The PDF exceeds the 20 MB upload limit.'
  return ''
}

function statusLabel(status) {
  if (status === 'SUCCEEDED') return 'OCR complete'
  if (status === 'FAILED') return 'OCR failed'
  if (status === 'PARTIAL_SUCCESS') return 'OCR partially complete'
  if (status === 'IN_PROGRESS') return 'OCR processing'
  return displayValue(status)?.replaceAll('_', ' ').toLowerCase() || 'Status unavailable'
}

function MetadataItem({ label, value }) {
  const text = displayValue(value)
  if (!text) return null
  return (
    <div className="document-metadata-item">
      <dt>{label}</dt>
      <dd>{text}</dd>
    </div>
  )
}

function DocumentTimeline({ job, insightsLoading, insights }) {
  const ocrSucceeded = job?.status === 'SUCCEEDED'
  const ocrFailed = job?.status === 'FAILED' || job?.status === 'PARTIAL_SUCCESS'
  const steps = [
    { label: 'Document received', complete: Boolean(job?.job_id), active: false },
    { label: 'OCR processing', complete: ocrSucceeded, active: job?.status === 'IN_PROGRESS' },
    { label: 'OCR complete', complete: ocrSucceeded, active: false, failed: ocrFailed },
    { label: 'AI insights generation', complete: Boolean(insights), active: insightsLoading },
    { label: 'Ready for human review', complete: Boolean(insights), active: false },
  ]
  return (
    <section className="document-panel document-timeline" aria-label="Document processing timeline">
      <div className="document-section-heading">
        <div><span className="eyebrow">PROCESSING STATUS</span><h2>Workflow</h2></div>
        {job?.status ? (
          <span className={`document-status status-${job.status.toLowerCase()}`} role="status">
            {statusLabel(job.status)}
          </span>
        ) : null}
      </div>
      <ol className="timeline-list">
        {steps.map((step) => (
          <li
            className={`timeline-step ${step.complete ? 'step-complete' : ''} ${step.active ? 'step-active' : ''} ${step.failed ? 'step-failed' : ''}`}
            key={step.label}
          >
            <span className="timeline-marker" aria-hidden="true">{step.complete ? '✓' : step.failed ? '!' : ''}</span>
            <span>{step.label}</span>
            {step.active ? <span className="timeline-note">In progress</span> : null}
            {step.failed ? <span className="timeline-note">Requires attention</span> : null}
          </li>
        ))}
      </ol>
    </section>
  )
}

function EvidenceReference({ quote, page }) {
  const evidenceQuote = displayValue(quote)
  const pageNumber = typeof page === 'number' && Number.isFinite(page) ? page : null
  if (!evidenceQuote && pageNumber === null) return null
  return (
    <div className="document-evidence">
      {evidenceQuote ? <blockquote>{evidenceQuote}</blockquote> : null}
      {pageNumber !== null ? <span>Source page {pageNumber}</span> : null}
    </div>
  )
}

function KeyFacts({ items }) {
  const facts = Array.isArray(items) ? items.filter(isRecord) : []
  return (
    <section className="document-panel">
      <div className="document-section-heading">
        <div><span className="eyebrow">EVIDENCE-BACKED FINDINGS</span><h2>Key facts</h2></div>
        <span className="document-count">{facts.length}</span>
      </div>
      {facts.length ? (
        <ol className="document-facts">
          {facts.map((item, index) => (
            <li className="document-fact" key={`${displayValue(item.fact) || 'fact'}-${index}`}>
              <p>{displayValue(item.fact) || 'Fact not provided'}</p>
              <EvidenceReference quote={item.evidence_quote} page={item.page} />
            </li>
          ))}
        </ol>
      ) : <p className="document-muted">No key facts were returned.</p>}
    </section>
  )
}

function ListSection({ title, eyebrow, items, kind }) {
  const rows = Array.isArray(items) ? items.filter((item) => isRecord(item) || typeof item === 'string') : []
  if (!rows.length) return null

  return (
    <section className="document-panel">
      <div className="document-section-heading">
        <div><span className="eyebrow">{eyebrow}</span><h2>{title}</h2></div>
        <span className="document-count">{rows.length}</span>
      </div>
      <ul className="document-item-list">
        {rows.map((item, index) => {
          const record = isRecord(item) ? item : {}
          const description = kind === 'actions'
            ? displayValue(record.action)
            : displayValue(record.description)
          const date = kind === 'deadlines'
            ? formatDate(record.date)
            : kind === 'actions'
              ? formatDate(record.due_date)
              : null
          const responsible = displayValue(record.responsible_department)
          const amount = kind === 'financial' ? displayValue(record.amount) : null
          const plainItem = typeof item === 'string' ? item : null
          const explicitlyRequired = record.explicitly_required === true
          return (
            <li className="document-item" key={`${description || plainItem || title}-${index}`}>
              <div className="document-item-heading">
                <strong>{description || plainItem || 'Details not provided'}</strong>
                {amount ? <span className="document-amount">{amount}</span> : null}
              </div>
              {date ? <span className="document-item-meta">{kind === 'deadlines' ? 'Date' : 'Due'}: {date}</span> : null}
              {responsible ? <span className="document-item-meta">Responsible department: {responsible}</span> : null}
              {kind === 'actions' ? (
                <span className={`document-action-type ${explicitlyRequired ? 'action-required' : ''}`}>
                  {explicitlyRequired ? 'Explicitly required in document' : 'Suggested action — verify before acting'}
                </span>
              ) : null}
              <EvidenceReference quote={record.evidence_quote} page={record.page} />
            </li>
          )
        })}
      </ul>
    </section>
  )
}

function HumanReview({ questions, reviewStatus }) {
  const rows = Array.isArray(questions)
    ? questions.filter((item) => typeof item === 'string' || isRecord(item))
    : []
  const questionsText = rows.map((item) => (
    typeof item === 'string'
      ? displayValue(item)
      : displayValue(item.question) || displayValue(item.text) || displayValue(item.description)
  )).filter(Boolean)
  return (
    <section className="document-review-panel">
      <div className="document-section-heading">
        <div><span className="eyebrow">HUMAN REVIEW</span><h2>Verification required</h2></div>
        {displayValue(reviewStatus) ? (
          <span className="review-tag review-needed">{displayValue(reviewStatus).replaceAll('_', ' ')}</span>
        ) : null}
      </div>
      <p>AI-generated findings are drafts. Verify every claim and citation against the original document before making an administrative decision.</p>
      {questionsText.length ? (
        <>
          <h3>Questions for review</h3>
          <ul>{questionsText.map((question, index) => <li key={`${question}-${index}`}>{question}</li>)}</ul>
        </>
      ) : <span className="document-muted">No follow-up questions were returned.</span>}
    </section>
  )
}

function InsightsReport({ report, job }) {
  const hasReport = isRecord(report) && Object.keys(report).length > 0
  if (!hasReport) {
    return <p className="document-muted">The service returned no document insights. You can retry generation.</p>
  }

  const sourcePageCount = typeof report.source?.page_count === 'number'
    ? report.source.page_count
    : job?.page_count
  const metadata = [
    ['Document type', report.document_type],
    ['Issuing department', report.issuing_department],
    ['Reference number', report.reference_number],
    ['Issue date', formatDate(report.issue_date)],
    ['Effective date', formatDate(report.effective_date)],
    ['Pages', sourcePageCount],
  ]
  const summary = displayValue(report.summary)
  const completedAt = formatDateTime(job?.completed_at)
  const title = displayValue(report.document_title)

  return (
    <div className="document-report">
      <section className="document-panel">
        <div className="document-section-heading">
          <div><span className="eyebrow">DOCUMENT OVERVIEW</span><h2>{title || 'Document insights'}</h2></div>
          <span className="review-tag review-needed">AI draft · verify source</span>
        </div>
        <dl className="document-metadata">
          {metadata.map(([label, value]) => <MetadataItem key={label} label={label} value={value} />)}
          {typeof job?.line_count === 'number' ? <MetadataItem label="OCR lines" value={job.line_count} /> : null}
          {completedAt ? <MetadataItem label="OCR completed" value={completedAt} /> : null}
        </dl>
      </section>
      <section className="document-panel">
        <div className="document-section-heading">
          <div><span className="eyebrow">EXECUTIVE SUMMARY</span><h2>Summary</h2></div>
        </div>
        {summary ? <p className="document-summary">{summary}</p> : <p className="document-muted">No summary was returned.</p>}
      </section>
      <KeyFacts items={report.key_facts} />
      <ListSection title="Deadlines" eyebrow="DATES TO VERIFY" items={report.deadlines} kind="deadlines" />
      <ListSection title="Action items" eyebrow="SUGGESTED FOLLOW-UP" items={report.action_items} kind="actions" />
      <ListSection title="Financial details" eyebrow="AMOUNTS AS STATED" items={report.financial_details} kind="financial" />
      <HumanReview questions={report.questions_for_human_review} reviewStatus={report.review_status} />
    </div>
  )
}

function OcrInspection({ job }) {
  if (typeof job?.text !== 'string' && !Array.isArray(job?.lines)) return null
  const lines = Array.isArray(job.lines) ? job.lines.filter(isRecord) : []
  return (
    <details className="document-panel ocr-inspection">
      <summary>Inspect extracted OCR text <span>Hidden until expanded</span></summary>
      {lines.length ? (
        <ol className="ocr-lines">
          {lines.map((line, index) => (
            <li key={`${line.page ?? 'page'}-${index}`}>
              <p>{displayValue(line.text) || 'Text not provided'}</p>
              <span>
                {typeof line.page === 'number' ? `Page ${line.page}` : 'Page not provided'}
                {typeof line.confidence === 'number' ? ` · ${line.confidence.toFixed(1)}% confidence` : ''}
              </span>
            </li>
          ))}
        </ol>
      ) : (
        <pre className="ocr-text">{job.text}</pre>
      )}
    </details>
  )
}

export default function GovernmentDocuments() {
  const [file, setFile] = useState(null)
  const [fileError, setFileError] = useState('')
  const [isDragging, setIsDragging] = useState(false)
  const [isUploading, setIsUploading] = useState(false)
  const [uploadMessage, setUploadMessage] = useState('')
  const [uploadError, setUploadError] = useState('')
  const [job, setJob] = useState(null)
  const [pollError, setPollError] = useState('')
  const [pollAttempt, setPollAttempt] = useState(0)
  const [insights, setInsights] = useState(null)
  const [insightsLoading, setInsightsLoading] = useState(false)
  const [insightsError, setInsightsError] = useState('')
  const uploadController = useRef(null)
  const insightsController = useRef(null)
  const uploadBusy = useRef(false)
  const insightsBusy = useRef(false)

  useEffect(() => () => {
    const uploadRequest = uploadController.current
    const insightsRequest = insightsController.current
    uploadController.current = null
    insightsController.current = null
    uploadRequest?.abort()
    insightsRequest?.abort()
  }, [])

  useEffect(() => {
    if (!job?.job_id || TERMINAL_STATUSES.has(job.status)) return undefined
    let active = true
    let timer
    let controller

    async function poll() {
      controller = new AbortController()
      try {
        const result = await getDocumentJob(job.job_id, job.poll_url, { signal: controller.signal })
        if (!active) return
        setJob((current) => ({
          ...current,
          ...result,
          job_id: job.job_id,
          poll_url: job.poll_url,
        }))
        setPollError('')
        if (result.status === 'IN_PROGRESS') timer = setTimeout(poll, POLL_INTERVAL_MS)
      } catch (error) {
        if (!active || (error instanceof Error && error.name === 'AbortError')) return
        setPollError(error instanceof Error
          ? error.message
          : 'Unable to retrieve document processing status.')
      }
    }

    void poll()
    return () => {
      active = false
      clearTimeout(timer)
      controller?.abort()
    }
  }, [job?.job_id, job?.poll_url, job?.status, pollAttempt])

  function chooseFile(selectedFile) {
    const validationError = validateFile(selectedFile)
    setFileError(validationError)
    setUploadError('')
    setUploadMessage('')
    setFile(validationError ? null : selectedFile)
  }

  async function handleUpload(event) {
    event.preventDefault()
    if (uploadBusy.current) return
    const validationError = validateFile(file)
    if (validationError) {
      setFileError(validationError)
      return
    }

    uploadBusy.current = true
    uploadController.current?.abort()
    insightsController.current?.abort()
    setIsUploading(true)
    setUploadError('')
    setUploadMessage('')
    setPollError('')
    setInsights(null)
    setInsightsError('')
    setJob(null)
    const controller = new AbortController()
    uploadController.current = controller

    try {
      const result = await uploadDocument(file, { signal: controller.signal })
      if (uploadController.current !== controller) return
      if (typeof result.job_id !== 'string' || !result.job_id.trim()) {
        throw new Error('The document service did not return a valid job ID.')
      }
      setJob({
        ...result,
        job_id: result.job_id.trim(),
        status: typeof result.status === 'string' ? result.status : 'IN_PROGRESS',
      })
      setUploadMessage(displayValue(result.message) || 'Document accepted for OCR processing.')
    } catch (error) {
      if (uploadController.current !== controller) return
      if (error instanceof Error && error.name === 'AbortError') {
        setUploadError('Upload request cancelled. The server may have accepted it if processing had already started.')
      } else {
        setUploadError(error instanceof Error ? error.message : 'The document could not be uploaded.')
      }
    } finally {
      if (uploadController.current === controller) {
        uploadBusy.current = false
        setIsUploading(false)
        uploadController.current = null
      }
    }
  }

  function resetWorkflow() {
    const uploadRequest = uploadController.current
    const insightsRequest = insightsController.current
    uploadController.current = null
    insightsController.current = null
    uploadRequest?.abort()
    insightsRequest?.abort()
    uploadBusy.current = false
    insightsBusy.current = false
    setIsUploading(false)
    setInsightsLoading(false)
    setFile(null)
    setFileError('')
    setUploadMessage('')
    setUploadError('')
    setPollError('')
    setInsightsError('')
    setInsights(null)
    setJob(null)
  }

  function retryPolling() {
    setPollError('')
    setPollAttempt((attempt) => attempt + 1)
  }

  async function handleGenerateInsights() {
    if (insightsBusy.current || job?.status !== 'SUCCEEDED') return
    insightsBusy.current = true
    setInsightsLoading(true)
    setInsightsError('')
    const controller = new AbortController()
    insightsController.current?.abort()
    insightsController.current = controller
    try {
      const result = await generateDocumentInsights(job.job_id, { signal: controller.signal })
      if (insightsController.current !== controller) return
      setInsights(result)
    } catch (error) {
      if (
        insightsController.current === controller
        && error instanceof Error
        && error.name !== 'AbortError'
      ) {
        setInsightsError(error.message)
      }
    } finally {
      if (insightsController.current === controller) {
        insightsBusy.current = false
        setInsightsLoading(false)
        insightsController.current = null
      }
    }
  }

  const ocrReady = job?.status === 'SUCCEEDED'
  const isTerminal = job && TERMINAL_STATUSES.has(job.status)

  return (
    <div className="dashboard-page government-documents-page">
      <div className="page-intro">
        <div>
          <p className="eyebrow">TAMILNADU DISTRICT INTELLIGENCE (TNDI) / GOVERNMENT DOCUMENTS</p>
          <h1>Government Document Intelligence</h1>
          <p>Process official documents, inspect extracted text, and review evidence-backed AI findings.</p>
        </div>
      </div>

      <div className="document-safety-note">
        <span aria-hidden="true">i</span>
        <p>Documents and AI findings are not verified administrative decisions. Check every citation against the original before taking action.</p>
      </div>

      <section className="document-panel document-upload-panel">
        <div className="document-section-heading">
          <div><span className="eyebrow">SECURE DOCUMENT PROCESSING</span><h2>Upload a government PDF</h2></div>
          {job ? <button className="button button-secondary" type="button" onClick={resetWorkflow}>Start new document</button> : null}
        </div>
        <form onSubmit={handleUpload}>
          <label
            className={`document-dropzone ${isDragging ? 'document-dropzone-active' : ''} ${isUploading ? 'document-dropzone-disabled' : ''}`}
            onDragOver={(event) => {
              event.preventDefault()
              if (!isUploading) setIsDragging(true)
            }}
            onDragLeave={() => setIsDragging(false)}
            onDrop={(event) => {
              event.preventDefault()
              setIsDragging(false)
              const droppedFile = event.dataTransfer.files?.[0]
              if (!isUploading && droppedFile) chooseFile(droppedFile)
            }}
          >
            <input
              type="file"
              accept="application/pdf,.pdf"
              disabled={isUploading}
              onChange={(event) => {
                const selectedFile = event.target.files?.[0]
                if (selectedFile) chooseFile(selectedFile)
                event.target.value = ''
              }}
            />
            <span className="document-upload-icon" aria-hidden="true">↑</span>
            <strong>{file ? file.name : 'Choose a PDF or drag it here'}</strong>
            <span>{file ? `${formatBytes(file.size)} · PDF` : 'PDF format · Maximum file size 20 MB'}</span>
          </label>
          {fileError ? <p className="document-inline-error" role="alert">{fileError}</p> : null}
          <div className="document-upload-actions">
            <button className="button button-primary" type="submit" disabled={!file || isUploading}>
              {isUploading ? 'Uploading…' : 'Upload and process'}
            </button>
            {isUploading ? (
              <button className="button button-secondary" type="button" onClick={resetWorkflow}>Cancel upload</button>
            ) : file ? (
              <button className="button button-secondary" type="button" onClick={resetWorkflow}>Clear selection</button>
            ) : null}
            {uploadMessage ? <span className="document-success" role="status">{uploadMessage}</span> : null}
          </div>
          {uploadError ? <p className="document-inline-error" role="alert">{uploadError}</p> : null}
        </form>
      </section>

      {job ? (
        <>
          <DocumentTimeline job={job} insightsLoading={insightsLoading} insights={insights} />
          <section className="document-panel document-job-panel">
            <div className="document-section-heading">
              <div><span className="eyebrow">ACTIVE DOCUMENT</span><h2>{displayValue(job.filename) || 'Uploaded document'}</h2></div>
              {job.status === 'IN_PROGRESS' ? <span className="document-processing-note">Checking OCR status every 4 seconds</span> : null}
            </div>
            {pollError ? (
              <div className="document-error-banner" role="alert">
                <p>{pollError}</p>
                <button className="button button-secondary" type="button" onClick={retryPolling}>Retry status check</button>
              </div>
            ) : null}
            {job.status === 'FAILED' ? (
              <p className="document-inline-error" role="alert">OCR processing failed. Insights are unavailable for this job.</p>
            ) : null}
            {job.status === 'PARTIAL_SUCCESS' ? (
              <p className="document-partial-note" role="status">
                OCR returned a partial result. Insights are disabled because the extracted content may be incomplete.
              </p>
            ) : null}
            {job.status === 'SUCCEEDED' ? (
              <div className="document-job-metadata">
                {typeof job.page_count === 'number' ? <span>{job.page_count} pages</span> : null}
                {typeof job.line_count === 'number' ? <span>{job.line_count} extracted lines</span> : null}
                {formatDateTime(job.completed_at) ? <span>Completed {formatDateTime(job.completed_at)}</span> : null}
              </div>
            ) : null}
            {isTerminal && job.status !== 'SUCCEEDED' && Array.isArray(job.warnings) && job.warnings.length ? (
              <p className="document-muted">{job.warnings.length} OCR warning{job.warnings.length === 1 ? '' : 's'} were returned.</p>
            ) : null}
            <div className="document-insights-action">
              <button
                className="button button-primary"
                type="button"
                disabled={!ocrReady || insightsLoading}
                onClick={handleGenerateInsights}
              >
                {insightsLoading ? 'Generating insights…' : insights ? 'Regenerate insights' : 'Generate insights'}
              </button>
              {!ocrReady ? <span>Insights become available after OCR succeeds.</span> : null}
              {insightsError ? (
                <span className="document-inline-error" role="alert">{insightsError}</span>
              ) : null}
            </div>
            {ocrReady ? <OcrInspection job={job} /> : null}
          </section>
          {insights ? <InsightsReport report={insights} job={job} /> : null}
        </>
      ) : null}
    </div>
  )
}
