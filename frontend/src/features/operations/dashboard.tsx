import { useState } from 'react'
import { Link } from '@tanstack/react-router'
import { useQuery } from '@tanstack/react-query'
import { api, keys, type School } from '../../api/session'

type Host = { id: string; hostname: string; device_type: string; ip_address: string | null; is_active: boolean }
type Node = { id: string; home_school_id: string; physical_host_id: string; health: string; port?: number; replica_count?: number; is_active: boolean; last_seen_at: string | null; health_changed_at: string | null }
type Replica = { id: string; version_id: string; document_id: string; physical_host_id: string; node_id: string; status: string; last_error: string | null; attempts: number; verified_at: string | null; repair_after?: string | null }
type Job = { id: string; document_id: string; version_id: string; status: string; attempts: number; created_at: string; updated_at: string; next_attempt_at: string | null; last_error: string | null }
type Attempt = { id: string; attempt_number: number; started_at: string; finished_at: string | null; outcome: string; destination_node_id: string | null }
type Version = { id: string; version_number: number; status: string; target_replicas: number; size_bytes: number; sha256: string }
type Document = { id: string; title: string; school_id: string; deleted_at: string | null; current_version_id: string | null; owner_id: string | null; versions: Version[] }
type Deficit = { version_id: string; target_replicas: number; registered_replicas: number; healthy_replicas: number; accessible_replicas: number; operational_deficit: number }
type Counts = Record<string, number>
type Overview = { documents: number; deleted_documents: number; versions: Counts; replicas: Counts; reconciliation: Counts; accessible_replicas: number; versions_in_deficit: number; versions_with_excess: number }
export type Page = 'overview' | 'topology' | 'nodes' | 'node' | 'replicas' | 'document' | 'deficits' | 'reconciliation' | 'job'

export const refreshInterval = 5000
const pageSize = 25
const base = '/admin/operations' as const
const navigation = [
  [base, 'Resumen'], ['/admin/operations/topology', 'Topología'], ['/admin/operations/nodes', 'Nodos'],
  ['/admin/operations/replicas', 'Réplicas'], ['/admin/operations/deficits', 'Déficits'],
  ['/admin/operations/reconciliation', 'Reconciliación'],
] as const
const labels = { overview: 'Monitorización distribuida', topology: 'Topología física', nodes: 'Nodos', node: 'Detalle del nodo', replicas: 'Réplicas', document: 'Replicación del documento', deficits: 'Déficits y reparación', reconciliation: 'Reconciliación de subidas', job: 'Detalle de reconciliación' }
const date = (value?: string | null) => value ? new Date(value).toLocaleString() : '—'
const sum = (data: Counts) => Object.values(data).reduce((total, count) => total + count, 0)

function useOperations<T>(path: string, params?: Record<string, string | number>, enabled = true) {
  return useQuery({
    queryKey: ['operations', path, params],
    queryFn: async ({ signal }) => (await api.get<T>('/operations/' + path, { params, signal })).data,
    enabled, refetchInterval: refreshInterval, retry: false,
  })
}

export function groupHosts(hosts: Host[], nodes: Node[]) {
  return hosts.map(host => {
    const members = nodes.filter(node => node.physical_host_id === host.id)
    const up = members.filter(node => node.is_active && node.health === 'up').length
    const availability = !host.is_active ? 'Deshabilitado' : up === 0 ? 'Sin nodos disponibles' : up === members.length ? 'Disponible' : 'Parcialmente disponible'
    return { host, members, availability }
  })
}

export function replicaSummary(replicas: Replica[], nodes: Node[], hosts: Host[], target: number) {
  const healthy = replicas.filter(replica => replica.status === 'healthy')
  const accessible = healthy.filter(replica => nodes.some(node => node.id === replica.node_id && node.is_active
    && node.health !== 'down' && hosts.some(host => host.id === node.physical_host_id && host.is_active)))
  return { healthy: healthy.length, accessible: accessible.length, deficit: Math.max(0, target - healthy.length),
    operationalDeficit: Math.max(0, target - accessible.length), excess: Math.max(0, replicas.length - target),
    healthyExcess: Math.max(0, healthy.length - target),
    distinctHosts: new Set(replicas.map(replica => replica.physical_host_id)).size,
    affinityViolation: new Set(replicas.map(replica => replica.physical_host_id)).size !== replicas.length }
}

