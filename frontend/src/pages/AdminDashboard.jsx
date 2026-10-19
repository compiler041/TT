import { useState, useEffect } from 'react'
import api from '../api/client'
import Topbar from '../components/Topbar'
import TimetableGrid from '../components/TimetableGrid'

const SECTIONS = [
    { id: 'overview', label: 'Overview', icon: '📊' },
    { id: 'timetables', label: 'All Timetables', icon: '📋' },
    { id: 'logs', label: 'Activity Log', icon: '📝' },
]

function actionBadge(action) {
    if (action.startsWith('login')) return 'badge-green'
    if (action.startsWith('logout')) return 'badge-amber'
    if (action.startsWith('generate')) return 'badge-purple'
    return 'badge-blue'
}

export default function AdminDashboard() {
    const [activeSection, setActiveSection] = useState('overview')

    // Overview
    const [depts, setDepts] = useState([])
    const [allTTs, setAllTTs] = useState([])
    const [logs, setLogs] = useState([])
    const [overviewLoading, setOverviewLoading] = useState(true)

    // Timetables
    const [filterDept, setFilterDept] = useState('')
    const [filteredTTs, setFilteredTTs] = useState([])
    const [ttLoading, setTtLoading] = useState(false)

    // Expand panel
    const [expandTT, setExpandTT] = useState(null) // { id, dept_id, status, admin_remark }
    const [expandGrid, setExpandGrid] = useState(null)
    const [remark, setRemark] = useState('')
    const [remarkMsg, setRemarkMsg] = useState('')

    // Logs
    const [logsLoading, setLogsLoading] = useState(false)

    useEffect(() => { loadOverview() }, [])

    async function loadOverview() {
        setOverviewLoading(true)
        try {
            const [dRes, tRes, lRes] = await Promise.all([
                api.get('/admin/departments'),
                api.get('/admin/timetables'),
                api.get('/admin/logs?limit=500'),
            ])
            setDepts(dRes.data.departments || [])
            setAllTTs(tRes.data.timetables || [])
            setFilteredTTs(tRes.data.timetables || [])
            setLogs(lRes.data.logs || [])
        } catch { }
        finally { setOverviewLoading(false) }
    }

    function showSection(id) {
        setActiveSection(id)
        if (id === 'timetables') loadTimetables(filterDept)
        if (id === 'logs') loadLogs()
    }

    async function loadTimetables(dept = '') {
        setTtLoading(true)
        try {
            const url = dept ? `/admin/timetables?dept_id=${encodeURIComponent(dept)}` : '/admin/timetables'
            const { data } = await api.get(url)
            setFilteredTTs(data.timetables || [])
        } catch { } finally { setTtLoading(false) }
    }

    async function loadLogs() {
        setLogsLoading(true)
        try {
            const { data } = await api.get('/admin/logs')
            setLogs(data.logs || [])
        } catch { } finally { setLogsLoading(false) }
    }

    function openExpand(row) {
        setExpandTT(row)
        setRemark(row.admin_remark || '')
        setRemarkMsg('')
        setExpandGrid(null)
    }

    function closeExpand() { setExpandTT(null); setExpandGrid(null) }

    async function saveRemark() {
        if (!expandTT) return
        try {
            await api.put(`/admin/timetable/${expandTT.id}/remark`, { remark })
            setRemarkMsg('✅ Remark saved.')
            loadTimetables(filterDept)
            setTimeout(() => setRemarkMsg(''), 3000)
        } catch { setRemarkMsg('❌ Could not save remark.') }
    }

    async function regenerate(id) {
        if (!confirm(`Regenerate timetable #${id}? This re-runs the scheduler.`)) return
        try {
            const { data } = await api.post(`/admin/timetable/${id}/regenerate`)
            if (data.success) { alert(`✅ Done! Status: ${data.status}, ${data.total_entries} sessions, ${(data.solve_time_ms / 1000).toFixed(1)}s`); loadTimetables(filterDept) }
        } catch (e) { alert('❌ ' + (e.response?.data?.detail || 'Failed')) }
    }

    async function downloadTT(id, deptId) {
        try {
            const res = await api.get(`/admin/export/${id}`, { responseType: 'blob' })
            const url = URL.createObjectURL(res.data)
            const a = document.createElement('a'); a.href = url; a.download = `${deptId}_timetable_${id}.xlsx`; a.click()
            URL.revokeObjectURL(url)
        } catch { alert('Download failed.') }
    }

    // Build dept stats for overview
    const ttByDept = {}
    allTTs.forEach(t => { (ttByDept[t.dept_id] = ttByDept[t.dept_id] || []).push(t) })

    return (
        <>
            <div className="bg-blobs"><div className="blob blob-1" /><div className="blob blob-2" /><div className="blob blob-3" /></div>
            <Topbar title="Admin Panel" icon="🔐" />
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

                    {/* ── Overview ── */}
                    {activeSection === 'overview' && (
                        <section>
                            <div className="section-header"><h2>System Overview</h2>
                                <button className="btn btn-ghost btn-sm" onClick={loadOverview}>🔄 Refresh</button>
                            </div>
                            {overviewLoading
                                ? <div className="spinner" style={{ margin: '3rem auto' }} />
                                : <>
                                    <div className="stats-grid" style={{ marginBottom: '2rem' }}>
                                        {[['Departments', depts.length], ['Timetables', allTTs.length], ['Log Entries', logs.length]].map(([l, v]) => (
                                            <div key={l} className="stat-card"><span className="stat-value">{v}</span><span className="stat-label">{l}</span></div>
                                        ))}
                                    </div>
                                    <h3 style={{ marginBottom: '1rem' }}>Registered Departments</h3>
                                    <div className="glass card">
                                        <div className="table-wrap">
                                            <table>
                                                <thead><tr><th>Dept ID</th><th>Department Name</th><th>Timetables</th><th>Last Generated</th></tr></thead>
                                                <tbody>
                                                    {depts.length === 0
                                                        ? <tr><td colSpan={4} style={{ textAlign: 'center', color: 'var(--text-3)' }}>No departments found.</td></tr>
                                                        : depts.map(d => {
                                                            const dTTs = ttByDept[d.dept_id] || []
                                                            return (
                                                                <tr key={d.dept_id} className="clickable" onClick={() => { setFilterDept(d.dept_id); showSection('timetables') }}>
                                                                    <td><span className="badge badge-purple">{d.dept_id}</span></td>
                                                                    <td>{d.display_name}</td>
                                                                    <td>{dTTs.length}</td>
                                                                    <td>{dTTs[0]?.generated_at || '—'}</td>
                                                                </tr>
                                                            )
                                                        })
                                                    }
                                                </tbody>
                                            </table>
                                        </div>
                                    </div>
                                </>
                            }
                        </section>
                    )}

                    {/* ── All Timetables ── */}
                    {activeSection === 'timetables' && (
                        <section>
                            <div className="section-header">
                                <h2>All Generated Timetables</h2>
                                <div style={{ display: 'flex', gap: '.75rem', alignItems: 'center', flexWrap: 'wrap' }}>
                                    <select className="glass-select" value={filterDept}
                                        onChange={e => { setFilterDept(e.target.value); loadTimetables(e.target.value) }}>
                                        <option value="">All Departments</option>
                                        {depts.map(d => <option key={d.dept_id} value={d.dept_id}>{d.dept_id} — {d.display_name}</option>)}
                                    </select>
                                    <button className="btn btn-ghost btn-sm" onClick={() => loadTimetables(filterDept)}>🔄 Refresh</button>
                                </div>
                            </div>

                            <div className="glass card" style={{ marginBottom: '1.5rem' }}>
                                <div className="table-wrap">
                                    <table>
                                        <thead><tr><th>#</th><th>Department</th><th>Generated At</th><th>Status</th><th>Admin Remark</th><th>Actions</th></tr></thead>
                                        <tbody>
                                            {ttLoading
                                                ? <tr><td colSpan={6} style={{ textAlign: 'center' }}><div className="spinner" style={{ margin: '.5rem auto' }} /></td></tr>
                                                : filteredTTs.length === 0
                                                    ? <tr><td colSpan={6} style={{ textAlign: 'center', color: 'var(--text-3)' }}>No timetables generated yet.</td></tr>
                                                    : filteredTTs.map(r => (
                                                        <tr key={r.id} className="clickable" onClick={() => openExpand(r)}>
                                                            <td>#{r.id}</td>
                                                            <td><span className="badge badge-purple">{r.dept_id}</span></td>
                                                            <td>{r.generated_at}</td>
                                                            <td><span className={`badge ${r.status === 'OPTIMAL' ? 'badge-green' : 'badge-amber'}`}>{r.status}</span></td>
                                                            <td style={{ maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                                                {r.admin_remark || <span className="text-muted">—</span>}
                                                            </td>
                                                            <td>
                                                                <div style={{ display: 'flex', gap: '.4rem' }}>
                                                                    <button className="btn btn-accent btn-sm"
                                                                        onClick={e => { e.stopPropagation(); downloadTT(r.id, r.dept_id) }}>⬇ Excel</button>
                                                                    <button className="btn btn-ghost btn-sm"
                                                                        onClick={e => { e.stopPropagation(); regenerate(r.id) }}>♻ Regen</button>
                                                                </div>
                                                            </td>
                                                        </tr>
                                                    ))
                                            }
                                        </tbody>
                                    </table>
                                </div>
                            </div>

                            {/* Expand panel */}
                            {expandTT && (
                                <div className="glass card" style={{ animation: 'fadeIn .2s ease' }}>
                                    <div className="section-header" style={{ marginBottom: '1rem' }}>
                                        <div style={{ display: 'flex', alignItems: 'center', gap: '1rem' }}>
                                            <h3>Timetable #{expandTT.id} — {expandTT.dept_id}</h3>
                                            <span className={`badge ${expandTT.status === 'OPTIMAL' ? 'badge-green' : 'badge-amber'}`}>{expandTT.status}</span>
                                        </div>
                                        <div style={{ display: 'flex', gap: '.75rem' }}>
                                            <button className="btn btn-accent btn-sm" onClick={() => downloadTT(expandTT.id, expandTT.dept_id)}>⬇ Excel</button>
                                            <button className="btn btn-ghost btn-sm" onClick={closeExpand}>✕ Close</button>
                                        </div>
                                    </div>

                                    <div style={{ marginBottom: '1.5rem' }}>
                                        <label style={{ fontSize: '.85rem', fontWeight: 600, color: 'var(--text-2)', display: 'block', marginBottom: '.5rem' }}>Admin Remark</label>
                                        <div className="remark-row">
                                            <textarea className="input remark-input" placeholder="Add a remark…" value={remark} onChange={e => setRemark(e.target.value)} />
                                            <button className="btn btn-primary btn-sm" onClick={saveRemark}>Save</button>
                                        </div>
                                        {remarkMsg && <p style={{ marginTop: '.4rem', fontSize: '.8rem', color: remarkMsg.startsWith('✅') ? 'var(--green)' : 'var(--red)' }}>{remarkMsg}</p>}
                                    </div>

                                    <p className="text-muted" style={{ textAlign: 'center', padding: '1.5rem 0' }}>
                                        ℹ️ Live timetable grid available via department dashboard or download the Excel file above.
                                    </p>
                                </div>
                            )}
                        </section>
                    )}

                    {/* ── Activity Logs ── */}
                    {activeSection === 'logs' && (
                        <section>
                            <div className="section-header">
                                <h2>Activity Log</h2>
                                <button className="btn btn-ghost btn-sm" onClick={loadLogs}>🔄 Refresh</button>
                            </div>
                            <div className="glass card">
                                <div className="table-wrap">
                                    <table>
                                        <thead><tr><th>#</th><th>Department</th><th>Action</th><th>Timestamp</th><th>IP Address</th></tr></thead>
                                        <tbody>
                                            {logsLoading
                                                ? <tr><td colSpan={5} style={{ textAlign: 'center' }}><div className="spinner" style={{ margin: '.5rem auto' }} /></td></tr>
                                                : logs.length === 0
                                                    ? <tr><td colSpan={5} style={{ textAlign: 'center', color: 'var(--text-3)' }}>No activity yet.</td></tr>
                                                    : logs.map(l => (
                                                        <tr key={l.id}>
                                                            <td>{l.id}</td>
                                                            <td><span className="badge badge-purple">{l.dept_id}</span></td>
                                                            <td><span className={`badge ${actionBadge(l.action)}`}>{l.action}</span></td>
                                                            <td>{l.timestamp}</td>
                                                            <td className="text-muted">{l.ip_address || '—'}</td>
                                                        </tr>
                                                    ))
                                            }
                                        </tbody>
                                    </table>
                                </div>
                            </div>
                        </section>
                    )}

                </main>
            </div>
        </>
    )
}
