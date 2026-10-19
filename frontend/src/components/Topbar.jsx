import { useNavigate } from 'react-router-dom'
import api from '../api/client'
import { useAuth } from '../context/AuthContext'

export default function Topbar({ title, icon = '🗓' }) {
    const { display, deptId, isAdmin, logout } = useAuth()
    const navigate = useNavigate()

    async function handleLogout() {
        try { await api.post('/auth/logout') } catch { }
        logout()
        navigate('/login')
    }

    return (
        <nav className="topbar">
            <div className="topbar-brand">
                <div className="logo">{icon}</div>
                <h1>{title || 'TT Generator'}</h1>
            </div>
            <div className="topbar-right">
                <span className="dept-badge">{isAdmin ? 'Administrator' : (display || deptId)}</span>
                <button className="btn btn-ghost btn-sm" onClick={handleLogout}>Logout</button>
            </div>
        </nav>
    )
}
