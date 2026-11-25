import { createRootRouteWithContext, createRoute, createRouter, redirect, Outlet, Link, useRouter, type RouterHistory } from '@tanstack/react-router'
import { isAxiosError } from 'axios'
import type { createSession } from '../api/session'
import { errorMessage } from '../api/session'
import { Login, ChangePassword } from '../features/auth/screens'
import { Layout, Home, Profile } from '../features/dashboard'
import { Button, Card, Skeleton } from '../components/ui'
import { parseFilters } from '../api/academic'
import { DocumentDetail, Explorer, HomeAcademic, SchoolDetail, Schools } from '../features/academic/portal'

export type Session = ReturnType<typeof createSession>
const root = createRootRouteWithContext<{ session: Session }>()({
  component: Outlet,
  pendingComponent: () => <main className="center"><Skeleton /></main>,
  notFoundComponent: () => <State title="Página no encontrada" />,
  errorComponent: ({ error, reset }) => isAxiosError(error) && error.response?.status === 403
    ? <State title="Acceso restringido" />
    : <State title={errorMessage(error)} retry={reset} />,
})
function State({ title, retry }: { title: string; retry?: () => void }) {
  const router = useRouter()
  return <main className="center"><Card><h1>{title}</h1>{retry && <Button onClick={() => { retry(); void router.invalidate() }}>Reintentar</Button>} <Link to="/">Ir al inicio</Link></Card></main>
}
const login = createRoute({
  getParentRoute: () => root, path: '/login',
  beforeLoad: async ({ context }) => {
    const user = await context.session.me()
    if (user) throw redirect({ to: user.must_change_password ? '/change-password' : '/' })
  },
  component: () => <Login session={login.useRouteContext().session} />,
})
const protectedRoute = createRoute({
  getParentRoute: () => root, id: 'private',
  beforeLoad: async ({ context, location }) => {
    const user = await context.session.me()
    if (!user) throw redirect({ to: '/login' })
    if (user.must_change_password && location.pathname !== '/change-password') throw redirect({ to: '/change-password' })
    return { user }
  },
  component: () => {
    const { user, session } = protectedRoute.useRouteContext()
    return user.must_change_password ? <Outlet /> : <Layout user={user} logout={session.end}><Outlet /></Layout>
  },
})
const home = createRoute({ getParentRoute: () => protectedRoute, path: '/', component: AcademicHome })
function AcademicHome() {
  const navigate = home.useNavigate()
  return <><Home user={protectedRoute.useRouteContext().user} /><HomeAcademic search={q => void navigate({ to: '/documents', search: { q } })} /></>
}
const documents = createRoute({
  getParentRoute: () => protectedRoute, path: '/documents', validateSearch: parseFilters,
  component: ExplorerRoute,
})
function ExplorerRoute() {
  const filters = documents.useSearch()
  const navigate = documents.useNavigate()
  return <Explorer filters={filters} update={(search, replace) => void navigate({ search, replace })} />
}
const documentDetail = createRoute({
  getParentRoute: () => protectedRoute, path: '/documents/$documentId',
  validateSearch: (raw: Record<string, unknown>) => {
    const version = Number(raw.version)
    return { ...parseFilters(raw), version: Number.isSafeInteger(version) && version > 0 ? version : undefined }
  },
  component: DocumentRoute,
})
function DocumentRoute() {
  const { documentId } = documentDetail.useParams()
  const { version, ...filters } = documentDetail.useSearch()
  const navigate = documentDetail.useNavigate()
  return <DocumentDetail id={documentId} version={version} filters={filters} select={value => void navigate({ search: { ...filters, version: value } })} />
}
const schools = createRoute({ getParentRoute: () => protectedRoute, path: '/schools', component: Schools })
const school = createRoute({
  getParentRoute: () => protectedRoute, path: '/schools/$schoolId',
  component: () => <SchoolDetail {...school.useParams()} />,
})
const subject = createRoute({
  getParentRoute: () => protectedRoute, path: '/schools/$schoolId/subjects/$subjectId',
  component: () => <SchoolDetail {...subject.useParams()} />,
})
const profile = createRoute({ getParentRoute: () => protectedRoute, path: '/profile', component: () => <Profile user={protectedRoute.useRouteContext().user} /> })
const password = createRoute({ getParentRoute: () => protectedRoute, path: '/change-password', component: () => {
  const { user, session } = protectedRoute.useRouteContext()
  return <ChangePassword user={user} session={session} />
} })
const forbidden = createRoute({ getParentRoute: () => root, path: '/403', component: () => <State title="Acceso restringido" /> })
export function makeRouter(session: Session, history?: RouterHistory) {
  const router = createRouter({ routeTree: root.addChildren([login, forbidden, protectedRoute.addChildren([home, profile, password, documents, documentDetail, schools, school, subject])]), context: { session }, history, defaultPendingMs: 0 })
  session.onEnd(() => { void router.navigate({ to: '/login', replace: true }) })
  return router
}
declare module '@tanstack/react-router' { interface Register { router: ReturnType<typeof makeRouter> } }
