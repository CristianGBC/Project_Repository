import { z } from 'zod'

const password = z.string().min(1, 'Ingresa tu contraseña.').max(1024, 'Máximo 1024 caracteres.')
export const loginSchema = z.object({
  email: z.string().trim().min(1, 'Ingresa tu correo.').email('Ingresa un correo válido.'),
  password,
})
export const passwordSchema = z.object({
  current_password: password,
  new_password: z.string().min(12, 'Usa al menos 12 caracteres.').max(1024, 'Máximo 1024 caracteres.'),
  confirmation: z.string(),
}).refine(data => data.new_password === data.confirmation, { path: ['confirmation'], message: 'Las contraseñas deben coincidir.' })
