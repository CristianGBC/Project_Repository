import { expect, it } from 'vitest'
import { clampZoom, documentsQuery, downloadName, parseFilters, selectedVersion, type Version } from './api/academic'

it('valida URL y separa las claves de búsquedas y páginas', () => {
  expect(parseFilters({ q: ' Ecuaciones ', page: 2, subject: 'no-id', school: 'school-math' }))
    .toEqual({ q: 'Ecuaciones', page: 2, subject: undefined, school: 'school-math', type: undefined })
  expect(parseFilters({ page: -1 }).page).toBeUndefined()
  expect(documentsQuery({ page: 2 }).queryKey).not.toEqual(documentsQuery({ page: 1 }).queryKey)
})
it('usa current_version_id y sólo ofrece versiones disponibles', () => {
  const first: Version = { version_id: 'v1', version_number: 1, filename: 'one.pdf', mime_type: 'application/pdf', size_bytes: 5, created_at: '2026-01-01', status: 'available' }
  const history = { current_version_id: 'v1', versions: [first, { ...first, version_id: 'v2', version_number: 2, status: 'failed' }] }
  expect(selectedVersion(history)).toBe(first)
  expect(selectedVersion(history, 2)).toBeUndefined()
  expect(selectedVersion(history, 1)).toBe(first)
})
it('prioriza el nombre Unicode, usa fallback y elimina separadores', () => {
  expect(downloadName("attachment; filename=notes.pdf; filename*=UTF-8''%C3%81lgebra.pdf", 'doc.pdf')).toBe('Álgebra.pdf')
  expect(downloadName("attachment; filename*=UTF-8''%XX", 'doc.pdf')).toBe('doc.pdf')
  expect(downloadName(undefined, '../private\\doc.pdf')).toBe('_private_doc.pdf')
})
it('aplica límites de zoom', () => {
  expect(clampZoom(0)).toBe(.5)
  expect(clampZoom(1.75)).toBe(1.75)
  expect(clampZoom(10)).toBe(2.5)
})
