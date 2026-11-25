import { useState } from 'react'
import { Link, useNavigate } from '@tanstack/react-router'
import { useQuery } from '@tanstack/react-query'
import { adminQuery, manageableSchools, pageSize, type AuditEvent, type ManagedDocument } from '../../api/administration'
import { isUuid, schoolsQuery, subjectsQuery, typesQuery, versionsQuery } from '../../api/academic'
import { api, type User } from '../../api/session'
import { Alert, Badge, Button, Card, Input } from '../../components/ui'
import { Confirm, formatted, Pages, Results } from './ui'
import { AdminDocumentForm } from './catalogs'

export function AdminDocuments({ actor }: { actor: User }) {
  const [page, setPage] = useState(0)
  const [filters, setFilters] = useState({ q: '', school_id: '', document_type_id: '', deleted: false })
  const schools = useQuery(schoolsQuery)
  const types = useQuery(typesQuery)
  const subjects = useQuery(subjectsQuery())
  const query = useQuery(adminQuery<ManagedDocument[]>('/documents/admin/list', {
    ...filters, school_id: filters.school_id || undefined, document_type_id: filters.document_type_id || undefined, offset: page * pageSize, limit: pageSize + 1,
  }))
  const rows = query.data ?? []
  return <main className="academic-stack"><h1>Documentos administrativos</h1>
    <Card><form className="academic-filters" onSubmit={event => {
      event.preventDefault()
      const data = new FormData(event.currentTarget)
      setFilters({ q: String(data.get('q')).trim(), school_id: String(data.get('school_id')), document_type_id: String(data.get('document_type_id')), deleted: data.get('deleted') === 'true' })
      setPage(0)
    }}>
      <label>Buscar por título<Input name="q" maxLength={200} /></label>
      <label>Escuela<select name="school_id"><option value="">Todas</option>{manageableSchools(actor, schools.data ?? []).map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
      <label>Tipo<select name="document_type_id"><option value="">Todos</option>{types.data?.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
      <label>Estado<select name="deleted"><option value="false">Vigentes</option><option value="true">Eliminados</option></select></label>
      <Button className="self-end">Buscar documentos</Button>
    </form></Card>
    <Results pending={query.isPending} retry={() => void query.refetch()} error={query.error} empty={!rows.length}>
      <div className="academic-grid">{rows.slice(0, pageSize).map(doc => <Card key={doc.document_id}>
        <h2><Link to="/admin/documents/$documentId" params={{ documentId: doc.document_id }}>{doc.title}</Link></h2>
        <p>{schools.data?.find(item => item.id === doc.school_id)?.name ?? doc.school_id}</p>
        <p>{doc.owner_name || 'Sin propietario registrado'}</p>
        <p>{types.data?.find(item => item.id === doc.document_type_id)?.name ?? 'Sin tipo'}</p>
        <p>{doc.subject_ids.map(id => subjects.data?.find(item => item.id === id)?.name).filter(Boolean).join(' · ')}</p>
        <Badge>{doc.deleted_at ? 'Eliminado' : doc.current_version ? `Publicado · v${doc.current_version}` : 'Sin versión publicada'}</Badge>
        <p className="muted">Creado {formatted(doc.created_at)} · Actualizado {formatted(doc.updated_at)}</p>
      </Card>)}</div>
    </Results><Pages page={page} count={rows.length} change={setPage} />
  </main>
}
export function AdminDocument({ id, edit = false, deleted = false }: { id: string; edit?: boolean; deleted?: boolean }) {
  const query = useQuery({ ...adminQuery<ManagedDocument[]>('/documents/admin/list', { document_id: id, deleted, limit: 1 }), enabled: isUuid(id) })
  const doc = query.data?.[0]
  const alternative = useQuery({ ...adminQuery<ManagedDocument[]>('/documents/admin/list', { document_id: id, deleted: !deleted, limit: 1 }), enabled: !!query.data && !doc })
  const row = doc ?? alternative.data?.[0]
  const navigate = useNavigate()
  const schools = useQuery(schoolsQuery)
  const types = useQuery(typesQuery)
  const subjects = useQuery({ ...subjectsQuery(row?.school_id), enabled: !!row })
  const history = useQuery({ ...versionsQuery(id), enabled: !!row && !row.deleted_at && !edit })
  if (!isUuid(id)) return <Alert>Documento no encontrado.</Alert>
  return <main className="academic-stack"><Link to="/admin/documents">Volver a documentos administrativos</Link>
    <Results pending={query.isPending || (!!query.data && !doc && alternative.isPending)} error={query.error || alternative.error}
      retry={() => { void query.refetch(); if (!doc) void alternative.refetch() }} empty={!row}>
      {row && <><h1>{edit ? `Editar ${row.title}` : row.title}</h1>
        <p>{schools.data?.find(item => item.id === row.school_id)?.name ?? row.school_id} · {row.owner_name || 'Sin propietario registrado'}</p>
        {edit ? row.deleted_at || !row.current_version ? <Alert>La edición requiere una publicación vigente.</Alert> : <AdminDocumentForm key={row.document_id} doc={row} /> :
          <><Card>{row.description && <p>{row.description}</p>}
            <p>{types.data?.find(item => item.id === row.document_type_id)?.name ?? 'Sin tipo'}</p>
            <p>{row.subject_ids.map(id => subjects.data?.find(item => item.id === id)?.name).filter(Boolean).join(' · ')}</p>
            <p>Actualizado {formatted(row.updated_at)}</p>
            {row.deleted_at && <Badge>Eliminado {formatted(row.deleted_at)}</Badge>}
            <div className="pdf-controls">
              {!row.deleted_at && row.current_version && <><Link className="button ghost" to="/admin/documents/$documentId/edit" params={{ documentId: id }}>Editar metadatos</Link>
                <Link className="button ghost" to="/documents/$documentId" params={{ documentId: id }} search={{ version: row.current_version }}>Abrir visor académico</Link></>}
              <Confirm label={row.deleted_at ? 'Restaurar documento' : 'Eliminar documento'} description={row.deleted_at ? 'El documento volverá a estar disponible si tiene una versión publicada.' : 'Se ocultará la publicación y se conservarán archivos e historial.'}
                action={() => row.deleted_at ? api.post(`/documents/${id}/restore`) : api.delete(`/documents/${id}`)}
                onSuccess={() => void navigate({ to: '/admin/documents' })} />
            </div>
          </Card>{!row.deleted_at && <Card><h2>Versiones disponibles</h2>
            <Results pending={history.isPending} error={history.error} retry={() => void history.refetch()} empty={!history.data?.versions.some(item => item.status === 'available')}>
              {history.data?.versions.filter(item => item.status === 'available').map(item => <div key={item.version_id} className="version-row">
                <Link to="/documents/$documentId" params={{ documentId: id }} search={{ version: item.version_number }}>Ver o descargar v{item.version_number} · {item.filename}</Link>
                {item.version_id === history.data?.current_version_id && <Badge>Actual</Badge>}
              </div>)}
            </Results></Card>}</>}
      </>}
    </Results>
  </main>
}

export function SecurityAudit() {
  const [page, setPage] = useState(0)
  const [filters, setFilters] = useState<Record<string, string | undefined>>({})
  const [validation, setValidation] = useState('')
  const query = useQuery(adminQuery<AuditEvent[]>('/operations/security-audit', { ...filters, offset: page * pageSize, limit: pageSize + 1 }))
  const rows = query.data ?? []
  return <main className="academic-stack"><h1>Auditoría de seguridad</h1><p className="muted">Acciones administrativas registradas por el coordinador.</p>
    <Card><form className="academic-filters" onSubmit={event => {
      event.preventDefault()
      const fields = new FormData(event.currentTarget)
      const actor = String(fields.get('actor_user_id')).trim()
      if (actor && !isUuid(actor)) { setValidation('Ingresa un UUID de actor válido.'); return }
      setValidation('')
      setFilters(Object.fromEntries([...fields].map(([key, value]) => [key, String(value).trim() || undefined])))
      setPage(0)
    }}>
      <label>Acción exacta<Input name="action" placeholder="create_user" maxLength={100} /></label>
      <label>UUID del actor<Input name="actor_user_id" maxLength={36} /></label>
      <label>Identificador del recurso<Input name="resource_id" maxLength={100} /></label>
      <label>Fecha (UTC)<Input name="day" type="date" /></label><Button className="self-end">Buscar eventos</Button>
    </form>{validation && <Alert>{validation}</Alert>}</Card>
    <Results pending={query.isPending} error={query.error} retry={() => void query.refetch()} empty={!rows.length}>
      <div className="academic-grid">{rows.slice(0, pageSize).map(event => <Card key={event.id}>
        <h2>{event.action}</h2><p>{formatted(event.created_at)} · {event.outcome === 'success' ? 'Correcto' : event.outcome}</p>
        <p>Actor {event.actor_user_id ?? 'Sistema'}</p><p>{event.resource_type} · {event.resource_id}</p>
        {event.detail && <p>{event.detail}</p>}
      </Card>)}</div>
    </Results><Pages page={page} count={rows.length} change={setPage} />
  </main>
}
