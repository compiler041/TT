import { useState, useRef, useCallback } from 'react'
import api from '../api/client'
import { useAuth } from '../context/AuthContext'
import Topbar from '../components/Topbar'
import TimetableGrid from '../components/TimetableGrid'

const SECTIONS = [
    { id: 'upload', label: 'Upload & Generate', icon: '📤' },
    { id: 'timetable', label: 'Timetable', icon: '📋' },
    { id: 'conflicts', label: 'Conflicts', icon: '⚠️' },
    { id: 'history', label: 'History', icon: '🕐' },
]

export default function DeptDashboard() {
    const { deptId } = useAuth()
    const [activeSection, setActiveSection] = useState('upload')

    // Upload state
    const [uploadStats, setUploadStats] = useState(null)
    const [uploadLoading, setUploadLoading] = useState(false)
    const [uploadError, setUploadError] = useState('')
    const fileInputRef = useRef()

    // Solve state
    const [solving, setSolving] = useState(false)
    const [solveResult, setSolveResult] = useState(null)
    const [step, setStep] = useState(1)
    const [timer, setTimer] = useState(0)
    const timerRef = useRef(null)

    // Timetable data
    const [ttData, setTtData] = useState(null)

    // Conflict data
    const [conflicts, setConflicts] = useState(null)
    const [conflictsLoading, setConflictsLoading] = useState(false)

    // History
    const [history, setHistory] = useState(null)
    const [historyLoading, setHistoryLoading] = useState(false)

    function showSection(id) {
        setActiveSection(id)
        if (id === 'conflicts') loadConflicts()
        if (id === 'history') loadHistory()
    }

    /* ── Upload ── */
    async function handleUpload(file) {
        if (!file?.name.match(/\.xlsx?$/i)) { setUploadError('Please upload a valid .xlsx file'); return }
        setUploadLoading(true)
        setUploadError('')
        setUploadStats(null)
        const fd = new FormData()
        fd.append('file', file)
        try {
            const { data } = await api.post('/dept/upload', fd, { headers: { 'Content-Type': 'multipart/form-data' } })
            if (data.success) { setUploadStats(data.data); setStep(2) }
            else setUploadError(data.error || 'Upload failed')
        } catch (e) {
            setUploadError('Cannot reach backend. Is it running on port 8000?')
        } finally { setUploadLoading(false) }
    }

    function onDrop(e) {
        e.preventDefault()
        const file = e.dataTransfer.files[0]
        if (file) handleUpload(file)
    }

    /* ── Solve ── */
    async function runSolve() {
        setSolving(true)
        setSolveResult(null)
        setTimer(0)
        timerRef.current = setInterval(() => setTimer(t => t + 1), 1000)
        try {
            const { data } = await api.post('/dept/solve')
            setSolveResult(data)
            setStep(3)
            const ttRes = await api.get('/dept/timetable')
            setTtData(ttRes.data)
        } catch (e) {
            alert('Solve failed: ' + (e.response?.data?.detail || e.message))
        } finally {
            clearInterval(timerRef.current)
            setSolving(false)
        }
    }

    /* ── Export ── */
    async function downloadExport() {
        try {
            const res = await api.get('/dept/export', { responseType: 'blob' })
            const url = URL.createObjectURL(res.data)
            const a = document.createElement('a')
            a.href = url; a.download = `${deptId}_timetable.xlsx`; a.click()
            URL.revokeObjectURL(url)
        } catch { alert('No timetable to download.') }
    }

    /* ── Conflicts ── */
    async function loadConflicts() {
        if (conflicts !== null) return
        setConflictsLoading(true)
        try {
            const { data } = await api.get('/dept/conflicts')
            setConflicts(data.conflicts || {})
        } catch { setConflicts({}) }
        finally { setConflictsLoading(false) }
    }

    /* ── History ── */
    async function loadHistory() {
        if (history !== null) return
        setHistoryLoading(true)
        try {
            const { data } = await api.get(`/admin/timetables?dept_id=${deptId}`)
            setHistory(data.timetables || [])
        } catch { setHistory([]) }
        finally { setHistoryLoading(false) }
    }

    async function downloadById(id) {
        try {
            const res = await api.get(`/admin/export/${id}`, { responseType: 'blob' })
            const url = URL.createObjectURL(res.data)
            const a = document.createElement('a'); a.href = url; a.download = `timetable_${id}.xlsx`; a.click()
            URL.revokeObjectURL(url)
        } catch { alert('Download failed.') }
    }

    return (
        <>
            <div className="bg-blobs"><div className="blob blob-1" /><div className="blob blob-2" /><div className="blob blob-3" /></div>
            <Topbar title="TT Generator" />
            <div className="dash-layout">
                {/* Sidebar */}
                <aside className="sidebar">
                    {SECTIONS.map(s => (
                        <button key={s.id} className={`sidebar-link ${activeSection === s.id ? 'active' : ''}`}
                            onClick={() => showSection(s.id)}>
                            <span className="sidebar-icon">{s.icon}</span>{s.label}
                        </button>
                    ))}
                </aside>

                <main className="dash-main">
                    {/* ── Upload & Generate ── */}
                    {activeSection === 'upload' && (
                        <section>
                            <div className="section-header">
                                <h2>Upload &amp; Generate Timetable</h2>
                            </div>

                            {/* Step indicator */}
                            <div className="step-flow">
                                {['Upload File', 'Generate', 'View & Export'].map((label, i) => {
                                    const n = i + 1
                                    const cls = n < step ? 'done' : n === step ? 'active' : ''
                                    return <>
                                        {i > 0 && <span key={`sep-${i}`} className="step-sep">›</span>}
                                        <div key={label} className={`step ${cls}`}>
                                            <div className="step-num">{n < step ? '✓' : n}</div>{label}
                                        </div>
                                    </>
                                })}
                            </div>

                            {/* Drop zone */}
                            <div className="glass card" style={{ marginBottom: '1.5rem' }}>
                                <div
                                    className="upload-zone"
                                    onDragOver={e => e.preventDefault()}
                                    onDrop={onDrop}
                                    onClick={() => fileInputRef.current.click()}
                                >
                                    <input ref={fileInputRef} type="file" accept=".xlsx,.xls" hidden
                                        onChange={e => e.target.files[0] && handleUpload(e.target.files[0])} />
                                    {uploadLoading
                                        ? <><div className="spinner" /><p style={{ marginTop: '1rem' }}>Parsing Excel file…</p></>
                                        : <><div className="upload-icon">📄</div>
                                            <p>Drag &amp; drop your Excel file here</p>
                                            <span style={{ color: 'var(--primary)', fontWeight: 600 }}>or click to browse</span>
                                        </>
                                    }
                                </div>
                                {uploadError && <div className="alert alert-red" style={{ marginTop: '1rem' }}>{uploadError}</div>}
                                {uploadStats && (
                                    <div className="stats-grid" style={{ marginTop: '1rem' }}>
                                        {[['Divisions', uploadStats.divisions], ['Courses', uploadStats.courses],
                                        ['Faculty', uploadStats.faculty], ['Rooms', uploadStats.rooms]].map(([label, val]) => (
                                            <div key={label} className="stat-card">
                                                <span className="stat-value">{val}</span>
                                                <span className="stat-label">{label}</span>
                                            </div>
                                        ))}
                                    </div>
                                )}
                            </div>

                            {/* Generate card */}
                            {step >= 2 && (
                                <div className="glass card">
                                    <h3 style={{ marginBottom: '.5rem' }}>Generate Timetable</h3>
                                    <p className="text-muted" style={{ marginBottom: '1.25rem' }}>Run the scheduler to produce conflict-free timetables for all divisions.</p>
                                    {!solving && !solveResult && (
                                        <button className="btn btn-primary" onClick={runSolve}>⚙️ Generate Timetable</button>
                                    )}
                                    {solving && (
                                        <div style={{ textAlign: 'center', padding: '1rem 0' }}>
                                            <div className="spinner" />
                                            <p style={{ marginTop: '0.75rem', color: 'var(--text-2)' }}>Solving… <strong>{timer}s</strong></p>
                                        </div>
                                    )}
                                    {solveResult && !solving && (
                                        <div>
                                            <div className="alert alert-green" style={{ display: 'flex', alignItems: 'center', gap: '1rem' }}>
                                                <span style={{ fontSize: '1.5rem' }}>✅</span>
                                                <div>
                                                    <strong>Timetable Generated!</strong>
                                                    <div className="text-muted">
                                                        {solveResult.total_entries} sessions · {(solveResult.solve_time_ms / 1000).toFixed(1)}s · {solveResult.unscheduled_count} unscheduled
                                                    </div>
                                                </div>
                                                <span className={`badge ${solveResult.status === 'OPTIMAL' ? 'badge-green' : 'badge-amber'}`} style={{ marginLeft: 'auto' }}>
                                                    {solveResult.status}
                                                </span>
                                            </div>
                                            <div style={{ display: 'flex', gap: '1rem', marginTop: '1rem', flexWrap: 'wrap' }}>
                                                <button className="btn btn-primary" onClick={() => showSection('timetable')}>📋 View Timetable</button>
                                                <button className="btn btn-success" onClick={downloadExport}>⬇️ Download Excel</button>
                                                <button className="btn btn-ghost" onClick={runSolve}>↺ Regenerate</button>
                                            </div>
                                        </div>
                                    )}
                                </div>
                            )}
                        </section>
                    )}

                    {/* ── Timetable View ── */}
                    {activeSection === 'timetable' && (
                        <section>
                            <div className="section-header">
                                <h2>Generated Timetable</h2>
                                <button className="btn btn-success" onClick={downloadExport}>⬇️ Download Excel</button>
                            </div>
                            <div className="glass card">
                                {ttData
                                    ? <TimetableGrid ttData={ttData} />
                                    : <p className="text-muted" style={{ textAlign: 'center', padding: '2rem' }}>
                                        Generate a timetable first.
                                    </p>
                                }
                            </div>
                        </section>
                    )}

                    {/* ── Conflicts ── */}
                    {activeSection === 'conflicts' && (
                        <section>
                            <div className="section-header"><h2>Scheduling Conflicts</h2></div>
                            <div className="glass card">
                                {conflictsLoading && <div className="spinner" style={{ margin: '2rem auto' }} />}
                                {!conflictsLoading && conflicts && Object.keys(conflicts).length === 0 && (
                                    <div className="alert alert-green">✅ No conflicts detected. All sessions were scheduled.</div>
                                )}
                                {!conflictsLoading && conflicts && Object.entries(conflicts).map(([cat, msgs]) => (
                                    <div key={cat} className="conflict-panel">
                                        <h4>{cat}</h4>
                                        <ul>{msgs.map((m, i) => <li key={i}>{m}</li>)}</ul>
                                    </div>
                                ))}
                                {!conflictsLoading && !conflicts && (
                                    <p className="text-muted">Generate a timetable first.</p>
                                )}
                            </div>
                        </section>
                    )}

                    {/* ── History ── */}
                    {activeSection === 'history' && (
                        <section>
                            <div className="section-header"><h2>My Generated Timetables</h2></div>
                            <div className="glass card">
                                {historyLoading && <div className="spinner" style={{ margin: '2rem auto' }} />}
                                {!historyLoading && (
                                    <div className="table-wrap">
                                        <table>
                                            <thead><tr><th>#</th><th>Generated At</th><th>Status</th><th>Admin Remark</th><th>Download</th></tr></thead>
                                            <tbody>
                                                {(!history || history.length === 0)
                                                    ? <tr><td colSpan={5} style={{ textAlign: 'center', color: 'var(--text-3)' }}>No timetables generated yet.</td></tr>
                                                    : history.map(r => (
                                                        <tr key={r.id}>
                                                            <td>#{r.id}</td>
                                                            <td>{r.generated_at}</td>
                                                            <td><span className={`badge ${r.status === 'OPTIMAL' ? 'badge-green' : 'badge-amber'}`}>{r.status}</span></td>
                                                            <td>{r.admin_remark || <span className="text-muted">—</span>}</td>
                                                            <td><button className="btn btn-accent btn-sm" onClick={() => downloadById(r.id)}>⬇ Excel</button></td>
                                                        </tr>
                                                    ))
                                                }
                                            </tbody>
                                        </table>
                                    </div>
                                )}
                            </div>
                        </section>
                    )}
                </main>
            </div>
        </>
    )
}
