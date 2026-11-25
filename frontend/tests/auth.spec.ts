import { randomUUID } from 'node:crypto'
import { test, expect } from '@playwright/test'

const api = 'http://127.0.0.1:18000'
const adminEmail = process.env.E2E_ADMIN_EMAIL
const adminPassword = process.env.E2E_ADMIN_PASSWORD

test('protege el perfil y valida el acceso real desde escritorio y móvil', async ({ page }) => {
  test.skip(process.env.VITE_API_BASE_URL !== api || !adminEmail || !adminPassword, 'Requiere coordinador aislado y credenciales de prueba')
  await page.goto('/profile')
  await expect(page).toHaveURL(/\/login$/)
  await page.screenshot({ path: test.info().outputPath('login.png') })
  await page.getByLabel('Correo electrónico').fill(adminEmail!)
  await page.getByLabel('Contraseña', { exact: true }).fill('incorrecta')
  await page.getByRole('button', { name: 'Iniciar sesión' }).click()
  await expect(page.getByRole('alert')).toContainText('Correo o contraseña incorrectos')
  await page.getByLabel('Contraseña', { exact: true }).fill(adminPassword!)
  await page.getByRole('button', { name: 'Iniciar sesión' }).click()
  await expect(page.getByRole('heading', { name: /^Hola/ })).toBeVisible()
  await page.getByRole('link', { name: 'Ver mi perfil' }).click()
  await expect(page.getByText(adminEmail!, { exact: true })).toBeVisible()
  await page.reload()
  await expect(page.getByRole('heading', { name: 'Mi perfil' })).toBeVisible()
  await page.setViewportSize({ width: 390, height: 844 })
  await page.getByRole('button', { name: 'Abrir menú' }).click()
  await expect(page.getByRole('dialog')).toBeVisible()
  await page.getByRole('button', { name: 'Cerrar menú' }).click()
  await page.getByRole('button', { name: 'Menú de cuenta' }).click()
  await page.getByRole('menuitem', { name: 'Cerrar sesión' }).click()
  await expect(page).toHaveURL(/\/login$/)
  await page.goto('/profile')
  await expect(page).toHaveURL(/\/login$/)
})

for (const [role, label] of [['student', 'Estudiante'], ['professor', 'Profesor'], ['school_admin', 'Administrador de escuela']] as const) {
  test(`cambio obligatorio, perfil y revocación de ${role}`, async ({ page, request, context }) => {
    test.skip(process.env.VITE_API_BASE_URL !== api || !adminEmail || !adminPassword, 'Requiere coordinador aislado y credenciales de prueba')
    const admin = await request.post(api + '/auth/login', { data: { email: adminEmail, password: adminPassword } })
    expect(admin.ok()).toBeTruthy()
    const headers = { Authorization: 'Bearer ' + (await admin.json()).access_token }
    const schools = await request.get(api + '/catalogs/schools', { headers })
    expect(schools.ok()).toBeTruthy()
    const school = (await schools.json())[0]
    const email = `frontend-${randomUUID()}@example.org`
    const temporary = randomUUID()
    const nextPassword = randomUUID()
    const created = await request.post(api + '/users', {
      headers, data: { first_name: 'Prueba', last_name: 'Frontend', email, temporary_password: temporary, memberships: [{ school_id: school.id, role }] },
    })
    expect(created.status()).toBe(201)
    const { id } = await created.json()
    try {
      const signIn = async (password: string) => {
        await page.getByLabel('Correo electrónico').fill(email)
        await page.getByLabel('Contraseña', { exact: true }).fill(password)
        await page.getByRole('button', { name: 'Iniciar sesión' }).click()
      }
      await page.goto('/login')
      await signIn(temporary)
      await expect(page).toHaveURL(/\/change-password$/)
      await page.goto('/profile')
      await expect(page).toHaveURL(/\/change-password$/)
      await page.getByLabel('Contraseña actual').fill(temporary)
      await page.getByLabel('Nueva contraseña', { exact: true }).fill(nextPassword)
      await page.getByLabel('Confirmar contraseña').fill(nextPassword)
      await page.getByRole('button', { name: 'Guardar contraseña' }).click()
      await expect(page).toHaveURL(/\/login$/)
      await signIn(nextPassword)
      await page.getByRole('link', { name: 'Ver mi perfil' }).click()
      await expect(page.getByText(label, { exact: true })).toBeVisible()
      await expect(page.getByText(school.name, { exact: true })).toBeVisible()
      await context.setOffline(true)
      await page.getByRole('link', { name: 'Inicio' }).click()
      await expect(page.getByRole('heading', { name: 'No se pudo conectar con el servicio.' })).toBeVisible()
      await context.setOffline(false)
      await page.getByRole('button', { name: 'Reintentar' }).click()
      await expect(page.getByRole('heading', { name: /^Hola/ })).toBeVisible()
      expect((await request.post(api + `/users/${id}/deactivate`, { headers })).ok()).toBeTruthy()
      await page.reload()
      await expect(page).toHaveURL(/\/login$/)
    } finally {
      await context.setOffline(false)
      await request.post(api + `/users/${id}/deactivate`, { headers })
    }
  })
}