function Table({ headings, rows }: { headings: string[]; rows: React.ReactNode[][] }) {
  return rows.length ? <div className="ops-table"><table><thead><tr>{headings.map(heading => <th key={heading}>{heading}</th>)}</tr></thead>
    <tbody>{rows.map((cells, index) => <tr key={index}>{cells.map((cell, col) => <td key={col}>{cell}</td>)}</tr>)}</tbody></table></div>
    : <p>No hay registros para esta consulta.</p>
}

function VersionPanel({ version, nodes, hosts, school }: { version: Version; nodes: Node[]; hosts: Host[]; school: string }) {
  const query = useOperations<Replica[]>('replicas', { version_id: version.id, limit: 500 })
  const rows = query.data
  const summary = rows && rows.length < 500 && replicaSummary(rows, nodes, hosts, version.target_replicas)
  const preferred = nodes.find(node => node.home_school_id === school)
  return <article className="ops-panel"><h2>Versión {version.version_number} · RF={version.target_replicas}</h2>
    <p>Estado de publicación {version.status} · {version.size_bytes} bytes</p>
    <p>SHA-256 <code className="ops-hash">{version.sha256}</code></p>
    {query.isError && <p role="alert">Falló la consulta de réplicas.</p>}
    {query.isPending && <p>Cargando réplicas…</p>}
    {rows?.length === 500 && <p>La consulta alcanzó el límite del inspector. Usa la tabla paginada de réplicas para ver todos los registros.</p>}
    {rows && summary && <><p>{summary.accessible >= version.target_replicas ? 'RF satisfecho' : 'Déficit de replicación'} · {summary.healthy} saludables · {summary.accessible} accesibles según catálogo</p>
      <p>{rows.length} registros · {summary.distinctHosts} hosts físicos</p>
      <p>Déficit saludable {summary.deficit} · Déficit accesible {summary.operationalDeficit} · Registros excedentes {summary.excess} · Saludables excedentes {summary.healthyExcess}</p>
      {summary.healthy > summary.accessible && <p>Réplicas saludables temporalmente inaccesibles {summary.healthy - summary.accessible}</p>}
      {summary.affinityViolation && <p>Incumplimiento de antiafinidad física</p>}
      {preferred && <p>Nodo preferido {preferred.id}{!rows.some(row => row.node_id === preferred.id && row.status === 'healthy') && ' · sin réplica saludable'}</p>}
      <Table headings={['Host', 'Nodo', 'Escuela', 'Réplica', 'Integridad', 'Salud del nodo', 'Accesible según catálogo', 'Última verificación', 'Próximo intento', 'Error']} rows={hosts.flatMap(host => {
        const copies = rows.filter(row => row.physical_host_id === host.id)
        return copies.length ? copies.map(row => {
          const node = nodes.find(item => item.id === row.node_id)
          return [host.id, row.node_id, node?.home_school_id ?? '—', row.id, row.status, node?.health.toUpperCase() ?? '—',
            row.status === 'healthy' && node?.is_active && node.health !== 'down' && host.is_active ? 'Sí' : 'No',
            date(row.verified_at), date(row.repair_after), row.last_error ?? '—']
        }) : [[host.id, '—', '—', '—', 'Sin réplica', '—', '—', '—', '—', '—']]
      })} />
    </>}
  </article>
}

