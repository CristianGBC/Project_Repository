import type { SchoolRole, User } from '../../api/session'

export const isSuperAdmin = (user: User) => user.global_role === 'super_admin'
export const hasSchoolRole = (user: User, school: string, role: SchoolRole) =>
  user.memberships.some(m => m.is_active && m.school_id === school && m.role === role)
export const hasAnySchoolRole = (user: User, role: SchoolRole) =>
  user.memberships.some(m => m.is_active && m.role === role)
export const canUploadToSchool = (user: User, school: string) => hasSchoolRole(user, school, 'professor')
export const canManageSchool = (user: User, school: string) => isSuperAdmin(user) || hasSchoolRole(user, school, 'school_admin')
export const canManageUsers = (user: User) => isSuperAdmin(user) || hasAnySchoolRole(user, 'school_admin')
export const canViewBasicOperations = canManageUsers
export const canViewFullOperations = isSuperAdmin
