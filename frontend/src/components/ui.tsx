import { cva } from 'class-variance-authority'
import { Dialog, DropdownMenu, Tooltip } from 'radix-ui'
import type { ComponentProps } from 'react'
import { X } from 'lucide-react'

const button = cva('button', { variants: { variant: { primary: 'primary', ghost: 'ghost' } }, defaultVariants: { variant: 'primary' } })
export function Button({ variant, className, ...props }: ComponentProps<'button'> & { variant?: 'primary' | 'ghost' }) {
  return <button className={button({ variant, className })} {...props} />
}
export function Input(props: ComponentProps<'input'>) { return <input className="input" {...props} /> }
export function Label(props: ComponentProps<'label'>) { return <label className="label" {...props} /> }
export function Card(props: ComponentProps<'section'>) { return <section className="card" {...props} /> }
export function Badge({ children }: ComponentProps<'span'>) { return <span className="badge">{children}</span> }
export function Alert({ children }: ComponentProps<'div'>) { return <div className="alert" role="alert">{children}</div> }
export function Avatar({ name }: { name: string }) { return <span className="avatar" aria-hidden="true">{name.slice(0, 2).toUpperCase()}</span> }
export function Skeleton() { return <p className="skeleton" role="status">Cargando…</p> }
export function Separator() { return <hr /> }
export { DropdownMenu, Tooltip, Dialog }
export function Sheet({ children, trigger }: { children: React.ReactNode; trigger: React.ReactNode }) {
  return <Dialog.Root><Dialog.Trigger asChild>{trigger}</Dialog.Trigger><Dialog.Portal>
    <Dialog.Overlay className="overlay" /><Dialog.Content className="sheet">
      <Dialog.Title>Menú principal</Dialog.Title>
      <Dialog.Description className="sr-only">Navegación del repositorio</Dialog.Description>
      <Dialog.Close asChild><Button className="close" variant="ghost" aria-label="Cerrar menú"><X size={18} /></Button></Dialog.Close>
      {children}
    </Dialog.Content></Dialog.Portal></Dialog.Root>
}
