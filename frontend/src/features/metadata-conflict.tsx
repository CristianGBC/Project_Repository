import { useState } from 'react'
import { api } from '../api/session'
import { Alert, Button } from '../components/ui'

type Snapshot = {
  metadata_revision: number
  title: string
  description: string | null
  document_type_id: string | null
  subjects: { name: string }[]
}

export function useMetadataConflict(document?: { document_id: string; school_id: string; metadata_revision: number }) {
  const [revision, setRevision] = useState(document?.metadata_revision)
  const [conflict, setConflict] = useState(false)
  const [snapshot, setSnapshot] = useState<Snapshot>()
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  async function load() {
    if (!document) return
    setLoading(true)
    setError('')
    try {
      const { data } = await api.get<Snapshot>(`/documents/${document.school_id}/${document.document_id}`)
      setSnapshot(data)
    } catch {
      setError('No se pudieron consultar los datos actuales.')
    } finally {
      setLoading(false)
    }
  }
  function flag() {
    setSnapshot(undefined)
    setConflict(true)
  }
  return { revision, setRevision, conflict, flag, notice: conflict && <Alert>
    <p>El documento cambió desde que abriste el formulario. Tus cambios siguen en el formulario.</p>
    <Button type="button" disabled={loading} onClick={() => void load()}>Consultar datos actualizados</Button>
    {error && <p>{error}</p>}
    {snapshot && <><p>Título actual {snapshot.title}</p><p>Descripción actual {snapshot.description ?? 'Sin descripción'}</p>
      <p>Tipo actual {snapshot.document_type_id ?? 'Sin tipo'}</p><p>Materias actuales {snapshot.subjects.map(subject => subject.name).join(', ') || 'Sin materias'}</p>
      <Button type="button" onClick={() => { setRevision(snapshot.metadata_revision); setConflict(false) }}>Usar esta revisión para volver a guardar</Button>
    </>}
  </Alert> }
}
