import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { AuthProvider, useAuth } from './context/AuthContext'
import LoginPage from './pages/LoginPage'
import DeptDashboard from './pages/DeptDashboard'
import AdminDashboard from './pages/AdminDashboard'

function PrivateRoute({ children, requireAdmin = false }) {
    const { token, isAdmin } = useAuth()
    if (!token) return <Navigate to="/login" replace />
    if (requireAdmin && !isAdmin) return <Navigate to="/dept" replace />
    if (!requireAdmin && isAdmin) return <Navigate to="/admin" replace />
    return children
}

export default function App() {
    return (
        <AuthProvider>
            <BrowserRouter>
                <Routes>
                    <Route path="/login" element={<LoginPage />} />
                    <Route path="/dept" element={
                        <PrivateRoute><DeptDashboard /></PrivateRoute>
                    } />
                    <Route path="/admin" element={
                        <PrivateRoute requireAdmin><AdminDashboard /></PrivateRoute>
                    } />
                    <Route path="*" element={<Navigate to="/login" replace />} />
                </Routes>
            </BrowserRouter>
        </AuthProvider>
    )
}
