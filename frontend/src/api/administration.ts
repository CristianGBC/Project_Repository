import { queryOptions } from '@tanstack/react-query'
import { api, keys, type School, type SchoolRole, type User } from './session'
import { canManageSchool, isSuperAdmin } from '../features/auth/permissions'

export type ManagedUser = User & { created_at: string; can_reset_password: boolean }
export type MembershipInput = { school_id: string; role: SchoolRole }
export interface ManagedDocument {
  document_id: string; title: string; description: string | null; school_id: string
  owner_name: string | null; document_type_id: string | null; subject_ids: string[]
  created_at: string; updated_at: string; deleted_at: string | null; current_version: number | null
}
export interface AuditEvent {
  id: string; actor_user_id: string | null; action: string; resource_type: string
  resource_id: string; outcome: string; created_at: string; detail?: string | null
}
export const adminKey = ['administration'] as const
export const pageSize = 15
export const roles: Record<SchoolRole, string> = {
  student: 'Estudiante', professor: 'Profesor', school_admin: 'Administrador escolar',
}
export const manageableSchools = (actor: User, schools: School[]) => schools.filter(school => canManageSchool(actor, school.id))
export const grantableRoles = (actor: User): SchoolRole[] => isSuperAdmin(actor) ? ['student', 'professor', 'school_admin'] : ['student', 'professor']
export const duplicateMembership = (items: MembershipInput[], next: MembershipInput) =>
  items.some(item => item.school_id === next.school_id && item.role === next.role)
export const adminChanges = [adminKey, keys.documents, ['professor', 'uploads'], keys.subjects, keys.types] as const

export function adminQuery<T>(path: string, params?: Record<string, unknown>) {
  return queryOptions({
    queryKey: [...adminKey, path, params],
    queryFn: async ({ signal }) => (await api.get<T>(path, { params, signal })).data,
  })
}
