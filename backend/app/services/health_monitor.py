"""Independent periodic health monitoring for configured storage nodes."""

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import logging

import httpx
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from backend.app.config import CoordinatorSettings
from backend.app.models import StorageNode
from backend.app.services.node_health import NodeHealthObservation, probe_node

logger = logging.getLogger(__name__)


@dataclass
class NodeHealthCounters:
    consecutive_failures: int = 0
    consecutive_successes: int = 0


class HealthMonitor:
    """Probe all nodes concurrently and persist threshold-based transitions."""

    def __init__(
        self,
        settings: CoordinatorSettings,
        session_factory: sessionmaker[Session],
        client: httpx.AsyncClient,
    ) -> None:
        self.settings = settings
        self.session_factory = session_factory
        self.client = client
        self.counters = {
            node.node_id: NodeHealthCounters() for node in settings.nodes
        }
        self._stop_event = asyncio.Event()

    def stop(self) -> None:
        self._stop_event.set()

    async def check_cycle(self) -> list[NodeHealthObservation]:
        observations = list(
            await asyncio.gather(
                *(
                    probe_node(
                        self.client,
                        node,
                        self.settings.health_check_timeout_seconds,
                    )
                    for node in self.settings.nodes
                )
            )
        )
        self._persist_observations(observations)
        return observations

    def _persist_observations(
        self, observations: list[NodeHealthObservation]
    ) -> None:
        now = datetime.now(timezone.utc)
        try:
            with self.session_factory.begin() as session:
                nodes = {
                    node.id: node
                    for node in session.scalars(
                        select(StorageNode).where(
                            StorageNode.id.in_(self.counters),
                            StorageNode.is_active.is_(True),
                        )
                    )
                }
                for observation in observations:
                    node = nodes.get(observation.node_id)
                    if node is None:
                        continue
                    counters = self.counters[observation.node_id]
                    previous = node.health
                    if observation.healthy:
                        counters.consecutive_failures = 0
                        counters.consecutive_successes += 1
                        node.last_seen_at = now
                        if (
                            node.health != "up"
                            and counters.consecutive_successes
                            >= self.settings.health_recovery_threshold
                        ):
                            node.health = "up"
                    else:
                        counters.consecutive_successes = 0
                        counters.consecutive_failures += 1
                        if (
                            node.health != "down"
                            and counters.consecutive_failures
                            >= self.settings.health_failure_threshold
                        ):
                            node.health = "down"

                    if previous != node.health:
                        node.health_changed_at = now
                        logger.warning(
                            "node health transition node_id=%s physical_host_id=%s "
                            "result=%s duration_ms=%.1f failures=%d successes=%d "
                            "previous=%s current=%s reason=threshold_reached",
                            node.id,
                            node.physical_host_id,
                            observation.result,
                            observation.duration_ms,
                            counters.consecutive_failures,
                            counters.consecutive_successes,
                            previous,
                            node.health,
                        )
                    elif not observation.healthy and node.health != "down":
                        logger.warning(
                            "node health check failed node_id=%s physical_host_id=%s "
                            "result=%s duration_ms=%.1f failures=%d successes=%d "
                            "state=%s",
                            node.id,
                            node.physical_host_id,
                            observation.result,
                            observation.duration_ms,
                            counters.consecutive_failures,
                            counters.consecutive_successes,
                            node.health,
                        )
        except SQLAlchemyError:
            logger.exception("could not persist health monitor cycle")

    async def run(self) -> None:
        logger.info(
            "health monitor started interval_seconds=%.1f timeout_seconds=%.1f "
            "failure_threshold=%d recovery_threshold=%d",
            self.settings.health_check_interval_seconds,
            self.settings.health_check_timeout_seconds,
            self.settings.health_failure_threshold,
            self.settings.health_recovery_threshold,
        )
        try:
            while not self._stop_event.is_set():
                await self.check_cycle()
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=self.settings.health_check_interval_seconds,
                    )
                except TimeoutError:
                    continue
        finally:
            logger.info("health monitor stopped")
