import { useState, type FormEvent } from 'react'
import { Link, useNavigate } from '@tanstack/react-router'
import { useQuery } from '@tanstack/react-query'
import { z } from 'zod'
import { isUuid, schoolsQuery } from '../../api/academic'
import { adminQuery, duplicateMembership, grantableRoles, manageableSchools, pageSize, roles, type ManagedUser, type MembershipInput } from '../../api/administration'
import { api, keys, type SchoolRole, type User } from '../../api/session'
import { useQueryClient } from '@tanstack/react-query'
import { Alert, Badge, Button, Card, Input } from '../../components/ui'
import { canManageSchool, isSuperAdmin } from '../auth/permissions'
import { Confirm, formatted, Pages, Results, TemporaryPassword, useAdminAction } from './ui'

const identity = z.object({
  first_name: z.string().trim().min(1).max(100),
  last_name: z.string().trim().min(1).max(100),
  email: z.email().max(255),
})
const password = z.string().min(12).max(1024)
const nameOf = (user: User) => [user.first_name, user.last_name].filter(Boolean).join(' ') || user.email
const userPath = '/admin/users/$userId'

export function AdminUsers({ actor }: { actor: User }) {
  const [page, setPage] = useState(0)
  const [filters, setFilters] = useState({ search: '', status: '', school: '', role: '' })
  const schools = useQuery(schoolsQuery)
  const query = useQuery(adminQuery<ManagedUser[]>('/users', {
    offset: page * pageSize, limit: pageSize + 1, search: filters.search || undefined,
    is_active: filters.status ? filters.status === 'active' : undefined,
    school_id: filters.school || undefined, role: filters.role || undefined,
  }))
  const rows = query.data ?? []
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const fields = new FormData(event.currentTarget)
    setFilters({ search: String(fields.get('search')).trim(), status: String(fields.get('status')), school: String(fields.get('school')), role: String(fields.get('role')) })
    setPage(0)
  }
  return <main className="academic-stack"><h1>Usuarios</h1><p className="muted">Cuentas y membresías dentro de tu alcance administrativo.</p>
    <Link className="button primary justify-self-start" to="/admin/users/new">Crear cuenta</Link>
    <Card><form className="academic-filters" onSubmit={submit}>
      <label>Nombre o correo<Input name="search" maxLength={100} /></label>
      <label>Estado<select name="status"><option value="">Todos</option><option value="active">Activas</option><option value="inactive">Inactivas</option></select></label>
      <label>Escuela<select name="school"><option value="">Todas</option>{manageableSchools(actor, schools.data ?? []).map(s => <option key={s.id} value={s.id}>{s.name}</option>)}</select></label>
      <label>Rol<select name="role"><option value="">Todos</option>{Object.entries(roles).map(([key, label]) => <option key={key} value={key}>{label}</option>)}{isSuperAdmin(actor) && <option value="super_admin">Superadministrador</option>}</select></label>
      <Button className="self-end">Buscar usuarios</Button>
    </form></Card>
    <Results pending={query.isPending} error={query.error} retry={() => void query.refetch()} empty={!rows.length}>
      <div className="academic-grid">{rows.slice(0, pageSize).map(user => <Card key={user.id}>
        <h2><Link to={userPath} params={{ userId: user.id }}>{nameOf(user)}</Link></h2><p className="muted">{user.email}</p>
        <Badge>{user.is_active ? 'Activa' : 'Inactiva'}</Badge>{user.global_role && <Badge>Superadministrador</Badge>}
        {user.memberships.map(item => <p key={item.id}>{schools.data?.find(school => school.id === item.school_id)?.name ?? item.school_id} · {roles[item.role]}{!item.is_active && ' · Inactiva'}</p>)}
        <p className="muted">Creada {formatted(user.created_at)}</p>
      </Card>)}</div>
    </Results><Pages page={page} count={rows.length} change={setPage} />
  </main>
}

