import { expect, test } from 'vitest'
import { groupHosts, refreshInterval, replicaSummary } from './dashboard'

const host = { id: 'pi', hostname: 'Lab', device_type: 'pi', ip_address: null, is_active: true }
const node = { id: 'bio', physical_host_id: 'pi', home_school_id: 'biology', is_active: true, health: 'up', last_seen_at: null, health_changed_at: null }
const replica = { id: 'copy', version_id: 'v', document_id: 'd', physical_host_id: 'pi', node_id: 'bio', status: 'healthy', attempts: 0, verified_at: null, last_error: null }

test('agrupa nodos del catálogo y distingue disponibilidad parcial', () => {
  const groups = groupHosts([host], [node, { ...node, id: 'phy', health: 'down' }, { ...node, id: 'other', health: 'unknown' }])
  expect(groups[0].members).toHaveLength(3)
  expect(groups[0].availability).toBe('Parcialmente disponible')
  expect(refreshInterval).toBe(5000)
})

test('contrasta salud, acceso, RF, exceso y antiafinidad', () => {
  expect(replicaSummary([replica], [node], [host], 1)).toMatchObject({ healthy: 1, accessible: 1, deficit: 0 })
  expect(replicaSummary([replica], [{ ...node, health: 'down' }], [host], 3)).toMatchObject({ healthy: 1, accessible: 0, deficit: 2, operationalDeficit: 3 })
  expect(replicaSummary([replica, { ...replica, id: 'duplicate' }], [node], [host], 1)).toMatchObject({ excess: 1, healthyExcess: 1, affinityViolation: true })
})
