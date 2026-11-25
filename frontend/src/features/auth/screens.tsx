import { useState, type ReactNode } from 'react'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { useNavigate } from '@tanstack/react-router'
import { BookOpen, Eye, EyeOff, LockKeyhole, ShieldCheck } from 'lucide-react'
import { toast } from 'sonner'
import type { Session } from '../../routes/router'
import { errorMessage, type User } from '../../api/session'
import { Alert, Button, Card, Input, Label } from '../../components/ui'
import { loginSchema, passwordSchema } from './forms'

export function Brand() {
  return <div className="brand"><span className="brand-icon"><BookOpen size={23} /></span><div><strong>YachayRepo</strong><small>Repositorio académico institucional</small></div></div>
}
function Field({ id, label, error, children }: { id: string; label: string; error?: string; children: ReactNode }) {
  return <div className="field"><Label htmlFor={id}>{label}</Label>{children}{error && <p id={id + '-error'} className="field-error" role="alert">{error}</p>}</div>
}
export function Login({ session }: { session: Session }) {
  const navigate = useNavigate()
  const [visible, setVisible] = useState(false)
  const [error, setError] = useState('')
  const { register, handleSubmit, formState: { errors, isSubmitting } } = useForm({ resolver: zodResolver(loginSchema) })
  const submit = handleSubmit(async values => {
    setError('')
    try {
      const user = await session.login(values.email, values.password)
      if (user) await navigate({ to: user.must_change_password ? '/change-password' : '/', replace: true })
      else setError('No se pudo validar la sesión.')
    } catch (cause) { setError(errorMessage(cause, true)) }
  })
  return <main className="login">
    <aside className="identity"><Brand /><div className="identity-copy"><ShieldCheck size={32} /><h1>El conocimiento de nuestra comunidad, en un solo lugar.</h1><p>Repositorio académico de la Universidad Yachay Tech.</p></div><span className="identity-foot">Yachay Tech · Ecuador</span></aside>
    <section className="login-side"><div className="mobile-brand"><Brand /></div><div className="login-form">
      <span className="form-icon"><LockKeyhole size={23} /></span>
      <h2>Iniciar sesión</h2><p className="muted">Accede con tu cuenta institucional.</p>
      <form onSubmit={submit} noValidate aria-busy={isSubmitting}>
        {error && <Alert>{error}</Alert>}
        <Field id="email" label="Correo electrónico" error={errors.email?.message}>
          <Input id="email" type="email" autoComplete="username" aria-invalid={!!errors.email} aria-describedby={errors.email ? 'email-error' : undefined} {...register('email')} />
        </Field>
        <Field id="password" label="Contraseña" error={errors.password?.message}>
          <div className="password-field"><Input id="password" type={visible ? 'text' : 'password'} autoComplete="current-password" aria-invalid={!!errors.password} aria-describedby={errors.password ? 'password-error' : undefined} {...register('password')} />
          <Button type="button" variant="ghost" aria-label={visible ? 'Ocultar contraseña' : 'Mostrar contraseña'} onClick={() => setVisible(!visible)}>{visible ? <EyeOff size={18} /> : <Eye size={18} />}</Button></div>
        </Field>
        <Button disabled={isSubmitting}>{isSubmitting ? 'Iniciando sesión…' : 'Iniciar sesión'}</Button>
      </form><p className="login-note">Las cuentas son administradas por la institución.</p>
    </div></section>
  </main>
}
export function ChangePassword({ user, session }: { user: User; session: Session }) {
  const [error, setError] = useState('')
  const { register, handleSubmit, formState: { errors, isSubmitting } } = useForm({ resolver: zodResolver(passwordSchema) })
  const submit = handleSubmit(async ({ current_password, new_password }) => {
    setError('')
    try { await session.changePassword(current_password, new_password); toast.success('Contraseña actualizada. Inicia sesión de nuevo.') }
    catch (cause) { setError(errorMessage(cause)) }
  })
  return <main className="center"><Card><h1>Cambiar contraseña</h1>
    <p className="muted">{user.must_change_password ? 'Cambia tu contraseña temporal para continuar.' : 'Actualiza la contraseña de tu cuenta.'}</p>
    <form onSubmit={submit} noValidate aria-busy={isSubmitting}>
      {error && <Alert>{error}</Alert>}
      {(['current_password', 'new_password', 'confirmation'] as const).map((field, index) =>
        <Field key={field} id={field} label={['Contraseña actual', 'Nueva contraseña', 'Confirmar contraseña'][index]} error={errors[field]?.message}>
          <Input id={field} type="password" autoComplete={index === 0 ? 'current-password' : 'new-password'} aria-invalid={!!errors[field]} aria-describedby={errors[field] ? field + '-error' : undefined} {...register(field)} />
        </Field>)}
      <Button disabled={isSubmitting}>{isSubmitting ? 'Guardando…' : 'Guardar contraseña'}</Button>
    </form>{user.must_change_password && <Button variant="ghost" onClick={session.end}>Cerrar sesión</Button>}
  </Card></main>
}
