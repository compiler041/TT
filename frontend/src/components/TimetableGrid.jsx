/**
 * TimetableGrid.jsx
 * Renders a weekly timetable grid from the JSON returned by the backend.
 * Compatible with both division grids and faculty/room grids.
 */
import { useState, useEffect } from 'react'
import api from '../api/client'

const DAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']

function cellClass(entry) {
    if (!entry) return 'tt-cell empty'
    const type = (entry.type || '').toLowerCase()
    if (type === 'practical') return 'tt-cell practical'
    if (type === 'tutorial') return 'tt-cell tutorial'
    return 'tt-cell lecture'
}

function GridDisplay({ grid }) {
    if (!grid || !grid.periods || !grid.rows) {
        return <p className="text-muted" style={{ padding: '2rem', textAlign: 'center' }}>No timetable data available.</p>
    }

    const periods = grid.periods
    const cols = periods.length + 1
    const colStyle = `repeat(${cols}, minmax(110px, 1fr))`

    return (
        <div className="grid-scroll">
            <div className="tt-grid" style={{ gridTemplateColumns: colStyle }}>
                {/* Header row */}
                <div className="tt-cell header">Day / Time</div>
                {periods.map((p, i) => (
                    <div key={i} className="tt-cell header">{p}</div>
                ))}

                {/* Data rows */}
                {grid.rows.map((row, ri) => (
                    <>
                        <div key={`day-${ri}`} className="tt-cell time-col">{row.day}</div>
                        {row.slots.map((slot, si) => (
                            <div key={`${ri}-${si}`} className={cellClass(slot)}>
                                {slot ? (
                                    <>
                                        <span className="tt-cell-subj">{slot.subject_code || slot.subject}</span>
                                        <span className="tt-cell-meta">{[slot.faculty, slot.room].filter(Boolean).join(' · ')}</span>
                                    </>
                                ) : null}
                            </div>
                        ))}
                    </>
                ))}
            </div>
        </div>
    )
}

export default function TimetableGrid({ ttData, mode = 'division' }) {
    const [activeTab, setActiveTab] = useState('division')
    const [divSelected, setDivSelected] = useState('')
    const [facSelected, setFacSelected] = useState('')
    const [roomSelected, setRoomSelected] = useState('')
    const [facGrid, setFacGrid] = useState(null)
    const [roomGrid, setRoomGrid] = useState(null)
    const [loading, setLoading] = useState(false)

    const divList = ttData?.division_list || []
    const facList = ttData?.faculty_list || []
    const roomList = ttData?.room_list || []

    useEffect(() => {
        if (divList.length && !divSelected) setDivSelected(divList[0])
    }, [divList])

    async function loadFacGrid(fac) {
        if (!fac) return
        setLoading(true)
        try {
            const { data } = await api.get(`/dept/timetable/faculty/${encodeURIComponent(fac)}`)
            setFacGrid(data.grid)
        } catch { setFacGrid(null) } finally { setLoading(false) }
    }

    async function loadRoomGrid(room) {
        if (!room) return
        setLoading(true)
        try {
            const { data } = await api.get(`/dept/timetable/room/${encodeURIComponent(room)}`)
            setRoomGrid(data.grid)
        } catch { setRoomGrid(null) } finally { setLoading(false) }
    }

    function handleTabChange(tab) {
        setActiveTab(tab)
        if (tab === 'faculty' && facList.length) { const f = facList[0]; setFacSelected(f); loadFacGrid(f) }
        if (tab === 'room' && roomList.length) { const r = roomList[0]; setRoomSelected(r); loadRoomGrid(r) }
    }

    const currentDivGrid = divSelected && ttData?.divisions?.[divSelected]?.grid

    return (
        <div>
            <div className="tabs-bar">
                {['division', 'faculty', 'room'].map(tab => (
                    <button key={tab} className={`tab-btn ${activeTab === tab ? 'active' : ''}`}
                        onClick={() => handleTabChange(tab)}>
                        {tab.charAt(0).toUpperCase() + tab.slice(1) + 's'}
                    </button>
                ))}
            </div>

            {/* Division panel */}
            {activeTab === 'division' && (
                <div>
                    <div className="controls-row">
                        <select className="glass-select" value={divSelected}
                            onChange={e => setDivSelected(e.target.value)}>
                            {divList.map(d => <option key={d} value={d}>{d}</option>)}
                        </select>
                    </div>
                    <GridDisplay grid={currentDivGrid} />
                </div>
            )}

            {/* Faculty panel */}
            {activeTab === 'faculty' && (
                <div>
                    <div className="controls-row">
                        <select className="glass-select" value={facSelected}
                            onChange={e => { setFacSelected(e.target.value); loadFacGrid(e.target.value) }}>
                            {facList.map(f => <option key={f} value={f}>{f}</option>)}
                        </select>
                    </div>
                    {loading ? <div className="spinner" style={{ margin: '2rem auto' }} /> : <GridDisplay grid={facGrid} />}
                </div>
            )}

            {/* Room panel */}
            {activeTab === 'room' && (
                <div>
                    <div className="controls-row">
                        <select className="glass-select" value={roomSelected}
                            onChange={e => { setRoomSelected(e.target.value); loadRoomGrid(e.target.value) }}>
                            {roomList.map(r => <option key={r} value={r}>{r}</option>)}
                        </select>
                    </div>
                    {loading ? <div className="spinner" style={{ margin: '2rem auto' }} /> : <GridDisplay grid={roomGrid} />}
                </div>
            )}
        </div>
    )
}
