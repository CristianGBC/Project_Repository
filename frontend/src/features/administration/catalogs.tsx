import { useState, type FormEvent } from 'react'
import { useQuery } from '@tanstack/react-query'
import { manageableSchools, type ManagedDocument } from '../../api/administration'
import { schoolsQuery, subjectsQuery, typesQuery, type DocumentType, type Subject } from '../../api/academic'
import { api, type User } from '../../api/session'
import { Alert, Badge, Button, Card, Input } from '../../components/ui'
import { canManageSchool } from '../auth/permissions'
import { isAxiosError } from 'axios'
import { useMetadataConflict } from '../metadata-conflict'
import { Confirm, Results, useAdminAction } from './ui'

export function AdminSubjects({ actor }: { actor: User }) {
  const [school, setSchool] = useState('')
  const [search, setSearch] = useState('')
  const [edit, setEdit] = useState<Subject | null | undefined>()
  const schools = useQuery(schoolsQuery)
  const query = useQuery(subjectsQuery(school || undefined))
  const options = manageableSchools(actor, schools.data ?? [])
  const subjects = query.data?.filter(item => canManageSchool(actor, item.school_id) &&
    (item.name + ' ' + item.code).toLocaleLowerCase('es').includes(search.toLocaleLowerCase('es'))) ?? []
  return <main className="academic-stack"><h1>Materias</h1>
    <Button className="justify-self-start" onClick={() => setEdit(null)}>Crear materia</Button>
    {edit !== undefined && <SubjectForm key={edit?.id ?? 'new'} subject={edit} schools={options} close={() => setEdit(undefined)} />}
    <Card><div className="academic-filters"><label>Buscar materia<Input value={search} onChange={event => setSearch(event.target.value)} /></label>
      <label>Escuela<select value={school} onChange={event => setSchool(event.target.value)}>
        <option value="">Todas</option>{options.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
    </div></Card>
    <Results pending={query.isPending || schools.isPending} error={query.error || schools.error} retry={() => { void query.refetch(); void schools.refetch() }} empty={!subjects.length}>
      <div className="academic-grid">{subjects.map(item => <Card key={item.id}><h2>{item.name}</h2>
        <p>{item.code} · {options.find(s => s.id === item.school_id)?.name ?? item.school_id}</p>
        {item.description && <p>{item.description}</p>}<Badge>{item.is_active ? 'Activa' : 'Inactiva'}</Badge>
        <Button variant="ghost" onClick={() => setEdit(item)}>Editar materia</Button>
        <Confirm label={item.is_active ? 'Desactivar materia' : 'Activar materia'} description="Se actualizará su disponibilidad en los catálogos."
          action={() => api.patch(`/subjects/${item.id}`, { is_active: !item.is_active })} />
      </Card>)}</div>
    </Results>
  </main>
}
function SubjectForm({ subject, schools, close }: { subject: Subject | null; schools: { id: string; name: string; is_active: boolean }[]; close: () => void }) {
  const state = useAdminAction()
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const data = new FormData(event.currentTarget)
    const values = { name: String(data.get('name')).trim(), code: String(data.get('code')).trim(), description: String(data.get('description')).trim() || null }
    void state.run(() => subject ? api.patch(`/subjects/${subject.id}`, values) : api.post('/subjects', { ...values, school_id: data.get('school_id') }), close)
  }
  return <Card><h2>{subject ? 'Editar materia' : 'Crear materia'}</h2><form onSubmit={submit}>
    {subject ? <p>{schools.find(item => item.id === subject.school_id)?.name ?? subject.school_id}</p> :
      <label>Escuela de la materia<select name="school_id" className="input" required><option value="">Selecciona una escuela</option>
        {schools.filter(item => item.is_active).map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>}
    <label>Código<Input name="code" required maxLength={30} defaultValue={subject?.code} /></label>
    <label>Nombre<Input name="name" required maxLength={150} defaultValue={subject?.name} /></label>
    <label>Descripción<textarea className="input" name="description" defaultValue={subject?.description ?? ''} /></label>
    {state.error && <Alert>{state.error}</Alert>}<div className="pdf-controls">
      <Button disabled={state.busy}>Guardar materia</Button><Button disabled={state.busy} type="button" variant="ghost" onClick={close}>Cancelar</Button>
    </div>
  </form></Card>
}
export function AdminTypes() {
  const query = useQuery(typesQuery)
  const [edit, setEdit] = useState<(DocumentType & { code?: string }) | null | undefined>()
  const state = useAdminAction()
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const data = new FormData(event.currentTarget)
    const values = { name: String(data.get('name')).trim(), requires_subject: data.has('requires_subject') }
    void state.run(() => edit ? api.patch(`/catalogs/document-types/${edit.id}`, values) :
      api.post('/catalogs/document-types', { ...values, id: data.get('id'), code: data.get('code') }), () => setEdit(undefined))
  }
  return <main className="academic-stack"><h1>Tipos documentales</h1><Button className="justify-self-start" onClick={() => setEdit(null)}>Crear tipo documental</Button>
    {edit !== undefined && <Card><h2>{edit ? 'Editar tipo' : 'Crear tipo'}</h2><form onSubmit={submit} key={edit?.id ?? 'new'}>
      {!edit && <><label>Identificador<Input name="id" required maxLength={30} pattern="[A-Za-z0-9_-]+" /></label>
        <label>Código<Input name="code" required maxLength={30} /></label></>}
      <label>Nombre<Input name="name" required maxLength={100} defaultValue={edit?.name} /></label>
      <label><input type="checkbox" name="requires_subject" defaultChecked={edit?.requires_subject} /> Requiere materia</label>
      {state.error && <Alert>{state.error}</Alert>}<div className="pdf-controls"><Button disabled={state.busy}>Guardar tipo</Button>
        <Button type="button" variant="ghost" disabled={state.busy} onClick={() => setEdit(undefined)}>Cancelar</Button></div>
    </form></Card>}
    <Results pending={query.isPending} error={query.error} retry={() => void query.refetch()} empty={!query.data?.length}>
      <div className="academic-grid">{query.data?.map(item => <Card key={item.id}><h2>{item.name}</h2>
        <p>{item.id} · {item.requires_subject ? 'Requiere materia' : 'Materia opcional'}</p><Badge>{item.is_active ? 'Activo' : 'Inactivo'}</Badge>
        <Button variant="ghost" onClick={() => setEdit(item)}>Editar tipo</Button>
        <Confirm label={item.is_active ? 'Desactivar tipo' : 'Activar tipo'} description="Se actualizará la disponibilidad del tipo documental."
          action={() => api.patch(`/catalogs/document-types/${item.id}`, { is_active: !item.is_active })} />
      </Card>)}</div>
    </Results>
  </main>
}
export function AdminDocumentForm({ doc }: { doc: ManagedDocument }) {
  const concurrency = useMetadataConflict(doc)
  const [type, setType] = useState(doc.document_type_id ?? '')
  const [selected, setSelected] = useState(doc.subject_ids)
  const [validation, setValidation] = useState('')
  const types = useQuery(typesQuery)
  const subjects = useQuery(subjectsQuery(doc.school_id))
  const state = useAdminAction()
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const data = new FormData(event.currentTarget)
    const title = String(data.get('title')).trim()
    if (!title || (types.data?.find(item => item.id === type)?.requires_subject && !selected.length)) {
      setValidation('Ingresa un título y selecciona materias cuando el tipo lo requiera.')
      return
    }
    setValidation('')
    const values = {
      title, description: String(data.get('description')).trim() || null,
      ...(type !== (doc.document_type_id ?? '') ? { document_type_id: type || null } : {}),
      ...(selected.join() !== doc.subject_ids.join() ? { subject_ids: selected } : {}),
    }
    if (concurrency.conflict) return
    void state.run(async () => {
      try {
        const { data } = await api.patch<{ metadata_revision: number }>(`/documents/${doc.document_id}`, { ...values, metadata_revision: concurrency.revision })
        concurrency.setRevision(data.metadata_revision)
      } catch (cause) {
        if (isAxiosError(cause) && cause.response?.status === 409) concurrency.flag()
        throw cause
      }
    })
  }
  return <Card><form onSubmit={submit}>
    <label>Título<Input name="title" required maxLength={255} defaultValue={doc.title} /></label>
    <label>Descripción<textarea className="input" name="description" defaultValue={doc.description ?? ''} /></label>
    <label>Tipo documental<select className="input" value={type} onChange={event => setType(event.target.value)}>
      <option value="">Sin tipo</option>{types.data?.filter(item => item.is_active || item.id === type).map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
    </select></label>
    <fieldset><legend>Materias</legend>{subjects.data?.filter(item => item.is_active || selected.includes(item.id)).map(item =>
      <label className="block" key={item.id}><input type="checkbox" checked={selected.includes(item.id)} onChange={event =>
        setSelected(event.target.checked ? [...selected, item.id] : selected.filter(id => id !== item.id))
      } /> {item.name}{!item.is_active && ' · Inactiva'}</label>)}</fieldset>
    {concurrency.notice}
    {(validation || (!concurrency.conflict && state.error)) && <Alert>{validation || state.error}</Alert>}<Button disabled={state.busy || concurrency.conflict}>Guardar metadatos</Button>
  </form></Card>
}
