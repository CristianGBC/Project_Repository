import { useEffect, useRef, useState, type FormEvent } from 'react'
import { Link, useBlocker } from '@tanstack/react-router'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { isAxiosError } from 'axios'
import { toast } from 'sonner'
import { api, errorMessage, type User } from '../../api/session'
import { isUuid, schoolsQuery, subjectsQuery, typesQuery } from '../../api/academic'
import { affectedDocuments, ownQuery, publish, uploadForm, uploadInProgress, uploadLabel, type MetadataInput, type OwnDocument } from '../../api/professor'
import { Alert, Badge, Button, Card, Dialog, Input, Skeleton } from '../../components/ui'
import { canUploadToSchool } from '../auth/permissions'

const formatDate = (value: string) => new Date(value).toLocaleString('es-EC')
const pageSize = 12
const ownPath = '/professor/documents/$documentId'
const published = (doc: OwnDocument) => doc.versions.find(version => version.version_id === doc.current_version_id)
function useRefreshDocuments() {
  const client = useQueryClient()
  return () => Promise.all(affectedDocuments.map(queryKey => client.invalidateQueries({ queryKey })))
}
function useUploads(params: Parameters<typeof ownQuery>[0], poll = false) {
  const client = useQueryClient()
  const query = useQuery({
    ...ownQuery(params),
    refetchInterval: result => poll && result.state.status !== 'error' && result.state.data?.documents.some(doc => doc.versions.some(uploadInProgress)) ? 5000 : false,
    refetchIntervalInBackground: false,
  })
  const previous = useRef(new Set<string>())
  useEffect(() => {
    if (!poll || !query.data) return
    const versions = query.data.documents.flatMap(doc => doc.versions)
    if (versions.some(version => version.status === 'available' && previous.current.has(version.version_id))) {
      void client.invalidateQueries({ queryKey: affectedDocuments[1] })
    }
    previous.current = new Set(versions.filter(uploadInProgress).map(version => version.version_id))
  }, [poll, query.data, client])
  return query
}
function QueryError({ error, retry }: { error: unknown; retry: () => void }) {
  return <Alert>{errorMessage(error)} <Button variant="ghost" onClick={retry}>Reintentar</Button></Alert>
}
function UploadHistory({ doc }: { doc: OwnDocument }) {
  if (!doc.versions.length) return <p>Sin versiones registradas.</p>
  return <div>{doc.versions.map(version => <div className="version-row" key={version.version_id}>
    <strong>Versión {version.version_number} · {version.filename}</strong>{' '}
    <Badge>{uploadLabel(version)}</Badge>{version.version_id === doc.current_version_id && <Badge>Actual</Badge>}
    <p className="muted">{formatDate(version.created_at)} · {Math.ceil(version.size_bytes / 1024)} KiB · {version.attempts} intentos de recuperación</p>
    {version.last_attempt_at && <p>Último intento {formatDate(version.last_attempt_at)}</p>}
    {version.next_attempt_at && uploadInProgress(version) && <p>Próximo intento {formatDate(version.next_attempt_at)}</p>}
    {version.requires_intervention && <p>La recuperación terminó sin publicar esta versión.</p>}
    {version.status === 'available' && !doc.deleted_at && <Link to="/documents/$documentId" params={{ documentId: doc.document_id }}
      search={{ version: version.version_number }}>Ver o descargar versión {version.version_number}</Link>}
  </div>)}</div>
}
export function ProfessorList({ mode }: { mode: 'active' | 'deleted' | 'uploads' }) {
  const [school, setSchool] = useState('')
  const [type, setType] = useState('')
  const [q, setQ] = useState('')
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(0)
  const schools = useQuery(schoolsQuery)
  const types = useQuery(typesQuery)
  const subjects = useQuery(subjectsQuery())
  const query = useUploads({ deleted: mode === 'uploads' ? undefined : mode === 'deleted', school_id: school || undefined, document_type_id: type || undefined, q: search || undefined, offset: page * pageSize, limit: pageSize + 1 }, mode === 'uploads')
  const rows = query.data?.documents ?? []
  const heading = { active: 'Mis documentos', deleted: 'Documentos eliminados', uploads: 'Mis subidas' }[mode]
  return <main className="academic-stack"><h1>{heading}</h1>
    {mode === 'active' && <Link className="button primary justify-self-start" to="/professor/documents/new">Subir documento</Link>}
    <Card><form className="academic-filters" onSubmit={event => { event.preventDefault(); setSearch(q.trim()); setPage(0) }}>
      <label>Buscar por título<Input value={q} maxLength={200} onChange={event => setQ(event.target.value)} /></label>
      <label>Escuela<select value={school} onChange={event => { setSchool(event.target.value); setPage(0) }}>
        <option value="">Todas</option>{schools.data?.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
      </select></label>
      <label>Tipo documental<select value={type} onChange={event => { setType(event.target.value); setPage(0) }}>
        <option value="">Todos</option>{types.data?.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
      </select></label><Button className="self-end">Buscar</Button>
    </form></Card>
    {query.isPending ? <Skeleton /> : query.isError ? <QueryError error={query.error} retry={() => void query.refetch()} />
      : !rows.length ? <Card>No hay documentos para esta consulta.</Card> : rows.slice(0, pageSize).map(doc => {
        const current = published(doc)
        return <Card key={doc.document_id}><h2><Link to={ownPath} params={{ documentId: doc.document_id }}>{doc.title}</Link></h2>
          <p>{schools.data?.find(item => item.id === doc.school_id)?.name ?? doc.school_id} · {types.data?.find(item => item.id === doc.document_type_id)?.name ?? 'Sin tipo'}</p>
          <p className="muted">Creado {formatDate(doc.created_at)} · Actualizado {formatDate(doc.updated_at)}</p>
          {!!doc.subject_ids.length && <p>{doc.subject_ids.map(id => subjects.data?.find(item => item.id === id)?.name).filter(Boolean).join(' · ')}</p>}
          {doc.deleted_at && <p>Eliminado {formatDate(doc.deleted_at)}</p>}
          {mode !== 'uploads' && current && <p>{current.filename} · v{current.version_number} · {Math.ceil(current.size_bytes / 1024)} KiB</p>}
          {mode !== 'uploads' && (current ?? doc.versions[0]) && <Badge>{uploadLabel((current ?? doc.versions[0]))}</Badge>}
          {mode === 'uploads' && <UploadHistory doc={doc} />}
        </Card>
      })}
    <nav className="pdf-controls" aria-label="Paginación de documentos propios">
      <Button variant="ghost" disabled={page === 0} onClick={() => setPage(page - 1)}>Anterior</Button>
      <span>Página {page + 1}</span>
      <Button variant="ghost" disabled={rows.length <= pageSize} onClick={() => setPage(page + 1)}>Siguiente</Button>
    </nav>
  </main>
}

function Lifecycle({ doc }: { doc: OwnDocument }) {
  const refresh = useRefreshDocuments()
  const [busy, setBusy] = useState(false)
  const [open, setOpen] = useState(false)
  const [error, setError] = useState('')
  async function apply() {
    setBusy(true)
    setError('')
    try {
      const path = `/documents/${doc.document_id}`
      if (doc.deleted_at) await api.post(path + '/restore')
      else await api.delete(path)
      await refresh()
      toast.success(doc.deleted_at ? 'Documento restaurado.' : 'Documento eliminado del portal.')
      setOpen(false)
    } catch (cause) { setError(errorMessage(cause)) }
    finally { setBusy(false) }
  }
  const label = doc.deleted_at ? 'Restaurar documento' : 'Eliminar documento'
  return <Dialog.Root open={open} onOpenChange={value => { if (!busy) setOpen(value) }}>
    <Dialog.Trigger asChild><Button variant="ghost">{label}</Button></Dialog.Trigger>
    <Dialog.Portal><Dialog.Overlay className="overlay" /><Dialog.Content className="professor-dialog card">
      <Dialog.Title>{label}</Dialog.Title>
      <Dialog.Description>{doc.deleted_at ? 'La publicación volverá al portal si tiene una versión disponible.' : 'El documento dejará de estar visible. Sus archivos e historial se conservan.'}</Dialog.Description>
      {error && <Alert>{error}</Alert>}
      <div className="pdf-controls"><Dialog.Close asChild><Button variant="ghost" disabled={busy}>Cancelar</Button></Dialog.Close>
        <Button disabled={busy} onClick={() => void apply()}>{busy ? 'Guardando…' : 'Confirmar'}</Button></div>
    </Dialog.Content></Dialog.Portal>
  </Dialog.Root>
}
export function ProfessorDocument({ id, user, mode }: { id: string; user: User; mode: 'view' | 'edit' | 'version' }) {
  const query = useQuery({ ...ownQuery({ document_id: id, limit: 1 }), enabled: isUuid(id) })
  const schools = useQuery(schoolsQuery)
  if (!isUuid(id)) return <Alert>Documento inexistente.</Alert>
  if (query.isPending) return <Skeleton />
  if (query.isError) return <QueryError error={query.error} retry={() => void query.refetch()} />
  const doc = query.data.documents[0]
  if (!doc) return <Alert>Documento propio no encontrado.</Alert>
  const school = schools.data?.find(item => item.id === doc.school_id)?.name ?? doc.school_id
  const mayWrite = canUploadToSchool(user, doc.school_id)
  const current = published(doc)
  if (mode !== 'view') {
    if (!mayWrite || doc.deleted_at || (mode === 'edit' && !current)) return <Alert>Esta acción no está disponible para el documento.</Alert>
    return <ProfessorForm key={id + mode} user={user} doc={doc} mode={mode} />
  }
  return <main className="academic-stack">
    {doc.deleted_at ? <Link to="/professor/documents/deleted">Volver a eliminados</Link> : <Link to="/professor/documents">Volver a mis documentos</Link>}
    <h1>{doc.title}</h1><p>{school}</p>
    {doc.description && <p>{doc.description}</p>}
    <Card><div className="pdf-controls">
      {!doc.deleted_at && current && <Link className="button ghost" to="/documents/$documentId" params={{ documentId: id }} search={{ version: current.version_number }}>Ver publicación actual</Link>}
      {mayWrite && !doc.deleted_at && current && <Link className="button ghost" to="/professor/documents/$documentId/edit" params={{ documentId: id }}>Editar metadatos</Link>}
      {mayWrite && !doc.deleted_at && <Link className="button ghost" to="/professor/documents/$documentId/new-version" params={{ documentId: id }}>Nueva versión</Link>}
      {mayWrite && <Lifecycle doc={doc} />}
    </div></Card>
    <Card><h2>Historial de versiones</h2><UploadHistory doc={doc} /></Card>
  </main>
}

export function ProfessorForm({ user, doc, mode = 'new' }: { user: User; doc?: OwnDocument; mode?: 'new' | 'edit' | 'version' }) {
  const [school, setSchool] = useState(doc?.school_id ?? '')
  const [title, setTitle] = useState(doc?.title ?? '')
  const [description, setDescription] = useState(doc?.description ?? '')
  const [type, setType] = useState(doc?.document_type_id ?? '')
  const [subjects, setSubjects] = useState(doc?.subject_ids ?? [])
  const [file, setFile] = useState<File>()
  const [busy, setBusy] = useState(false)
  const [progress, setProgress] = useState<number | null>(null)
  const [message, setMessage] = useState('')
  const [result, setResult] = useState<{ id: string; published: boolean }>()
  const [uncertain, setUncertain] = useState(false)
  const input = useRef<HTMLInputElement>(null)
  const request = useRef<AbortController>(null)
  const refresh = useRefreshDocuments()
  const schools = useQuery(schoolsQuery)
  const types = useQuery({ ...typesQuery, enabled: mode !== 'version' })
  const compatible = useQuery({ ...subjectsQuery(school), enabled: !!school && mode !== 'version' })
  const limit = useQuery({ ...ownQuery({ limit: 1 }), enabled: mode !== 'edit' })
  const choices = schools.data?.filter(item => item.is_active && canUploadToSchool(user, item.id)) ?? []
  const activeSubjects = compatible.data?.filter(item => item.is_active) ?? []
  const typeInfo = types.data?.find(item => item.id === type)
  const max = limit.data?.maxBytes
  const current = doc && published(doc)
  const dirty = !result && !uncertain && (!!file || title !== (doc?.title ?? '') ||
    description !== (doc?.description ?? '') || type !== (doc?.document_type_id ?? '') ||
    school !== (doc?.school_id ?? '') || subjects.join() !== (doc?.subject_ids ?? []).join())
  useBlocker({
    shouldBlockFn: ({ next }) => dirty && next.pathname !== '/login' &&
      !window.confirm('Hay cambios sin guardar. ¿Quieres salir del formulario?'),
    enableBeforeUnload: dirty,
  })
  useEffect(() => () => {
    const active = request.current
    request.current = null
    active?.abort()
  }, [])
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (request.current || uncertain || result) return
    setMessage('')
    if (!school || !canUploadToSchool(user, school) || (mode === 'new' && !choices.some(item => item.id === school))) {
      setMessage('Selecciona una escuela en la que tengas una membresía activa de profesor.')
      return
    }
    if (mode !== 'version' && (!title.trim() || title.trim().length > 255 ||
      (type && !typeInfo?.is_active) || (typeInfo?.requires_subject && subjects.length === 0) ||
      subjects.some(id => !activeSubjects.some(item => item.id === id)))) {
      setMessage('Revisa el título, el tipo y las materias activas de la escuela.')
      return
    }
    if (mode !== 'edit' && (!file?.size || (max && file.size > max))) {
      setMessage(file?.size ? 'El archivo supera el límite configurado.' : 'Selecciona un archivo con contenido.')
      return
    }
    const controller = new AbortController()
    request.current = controller
    setBusy(true)
    setProgress(null)
    const metadata: MetadataInput = { title: title.trim(), description: description.trim(), document_type_id: type || null, subject_ids: subjects }
    try {
      if (mode === 'edit') {
        await api.patch(`/documents/${doc!.document_id}`, metadata, { signal: controller.signal })
        if (request.current !== controller) return
        setResult({ id: doc!.document_id, published: true })
        toast.success('Metadatos actualizados.')
      } else {
        const response = await publish(doc ? `/documents/${doc.document_id}/versions` : '/documents',
          uploadForm(file!, doc ? undefined : school, doc ? undefined : metadata), controller.signal, setProgress)
        if (request.current !== controller) return
        setResult({ id: response.document_id, published: response.version_status === 'available' })
      }
      await refresh()
    } catch (cause) {
      if (request.current !== controller) return
      const status = isAxiosError(cause) ? cause.response?.status : undefined
      if (mode !== 'edit' && (!status || status >= 500)) {
        const detail: unknown = isAxiosError(cause) ? cause.response?.data?.detail : undefined
        if (detail && typeof detail === 'object' && 'document_id' in detail && isUuid(detail.document_id)) {
          setResult({ id: detail.document_id, published: false })
        }
        setUncertain(true)
        setMessage('El servidor puede haber registrado la operación. Consulta Mis subidas antes de repetirla.')
        await refresh()
      } else if (status === 413) setMessage('El archivo supera el límite del coordinador.')
      else if (status === 422) setMessage('Revisa los campos obligatorios, las materias y el archivo.')
      else if (status === 400) setMessage('El coordinador rechazó los datos. Revisa la escuela, el tipo, las materias y el archivo.')
      else setMessage(errorMessage(cause))
    } finally {
      if (request.current === controller) {
        request.current = null
        setBusy(false)
      }
    }
  }
  const heading = { new: 'Subir documento', edit: 'Editar metadatos', version: 'Nueva versión' }[mode]
  return <main className="academic-stack"><Link to="/professor/documents">Volver a mis documentos</Link>
    <h1>{heading}</h1>{doc && <p>{doc.title} · {schools.data?.find(item => item.id === school)?.name ?? school}</p>}
    {current && mode === 'version' && <p>Versión actual {current.version_number} · {current.filename} · {formatDate(current.created_at)}</p>}
    <Card>{mode !== 'edit' && <p className="muted">La publicación se confirma después de verificar las copias requeridas.</p>}
      {message && <Alert>{message}</Alert>}
      {result && <div role="status">{result.published ? mode === 'edit' ? 'Metadatos guardados.' : 'Publicación confirmada.' : 'Publicación sin confirmar.'}{' '}
        <Link to={ownPath} params={{ documentId: result.id }}>Ver documento</Link></div>}
      {(uncertain || result) ? <Link to="/professor/uploads">Consultar Mis subidas</Link> : <form onSubmit={submit}>
        <fieldset className="academic-stack" disabled={busy}>
          {mode === 'new' && <label>Escuela<select className="input" required value={school} onChange={event => { setSchool(event.target.value); setSubjects([]) }}>
            <option value="">Selecciona una escuela</option>{choices.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
          </select></label>}
          {mode !== 'version' && <>
            <label>Título<Input required maxLength={255} value={title} onChange={event => setTitle(event.target.value)} /></label>
            <label>Descripción<textarea className="input" value={description} onChange={event => setDescription(event.target.value)} /></label>
            <label>Tipo documental<select className="input" value={type} onChange={event => setType(event.target.value)}>
              <option value="">Sin tipo</option>{types.data?.filter(item => item.is_active).map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
            </select></label>
            <fieldset><legend>Materias {typeInfo?.requires_subject ? '(al menos una)' : '(opcional)'}</legend>
              {!school ? <p>Selecciona una escuela.</p> : compatible.isPending ? <Skeleton /> : !activeSubjects.length ? <p>Sin materias activas.</p>
                : activeSubjects.map(item => <label className="block" key={item.id}><input type="checkbox" checked={subjects.includes(item.id)} onChange={event => {
                  setSubjects(event.target.checked ? [...subjects, item.id] : subjects.filter(id => id !== item.id))
                }} /> {item.name}</label>)}
            </fieldset>
          </>}
          {mode !== 'edit' && <div className="file-drop" onDragOver={event => event.preventDefault()} onDrop={event => {
            event.preventDefault()
            if (!busy) setFile(event.dataTransfer.files[0])
          }}>
            <label>Archivo<Input ref={input} type="file" onChange={event => setFile(event.target.files?.[0])} /></label>
            <p>Arrastra un archivo o selecciónalo. {max ? `Máximo ${(max / 1048576).toFixed(1)} MiB.` : 'El coordinador valida el tamaño.'}</p>
            {file && <p>{file.name} · {Math.ceil(file.size / 1024)} KiB · {file.type || 'Formato sin especificar'}{' '}
              <Button type="button" variant="ghost" onClick={() => { setFile(undefined); if (input.current) input.current.value = '' }}>Quitar archivo</Button></p>}
          </div>}
          {schools.isError && <QueryError error={schools.error} retry={() => void schools.refetch()} />}
          {mode !== 'version' && types.isError && <QueryError error={types.error} retry={() => void types.refetch()} />}
          {mode !== 'version' && compatible.isError && <QueryError error={compatible.error} retry={() => void compatible.refetch()} />}
          <Button>{mode === 'edit' ? 'Guardar cambios' : 'Enviar archivo'}</Button>
        </fieldset>
      </form>}
      {busy && <div role="status">{mode === 'edit' ? 'Guardando…' : progress === 100 ? 'Procesando publicación…' : progress === null ? 'Transfiriendo archivo…' : `Transfiriendo archivo · ${progress}%`}
        {mode !== 'edit' && <><progress aria-label="Transferencia" max={100} value={progress ?? undefined} />
          <Button variant="ghost" onClick={() => request.current?.abort()}>Cancelar solicitud</Button>
          <p>Cancelar la solicitud puede dejar la publicación en curso en el servidor.</p></>}
      </div>}
    </Card>
  </main>
}
