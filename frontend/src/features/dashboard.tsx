import type { ReactNode } from 'react'
import { Link, useLocation } from '@tanstack/react-router'
import { useQuery } from '@tanstack/react-query'
import { House, UserRound, Menu, LogOut, ChevronDown, Building2, KeyRound, Library, Folder, FileUp, History, Trash2 } from 'lucide-react'
import { api, keys, errorMessage, type School, type User } from '../api/session'
import { Alert, Avatar, Badge, Button, Card, Dialog, DropdownMenu, Separator, Sheet, Skeleton } from '../components/ui'
import { Brand } from './auth/screens'
import { hasAnySchoolRole } from './auth/permissions'

const navigation = [
  { to: '/', label: 'Inicio', icon: House },
  { to: '/documents', label: 'Explorar documentos', icon: Library },
  { to: '/schools', label: 'Escuelas', icon: Building2 },
  { to: '/profile', label: 'Mi perfil', icon: UserRound },
] as const
const professorNavigation = [
  { to: '/professor/documents', label: 'Mis documentos', icon: Folder },
  { to: '/professor/documents/new', label: 'Subir documento', icon: FileUp },
  { to: '/professor/uploads', label: 'Mis subidas', icon: History },
  { to: '/professor/documents/deleted', label: 'Documentos eliminados', icon: Trash2 },
] as const
const roles = { student: 'Estudiante', professor: 'Profesor', school_admin: 'Administrador de escuela' }
const displayName = (user: User) => [user.first_name, user.last_name].filter(Boolean).join(' ') || user.email
function Navigation({ user, mobile = false }: { user: User; mobile?: boolean }) {
  const items = hasAnySchoolRole(user, 'professor') ? [...navigation, ...professorNavigation] : navigation
  return <nav aria-label="Navegación principal">{items.map(({ to, label, icon: Icon }) => {
    const link = <Link key={to} to={to} search={{}} activeOptions={{ exact: to === '/' || to.startsWith('/professor') }} className="nav-link"><Icon size={19} />{label}</Link>
    return mobile ? <Dialog.Close key={to} asChild>{link}</Dialog.Close> : link
  })}</nav>
}
export function Layout({ user, logout, children }: { user: User; logout: () => void; children: ReactNode }) {
  const pathname = useLocation({ select: location => location.pathname })
  const section = [...professorNavigation, ...navigation].find(item => item.to === pathname) ??
    [...professorNavigation, ...navigation].find(item => item.to !== '/' && pathname.startsWith(item.to + '/'))
  return <div className="layout"><a className="skip" href="#content">Ir al contenido</a>
    <aside className="sidebar"><Brand /><Separator /><Navigation user={user} />
      <Button variant="ghost" className="sidebar-logout" onClick={logout}><LogOut size={18} />Cerrar sesión</Button>
    </aside>
    <div className="workspace"><header className="topbar">
      <div className="heading"><div className="mobile-menu"><Sheet trigger={<Button variant="ghost" aria-label="Abrir menú"><Menu size={22} /></Button>}><Navigation user={user} mobile /></Sheet></div>
        <strong>{section?.label ?? 'Cambiar contraseña'}</strong></div>
      <DropdownMenu.Root><DropdownMenu.Trigger asChild>
        <Button variant="ghost" aria-label="Menú de cuenta"><Avatar name={displayName(user)} /><span className="account-name">{displayName(user)}</span><ChevronDown size={16} /></Button>
      </DropdownMenu.Trigger><DropdownMenu.Portal><DropdownMenu.Content align="end" sideOffset={8} className="dropdown">
        <DropdownMenu.Item asChild><Link to="/profile"><UserRound size={16} />Mi perfil</Link></DropdownMenu.Item>
        <DropdownMenu.Item onSelect={logout}><LogOut size={16} />Cerrar sesión</DropdownMenu.Item>
      </DropdownMenu.Content></DropdownMenu.Portal></DropdownMenu.Root>
    </header><div id="content" tabIndex={-1} className="content">{children}</div></div>
  </div>
}
function Memberships({ user }: { user: User }) {
  const schools = useQuery({
    queryKey: keys.schools, enabled: user.memberships.length > 0,
    queryFn: async ({ signal }) => (await api.get<School[]>('/catalogs/schools', { signal })).data,
  })
  return <Card><div className="section-title"><Building2 size={22} /><h2>Escuelas y membresías</h2></div>
    {!user.memberships.length && <p className="muted">Todavía no tienes membresías asignadas.</p>}
    {user.memberships.length > 0 && schools.isPending && <Skeleton />}
    {schools.isError && <Alert>{errorMessage(schools.error)} <Button variant="ghost" onClick={() => void schools.refetch()}>Reintentar</Button></Alert>}
    <ul className="membership-list">{user.memberships.map(m => <li key={m.id}>
      <span>{schools.data?.find(s => s.id === m.school_id)?.name ?? m.school_id}</span>
      <span><Badge>{roles[m.role]}</Badge>{!m.is_active && <Badge>Inactiva</Badge>}</span>
    </li>)}</ul>
  </Card>
}
export function Home({ user }: { user: User }) {
  return <main><section className="welcome"><p>Repositorio académico institucional</p>
    <h1>Hola, {user.first_name || displayName(user)}</h1><p>Consulta tus escuelas y los roles asignados a tu cuenta.</p>
    <Link className="button primary" to="/profile"><UserRound size={18} />Ver mi perfil</Link></section>
    <Memberships user={user} /></main>
}
export function Profile({ user }: { user: User }) {
  return <main className="profile"><h1>Mi perfil</h1><p className="muted">Información de tu cuenta institucional.</p>
    <Card><div className="section-title"><Avatar name={displayName(user)} /><h2>{displayName(user)}</h2></div>
      <dl><dt>Nombre</dt><dd>{user.first_name || 'Sin registrar'}</dd><dt>Apellido</dt><dd>{user.last_name || 'Sin registrar'}</dd>
      <dt>Correo electrónico</dt><dd>{user.email}</dd><dt>Estado</dt><dd>{user.is_active ? 'Activa' : 'Inactiva'}</dd>
      <dt>Rol global</dt><dd>{user.global_role === 'super_admin' ? 'Administrador global' : 'Sin rol global'}</dd></dl>
      <Link to="/change-password" className="button ghost"><KeyRound size={18} />Cambiar contraseña</Link>
    </Card><Memberships user={user} /></main>
}
