import { useEffect, useRef, useState } from 'react'
import { getDocument, GlobalWorkerOptions, type PDFDocumentProxy } from 'pdfjs-dist'
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url'
import { Button, Alert, Skeleton } from '../../components/ui'
import { clampZoom } from '../../api/academic'

GlobalWorkerOptions.workerSrc = workerUrl

export default function Pdf({ blob }: { blob: Blob }) {
  const [document, setDocument] = useState<PDFDocumentProxy>()
  const [page, setPage] = useState(1)
  const [zoom, setZoom] = useState<number | null>(null)
  const [width, setWidth] = useState(0)
  const [error, setError] = useState('')
  const area = useRef<HTMLDivElement>(null)
  const canvas = useRef<HTMLCanvasElement>(null)
  const frame = useRef<HTMLDivElement>(null)
  useEffect(() => {
    let active = true
    let task: ReturnType<typeof getDocument> | undefined
    void blob.arrayBuffer().then(bytes => {
      if (!active) return
      task = getDocument({ data: new Uint8Array(bytes), isEvalSupported: false })
      task.onPassword = () => { if (active) setError('La vista previa no admite PDF protegidos con contraseña.') }
      return task.promise.then(pdf => { if (active) setDocument(pdf) })
    }).catch(() => { if (active) setError('No se pudo visualizar este PDF.') })
    return () => { active = false; void task?.destroy().catch(() => {}) }
  }, [blob])
  useEffect(() => {
    if (!area.current) return
    const observer = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width))
    observer.observe(area.current)
    return () => observer.disconnect()
  }, [])
  useEffect(() => {
    const target = canvas.current
    if (!document || !target || !width) return
    let active = true
    let task: ReturnType<Awaited<ReturnType<PDFDocumentProxy['getPage']>>['render']> | undefined
    void document.getPage(page).then(pdfPage => {
      if (!active) return
      const base = pdfPage.getViewport({ scale: 1 })
      const scale = zoom ?? Math.max(.1, (width - 16) / base.width)
      const viewport = pdfPage.getViewport({ scale })
      const density = Math.min(2, window.devicePixelRatio || 1, 4096 / Math.max(viewport.width, viewport.height))
      target.width = Math.max(1, Math.floor(viewport.width * density))
      target.height = Math.max(1, Math.floor(viewport.height * density))
      target.style.width = viewport.width + 'px'
      target.style.height = viewport.height + 'px'
      task = pdfPage.render({ canvas: target, viewport, transform: [density, 0, 0, density, 0, 0] })
      return task.promise
    }).catch(() => { if (active) setError('No se pudo dibujar la página del PDF.') })
    return () => { active = false; task?.cancel() }
  }, [document, page, zoom, width])
  return <div ref={frame} className="pdf-frame">
    {error ? <Alert>{error}</Alert> : !document ? <Skeleton /> : <div className="pdf-controls">
      <Button variant="ghost" disabled={page === 1} onClick={() => setPage(page - 1)}>Anterior</Button>
      <form className="pdf-page-form" onSubmit={event => {
        event.preventDefault()
        const number = Number(new FormData(event.currentTarget).get('page'))
        if (Number.isInteger(number) && number >= 1 && number <= document.numPages) setPage(number)
      }}>
        <label>Página <input key={page} aria-label="Número de página" name="page" type="number" required min={1} max={document.numPages} defaultValue={page} /> de {document.numPages}</label>
        <Button variant="ghost">Ir</Button>
      </form>
      <Button variant="ghost" disabled={page === document.numPages} onClick={() => setPage(page + 1)}>Siguiente</Button>
      <Button variant="ghost" aria-label="Disminuir zoom" disabled={zoom === .5} onClick={() => setZoom(clampZoom((zoom ?? 1) - .25))}>−</Button>
      <Button variant="ghost" onClick={() => setZoom(1)}>Restablecer zoom</Button>
      <Button variant="ghost" aria-label="Aumentar zoom" disabled={zoom === 2.5} onClick={() => setZoom(clampZoom((zoom ?? 1) + .25))}>+</Button>
      <Button variant="ghost" onClick={() => setZoom(null)}>Ajustar al ancho</Button>
      {window.document.fullscreenEnabled && <Button variant="ghost" onClick={() => {
        const operation = window.document.fullscreenElement ? window.document.exitFullscreen() : frame.current?.requestFullscreen()
        void operation?.catch(() => {})
      }}>Pantalla completa</Button>}
      <span role="status">{zoom === null ? 'Ancho disponible' : Math.round(zoom * 100) + '%'}</span>
    </div>}
    <div className="pdf-area" ref={area} hidden={!document || !!error}>
      <canvas ref={canvas} aria-label={`Página ${page} del PDF`} />
    </div>
  </div>
}
