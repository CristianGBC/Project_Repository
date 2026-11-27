import { randomUUID } from 'node:crypto'
import { expect, test } from '@playwright/test'

const base = 'http://127.0.0.1:18000'
test('administra cuentas, catálogos y documentos con alcance escolar', async ({ page, request }) => {
  test.skip(process.env.VITE_API_BASE_URL !== base || !process.env.E2E_ADMIN_EMAIL || !process.env.E2E_ADMIN_PASSWORD, 'Requiere entorno aislado')
  test.setTimeout(120000)
  const suffix = randomUUID().slice(0, 8)
  const accounts: string[] = []
  let docId: string | undefined
  async function apiLogin(email: string, password: string) {
    const response = await request.post(base + '/auth/login', { data: { email, password } })
    expect(response.ok()).toBeTruthy()
    return { Authorization: 'Bearer ' + (await response.json()).access_token }
  }
  const admin = await apiLogin(process.env.E2E_ADMIN_EMAIL!, process.env.E2E_ADMIN_PASSWORD!)
  async function browserLogin(email: string, password: string, forced = false) {
    await page.goto('/login')
    await page.getByLabel('Correo electrónico').fill(email)
    await page.getByLabel('Contraseña', { exact: true }).fill(password)
    await page.getByRole('button', { name: 'Iniciar sesión' }).click()
    if (forced) await expect(page).toHaveURL(/\/change-password$/)
    else await expect(page.getByRole('heading', { name: /^Hola/ })).toBeVisible()
  }
  async function logout() {
    await page.getByRole('button', { name: 'Menú de cuenta' }).click()
    await page.getByRole('menuitem', { name: 'Cerrar sesión' }).click()
    await expect(page).toHaveURL(/\/login$/)
  }
  async function fixture(role: string, schools: string[], label: string) {
    const email = `${label}-${suffix}@example.org`
    const temporary = randomUUID()
    const password = randomUUID()
    const created = await request.post(base + '/users', { headers: admin, data: {
      first_name: label, last_name: 'Validación', email, temporary_password: temporary,
      memberships: schools.map(school_id => ({ school_id, role })),
    } })
    expect(created.status()).toBe(201)
    const data = await created.json()
    accounts.push(data.id)
    expect((await request.post(base + '/auth/change-password', { headers: await apiLogin(email, temporary),
      data: { current_password: temporary, new_password: password } })).ok()).toBeTruthy()
    return { ...data, password, headers: await apiLogin(email, password) }
  }
  try {
    await browserLogin(process.env.E2E_ADMIN_EMAIL!, process.env.E2E_ADMIN_PASSWORD!)
    await page.getByRole('link', { name: 'Usuarios', exact: true }).click()
    await page.getByRole('link', { name: 'Crear cuenta' }).click()
    const studentEmail = `admin-student-${suffix}@example.org`
    const studentPassword = randomUUID()
    await page.getByLabel('Nombres').fill('Estudiante ' + suffix)
    await page.getByLabel('Apellidos').fill('Temporal')
    await page.getByLabel('Correo electrónico').fill(studentEmail)
    await page.getByLabel('Contraseña temporal').fill(studentPassword)
    await page.getByRole('button', { name: 'Agregar membresía' }).click()
    await page.getByRole('combobox', { name: 'Escuela de membresía' }).selectOption('school-math')
    const creation = page.waitForResponse(response => response.url() === base + '/users' && response.request().method() === 'POST')
    await page.getByRole('button', { name: 'Crear usuario' }).click()
    const created = await creation
    expect(created.status()).toBe(201)
    const student = await created.json()
    accounts.push(student.id)
    await expect(page.getByText(studentEmail, { exact: true })).toBeVisible()
    await page.getByRole('link', { name: 'Editar cuenta' }).click()
    await page.getByLabel('Apellidos').fill('Actualizado')
    await page.getByRole('button', { name: 'Guardar cuenta' }).click()
    await expect(page.getByRole('heading', { name: 'Estudiante ' + suffix + ' Actualizado' })).toBeVisible()
    const oldToken = await apiLogin(studentEmail, studentPassword)
    await page.getByRole('button', { name: 'Restablecer contraseña' }).click()
    const replacement = randomUUID()
    await page.getByLabel('Contraseña temporal').fill(replacement)
    await page.getByRole('dialog').getByRole('button', { name: 'Confirmar' }).click()
    await expect(page.getByRole('dialog')).toHaveCount(0)
    expect((await request.get(base + '/auth/me', { headers: oldToken })).status()).toBe(401)
    const temporary = await apiLogin(studentEmail, replacement)
    await page.getByRole('button', { name: 'Desactivar cuenta' }).click()
    await page.getByRole('dialog').getByRole('button', { name: 'Confirmar' }).click()
    await expect(page.getByRole('button', { name: 'Activar cuenta' })).toBeVisible()
    expect((await request.get(base + '/auth/me', { headers: temporary })).status()).toBe(401)
    await page.getByRole('button', { name: 'Activar cuenta' }).click()
    await page.getByRole('dialog').getByRole('button', { name: 'Confirmar' }).click()
    await expect(page.getByRole('button', { name: 'Desactivar cuenta' })).toBeVisible()

    await page.goto('/admin/document-types')
    await page.getByRole('button', { name: 'Crear tipo documental' }).click()
    const type = 'lab-' + suffix
    await page.getByLabel('Identificador', { exact: true }).fill(type)
    await page.getByLabel('Código', { exact: true }).fill(type)
    await page.getByLabel('Nombre', { exact: true }).fill('Tipo ' + suffix)
    await page.getByRole('button', { name: 'Guardar tipo' }).click()
    await expect(page.getByRole('heading', { name: 'Tipo ' + suffix })).toBeVisible()
    await page.goto('/admin/audit')
    await page.getByLabel('Identificador del recurso').fill(student.id)
    await page.getByRole('button', { name: 'Buscar eventos' }).click()
    for (const event of ['create_user', 'reset_password', 'deactivate_user', 'activate_user']) {
      await expect(page.getByRole('heading', { name: event, exact: true })).toBeVisible()
    }
    await logout()
    await browserLogin(studentEmail, replacement, true)
    await page.getByRole('button', { name: 'Cerrar sesión' }).click()
    const school = await fixture('school_admin', ['school-math'], 'escolar')
    const professor = await fixture('professor', ['school-math', 'school-phy'], 'compartido')
    const outside = await fixture('student', ['school-phy'], 'externo')
    await browserLogin(school.email, school.password)
    await expect(page.getByRole('link', { name: 'Auditoría', exact: true })).toHaveCount(0)
    await page.goto('/admin/audit')
    await expect(page.getByRole('heading', { name: 'Acceso restringido' })).toBeVisible()
    await page.goto('/admin/document-types')
    await expect(page.getByRole('heading', { name: 'Acceso restringido' })).toBeVisible()
    expect((await request.patch(base + '/catalogs/document-types/' + type, { headers: school.headers, data: { is_active: false } })).status()).toBe(403)
    await page.goto('/admin/users/' + professor.id)
    await expect(page.getByText(/Escuela de Ciencias Matemáticas/)).toBeVisible()
    await expect(page.getByText(/Escuela de Ciencias Físicas/)).toHaveCount(0)
    await expect(page.getByRole('button', { name: 'Restablecer contraseña' })).toHaveCount(0)
    expect((await request.get(base + '/users/' + outside.id, { headers: school.headers })).status()).toBe(404)
    expect((await request.post(base + '/users', { headers: school.headers, data: {
      first_name: 'Prueba', last_name: 'Denegada', email: `denied-${suffix}@example.org`, temporary_password: randomUUID(),
      memberships: [{ school_id: 'school-math', role: 'school_admin' }],
    } })).status()).toBe(403)
    await page.goto('/admin/subjects')
    await page.getByRole('button', { name: 'Crear materia' }).click()
    await page.getByRole('combobox', { name: 'Escuela de la materia' }).selectOption('school-math')
    await page.getByLabel('Código', { exact: true }).fill(suffix)
    await page.getByLabel('Nombre', { exact: true }).fill('Materia ' + suffix)
    await page.getByRole('button', { name: 'Guardar materia' }).click()
    await expect(page.getByRole('heading', { name: 'Materia ' + suffix })).toBeVisible()
    const uploaded = await request.post(base + '/documents', { headers: professor.headers, multipart: {
      school_id: 'school-math', title: 'Documento ' + suffix, file: { name: 'material.txt', mimeType: 'text/plain', buffer: Buffer.from('contenido de prueba') },
    } })
    expect(uploaded.status()).toBe(201)
    docId = (await uploaded.json()).document_id
    await page.goto('/admin/documents/' + docId)
    await page.getByRole('link', { name: 'Editar metadatos' }).click()
    await page.getByLabel('Título', { exact: true }).fill('Editado ' + suffix)
    const concurrent = await request.patch(base + '/documents/' + docId, {
      headers: professor.headers, data: { title: 'Cambio concurrente ' + suffix, metadata_revision: 1 },
    })
    expect(concurrent.ok()).toBeTruthy()
    await page.getByRole('button', { name: 'Guardar metadatos' }).click()
    await expect(page.getByText(/El documento cambió desde que abriste/)).toBeVisible()
    await expect(page.getByLabel('Título', { exact: true })).toHaveValue('Editado ' + suffix)
    await page.getByRole('button', { name: 'Consultar datos actualizados' }).click()
    await expect(page.getByText('Título actual Cambio concurrente ' + suffix)).toBeVisible()
    await page.getByRole('button', { name: 'Usar esta revisión para volver a guardar' }).click()
    await page.getByRole('button', { name: 'Guardar metadatos' }).click()
    await expect(page.getByRole('heading', { name: 'Editar Editado ' + suffix })).toBeVisible()
    await page.goto('/admin/documents/' + docId)
    await page.getByRole('button', { name: 'Eliminar documento' }).click()
    await page.getByRole('dialog').getByRole('button', { name: 'Confirmar' }).click()
    await expect(page).toHaveURL(/\/admin\/documents$/)
    await page.goto('/admin/documents/' + docId)
    await page.getByRole('button', { name: 'Restaurar documento' }).click()
    await page.getByRole('dialog').getByRole('button', { name: 'Confirmar' }).click()
    await expect(page).toHaveURL(/\/admin\/documents$/)
    if (!docId) throw new Error("No se obtuvo el ID del documento publicado")
    const publicRow = await request.get(base + '/documents', { headers: professor.headers, params: { document_id: docId } })
    expect((await publicRow.json())[0].title).toBe('Editado ' + suffix)
    await page.goto(`/admin/users/${professor.id}/memberships`)
    await page.getByRole('button', { name: 'Revocar membresía' }).click()
    await page.getByRole('dialog').getByRole('button', { name: 'Confirmar' }).click()
    await expect(page).toHaveURL(/\/admin\/users$/)
    expect((await request.get(base + '/auth/me', { headers: professor.headers })).status()).toBe(401)
    const fresh = await apiLogin(professor.email, professor.password)
    const profile = await request.get(base + '/auth/me', { headers: fresh })
    expect((await profile.json()).memberships.map((item: { school_id: string }) => item.school_id)).toEqual(['school-phy'])
    await page.goto('/admin/users/new')
    await expect(page.getByRole('combobox', { name: 'Escuela de membresía' }).locator('option')).toHaveCount(2)
    await expect(page.getByRole('combobox', { name: 'Rol de membresía' }).locator('option')).toHaveCount(2)
    const schoolEmail = `school-created-${suffix}@example.org`
    await page.getByLabel('Nombres').fill('Cuenta escolar')
    await page.getByLabel('Apellidos').fill(suffix)
    await page.getByLabel('Correo electrónico').fill(schoolEmail)
    await page.getByLabel('Contraseña temporal').fill(randomUUID())
    await page.getByRole('combobox', { name: 'Escuela de membresía' }).selectOption('school-math')
    await page.getByRole('combobox', { name: 'Rol de membresía' }).selectOption('professor')
    const schoolCreation = page.waitForResponse(response => response.url() === base + '/users' && response.request().method() === 'POST')
    await page.getByRole('button', { name: 'Crear usuario' }).click()
    const made = await schoolCreation
    expect(made.status()).toBe(201)
    accounts.push((await made.json()).id)
    await expect(page.getByText(schoolEmail, { exact: true })).toBeVisible()
    await page.setViewportSize({ width: 390, height: 844 })
    await page.goto('/admin/users')
    await expect(page.getByRole('heading', { name: 'Usuarios', exact: true })).toBeVisible()
    await page.screenshot({ path: test.info().outputPath('administracion-movil.png') })
  } finally {
    if (docId) await request.delete(base + '/documents/' + docId, { headers: admin })
    for (const id of accounts) await request.post(base + `/users/${id}/deactivate`, { headers: admin })
  }
})
