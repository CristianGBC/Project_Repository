"""Preview physical endpoint changes and resolve runtime routes from PostgreSQL."""

import argparse
from ipaddress import IPv4Address
import os
from urllib.parse import urlsplit

from sqlalchemy import func, select

from backend.app.database import create_database_engine, create_session_factory
from backend.app.models import DocumentReplica, PhysicalHost, StorageNode
from backend.app.seed import NODES


def url_key(node_id: str) -> str:
    return node_id.upper().replace("-", "_") + "_URL"


def configure(session, apply: bool, verified: bool) -> None:
    hosts = {}
    node_ports = []
    for expected in NODES:
        node = session.get(StorageNode, expected["id"], with_for_update=apply)
        if node is None or node.physical_host_id != expected["physical_host_id"]:
            raise ValueError(f"Missing node or unexpected placement: {expected['id']}")
        url = urlsplit(os.environ[url_key(node.id)])
        if url.scheme != "http" or url.username or url.password or url.path or url.query or url.fragment:
            raise ValueError(f"Expected an HTTP origin for {node.id}")
        address = str(IPv4Address(url.hostname))
        port = url.port
        if not port:
            raise ValueError(f"Explicit port required for {node.id}")
        if hosts.setdefault(node.physical_host_id, address) != address:
            raise ValueError(f"Inconsistent addresses for {node.physical_host_id}")
        node_ports.append((node, port))
    if len(set(hosts.values())) != len(hosts):
        raise ValueError("Distinct physical hosts must have distinct addresses")
    if len({(node.physical_host_id, port) for node, port in node_ports}) != len(node_ports):
        raise ValueError("Ports must be unique within each host")

    for node, port in node_ports:
        host = session.get(PhysicalHost, node.physical_host_id, with_for_update=apply)
        address = hosts[host.id]
        existing = str(host.ip_address) if host.ip_address else None
        count = session.scalar(
            select(func.count()).select_from(DocumentReplica).where(DocumentReplica.node_id == node.id)
        )
        print(f"{node.id}: {existing}:{node.port} -> {address}:{port}, replicas={count}")
        if apply and count and (existing != address or node.port != port) and not verified:
            raise ValueError(f"Verify the replica files for {node.id} before changing its endpoint")

    agro = session.get(PhysicalHost, "raspberry-4")
    print(f"raspberry-4: {agro.hostname} ({agro.device_type}) -> IdeaPad-5 (ubuntu_pc)")
    if apply:
        for host_id, address in hosts.items():
            session.get(PhysicalHost, host_id).ip_address = address
        agro.hostname = "IdeaPad-5"
        agro.device_type = "ubuntu_pc"
        for node, port in node_ports:
            node.port = port


def runtime_urls(session) -> dict[str, str]:
    node_ids = [node["id"] for node in NODES]
    rows = session.execute(
        select(StorageNode.id, PhysicalHost.ip_address, StorageNode.port).join(PhysicalHost)
        .where(StorageNode.id.in_(node_ids))
    )
    urls = {
        url_key(node_id): f"http://{IPv4Address(str(address))}:{port}"
        for node_id, address, port in rows
        if address is not None
    }
    if any(url_key(node["id"]) not in urls for node in NODES):
        raise ValueError("Physical node addresses are incomplete. Configure the catalog first.")
    return urls


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    commands.add_parser("preview")
    apply = commands.add_parser("apply")
    apply.add_argument("--verified-replicas", action="store_true")
    run = commands.add_parser("run")
    run.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.action == "run" and not args.command:
        parser.error("run requires a command")
    engine = create_database_engine()
    try:
        with create_session_factory(engine).begin() as session:
            if args.action == "run":
                urls = runtime_urls(session)
            else:
                configure(session, args.action == "apply", getattr(args, "verified_replicas", False))
    finally:
        engine.dispose()
    if args.action == "run":
        os.execvpe(args.command[0], args.command, {**os.environ, **urls})


if __name__ == "__main__":
    main()
