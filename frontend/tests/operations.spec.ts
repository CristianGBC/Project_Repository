import { expect, test } from '@playwright/test'
import { execFileSync } from 'node:child_process'
import { randomUUID } from 'node:crypto'

test('superadministración consulta el resumen y la topología reales', async ({ page }) => {
  const email = process.env.E2E_ADMIN_EMAIL
  const password = process.env.E2E_ADMIN_PASSWORD
  test.skip(process.env.VITE_API_BASE_URL !== 'http://127.0.0.1:18000' || !email || !password, 'Requiere entorno aislado')
  await page.goto('/login')
  await page.getByLabel('Correo electrónico').fill(email!)
  await page.getByLabel('Contraseña', { exact: true }).fill(password!)
  await page.getByRole('button', { name: 'Iniciar sesión' }).click()
  await page.getByRole('link', { name: 'Monitorización' }).click()
  await expect(page.getByRole('heading', { name: 'Monitorización distribuida' })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Hosts físicos' })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Documentos', exact: true })).toBeVisible()
  await page.getByRole('link', { name: 'Topología', exact: true }).click()
  await expect(page.locator('.ops-grid > article')).toHaveCount(4)
  await expect(page.locator('.ops-grid li')).toHaveCount(6)
  await page.getByRole('link', { name: 'node-bio' }).click()
  await expect(page.getByRole('heading', { name: 'Detalle del nodo' })).toBeVisible()
  await expect(page.getByRole('table').first()).toContainText('node-bio')
  await page.getByRole('link', { name: 'Reconciliación', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Reconciliación de subidas' })).toBeVisible()
  await page.route('**/operations/nodes', route => route.abort())
  await page.getByRole('link', { name: 'Topología', exact: true }).click()
  await page.getByRole('button', { name: 'Actualizar' }).click()
  await expect(page.getByRole('alert')).toContainText('desactualizados')
  await expect(page.locator('.ops-grid li')).toHaveCount(6)
})

test('observa failover, reparación, exceso y borrado en Docker aislado', async ({ page, request }) => {
  const project = process.env.E2E_ISOLATED_PROJECT
  const email = process.env.E2E_ADMIN_EMAIL
  const password = process.env.E2E_ADMIN_PASSWORD
  const base = 'http://127.0.0.1:18000'
  test.skip(!['yachayrepo-75-browser', 'yachayrepo-76-browser'].includes(project ?? '') ||
    process.env.VITE_API_BASE_URL !== base || !email || !password, 'Requiere un proyecto Docker aislado de pruebas')
  test.setTimeout(120000)
  const compose = (args: string[], cleanup = 'false') => execFileSync('docker',
    ['compose', '-p', project!, '-f', '../infrastructure/docker/compose.rf3-test.yaml', '--profile', 'monitor', '--profile', 'repair', ...args],
    { env: { ...process.env, RF3_TEST_CLEANUP_ENABLED: cleanup }, stdio: 'ignore', timeout: 30000 })
  const login = async (address: string, secret: string) => {
    const result = await request.post(base + '/auth/login', { data: { email: address, password: secret } })
    expect(result.ok()).toBeTruthy()
    return { Authorization: 'Bearer ' + (await result.json()).access_token }
  }
  const admin = await login(email!, password!)
  const teacherEmail = `ops-${randomUUID()}@example.org`
  const temporary = randomUUID()
  const teacherPassword = randomUUID()
  const created = await request.post(base + '/users', { headers: admin, data: {
    first_name: 'Pruebas', last_name: 'Operaciones', email: teacherEmail, temporary_password: temporary,
    memberships: [{ school_id: 'school-math', role: 'professor' }],
  } })
  expect(created.status()).toBe(201)
  const reset = await request.post(base + '/auth/change-password', { headers: await login(teacherEmail, temporary),
    data: { current_password: temporary, new_password: teacherPassword } })
  expect(reset.ok()).toBeTruthy()
  const teacher = await login(teacherEmail, teacherPassword)
  const title = 'Operaciones ' + randomUUID()
  const uploaded = await request.post(base + '/documents', { headers: teacher, multipart: {
    school_id: 'school-math', title, document_type_id: 'article', file: {
      name: 'ops.txt', mimeType: 'text/plain', buffer: Buffer.from(title),
    },
  } })
  expect(uploaded.status()).toBe(201)
  const { document_id: id } = await uploaded.json()
  const copies = async () => await (await request.get(base + '/operations/replicas', { headers: admin, params: { document_id: id } })).json() as { physical_host_id: string; node_id: string; status: string }[]
  const before = await copies()
  expect(before).toHaveLength(3)
  expect(new Set(before.map(row => row.physical_host_id)).size).toBe(3)
  expect(before.some(row => row.node_id === 'node-math' && row.status === 'healthy')).toBeTruthy()
  await page.goto('/login')
  await page.getByLabel('Correo electrónico').fill(email!)
  await page.getByLabel('Contraseña', { exact: true }).fill(password!)
  await page.getByRole('button', { name: 'Iniciar sesión' }).click()
  await expect(page.getByRole('link', { name: 'Monitorización' })).toBeVisible()
  try {
    compose(['up', '-d', 'health-monitor-test', 'replica-repair-test'])
    await page.goto('/admin/operations/documents/' + id)
    await expect(page.getByText('Nodo preferido node-math', { exact: true })).toBeVisible()
    compose(['stop', 'node-math-test'])
    await expect.poll(async () => {
      const rows = await (await request.get(base + '/operations/nodes', { headers: admin })).json() as { id: string; health: string }[]
      return rows.find(row => row.id === 'node-math')?.health
    }, { timeout: 20000 }).toBe('down')
    const downloaded = await request.get(base + `/documents/${id}/versions/1/download`, { headers: teacher })
    expect(await downloaded.text()).toBe(title)
    await expect.poll(async () => (await copies()).filter(row => row.status === 'healthy').length, { timeout: 25000 }).toBe(4)
    await expect(page.getByText('Registros excedentes 1', { exact: false })).toBeVisible({ timeout: 15000 })
    compose(['start', 'node-math-test'])
    await expect(page.getByText('4 accesibles según catálogo', { exact: false })).toBeVisible({ timeout: 15000 })
    compose(['up', '-d', '--force-recreate', 'replica-repair-test'], 'true')
    await expect.poll(async () => (await copies()).length, { timeout: 25000 }).toBe(3)
    expect((await copies()).some(row => row.node_id === 'node-math')).toBeTruthy()
    compose(['stop', 'node-math-test'])
    expect((await request.delete(base + '/documents/' + id, { headers: teacher })).ok()).toBeTruthy()
    compose(['start', 'node-math-test'])
    expect((await request.get(base + `/documents/${id}/versions/1/download`, { headers: teacher })).status()).toBe(404)
    await expect(page.getByText('Documento eliminado lógicamente', { exact: false })).toBeVisible({ timeout: 15000 })
  } finally {
    compose(['start', 'node-math-test'])
    await expect.poll(async () => {
      const response = await request.get(base + '/operations/nodes', { headers: admin })
      const rows = await response.json() as { id: string; health: string }[]
      return rows.find(row => row.id === 'node-math')?.health
    }, { timeout: 20000 }).toBe('up')
    compose(['stop', 'replica-repair-test', 'health-monitor-test'])
  }
})
