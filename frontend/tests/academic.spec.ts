import { execFileSync } from 'node:child_process'
import { createHash, randomUUID } from 'node:crypto'
import { readFile } from 'node:fs/promises'
import { expect, test, type APIRequestContext } from '@playwright/test'

const base = 'http://127.0.0.1:18000'
const compose = ['compose', '-p', process.env.E2E_ISOLATED_PROJECT ?? 'yachayrepo-academic-validation', '-f', '../infrastructure/docker/compose.rf3-test.yaml']
const nodes = ['math', 'phy', 'bio', 'chem', 'earth', 'agro'].map(id => `node-${id}-test`)
function pdf(pages: number) {
  const objects = [
    '<< /Type /Catalog /Pages 2 0 R >>',
    `<< /Type /Pages /Count ${pages} /Kids [${Array.from({ length: pages }, (_, i) => `${i + 3} 0 R`).join(' ')}] >>`,
    ...Array.from({ length: pages }, () => `<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 400] /Contents ${pages + 3} 0 R >>`),
    '<< /Length 28 >>\nstream\n0 0 1 rg 20 20 200 100 re f\n\nendstream',
  ]
  let source = '%PDF-1.4\n'
  const offsets = [0]
  objects.forEach((object, index) => { offsets.push(source.length); source += `${index + 1} 0 obj\n${object}\nendobj\n` })
  return Buffer.from(source + `xref\n0 ${offsets.length}\n0000000000 65535 f \n` + offsets.slice(1).map(n => `${String(n).padStart(10, '0')} 00000 n \n`).join('') +
    `trailer\n<< /Size ${offsets.length} /Root 1 0 R >>\nstartxref\n${source.length}\n%%EOF\n`)
}
const digest = (bytes: Buffer) => createHash('sha256').update(bytes).digest('hex')
async function auth(request: APIRequestContext, email: string, password: string) {
  const response = await request.post(base + '/auth/login', { data: { email, password } })
  expect(response.ok()).toBeTruthy()
  return { Authorization: 'Bearer ' + (await response.json()).access_token }
}