function MembershipFields({ actor, prefix = '' }: { actor: User; prefix?: string }) {
  const schools = useQuery(schoolsQuery)
  return <div className="academic-filters">
    <label>Escuela de membresía<select name={prefix + 'school'} required><option value="">Selecciona una escuela</option>
      {manageableSchools(actor, schools.data ?? []).filter(school => school.is_active).map(s => <option key={s.id} value={s.id}>{s.name}</option>)}</select></label>
    <label>Rol de membresía<select name={prefix + 'role'} required>
      {grantableRoles(actor).map(role => <option key={role} value={role}>{roles[role]}</option>)}</select></label>
  </div>
}
export function AccountForm({ actor, target }: { actor: User; target?: ManagedUser }) {
  const [rows, setRows] = useState<number[]>(target || isSuperAdmin(actor) ? [] : [0])
  const [sequence, setSequence] = useState(1)
  const [validation, setValidation] = useState('')
  const navigate = useNavigate()
  const state = useAdminAction()
  const client = useQueryClient()
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = event.currentTarget
    const input = new FormData(form)
    setValidation('')
    const result = identity.safeParse({ first_name: input.get('first_name'), last_name: input.get('last_name'), email: input.get('email') })
    if (!result.success) { setValidation('Ingresa nombres, apellidos y un correo válido.'); return }
    if (target) {
      void state.run(async () => {
        await api.patch(`/users/${target.id}`, result.data)
        if (target.id === actor.id) await client.invalidateQueries({ queryKey: keys.me })
      },
        () => void navigate({ to: userPath, params: { userId: target.id } }))
      return
    }
    const temporary = password.safeParse(input.get('temporary_password'))
    if (!temporary.success) { setValidation('La contraseña temporal debe tener entre 12 y 1024 caracteres.'); return }
    const memberships: MembershipInput[] = []
    for (const row of rows) {
      const next = { school_id: String(input.get(`${row}-school`)), role: input.get(`${row}-role`) as SchoolRole }
      if (duplicateMembership(memberships, next)) { setValidation('La misma escuela y rol aparecen más de una vez.'); return }
      memberships.push(next)
    }
    if (!isSuperAdmin(actor) && !memberships.length) { setValidation('Agrega al menos una membresía de tu escuela.'); return }
    const elevated = isSuperAdmin(actor) && input.get('global_role') === 'super_admin'
    if ((elevated || memberships.some(item => item.role === 'school_admin')) && !window.confirm('¿Confirmas conceder privilegios administrativos?')) return
    void state.run(async () => {
      const response = await api.post<ManagedUser>('/users', {
        ...result.data, temporary_password: temporary.data, memberships, global_role: elevated ? 'super_admin' : null,
      })
      form.reset()
      void navigate({ to: userPath, params: { userId: response.data.id } })
    })
  }
  return <main className="academic-stack"><Link to="/admin/users">Volver a usuarios</Link>
    <h1>{target ? 'Editar cuenta' : 'Crear cuenta'}</h1><Card><form onSubmit={submit} aria-busy={state.busy}>
      <fieldset disabled={state.busy} className="academic-stack">
        <label>Nombres<Input name="first_name" required maxLength={100} defaultValue={target?.first_name ?? ''} /></label>
        <label>Apellidos<Input name="last_name" required maxLength={100} defaultValue={target?.last_name ?? ''} /></label>
        <label>Correo electrónico<Input name="email" required type="email" maxLength={255} defaultValue={target?.email} /></label>
        {!target && <>
          <TemporaryPassword />
          {isSuperAdmin(actor) && <label>Rol global<select className="input" name="global_role"><option value="">Sin rol global</option><option value="super_admin">Superadministrador</option></select></label>}
          <fieldset><legend>Membresías iniciales</legend>{rows.map(row => <div key={row} className="version-row">
            <MembershipFields actor={actor} prefix={`${row}-`} />
            <Button variant="ghost" type="button" onClick={() => setRows(rows.filter(id => id !== row))}>Quitar membresía</Button>
          </div>)}
            <Button type="button" variant="ghost" onClick={() => { setRows([...rows, sequence]); setSequence(sequence + 1) }}>Agregar membresía</Button>
          </fieldset>
        </>}
        {(state.error || validation) && <Alert>{state.error || validation}</Alert>}
        <Button>{state.busy ? 'Guardando…' : target ? 'Guardar cuenta' : 'Crear usuario'}</Button>
      </fieldset>
    </form></Card>
  </main>
}
export function AdminUser({ id, actor, mode }: { id: string; actor: User; mode: 'view' | 'edit' | 'memberships' }) {
  const path = `/users/${id}`
  const query = useQuery({ ...adminQuery<ManagedUser>(path), enabled: isUuid(id) })
  const schools = useQuery(schoolsQuery)
  const memberships = useQuery({ ...adminQuery<User['memberships']>(path + '/memberships'), enabled: mode === 'memberships' && !!query.data })
  const state = useAdminAction()
  const navigate = useNavigate()
  if (!isUuid(id)) return <Alert>Cuenta no encontrada.</Alert>
  const user = query.data
  const self = id === actor.id
  if (mode === 'edit' && user && isSuperAdmin(actor)) return <AccountForm key={id} actor={actor} target={user} />
  return <main className="academic-stack"><Link to="/admin/users">Volver a usuarios</Link>
    <Results pending={query.isPending} error={query.error} retry={() => void query.refetch()} empty={!user}>
      {user && <><h1>{nameOf(user)}</h1><Card><p>{user.email}</p><p>Creada {formatted(user.created_at)}</p>
        <Badge>{user.is_active ? 'Activa' : 'Inactiva'}</Badge>{user.global_role && <Badge>Superadministrador</Badge>}
        {user.must_change_password && <Badge>Cambio de contraseña pendiente</Badge>}
        <div className="pdf-controls">
          {isSuperAdmin(actor) && <Link className="button ghost" to="/admin/users/$userId/edit" params={{ userId: id }}>Editar cuenta</Link>}
          {isSuperAdmin(actor) && !self && <Confirm label={user.is_active ? 'Desactivar cuenta' : 'Activar cuenta'} description={user.is_active ? 'La cuenta perderá el acceso al repositorio y su sesión será revocada.' : 'La cuenta podrá iniciar sesión de nuevo.'}
            action={() => api.post(path + (user.is_active ? '/deactivate' : '/activate'))} />}
          {user.can_reset_password && !self && <Confirm label="Restablecer contraseña" description="La cuenta deberá cambiar la contraseña temporal y el JWT anterior quedará invalidado."
            fields={<TemporaryPassword />} action={data => api.post(path + '/reset-password', { temporary_password: password.parse(data.get('temporary_password')) })} />}
          {mode !== 'memberships' && <Link className="button ghost" to="/admin/users/$userId/memberships" params={{ userId: id }}>Gestionar membresías</Link>}
        </div>
      </Card>
      <Card><h2>Membresías visibles</h2>
        {mode === 'memberships' && memberships.isPending && <p>Cargando membresías…</p>}
        {mode === 'memberships' && memberships.isError && <Alert>No se pudieron consultar las membresías. <Button onClick={() => void memberships.refetch()}>Reintentar</Button></Alert>}
        {(memberships.data ?? user.memberships).map(item => <div key={item.id} className="version-row">
          <strong>{schools.data?.find(s => s.id === item.school_id)?.name ?? item.school_id}</strong>
          <p>{roles[item.role]} · {item.is_active ? 'Activa' : 'Inactiva'}</p>
          {mode === 'memberships' && !self && canManageSchool(actor, item.school_id) && grantableRoles(actor).includes(item.role) &&
            <Confirm label="Revocar membresía" description="Se retirará este rol escolar y se invalidará la sesión anterior de la cuenta."
              action={() => api.delete(`${path}/memberships/${item.id}`)} onSuccess={() => void navigate({ to: '/admin/users' })} />}
        </div>)}
        {mode === 'memberships' && !self && <form onSubmit={event => {
          event.preventDefault()
          const data = new FormData(event.currentTarget)
          const next = { school_id: String(data.get('school')), role: data.get('role') as SchoolRole }
          if (next.role === 'school_admin' && !window.confirm('¿Confirmas conceder privilegios administrativos en esta escuela?')) return
          void state.run(() => api.post(path + '/memberships', next))
        }}><MembershipFields actor={actor} />{state.error && <Alert>{state.error}</Alert>}<Button disabled={state.busy}>Asignar membresía</Button></form>}
      </Card></>}
    </Results>
  </main>
}
