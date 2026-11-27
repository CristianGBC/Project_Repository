import { queryOptions } from '@tanstack/react-query'
import { api, keys } from './session'

export interface UploadVersion {
  version_id: string
  version_number: number
  status: 'pending' | 'available' | 'failed'
  reconciliation_status: 'pending' | 'in_progress' | 'retry_wait' | 'completed' | 'failed' | 'cancelled' | null
  filename: string
  size_bytes: number
  created_at: string
  last_attempt_at: string | null
  next_attempt_at: string | null
  attempts: number
  requires_intervention: boolean
  awaiting_reconciliation?: boolean
  publication_cancelled?: boolean
}
export interface OwnDocument {
  metadata_revision: number
  document_id: string
  school_id: string
  title: string
  description: string | null
  document_type_id: string | null
  subject_ids: string[]
  current_version_id: string | null
  created_at: string
  updated_at: string
  deleted_at: string | null
  versions: UploadVersion[]
}
export const ownKey = ['professor', 'uploads'] as const
export const ownQuery = (params: Record<string, string | number | boolean | undefined>) => queryOptions({
  queryKey: [...ownKey, params],
  queryFn: async ({ signal }) => {
    const response = await api.get<OwnDocument[]>('/documents/my/uploads', { params, signal })
    const limit = Number(response.headers['x-max-upload-bytes'])
    return { documents: response.data, maxBytes: Number.isSafeInteger(limit) && limit > 0 ? limit : null }
  },
})
export const uploadInProgress = (version: UploadVersion) =>
  !version.publication_cancelled && (version.reconciliation_status === 'pending' || version.reconciliation_status === 'retry_wait' ||
  version.reconciliation_status === 'in_progress' ||
  (version.reconciliation_status === null && (version.status === 'pending' || version.awaiting_reconciliation === true)))

export function uploadLabel(version: UploadVersion) {
  if (version.publication_cancelled) return 'Publicación cancelada por eliminación'
  if (version.status === 'available') return 'Publicado'
  if (version.reconciliation_status === 'in_progress') return 'En recuperación'
  if (version.reconciliation_status === 'retry_wait' || version.awaiting_reconciliation) return 'Esperando recuperación'
  if (uploadInProgress(version)) return 'Pendiente'
  if (version.reconciliation_status === 'cancelled') return 'Recuperación cancelada'
  return 'Fallido'
}
export interface MetadataInput {
  title: string; description: string; document_type_id: string | null; subject_ids: string[]
}
export function uploadForm(file: File, school?: string, metadata?: MetadataInput) {
  const form = new FormData()
  form.append('file', file)
  if (school) form.append('school_id', school)
  if (metadata) {
    form.append('title', metadata.title)
    if (metadata.description) form.append('description', metadata.description)
    if (metadata.document_type_id) form.append('document_type_id', metadata.document_type_id)
    metadata.subject_ids.forEach(id => form.append('subject_ids', id))
  }
  return form
}
export async function publish(path: string, form: FormData, signal: AbortSignal, progress: (value: number | null) => void) {
  return (await api.post<{ document_id: string; version_status: string }>(path, form, {
    signal, timeout: 180000,
    onUploadProgress: event => progress(event.total ? Math.min(100, Math.floor(100 * event.loaded / event.total)) : null),
  })).data
}
export const affectedDocuments = [ownKey, keys.documents] as const
