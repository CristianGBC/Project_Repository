import axios from 'axios'
import type { QueryClient } from '@tanstack/react-query'

export type SchoolRole = 'student' | 'professor' | 'school_admin'
export interface User {
  id: string
  first_name: string | null
  last_name: string | null
  email: string
  is_active: boolean
  global_role: 'super_admin' | null
  must_change_password: boolean
  memberships: { id: string; school_id: string; role: SchoolRole; is_active: boolean }[]
}
export interface School { id: string; code: string; name: string; is_active: boolean }
interface Token { access_token: string; expires_at: string }
const key = 'yachayrepo.session'
export const keys = { me: ['auth', 'me'], schools: ['catalogs', 'schools'] } as const
export const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000',
  timeout: 10000,
  headers: { Accept: 'application/json' },
})

export function errorMessage(error: unknown, login = false) {
  const status = axios.isAxiosError(error) ? error.response?.status : undefined
  switch (status) {
    case 400: return 'Revisa los datos ingresados. La contraseña actual debe ser correcta y la nueva, diferente.'
    case 401: return login ? 'Correo o contraseña incorrectos.' : 'Tu sesión ha terminado.'
    case 403: return 'No tienes permisos para realizar esta acción.'
    case 404: return 'No encontramos el recurso solicitado.'
    case 409: return 'La solicitud entra en conflicto con el estado actual.'
    case 422: return 'Revisa los datos ingresados.'
    case 429: return 'Demasiados intentos. Vuelve a intentarlo más tarde.'
    case 503: return 'El servicio no está disponible temporalmente.'
    default: return status ? 'Ocurrió un error en el servidor.' : 'No se pudo conectar con el servicio.'
  }
}

export function createSession(query: QueryClient) {
  let token: Token | null = null
  let expiry: ReturnType<typeof setTimeout> | undefined
  let epoch = 0
  let onEnd = () => {}
  function clear() {
    epoch++
    token = null
    clearTimeout(expiry)
    sessionStorage.removeItem(key)
    void query.cancelQueries()
    query.clear()
  }
  function end() { clear(); onEnd() }
  function save(next: Token) {
    const remaining = Date.parse(next.expires_at) - Date.now()
    if (!next.access_token || !Number.isFinite(remaining) || remaining <= 0) { clear(); return }
    token = { access_token: next.access_token, expires_at: next.expires_at }
    sessionStorage.setItem(key, JSON.stringify(token))
    expiry = setTimeout(end, remaining)
  }
  try { const raw = sessionStorage.getItem(key); if (raw) save(JSON.parse(raw)) }
  catch { clear() }
  const requestId = api.interceptors.request.use(config => {
    if (config.url !== '/auth/login' && token) config.headers.set('Authorization', 'Bearer ' + token.access_token)
    return config
  })
  const responseId = api.interceptors.response.use(undefined, (error: unknown) => {
    if (axios.isAxiosError(error) && error.response?.status === 401 && token &&
        error.config?.headers.get('Authorization') === 'Bearer ' + token.access_token) end()
    return Promise.reject(error)
  })
  async function me(): Promise<User | null> {
    if (!token) return null
    if (Date.parse(token.expires_at) <= Date.now()) { end(); return null }
    const start = epoch
    try {
      const user = await query.fetchQuery({
        queryKey: keys.me, staleTime: 0, networkMode: 'always',
        queryFn: async ({ signal }) => (await api.get<User>('/auth/me', { signal })).data,
      })
      if (start !== epoch) return null
      if (!user.is_active) { end(); return null }
      return user
    } catch (error) {
      if (start !== epoch) return null
      throw error
    }
  }
  return {
    me, end,
    onEnd(callback: () => void) { onEnd = callback },
    async login(email: string, password: string) {
      clear()
      const start = epoch
      const { data } = await api.post<Token>('/auth/login', { email, password })
      if (start !== epoch) return null
      save(data)
      return me()
    },
    async changePassword(current_password: string, new_password: string) {
      const start = epoch
      await api.post('/auth/change-password', { current_password, new_password })
      if (start === epoch) end()
    },
    dispose() {
      clearTimeout(expiry)
      api.interceptors.request.eject(requestId)
      api.interceptors.response.eject(responseId)
    },
  }
}
