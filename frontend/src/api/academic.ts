import { queryOptions } from '@tanstack/react-query'
import { api, keys, type School } from './session'

export interface Subject { id: string; school_id: string; code: string; name: string; is_active: boolean; description?: string | null }
export interface DocumentType { id: string; name: string; is_active: boolean; requires_subject: boolean }
export interface AcademicDocument {
  document_id: string; title: string | null; filename: string; description: string | null
  school_id: string; school: string; subjects: Subject[]; document_type_name: string | null
  created_at: string; size_bytes: number; version_number: number; version_status: string
}
export interface Version {
  version_id: string; version_number: number; filename: string; mime_type: string
  size_bytes: number; created_at: string; status: string
}
interface History { current_version_id: string | null; versions: Version[] }
export type Filters = { school?: string; subject?: string; type?: string; q?: string; page?: number }
export const pageSize = 12
export const clampZoom = (value: number) => Math.min(2.5, Math.max(.5, value))
export const isUuid = (value: unknown): value is string => typeof value === 'string' && /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(value)
export function parseFilters(raw: Record<string, unknown>): Filters {
  const key = (value: unknown) => typeof value === 'string' && /^[\w-]{1,30}$/.test(value) ? value : undefined
  const page = Number(raw.page)
  return {
    school: key(raw.school), subject: isUuid(raw.subject) ? raw.subject : undefined,
    type: key(raw.type), q: typeof raw.q === 'string' ? raw.q.trim().slice(0, 200) || undefined : undefined,
    page: Number.isSafeInteger(page) && page > 1 && page <= 100000 ? page : undefined,
  }
}
export const schoolsQuery = queryOptions({
  queryKey: keys.schools, queryFn: async ({ signal }) => (await api.get<School[]>('/catalogs/schools', { signal })).data,
})
export const subjectsQuery = (school?: string) => queryOptions({
  queryKey: [...keys.subjects, school],
  queryFn: async ({ signal }) => (await api.get<Subject[]>('/subjects', { signal, params: { school_id: school } })).data,
})
export const typesQuery = queryOptions({
  queryKey: keys.types, queryFn: async ({ signal }) => (await api.get<DocumentType[]>('/catalogs/document-types', { signal })).data,
})
export const documentsQuery = (filters: Filters, size = pageSize) => queryOptions({
  queryKey: [...keys.documents, 'list', filters, size],
  queryFn: async ({ signal }) => (await api.get<AcademicDocument[]>('/documents', {
    signal, params: {
      school_id: filters.school, subject_id: filters.subject, document_type_id: filters.type,
      q: filters.q, offset: ((filters.page ?? 1) - 1) * size, limit: size + 1,
    },
  })).data,
})
export const detailQuery = (id: string) => queryOptions({
  queryKey: [...keys.documents, 'detail', id],
  queryFn: async ({ signal }) => (await api.get<AcademicDocument[]>('/documents', {
    signal, params: { document_id: id, limit: 1 },
  })).data[0] ?? null,
})
export const versionsQuery = (id: string) => queryOptions({
  queryKey: [...keys.documents, 'versions', id],
  queryFn: async ({ signal }) => (await api.get<History>(`/documents/${id}/versions`, { signal })).data,
})
export function selectedVersion(history: History, number?: number) {
  return history.versions.find(v => v.status === 'available' &&
    (number === undefined ? v.version_id === history.current_version_id : v.version_number === number))
}
export function downloadName(header: string | undefined, fallback: string) {
  const extended = header?.match(/filename\*\s*=\s*UTF-8''([^;]+)/i)?.[1]
  const plain = header?.match(/filename\s*=\s*(?:"([^"]+)"|([^;]+))/i)
  let name = plain?.[1] ?? plain?.[2] ?? fallback
  if (extended) {
    try { name = decodeURIComponent(extended.trim()) } catch { /* use plain name */ }
  }
  return name.replace(/[\\/:*?"<>|\u0000-\u001f\u007f]/g, '_').trim().replace(/^\.+|[. ]+$/g, '') || 'documento'
}
export async function fetchFile(id: string, version: Version, signal: AbortSignal) {
  const response = await api.get<Blob>(`/documents/${id}/versions/${version.version_number}/download`, {
    signal, responseType: 'blob', timeout: 120000,
  })
  return { blob: response.data, name: downloadName(response.headers['content-disposition'], version.filename) }
}
