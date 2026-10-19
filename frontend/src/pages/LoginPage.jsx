import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import api from '../api/client'
import { useAuth } from '../context/AuthContext'

export default function LoginPage() {
    const [deptId, setDeptId] = useState('')
    const [password, setPassword] = useState('')
    const [error, setError] = useState('')
    const [loading, setLoading] = useState(false)
    const { login } = useAuth()
    const navigate = useNavigate()

    async function handleLogin(e) {
        e.preventDefault()
        setError('')
        if (!deptId || !password) { setError('Please select a department and enter your password.'); return }

        setLoading(true)
        try {
            const { data } = await api.post('/auth/login', { dept_id: deptId, password })
            login(data)
            navigate(data.is_admin ? '/admin' : '/dept')
        } catch (err) {
            setError(err.response?.data?.detail || 'Invalid credentials. Please try again.')
        } finally {
            setLoading(false)
        }
    }

    return (
        <>
            <div className="bg-blobs">
                <div className="blob blob-1" />
                <div className="blob blob-2" />
                <div className="blob blob-3" />
            </div>
            <div className="login-page">
                <div className="glass login-card">
                    <div className="login-logo">🗓</div>
                    <h2>Timetable Generator</h2>
                    <p>Sign in with your department credentials</p>

                    {error && <div className="alert alert-red">{error}</div>}

                    <form onSubmit={handleLogin}>
                        <div className="input-group">
                            <label>Department</label>
                            <select className="input" value={deptId} onChange={e => setDeptId(e.target.value)}>
                                <option value="">— Select your department —</option>
                                <option value="EXCP">Electronics &amp; Computer (EXCP)</option>
                                <option value="CSBS">Computer Science &amp; BS (CSBS)</option>
                                <option value="VLSI">VLSI Design (VLSI)</option>
                                <option value="IT">Information Technology (IT)</option>
                                <option value="MECH">Mechanical Engineering (MECH)</option>
                                <option value="admin">🔐 Administrator</option>
                            </select>
                        </div>

                        <div className="input-group">
                            <label>Password</label>
                            <input
                                className="input" type="password" placeholder="Enter password"
                                value={password} onChange={e => setPassword(e.target.value)}
                                autoComplete="current-password"
                            />
                        </div>

                        <button className="btn btn-primary btn-full" type="submit" disabled={loading}>
                            {loading ? 'Signing in…' : 'Sign In →'}
                        </button>
                    </form>

                    <p style={{ marginTop: '1.5rem', textAlign: 'center', fontSize: '.8rem', color: 'var(--text-3)' }}>
                        Multi-Department Timetable Management System
                    </p>
                </div>
            </div>
        </>
    )
}
