import { expect, it } from 'vitest'
import { uploadForm, uploadInProgress, uploadLabel, type UploadVersion } from './api/professor'

it('envía materias repetidas y reserva el multipart de versiones al archivo', () => {
  const file = new File(['material'], 'material.txt')
  const form = uploadForm(file, 'school-math', { title: 'Notas', description: '', document_type_id: 'article', subject_ids: ['a', 'b'] })
  expect(form.getAll('subject_ids')).toEqual(['a', 'b'])
  expect(form.get('school_id')).toBe('school-math')
  expect(form.has('owner_id')).toBe(false)
  expect([...uploadForm(file).keys()]).toEqual(['file'])
})

it('distingue una recuperación programada del fallo terminal', () => {
  const row: UploadVersion = {
    version_id: 'a', version_number: 1, status: 'failed', reconciliation_status: 'retry_wait',
    filename: 'file.pdf', size_bytes: 10, created_at: '', last_attempt_at: null, next_attempt_at: null,
    attempts: 2, requires_intervention: false,
  }
  expect(uploadInProgress(row)).toBe(true)
  expect(uploadLabel(row)).toBe('Esperando recuperación')
  expect(uploadInProgress({ ...row, reconciliation_status: 'failed' })).toBe(false)
  expect(uploadInProgress({ ...row, reconciliation_status: null, awaiting_reconciliation: true })).toBe(true)
  expect(uploadLabel({ ...row, status: 'available', reconciliation_status: 'completed' })).toBe('Publicado')
})