export function Operations({ page, id, global }: { page: Page; id?: string; global: boolean }) {
  const [filters, setFilters] = useState<Record<string, string>>({})
  const [offset, setOffset] = useState(0)
  const [selectedVersion, setSelectedVersion] = useState('')
  const paged = { limit: pageSize, offset }
  const showNodes = ['overview', 'topology', 'nodes', 'node', 'document'].includes(page)
  const nodes = useOperations<Node[]>('nodes', undefined, showNodes)
  const hosts = useOperations<Host[]>('hosts', undefined, showNodes && global)
  const schools = useQuery({ queryKey: keys.schools, queryFn: async ({ signal }) => (await api.get<School[]>('/catalogs/schools', { signal })).data, enabled: showNodes })
  const overview = useOperations<Overview>('overview', undefined, page === 'overview' && global)
  const repairView = page === 'deficits' && Boolean(filters.status)
  const replicas = useOperations<Replica[]>('replicas', { ...paged, ...filters, ...(page === 'node' && id ? { node_id: id } : {}) }, page === 'replicas' || page === 'node' || repairView)
  const deficits = useOperations<Deficit[]>('deficits', paged, page === 'deficits' && !repairView)
  const jobs = useOperations<Job[]>('reconciliation/jobs', { ...paged, ...filters, ...(page === 'job' && id ? { job_id: id } : {}) }, page === 'job' || page === 'reconciliation')
  const attempts = useOperations<Attempt[]>('reconciliation/jobs/' + id + '/attempts', undefined, page === 'job')
  const doc = useOperations<Document>('documents/' + id, paged, page === 'document')
  const queries = [...(showNodes ? [nodes, ...(global ? [hosts] : [])] : []),
    ...(page === 'overview' && global ? [overview] : []), ...(['replicas', 'node'].includes(page) ? [replicas] : []),
    ...(page === 'deficits' ? [repairView ? replicas : deficits] : []), ...(['job', 'reconciliation'].includes(page) ? [jobs] : []),
    ...(page === 'job' ? [attempts] : []), ...(page === 'document' ? [doc] : [])]
  const updated = Math.max(0, ...queries.map(query => query.dataUpdatedAt))
  const groups = groupHosts(hosts.data ?? [], nodes.data ?? [])
  const data = overview.data
  const version = doc.data?.versions.find(row => row.id === selectedVersion) ?? doc.data?.versions[0]
  const schoolName = (school: string) => schools.data?.find(row => row.id === school)?.name ?? school
  const setFilter = (key: string, value: string) => {
    setFilters(previous => Object.fromEntries(Object.entries({ ...previous, [key]: value }).filter(([, entry]) => entry)))
    setOffset(0)
  }
  const selectedNodes = (nodes.data ?? []).filter(node => (!id || node.id === id)
    && (!filters.host || filters.host === node.physical_host_id)
    && (!filters.school || filters.school === node.home_school_id)
    && (!filters.health || filters.health === node.health)
    && (!filters.active || filters.active === String(node.is_active)))
  const options = (label: string, key: string, values: string[]) => <label>{label}<select value={filters[key] ?? ''} onChange={e => setFilter(key, e.target.value)}><option value="">Todos</option>{values.map(value => <option key={value}>{value}</option>)}</select></label>
  const input = (label: string, key: string) => <label>{label}<input type="search" defaultValue={filters[key] ?? ''} onBlur={e => setFilter(key, e.target.value.trim())} onKeyDown={e => { if (e.key === 'Enter') e.currentTarget.blur() }} /></label>
  const visibleCount = page === 'document' ? doc.data?.versions.length : page === 'reconciliation' ? jobs.data?.length
    : page === 'deficits' && !repairView ? deficits.data?.length : replicas.data?.length
  return <section className="ops-page">
    <h1>{labels[page]}</h1>
    <nav aria-label="Operaciones">{navigation.filter(([to]) => global || to === base || to.endsWith('/nodes')).map(([to, label]) => <Link key={to} to={to}>{label}</Link>)}</nav>
    <div className="ops-toolbar">
      <button type="button" disabled={queries.some(query => query.isFetching)} onClick={() => queries.forEach(query => { void query.refetch() })}>Actualizar</button>
      <span aria-live="polite">{updated ? 'Última actualización ' + date(new Date(updated).toISOString()) : 'Esperando datos'}{queries.some(query => query.isFetching) && ' · Actualizando'}</span>
    </div>
    {queries.some(query => query.isError) && <p role="alert">Error de comunicación con el coordinador. Los datos anteriores pueden estar desactualizados.</p>}
    {page === 'overview' && nodes.data && <div className="ops-grid">
      <article className="ops-panel"><h2>Nodos lógicos</h2><strong>{nodes.data.length}</strong><p>{['up', 'down', 'unknown'].map(status => status.toUpperCase() + ' ' + nodes.data.filter(node => node.health === status).length).join(' · ')}</p></article>
      {global && hosts.data && <article className="ops-panel"><h2>Hosts físicos</h2><strong>{groups.length}</strong><p>Habilitados {hosts.data.filter(host => host.is_active).length}</p>
        {['Disponible', 'Parcialmente disponible', 'Sin nodos disponibles', 'Deshabilitado'].map(state => <div key={state}>{state} · {groups.filter(group => group.availability === state).length}</div>)}</article>}
      {data && <><article className="ops-panel"><h2>Documentos</h2><strong>{data.documents}</strong><p>Eliminados {data.deleted_documents} · Versiones {sum(data.versions)} · Disponibles {data.versions.available ?? 0}</p></article>
        <article className="ops-panel"><h2>Réplicas</h2><strong>{sum(data.replicas)}</strong><p>Saludables {data.replicas.healthy ?? 0} · Accesibles según catálogo {data.accessible_replicas}</p>
          <p>Pendientes {data.replicas.pending ?? 0} · Faltantes {data.replicas.missing ?? 0} · Corruptas {data.replicas.corrupted ?? 0} · Fallidas {data.replicas.failed ?? 0}</p>
          <p>Versiones con déficit {data.versions_in_deficit} · Con registros excedentes {data.versions_with_excess}</p></article>
        <article className="ops-panel"><h2>Reconciliación</h2><p>Pendientes {data.reconciliation.pending ?? 0} · Fallidos {data.reconciliation.failed ?? 0}</p></article></>}
    </div>}
    {page === 'topology' && <><p>Disponibilidad de los nodos según el monitor. Estado físico de los equipos sin verificar.</p><div className="ops-grid">{groups.map(({ host, members, availability }) => <article key={host.id} className="ops-panel">
      <h2>{host.hostname}</h2><p>{host.id} · {host.device_type} · {host.ip_address ?? 'Dirección sin registrar'}</p><p>{availability}</p>
      <ul>{members.map(node => <li key={node.id}><Link to="/admin/operations/nodes/$nodeId" params={{ nodeId: node.id }}>{node.id}</Link> · {schoolName(node.home_school_id)} · {node.port ?? '—'} · {node.health.toUpperCase()} · {node.is_active ? 'Habilitado' : 'Deshabilitado'}</li>)}</ul>
    </article>)}</div></>}
    {['nodes', 'node'].includes(page) && <>
      {page === 'nodes' && <div className="ops-toolbar">{options('Host físico', 'host', [...new Set(nodes.data?.map(node => node.physical_host_id))])}{options('Escuela', 'school', [...new Set(nodes.data?.map(node => node.home_school_id))])}{options('Salud', 'health', ['up', 'down', 'unknown'])}{options('Habilitado', 'active', ['true', 'false'])}</div>}
      {nodes.data && <Table headings={['Nodo', 'Escuela', 'Host', ...(global ? ['Dirección', 'Puerto', 'Réplicas'] : []), 'Administración', 'Salud', 'Última comunicación', 'Cambio de salud']}
        rows={selectedNodes.map(node => [global ? <Link to="/admin/operations/nodes/$nodeId" params={{ nodeId: node.id }}>{node.id}</Link> : node.id,
          schoolName(node.home_school_id), node.physical_host_id, ...(global ? [hosts.data?.find(host => host.id === node.physical_host_id)?.ip_address ?? '—', node.port ?? '—', node.replica_count ?? '—'] : []),
          node.is_active ? 'Habilitado' : 'Deshabilitado', node.health.toUpperCase(), date(node.last_seen_at), date(node.health_changed_at)])} />}
    </>}
    {page === 'replicas' && <div className="ops-toolbar">{input('Documento (UUID)', 'document_id')}{input('Versión (UUID)', 'version_id')}{input('Nodo', 'node_id')}{input('Host', 'host_id')}{options('Integridad', 'status', ['healthy', 'pending', 'missing', 'corrupted', 'failed'])}</div>}
    {(['replicas', 'node'].includes(page) || repairView) && replicas.data && <Table headings={['Documento', 'Versión', 'Nodo', 'Host', 'Integridad', 'Intentos', 'Verificada', 'Próximo intento', 'Error']}
      rows={replicas.data.map(row => [<Link to="/admin/operations/documents/$documentId" params={{ documentId: row.document_id }}>{row.document_id}</Link>,
        row.version_id, row.node_id, row.physical_host_id, row.status, row.attempts, date(row.verified_at), date(row.repair_after), row.last_error ?? '—'])} />}
    {page === 'document' && doc.data && <><h2>{doc.data.title}</h2><p>{doc.data.id} · {schoolName(doc.data.school_id)} · Propietario {doc.data.owner_id ?? '—'}</p>
      <p>Versión actual {doc.data.current_version_id ?? 'Sin publicar'}</p>
      {doc.data.deleted_at && <p>Documento eliminado lógicamente · {date(doc.data.deleted_at)}</p>}
      {version && <><label>Versión <select value={version.id} onChange={event => setSelectedVersion(event.target.value)}>
        {doc.data.versions.map(row => <option key={row.id} value={row.id}>Versión {row.version_number} · {row.status}{row.id === doc.data?.current_version_id ? ' · actual' : ''}</option>)}
      </select></label>
        {nodes.data && hosts.data && <VersionPanel key={version.id} version={version} nodes={nodes.data} hosts={hosts.data} school={doc.data.school_id} />}
      </>}</>}
    {page === 'deficits' && <><p>Déficits calculados con el catálogo. Disponibilidad actual del reparador e historial de trabajos sin verificar.</p>
      <div className="ops-toolbar"><label>Vista <select value={filters.status ?? ''} onChange={event => setFilter('status', event.target.value)}>
        <option value="">Versiones con déficit</option><option value="missing">Réplicas faltantes</option>
        <option value="corrupted">Réplicas corruptas</option><option value="pending">Réplicas pendientes</option><option value="failed">Réplicas fallidas</option>
      </select></label></div>
      {!repairView && deficits.data && <Table headings={['Versión', 'RF objetivo', 'Registros', 'Saludables', 'Accesibles', 'Déficit operativo']}
        rows={deficits.data.map(row => [row.version_id, row.target_replicas, row.registered_replicas, row.healthy_replicas, row.accessible_replicas, row.operational_deficit])} />}</>}
    {page === 'reconciliation' && <div className="ops-toolbar">{input('Trabajo (UUID)', 'job_id')}{input('Documento (UUID)', 'document_id')}{options('Estado', 'status', ['pending', 'in_progress', 'retry_wait', 'completed', 'failed', 'cancelled'])}</div>}
    {['job', 'reconciliation'].includes(page) && jobs.data && <Table headings={['Trabajo', 'Documento', 'Versión', 'Estado', 'Intentos', 'Creado', 'Actualizado', 'Próximo intento', 'Último error']}
      rows={jobs.data.map(job => [<Link to="/admin/operations/reconciliation/$jobId" params={{ jobId: job.id }}>{job.id}</Link>,
        <Link to="/admin/operations/documents/$documentId" params={{ documentId: job.document_id }}>{job.document_id}</Link>, job.version_id, job.status,
        job.attempts, date(job.created_at), date(job.updated_at), date(job.next_attempt_at), job.last_error ?? '—'])} />}
    {page === 'job' && attempts.data && <Table headings={['Intento', 'Inicio', 'Fin', 'Resultado', 'Nodo de destino']}
      rows={attempts.data.map(row => [row.attempt_number, date(row.started_at), date(row.finished_at), row.outcome, row.destination_node_id ?? '—'])} />}
    {['node', 'replicas', 'document', 'reconciliation', 'deficits'].includes(page) && <div className="ops-toolbar">
      <button type="button" disabled={!offset} onClick={() => setOffset(offset - pageSize)}>Anterior</button>
      <span>Página {1 + offset / pageSize}</span>
      <button type="button" disabled={visibleCount !== pageSize} onClick={() => setOffset(offset + pageSize)}>Siguiente</button>
    </div>}
  </section>
}
