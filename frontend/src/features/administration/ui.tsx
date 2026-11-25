import { useRef, useState, type FormEvent, type ReactNode } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { isAxiosError } from 'axios'
import { toast } from 'sonner'
import { adminChanges, pageSize } from '../../api/administration'
import { errorMessage } from '../../api/session'
import { Alert, Button, Card, Dialog, Input, Skeleton } from '../../components/ui'

export const formatted = (date: string) => new Date(date).toLocaleString('es-EC')
export function adminError(error: unknown) {
  const status = isAxiosError(error) ? error.response?.status : undefined
  if (status === 409) {
    const detail: unknown = isAxiosError(error) ? error.response?.data?.detail : null
    return detail === 'cannot deactivate the last active super administrator'
      ? 'La última cuenta de superadministración activa debe conservarse.'
      : 'Ya existe un registro con esos datos o la operación entra en conflicto con su estado.'
  }
  if (status === 400 || status === 422) return 'Revisa los campos, la política de contraseña y los permisos de escuela.'
  return errorMessage(error)
}
export function useAdminAction() {
  const client = useQueryClient()
  const running = useRef(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  async function run(action: () => Promise<unknown>, onSuccess?: () => void) {
    if (running.current) return
    running.current = true
    setBusy(true)
    setError('')
    try {
      await action()
      await Promise.all(adminChanges.map(queryKey => client.invalidateQueries({ queryKey })))
      toast.success('Cambios guardados.')
      onSuccess?.()
    } catch (cause) { setError(adminError(cause)) }
    finally { running.current = false; setBusy(false) }
  }
  return { run, busy, error }
}
export function Results({ pending, error, retry, empty, children }: { pending: boolean; error: unknown; retry: () => void; empty: boolean; children: ReactNode }) {
  if (pending) return <Skeleton />
  if (error) return <Alert>{adminError(error)} <Button variant="ghost" onClick={retry}>Reintentar</Button></Alert>
  if (empty) return <Card>No se encontraron registros.</Card>
  return <>{children}</>
}
export function Pages({ page, count, change }: { page: number; count: number; change: (page: number) => void }) {
  return <nav aria-label="Paginación administrativa" className="pdf-controls">
    <Button variant="ghost" disabled={page === 0} onClick={() => change(page - 1)}>Anterior</Button>
    <span>Página {page + 1}</span>
    <Button variant="ghost" disabled={count <= pageSize} onClick={() => change(page + 1)}>Siguiente</Button>
  </nav>
}
export function Confirm({ label, description, action, onSuccess, fields }: {
  label: string; description: string; action: (data: FormData) => Promise<unknown>
  onSuccess?: () => void; fields?: ReactNode
}) {
  const [open, setOpen] = useState(false)
  return <Dialog.Root open={open} onOpenChange={setOpen}><Dialog.Trigger asChild><Button variant="ghost">{label}</Button></Dialog.Trigger>
    <Dialog.Portal><Dialog.Overlay className="overlay" /><Dialog.Content className="professor-dialog card">
      <Dialog.Title>{label}</Dialog.Title><Dialog.Description>{description}</Dialog.Description>
      {open && <ConfirmationForm action={action} close={() => { setOpen(false); onSuccess?.() }}>{fields}</ConfirmationForm>}
    </Dialog.Content></Dialog.Portal>
  </Dialog.Root>
}
function ConfirmationForm({ action, close, children }: { action: (data: FormData) => Promise<unknown>; close: () => void; children?: ReactNode }) {
  const state = useAdminAction()
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = event.currentTarget
    const data = new FormData(form)
    void state.run(() => action(data), () => { form.reset(); close() })
  }
  return <form onSubmit={submit}>
    {children}{state.error && <Alert>{state.error}</Alert>}
    <div className="pdf-controls">
      <Dialog.Close asChild><Button variant="ghost" type="button" disabled={state.busy}>Cancelar</Button></Dialog.Close>
      <Button disabled={state.busy}>{state.busy ? 'Aplicando…' : 'Confirmar'}</Button>
    </div>
  </form>
}
export function TemporaryPassword() {
  const [visible, setVisible] = useState(false)
  return <div><label>Contraseña temporal<Input name="temporary_password" required minLength={12} maxLength={1024} type={visible ? 'text' : 'password'} autoComplete="new-password" /></label>
    <Button variant="ghost" type="button" onClick={() => setVisible(!visible)}>{visible ? 'Ocultar contraseña' : 'Mostrar contraseña'}</Button>
  </div>
}
