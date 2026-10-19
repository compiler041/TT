import { createContext, useContext, useState } from 'react'

const AuthContext = createContext(null)

export function AuthProvider({ children }) {
    const [token, setToken] = useState(() => sessionStorage.getItem('tt_token'))
    const [deptId, setDeptId] = useState(() => sessionStorage.getItem('tt_dept_id'))
    const [display, setDisplay] = useState(() => sessionStorage.getItem('tt_display'))
    const [isAdmin, setIsAdmin] = useState(() => sessionStorage.getItem('tt_is_admin') === '1')

    const login = (data) => {
        sessionStorage.setItem('tt_token', data.token)
        sessionStorage.setItem('tt_dept_id', data.dept_id)
        sessionStorage.setItem('tt_display', data.display_name)
        sessionStorage.setItem('tt_is_admin', data.is_admin ? '1' : '0')
        setToken(data.token)
        setDeptId(data.dept_id)
        setDisplay(data.display_name)
        setIsAdmin(data.is_admin)
    }

    const logout = () => {
        sessionStorage.clear()
        setToken(null)
        setDeptId(null)
        setDisplay(null)
        setIsAdmin(false)
    }

    return (
        <AuthContext.Provider value={{ token, deptId, display, isAdmin, login, logout }}>
            {children}
        </AuthContext.Provider>
    )
}

export function useAuth() { return useContext(AuthContext) }
