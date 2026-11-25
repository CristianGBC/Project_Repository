import { execFileSync } from 'node:child_process'
import { createHash, randomUUID } from 'node:crypto'
import { expect, test } from '@playwright/test'

const base = 'http://127.0.0.1:18000'
const docker = ['compose', '-p', 'yachayrepo-professor-validation', '-f', '../infrastructure/docker/compose.rf3-test.yaml']
const hash = (bytes: Buffer) => createHash('sha256').update(bytes).digest('hex')
function testPdf(color: string) {
  const drawing = `${color} rg 20 20 120 80 re f\n`
  const objects = [
    '<< /Type /Catalog /Pages 2 0 R >>',
    '<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
    '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 400] /Contents 4 0 R >>',
    `<< /Length ${drawing.length} >>\nstream\n${drawing}endstream`,
  ]
  let source = '%PDF-1.4\n'
  const offsets = objects.map((object, i) => {
    const offset = source.length
    source += `${i + 1} 0 obj\n${object}\nendobj\n`
    return offset
  })
  return Buffer.from(source + 'xref\n0 5\n0000000000 65535 f \n' +
    offsets.map(offset => `${String(offset).padStart(10, '0')} 00000 n \n`).join('') +
    `trailer\n<< /Size 5 /Root 1 0 R >>\nstartxref\n${source.length}\n%%EOF\n`)
}

