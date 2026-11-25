import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createMemoryHistory, RouterProvider } from '@tanstack/react-router'
import { AxiosError } from 'axios'
import { api, createSession, errorMessage, type User } from './api/session'
import { canUploadToSchool, canManageSchool, canViewFullOperations, hasSchoolRole } from './features/auth/permissions'
import { loginSchema, passwordSchema } from './features/auth/forms'
import { makeRouter } from './routes/router'

const user: User = {
  id: 'test', first_name: 'Ana', last_name: 'Pérez', email: 'ana@example.org',
  is_active: true, global_role: null, must_change_password: false,
  memberships: [
    { id: '1', school_id: 'math', role: 'professor', is_active: true },
    { id: '2', school_id: 'phy', role: 'student', is_active: true },
    { id: '3', school_id: 'phy', role: 'school_admin', is_active: true },
  ],
}
const originalAdapter = api.defaults.adapter
let client: QueryClient
let session: ReturnType<typeof createSession>
let profile: User
let invalid: number | undefined
let expiry: number
beforeEach(() => {
  vi.spyOn(window, 'scrollTo').mockImplementation(() => {})
  profile = user
  invalid = undefined
  expiry = 60000
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  session = createSession(client)
  api.defaults.adapter = async config => {
    const data = config.url === '/auth/login' ? { access_token: 'test-token', expires_at: new Date(Date.now() + expiry).toISOString() }
      : config.url === '/catalogs/schools' ? [{ id: 'math', name: 'Matemáticas' }]
      : ['/documents', '/subjects', '/catalogs/document-types'].includes(config.url ?? '') ? [] : profile
    const response = { config, data, headers: {}, status: invalid ?? 200, statusText: '' }
    if (invalid) throw new AxiosError('test', undefined, config, undefined, response)
    if (config.url === '/auth/me') expect(config.headers.get('Authorization')).toBe('Bearer test-token')
    return response
  }
})
afterEach(() => {
  cleanup()
  session.dispose()
  client.clear()
  sessionStorage.clear()
  api.defaults.adapter = originalAdapter
  vi.useRealTimers()
  vi.restoreAllMocks()
})
function show(path: string) {
  const router = makeRouter(session, createMemoryHistory({ initialEntries: [path] }))
  render(<QueryClientProvider client={client}><RouterProvider router={router} /></QueryClientProvider>)
  return router
}
it('valida correo, contraseña y confirmación con los límites del contrato', () => {
  expect(loginSchema.safeParse({ email: 'bad', password: '' }).success).toBe(false)
  expect(loginSchema.safeParse({ email: user.email, password: 'x' }).success).toBe(true)
  expect(passwordSchema.safeParse({ current_password: 'x', new_password: 'x'.repeat(12), confirmation: 'y' }).success).toBe(false)
})
it('aplica roles múltiples por escuela sin otorgar subida al administrador', () => {
  expect(canUploadToSchool(user, 'math')).toBe(true)
  expect(hasSchoolRole(user, 'phy', 'student')).toBe(true)
  expect(canManageSchool(user, 'phy')).toBe(true)
  expect(canUploadToSchool(user, 'phy')).toBe(false)
  expect(canViewFullOperations(user)).toBe(false)
})
it('redirige una ruta privada al login y presenta un 401 de credenciales', async () => {
  const router = show('/profile')
  const button = await screen.findByRole('button', { name: 'Iniciar sesión' })
  expect(router.state.location.pathname).toBe('/login')
  invalid = 401
  const input = userEvent.setup()
  await input.type(screen.getByLabelText('Correo electrónico'), user.email)
  await input.type(screen.getByLabelText('Contraseña', { exact: true }), 'incorrecta')
  await input.click(button)
  expect((await screen.findByRole('alert')).textContent).toContain('Correo o contraseña incorrectos')
})
it('valida el perfil, resuelve escuelas y muestra solo enlaces implementados', async () => {
  await session.login(user.email, 'password')
  show('/profile')
  expect(await screen.findByRole('heading', { name: 'Ana Pérez' })).toBeTruthy()
  expect(await screen.findByText('Matemáticas')).toBeTruthy()
  expect(screen.queryByRole('link', { name: 'Usuarios' })).toBeNull()
})
it('oculta el portal del profesor a administradores sin membresía docente y protege la URL', async () => {
  profile = { ...user, global_role: 'super_admin', memberships: user.memberships.filter(m => m.role !== 'professor') }
  await session.login(user.email, 'password')
  const router = show('/profile')
  await screen.findByRole('heading', { name: 'Mi perfil' })
  expect(screen.queryByRole('link', { name: 'Mis documentos' })).toBeNull()
  await router.navigate({ to: '/professor/documents/new' })
  await screen.findByRole('heading', { name: 'Acceso restringido' })
})
it('explora con búsqueda diferida, reinicia la página al filtrar y limpia la URL', async () => {
  await session.login(user.email, 'password')
  const router = show('/documents?page=2')
  const field = await screen.findByLabelText('Buscar por título')
  const input = userEvent.setup()
  await input.type(field, 'Álgebra')
  expect(router.state.location.search.q).toBeUndefined()
  expect(await screen.findByText('Página 1')).toBeTruthy()
  expect(router.state.location.search.q).toBe('Álgebra')
  await input.selectOptions(await screen.findByLabelText('Escuela'), 'math')
  expect(router.state.location.search.school).toBe('math')
  expect(router.state.location.search.q).toBe('Álgebra')
  await input.click(screen.getByRole('button', { name: 'Limpiar filtros' }))
  expect(router.state.location.searchStr).toBe('')
})
it('bloquea el perfil con cambio pendiente y termina la sesión después del cambio', async () => {
  profile = { ...user, must_change_password: true }
  await session.login(user.email, 'temporary')
  const router = show('/profile')
  expect(await screen.findByRole('heading', { name: 'Cambiar contraseña' })).toBeTruthy()
  expect(router.state.location.pathname).toBe('/change-password')
  await session.changePassword('temporary', 'another password')
  expect(await screen.findByRole('button', { name: 'Iniciar sesión' })).toBeTruthy()
})
it('conserva sesión con 403 y 503 y limpia caché ante 401 autenticado', async () => {
  await session.login(user.email, 'password')
  client.setQueryData(['private'], 'record')
  for (const status of [403, 503]) {
    invalid = status
    await expect(session.me()).rejects.toBeInstanceOf(AxiosError)
    expect(sessionStorage.length).toBe(1)
  }
  invalid = 401
  expect(await session.me()).toBeNull()
  expect(client.getQueryData(['private'])).toBeUndefined()
  expect(sessionStorage.length).toBe(0)
  expect(errorMessage(new Error())).toContain('conectar')
})
it('limpia el token y datos privados al expirar o cerrar sesión', async () => {
  vi.useFakeTimers()
  expiry = 1000
  await session.login(user.email, 'password')
  await vi.advanceTimersByTimeAsync(1001)
  expect(await session.me()).toBeNull()
  expect(sessionStorage.length).toBe(0)
  await session.login(user.email, 'password')
  client.setQueryData(['private'], 'record')
  session.end()
  expect(await session.me()).toBeNull()
  expect(client.getQueryData(['private'])).toBeUndefined()
})
