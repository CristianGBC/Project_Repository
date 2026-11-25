import { lazy, Suspense, useEffect, useRef, useState, type FormEvent } from 'react'
import { Link } from '@tanstack/react-router'
import { useQuery } from '@tanstack/react-query'
import { isAxiosError } from 'axios'
import { Building2, FileText, Search } from 'lucide-react'
import { Alert, Badge, Button, Card, Input, Skeleton } from '../../components/ui'
import { errorMessage } from '../../api/session'
import {
  detailQuery, documentsQuery, fetchFile, isUuid, pageSize, schoolsQuery,
  selectedVersion, subjectsQuery, typesQuery, versionsQuery,
  type AcademicDocument, type Filters, type Version,
} from '../../api/academic'

const Pdf = lazy(() => import('./pdf'))
const date = (value: string) => new Intl.DateTimeFormat('es', { dateStyle: 'medium' }).format(new Date(value))
const size = (bytes: number) => bytes < 1024 ? `${bytes} B` : `${(bytes / 1024).toFixed(1)} KiB`

function Failed({ error, retry, transfer = false }: { error: unknown; retry: () => void; transfer?: boolean }) {
  const status = isAxiosError(error) ? error.response?.status : undefined
  const message = transfer && status === 409 ? 'Por el momento no se pudo obtener una copia íntegra.'
    : transfer && status === 503 ? 'El archivo no está disponible temporalmente.' : errorMessage(error)
  return <Alert>{message} <Button variant="ghost" onClick={retry}>Reintentar</Button></Alert>
}
function DocumentList({ documents, filters = {} }: { documents: AcademicDocument[]; filters?: Filters }) {
  if (!documents.length) return <Card><p>No hay documentos para esta consulta.</p></Card>
  return <div className="academic-grid">{documents.map(doc => <Card key={doc.document_id}>
    <div className="section-title"><FileText size={19} /><Link to="/documents/$documentId" params={{ documentId: doc.document_id }} search={{ ...filters, version: undefined }}>{doc.title || doc.filename}</Link></div>
    <p className="muted">{doc.school} · {date(doc.created_at)}</p>
    <p className="muted">{doc.filename} · {size(doc.size_bytes)} · v{doc.version_number}</p>
    {doc.document_type_name && <Badge>{doc.document_type_name}</Badge>}
    {doc.subjects.map(subject => <Badge key={subject.id}>{subject.name}</Badge>)}
  </Card>)}</div>
}
export function Published({ filters, recent = false }: { filters: Filters; recent?: boolean }) {
  const query = useQuery(documentsQuery(filters, recent ? 4 : pageSize))
  if (query.isPending) return <Skeleton />
  if (query.isError) return <Failed error={query.error} retry={() => void query.refetch()} />
  return <DocumentList documents={query.data.slice(0, recent ? 4 : pageSize)} filters={filters} />
}
export function HomeAcademic({ search }: { search: (q: string) => void }) {
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    search(String(new FormData(event.currentTarget).get('q') ?? '').trim())
  }
  return <section className="academic-stack">
    <Card><h2>Explorar el repositorio</h2><form className="academic-search" onSubmit={submit}>
      <Input name="q" aria-label="Buscar documentos" placeholder="Buscar por título" maxLength={200} /><Button><Search size={18} />Buscar</Button>
    </form><Link to="/schools">Consultar escuelas</Link></Card>
    <h2>Publicaciones recientes</h2><Published filters={{}} recent />
  </section>
}
export function Schools() {
  const query = useQuery(schoolsQuery)
  return <main className="academic-stack"><h1>Escuelas</h1>
    {query.isPending ? <Skeleton /> : query.isError ? <Failed error={query.error} retry={() => void query.refetch()} />
      : query.data.length ? <div className="academic-grid">{query.data.map(school => <Card key={school.id}>
        <div className="section-title"><Building2 size={22} /><h2><Link to="/schools/$schoolId" params={{ schoolId: school.id }}>{school.name}</Link></h2></div>
        {!school.is_active && <Badge>Inactiva</Badge>}
      </Card>)}</div> : <Card>No hay escuelas en el catálogo.</Card>}
  </main>
}
export function SchoolDetail({ schoolId, subjectId }: { schoolId: string; subjectId?: string }) {
  const schools = useQuery(schoolsQuery)
  const school = schools.data?.find(item => item.id === schoolId)
  const subjects = useQuery({ ...subjectsQuery(schoolId), enabled: !!school })
  if (schools.isPending) return <Skeleton />
  if (schools.isError) return <Failed error={schools.error} retry={() => void schools.refetch()} />
  if (!school) return <Alert>Escuela inexistente. <Link to="/schools">Volver a escuelas</Link></Alert>
  if (subjects.isPending) return <Skeleton />
  if (subjects.isError) return <Failed error={subjects.error} retry={() => void subjects.refetch()} />
  const subject = subjects.data.find(item => item.id === subjectId)
  if (subjectId && !subject) return <Alert>Materia inexistente en esta escuela. <Link to="/schools/$schoolId" params={{ schoolId }}>Volver a la escuela</Link></Alert>
  const filters = { school: schoolId, subject: subjectId }
  return <main className="academic-stack">
    {subject ? <Link to="/schools/$schoolId" params={{ schoolId }}>Volver a la escuela</Link> : <Link to="/schools">Volver a escuelas</Link>}
    <h1>{subject?.name ?? school.name}</h1>
    {subject ? <><p>{school.name}</p>{subject.description && <p>{subject.description}</p>}</> : <Card><h2>Materias</h2>
      {subjects.data.length ? <ul>{subjects.data.map(item => <li key={item.id}><Link to="/schools/$schoolId/subjects/$subjectId"
        params={{ schoolId, subjectId: item.id }}>{item.name}</Link></li>)}</ul> : <p>Esta escuela no tiene materias registradas.</p>}
    </Card>}
    <h2>Documentos publicados</h2><Published filters={filters} />
    <Link to="/documents" search={filters}>Ver todos en el explorador</Link>
  </main>
}
export function Explorer({ filters, update }: { filters: Filters; update: (filters: Filters, replace?: boolean) => void }) {
  const schools = useQuery(schoolsQuery)
  const subjects = useQuery(subjectsQuery(filters.school))
  const types = useQuery(typesQuery)
  const documents = useQuery(documentsQuery(filters))
  const [draft, setDraft] = useState<string | null>(null)
  const page = filters.page ?? 1
  useEffect(() => {
    if (draft === null) return
    const timer = setTimeout(() => {
      setDraft(null)
      if (draft.trim() !== (filters.q ?? '')) update({ ...filters, q: draft.trim() || undefined, page: undefined }, true)
    }, 350)
    return () => clearTimeout(timer)
  }, [draft, filters, update])
  function change(next: Filters) {
    setDraft(null)
    update({ ...filters, q: draft === null ? filters.q : draft.trim() || undefined, ...next, page: undefined })
  }
  const entries = documents.data?.slice(0, pageSize) ?? []
  return <main className="academic-stack"><h1>Explorar documentos</h1><p className="muted">Busca publicaciones de todas las escuelas.</p>
    <Card><div className="academic-filters">
      <label>Buscar por título<Input value={draft ?? filters.q ?? ''} maxLength={200} onChange={e => setDraft(e.target.value)} /></label>
      <label>Escuela<select value={filters.school ?? ''} onChange={e => change({ school: e.target.value || undefined, subject: undefined })}>
        <option value="">Todas las escuelas</option>{schools.data?.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
      </select></label>
      <label>Materia<select value={filters.subject ?? ''} onChange={e => change({ subject: e.target.value || undefined })}>
        <option value="">Todas las materias</option>{subjects.data?.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
      </select></label>
      <label>Tipo documental<select value={filters.type ?? ''} onChange={e => change({ type: e.target.value || undefined })}>
        <option value="">Todos los tipos</option>{types.data?.map(t => <option key={t.id} value={t.id}>{t.name}</option>)}
      </select></label>
    </div><Button variant="ghost" onClick={() => { setDraft(null); update({}) }}>Limpiar filtros</Button>
    {[schools, subjects, types].some(query => query.isError) && <Alert>Algún catálogo de filtros no está disponible. <Button variant="ghost" onClick={() => {
      void schools.refetch(); void subjects.refetch(); void types.refetch()
    }}>Reintentar catálogos</Button></Alert>}</Card>
    {documents.isPending ? <Skeleton /> : documents.isError ? <Failed error={documents.error} retry={() => void documents.refetch()} /> : <DocumentList documents={entries} filters={filters} />}
    <nav aria-label="Paginación" className="pdf-controls">
      <Button variant="ghost" disabled={page <= 1} onClick={() => update({ ...filters, page: page - 1 })}>Anterior</Button>
      <span>Página {page}</span>
      <Button variant="ghost" disabled={(documents.data?.length ?? 0) <= pageSize} onClick={() => update({ ...filters, page: page + 1 })}>Siguiente</Button>
    </nav>
  </main>
}

function VersionFile({ id, version }: { id: string; version: Version }) {
  const pdf = version.mime_type === 'application/pdf' || (version.mime_type === 'application/octet-stream' && /\.pdf$/i.test(version.filename))
  const [file, setFile] = useState<Awaited<ReturnType<typeof fetchFile>>>()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>()
  const [attempt, setAttempt] = useState(0)
  const download = useRef<AbortController>(null)
  const urls = useRef(new Set<string>())
  useEffect(() => {
    const activeUrls = urls.current
    return () => {
      download.current?.abort()
      activeUrls.forEach(url => URL.revokeObjectURL(url))
      activeUrls.clear()
    }
  }, [])
  useEffect(() => {
    if (!pdf) return
    const controller = new AbortController()
    void fetchFile(id, version, controller.signal).then(result => {
      if (!controller.signal.aborted) setFile(result)
    }).catch(cause => { if (!controller.signal.aborted) setError(cause) })
    return () => controller.abort()
  }, [id, version, pdf, attempt])
  async function save() {
    const controller = new AbortController()
    download.current = controller
    setBusy(true)
    setError(undefined)
    try {
      const result = file ?? await fetchFile(id, version, controller.signal)
      if (controller.signal.aborted) return
      const url = URL.createObjectURL(result.blob)
      const activeUrls = urls.current
      activeUrls.add(url)
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = result.name
      anchor.click()
      setTimeout(() => { if (activeUrls.delete(url)) URL.revokeObjectURL(url) }, 1000)
    } catch (cause) { if (!controller.signal.aborted) setError(cause) }
    finally { if (!controller.signal.aborted) setBusy(false) }
  }
  return <section className="academic-stack">
    <Button disabled={busy} onClick={() => void save()}>{busy ? 'Descargando…' : 'Descargar versión'}</Button>
    {error ? <Failed error={error} transfer retry={() => { setError(undefined); if (pdf) setAttempt(value => value + 1); else void save() }} />
      : pdf ? file ? <Suspense fallback={<Skeleton />}><Pdf blob={file.blob} /></Suspense> : <Skeleton />
        : <Card>Vista previa no disponible para este formato. Puedes descargar el archivo.</Card>}
  </section>
}
export function DocumentDetail({ id, version, select, filters }: { id: string; version?: number; select: (version?: number) => void; filters: Filters }) {
  const detail = useQuery({ ...detailQuery(id), enabled: isUuid(id) })
  const history = useQuery({ ...versionsQuery(id), enabled: !!detail.data })
  if (!isUuid(id)) return <Alert>Documento inexistente.</Alert>
  if (detail.isPending) return <Skeleton />
  if (detail.isError) return <Failed error={detail.error} retry={() => void detail.refetch()} />
  const doc = detail.data
  if (!doc) return <Alert>Documento no disponible. <Link to="/documents" search={filters}>Volver al explorador</Link></Alert>
  const selected = history.data && selectedVersion(history.data, version)
  return <main className="academic-stack">
    <Link to="/documents" search={filters}>Volver al explorador</Link>
    <h1>{doc.title || doc.filename}</h1><p>{doc.school} {doc.document_type_name && <Badge>{doc.document_type_name}</Badge>}</p>
    <div className="document-detail">
      {history.isPending ? <Skeleton /> : history.isError ? <Failed error={history.error} retry={() => void history.refetch()} />
        : selected ? <VersionFile key={selected.version_id} id={id} version={selected} /> : <Alert>Versión no disponible. <Button variant="ghost" onClick={() => select()}>Ir a la versión actual</Button></Alert>}
      <aside className="academic-stack"><Card><h2>Información</h2>
        {doc.description && <p>{doc.description}</p>}
        <p>{doc.subjects.map(subject => subject.name).join(' · ')}</p>
        {selected && <><p>{selected.filename}</p><p>{selected.mime_type} · {size(selected.size_bytes)}</p><p>{date(selected.created_at)}</p></>}
      </Card><Card><h2>Versiones</h2>
        {history.data?.versions.filter(v => v.status === 'available').map(v => <div key={v.version_id} className="version-row">
          <Button variant="ghost" aria-pressed={selected?.version_id === v.version_id} onClick={() => select(v.version_number)}>Ver versión {v.version_number}</Button>
          {v.version_id === history.data?.current_version_id && <Badge>Actual</Badge>}
          <p className="muted">{v.filename} · {date(v.created_at)} · {size(v.size_bytes)}</p>
        </div>)}
      </Card></aside>
    </div>
  </main>
}