test('profesor publica, versiona, edita, elimina y restaura con FastAPI real', async ({ page, request }) => {
  test.skip(process.env.VITE_API_BASE_URL !== base || !process.env.E2E_ADMIN_EMAIL || !process.env.E2E_ADMIN_PASSWORD, 'Requiere servicios de prueba')
  test.setTimeout(120000)
  async function login(email: string, password: string) {
    const response = await request.post(base + '/auth/login', { data: { email, password } })
    expect(response.ok()).toBeTruthy()
    return { Authorization: 'Bearer ' + (await response.json()).access_token }
  }
  const admin = await login(process.env.E2E_ADMIN_EMAIL!, process.env.E2E_ADMIN_PASSWORD!)
  const suffix = randomUUID().slice(0, 8)
  const title = 'Notas ' + suffix
  const accounts: string[] = []
  const docs: string[] = []
  let nodeStopped = false
  let workerStopped = false
  const service = (action: 'stop' | 'start', name: string) =>
    execFileSync('docker', [...docker, '--profile', 'reconciliation', action, name], { stdio: 'ignore', timeout: 30000 })
  async function account(role: 'professor' | 'student', schools: string[]) {
    const email = `profportal-${role}-${suffix}@example.org`
    const temporary = randomUUID()
    const password = randomUUID()
    const created = await request.post(base + '/users', { headers: admin, data: {
      first_name: 'Prueba', last_name: 'Docente', email, temporary_password: temporary,
      memberships: schools.map(school_id => ({ school_id, role })),
    } })
    expect(created.status()).toBe(201)
    const user = await created.json()
    accounts.push(user.id)
    expect((await request.post(base + '/auth/change-password', { headers: await login(email, temporary),
      data: { current_password: temporary, new_password: password } })).ok()).toBeTruthy()
    return { email, password, user, headers: await login(email, password) }
  }
  async function signIn(email: string, password: string) {
    await page.goto('/login')
    await page.getByLabel('Correo electrónico').fill(email)
    await page.getByLabel('Contraseña', { exact: true }).fill(password)
    await page.getByRole('button', { name: 'Iniciar sesión' }).click()
    await expect(page.getByRole('heading', { name: /^Hola/ })).toBeVisible()
  }
  async function logout() {
    await page.getByRole('button', { name: 'Menú de cuenta' }).click()
    await page.getByRole('menuitem', { name: 'Cerrar sesión' }).click()
    await expect(page).toHaveURL(/\/login$/)
  }
  try {
    const professor = await account('professor', ['school-math', 'school-phy'])
    const student = await account('student', ['school-bio'])
    const subjectNames = ['Álgebra ' + suffix, 'Análisis ' + suffix]
    const subjectIds = []
    for (const [index, name] of subjectNames.entries()) {
      const subject = await request.post(base + '/subjects', { headers: admin, data: { school_id: 'school-math', code: suffix + index, name } })
      expect(subject.status()).toBe(201)
      subjectIds.push((await subject.json()).id)
    }
    await signIn(student.email, student.password)
    await expect(page.getByRole('link', { name: 'Mis documentos' })).toHaveCount(0)
    await page.goto('/professor/documents/new')
    await expect(page.getByRole('heading', { name: 'Acceso restringido' })).toBeVisible()
    await page.goto('/profile')
    await logout()
    await signIn(process.env.E2E_ADMIN_EMAIL!, process.env.E2E_ADMIN_PASSWORD!)
    await expect(page.getByRole('link', { name: 'Mis documentos' })).toHaveCount(0)
    await logout()
    await signIn(professor.email, professor.password)
    await page.getByRole('link', { name: 'Subir documento' }).click()
    const school = page.getByRole('combobox', { name: 'Escuela', exact: true })
    await expect(school.locator('option')).toHaveCount(3)
    await expect(school.locator('option[value=school-phy]')).toHaveCount(1)
    await school.selectOption('school-math')
    await page.getByLabel('Título').fill(title)
    await page.getByRole('combobox', { name: 'Tipo documental' }).selectOption('study_material')
    for (const name of subjectNames) await page.getByLabel(name).check()
    const original = testPdf('0 0 1')
    const newer = testPdf('0 1 0')
    await page.getByLabel('Archivo', { exact: true }).setInputFiles({ name: 'notas.pdf', mimeType: 'application/pdf', buffer: original })
    const response = page.waitForResponse(result => result.url() === base + '/documents' && result.request().method() === 'POST')
    await page.getByRole('button', { name: 'Enviar archivo' }).click()
    const uploaded = await response
    expect(uploaded.status()).toBe(201)
    const data = await uploaded.json()
    const id = data.document_id as string
    docs.push(id)
    expect(data.owner_id).toBe(professor.user.id)
    expect(data.node_id).toBe('node-math')
    expect(data.target_replicas).toBe(3)
    expect(data.sha256).toBe(hash(original))
    expect(data.subjects.map((subject: { id: string }) => subject.id).sort()).toEqual(subjectIds.sort())
    const replicas = await request.get(base + '/operations/replicas', { headers: admin, params: { version_id: data.version_id } })
    expect(replicas.ok()).toBeTruthy()
    const copies: { physical_host_id: string; status: string }[] = await replicas.json()
    expect(copies).toHaveLength(3)
    expect(copies.every(copy => copy.status === 'healthy')).toBeTruthy()
    expect(new Set(copies.map(copy => copy.physical_host_id)).size).toBe(3)
    await page.getByRole('link', { name: 'Ver documento' }).click()
    await page.getByRole('link', { name: 'Editar metadatos' }).click()
    await page.getByLabel('Título').fill(title + ' actualizado')
    await page.getByLabel(subjectNames[0]).uncheck()
    await page.getByRole('button', { name: 'Guardar cambios' }).click()
    await page.getByRole('link', { name: 'Ver documento' }).click()
    await expect(page.getByText('Versión 1 · notas.pdf')).toBeVisible()
    await expect(page.getByText(/^Versión 2 ·/)).toHaveCount(0)
    await page.getByRole('link', { name: 'Nueva versión' }).click()
    await page.getByLabel('Archivo', { exact: true }).setInputFiles({ name: 'notas-v2.pdf', mimeType: 'application/pdf', buffer: newer })
    await page.getByRole('button', { name: 'Enviar archivo' }).click()
    await page.getByRole('link', { name: 'Ver documento' }).click()
    await expect(page.getByText('Versión 2 · notas-v2.pdf')).toBeVisible()
    const history = await request.get(base + `/documents/${id}/versions`, { headers: student.headers })
    const versions = (await history.json()).versions
    expect(versions.find((item: { is_current: boolean }) => item.is_current).version_number).toBe(2)
    for (const [index, bytes] of [original, newer].entries()) {
      const file = await request.get(base + `/documents/${id}/versions/${index + 1}/download`, { headers: student.headers })
      expect(file.ok()).toBeTruthy()
      expect(hash(await file.body())).toBe(hash(bytes))
      console.log(`SHA-256 versión ${index + 1}: ${hash(bytes)}`)
    }
    await page.getByRole('button', { name: 'Eliminar documento' }).click()
    await page.getByRole('dialog').getByRole('button', { name: 'Confirmar' }).click()
    await expect(page.getByRole('button', { name: 'Restaurar documento' })).toBeVisible()
    const visible = async () => (await request.get(base + '/documents', { headers: student.headers, params: { document_id: id } })).json()
    expect(await visible()).toEqual([])
    await page.getByRole('link', { name: 'Volver a eliminados' }).click()
    await page.getByRole('link', { name: title + ' actualizado' }).click()
    await page.getByRole('button', { name: 'Restaurar documento' }).click()
    await page.getByRole('dialog').getByRole('button', { name: 'Confirmar' }).click()
    await expect(page.getByRole('link', { name: 'Ver publicación actual' })).toBeVisible()
    expect((await visible())[0].title).toBe(title + ' actualizado')
    if (process.env.E2E_PROFESSOR_FAILURES === '1') {
      service('stop', 'upload-reconciler-test'); workerStopped = true
      service('stop', 'node-math-test'); nodeStopped = true
      await page.getByRole('link', { name: 'Nueva versión' }).click()
      await page.getByLabel('Archivo', { exact: true }).setInputFiles({ name: 'notas-v3.txt', mimeType: 'text/plain', buffer: Buffer.from('recuperado') })
      await page.getByRole('button', { name: 'Enviar archivo' }).click()
      await page.getByRole('link', { name: 'Consultar Mis subidas' }).click()
      await expect(page.getByText(/Pendiente|Esperando recuperación|En recuperación/).first()).toBeVisible()
      expect((await visible())[0].version_number).toBe(2)
      service('start', 'node-math-test'); nodeStopped = false
      service('start', 'upload-reconciler-test'); workerStopped = false
      await expect.poll(async () => (await visible())[0]?.version_number, { timeout: 35000 }).toBe(3)
      const recovered = page.locator('.version-row').filter({ hasText: 'Versión 3 · notas-v3.txt' })
      await expect(recovered.getByText('Actual', { exact: true })).toBeVisible({ timeout: 12000 })
    }
    await page.setViewportSize({ width: 390, height: 844 })
    await page.goto('/professor/documents/new')
    await expect(page.getByLabel('Archivo', { exact: true })).toBeVisible()
    await page.screenshot({ path: test.info().outputPath('subida-movil.png') })
    await logout()
    await signIn(student.email, student.password)
    await page.goto('/documents?q=' + encodeURIComponent(title))
    await page.getByRole('link', { name: title + ' actualizado' }).click()
    await page.getByRole('button', { name: 'Ver versión 1' }).click()
    await expect(page.locator('canvas')).toBeVisible()
    const membership = professor.user.memberships.find((item: { school_id: string }) => item.school_id === 'school-math')
    expect((await request.delete(base + `/users/${professor.user.id}/memberships/${membership.id}`, { headers: admin })).ok()).toBeTruthy()
    const multipart = { school_id: 'school-math', file: { name: 'notas.pdf', mimeType: 'application/pdf', buffer: original } }
    expect((await request.post(base + '/documents', { headers: professor.headers, multipart })).status()).toBe(401)
    const renewed = await login(professor.email, professor.password)
    expect((await request.post(base + '/documents', { headers: renewed, multipart })).status()).toBe(403)
  } finally {
    if (nodeStopped) service('start', 'node-math-test')
    if (workerStopped) service('start', 'upload-reconciler-test')
    for (const id of docs) await request.delete(base + `/documents/${id}`, { headers: admin })
    for (const id of accounts) await request.post(base + `/users/${id}/deactivate`, { headers: admin })
  }
})
