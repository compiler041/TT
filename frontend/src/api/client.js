import axios from 'axios'

const api = axios.create({ baseURL: '/api' })

// Attach auth token to every request automatically
api.interceptors.request.use(cfg => {
    const token = sessionStorage.getItem('tt_token')
    if (token) cfg.headers.Authorization = `Bearer ${token}`
    return cfg
})

export default api
