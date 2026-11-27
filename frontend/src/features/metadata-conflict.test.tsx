import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, test, vi } from 'vitest'
import { api } from '../api/session'
import { useMetadataConflict } from './metadata-conflict'

afterEach(() => { cleanup(); vi.restoreAllMocks() })

function Form() {
  const state = useMetadataConflict({ document_id: 'record', school_id: 'school', metadata_revision: 1 })
  return <>
    <label>Título<input defaultValue="Original" /></label>
    <output aria-label="Revisión">{state.revision}</output>
    <button onClick={state.flag}>Simular conflicto</button>
    {state.notice}
  </>
}

test('conserva el borrador y solo acepta la revisión actual por decisión explícita', async () => {
  const get = vi.spyOn(api, 'get').mockResolvedValue({ data: {
    metadata_revision: 2, title: 'Edición remota', description: null, document_type_id: null, subjects: [],
  } })
  const user = userEvent.setup()
  render(<Form />)
  const title = screen.getByRole('textbox', { name: 'Título' }) as HTMLInputElement
  await user.clear(title)
  await user.type(title, 'Mi borrador')
  await user.click(screen.getByRole('button', { name: 'Simular conflicto' }))
  expect(get).not.toHaveBeenCalled()
  await user.click(screen.getByRole('button', { name: 'Consultar datos actualizados' }))
  await screen.findByText('Título actual Edición remota')
  expect(screen.getByLabelText('Revisión').textContent).toBe('1')
  expect(title.value).toBe('Mi borrador')
  await user.click(screen.getByRole('button', { name: 'Usar esta revisión para volver a guardar' }))
  expect(screen.getByLabelText('Revisión').textContent).toBe('2')
  expect(title.value).toBe('Mi borrador')
}
)