test('portal académico con PDF, versión exacta, descarga íntegra y failover', async ({ page, request }) => {
  test.skip(process.env.VITE_API_BASE_URL !== base || !process.env.E2E_ADMIN_EMAIL || !process.env.E2E_ADMIN_PASSWORD, 'Requiere servicios de prueba')
  test.setTimeout(120000)
  const admin = await auth(request, process.env.E2E_ADMIN_EMAIL!, process.env.E2E_ADMIN_PASSWORD!)
  const suffix = randomUUID().slice(0, 8)
  const title = 'Álgebra ' + suffix
  const accounts: string[] = []
  const documents: string[] = []
  const stopped = new Set<string>()
  function nodesAction(action: 'stop' | 'start', affected: string[]) {
    execFileSync('docker', [...compose, action, ...affected], { stdio: 'ignore', timeout: 30000 })
    affected.forEach(node => action === 'stop' ? stopped.add(node) : stopped.delete(node))
  }
  async function account(role: 'professor' | 'student', school: string) {
    const email = `${role}-${suffix}@example.org`
    const temporary = randomUUID()
    const password = randomUUID()
    const created = await request.post(base + '/users', { headers: admin, data: {
      first_name: 'Prueba', last_name: 'Académica', email, temporary_password: temporary,
      memberships: [{ school_id: school, role }, ...(role === 'professor' ? [{ school_id: 'school-phy', role }] : [])],
    } })
    expect(created.status()).toBe(201)
    accounts.push((await created.json()).id)
    const changed = await request.post(base + '/auth/change-password', {
      headers: await auth(request, email, temporary), data: { current_password: temporary, new_password: password },
    })
    expect(changed.ok()).toBeTruthy()
    return { email, password, headers: await auth(request, email, password) }
  }
  try {
    const professor = await account('professor', 'school-math')
    const student = await account('student', 'school-bio')
    const subject = await request.post(base + '/subjects', { headers: admin, data: { school_id: 'school-math', code: suffix, name: 'Álgebra ' + suffix } })
    expect(subject.status()).toBe(201)
    const subjectId = (await subject.json()).id
    async function upload(name: string, mimeType: string, buffer: Buffer, label: string, school = 'school-math') {
      const response = await request.post(base + '/documents', { headers: professor.headers, multipart: {
        title: label, school_id: school, document_type_id: 'article',
        ...(school === 'school-math' ? { subject_ids: subjectId } : {}),
        file: { name, mimeType, buffer },
      } })
      expect(response.status()).toBe(201)
      const data = await response.json()
      if (school === 'school-math') expect(data.node_id).toBe('node-math')
      documents.push(data.document_id)
      return data.document_id as string
    }
    const original = pdf(2)
    const id = await upload('Álgebra inicial.pdf', 'application/pdf', original, title)
    const next = await request.post(base + `/documents/${id}/versions`, { headers: professor.headers, multipart: {
      file: { name: 'Álgebra actual.pdf', mimeType: 'application/pdf', buffer: pdf(1) },
    } })
    expect(next.status()).toBe(201)
    const textId = await upload('apuntes.txt', 'text/plain', Buffer.from('apuntes'), 'Apuntes ' + suffix)
    const invalidId = await upload('inválido.pdf', 'application/pdf', Buffer.from('not a PDF'), 'Óptica ' + suffix, 'school-phy')
    await page.goto('/login')
    await page.getByLabel('Correo electrónico').fill(student.email)
    await page.getByLabel('Contraseña', { exact: true }).fill(student.password)
    await page.getByRole('button', { name: 'Iniciar sesión' }).click()
    await expect(page.getByRole('heading', { name: 'Publicaciones recientes' })).toBeVisible()
    await page.getByRole('link', { name: 'Escuelas', exact: true }).click()
    await page.getByRole('link', { name: /Escuela de Ciencias Matemáticas/ }).click()
    await page.getByRole('link', { name: title, exact: true }).first().click()
    await expect(page).toHaveURL(new RegExp(`/subjects/${subjectId}$`))
    await page.getByRole('link', { name: 'Ver todos en el explorador' }).click()
    await page.getByLabel('Buscar por título').fill(title)
    await page.getByLabel('Tipo documental').selectOption('article')
    await expect(page.getByRole('link', { name: title, exact: true })).toBeVisible()
    await page.getByRole('link', { name: title, exact: true }).click()
    await expect(page.getByRole('heading', { name: title, exact: true })).toBeVisible()
    const canvas = page.locator('canvas')
    await expect(canvas).toBeVisible()
    if (process.env.E2E_NODE_FAILURES === '1') {
      nodesAction('stop', [nodes[0]])
    }
    await page.getByRole('button', { name: 'Ver versión 1' }).click()
    await expect(page.getByText('de 2', { exact: false })).toBeVisible()
    await expect.poll(() => canvas.evaluate((element: HTMLCanvasElement) =>
      element.getContext('2d')?.getImageData(30, element.height - 40, 1, 1).data[3] ?? 0)).toBeGreaterThan(0)
    await page.getByRole('button', { name: 'Siguiente', exact: true }).click()
    await expect(page.getByLabel('Número de página')).toHaveValue('2')
    await page.getByRole('button', { name: 'Aumentar zoom' }).click()
    await expect(page.getByText('125%', { exact: true })).toBeVisible()
    const downloaded = page.waitForEvent('download')
    await page.getByRole('button', { name: 'Descargar versión' }).click()
    const file = await downloaded
    expect(file.suggestedFilename()).toBe('Álgebra inicial.pdf')
    const hash = digest(await readFile((await file.path())!))
    expect(hash).toBe(digest(original))
    console.log('SHA-256 versión 1:', hash)
    if (process.env.E2E_NODE_FAILURES === '1') {
      nodesAction('stop', nodes.slice(1))
      const unavailable = page.waitForResponse(response => response.url().endsWith(`/documents/${id}/versions/1/download`))
      await page.reload()
      expect((await unavailable).status()).toBe(503)
      await expect(page.getByRole('alert')).toContainText('no está disponible temporalmente')
      await expect(page.getByRole('button', { name: 'Reintentar', exact: true })).toBeVisible({ timeout: 25000 })
      nodesAction('start', nodes)
      await expect(async () => {
        const response = await request.get(base + `/documents/${id}/versions/1/download`, { headers: student.headers })
        expect(response.ok()).toBeTruthy()
      }).toPass({ timeout: 15000 })
      await page.getByRole('button', { name: 'Reintentar', exact: true }).click()
      await expect(canvas).toBeVisible()
    }
    await page.goto(`/documents/${textId}`)
    await expect(page.getByText(/Vista previa no disponible/)).toBeVisible()
    await page.goto(`/documents/${invalidId}`)
    await expect(page.getByRole('alert')).toContainText('No se pudo visualizar este PDF.')
    await page.setViewportSize({ width: 390, height: 844 })
    await page.goto(`/documents/${id}?version=1`)
    await expect(canvas).toBeVisible()
    await page.screenshot({ path: test.info().outputPath('visor-movil.png') })
    await page.getByRole('button', { name: 'Menú de cuenta' }).click()
    await page.getByRole('menuitem', { name: 'Cerrar sesión' }).click()
    await expect(page).toHaveURL(/\/login$/)
    await expect(canvas).toHaveCount(0)
  } finally {
    if (stopped.size) nodesAction('start', [...stopped])
    for (const id of documents) await request.delete(base + `/documents/${id}`, { headers: admin })
    for (const id of accounts) await request.post(base + `/users/${id}/deactivate`, { headers: admin })
  }
})
