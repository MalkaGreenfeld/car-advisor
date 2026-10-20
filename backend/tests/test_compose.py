import posixpath
import re
from pathlib import Path
from typing import Any, NamedTuple

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = ROOT / "compose.yaml"
ENV_EXAMPLE = ROOT / ".env.example"
BACKEND_DOCKERFILE = ROOT / "backend" / "Dockerfile"
NGINX_CONF = ROOT / "frontend" / "nginx.conf"

LOOPBACK = "127.0.0.1"
SERVICES = ("db", "backend", "frontend")
DURATION_UNITS = {"h": 3600.0, "m": 60.0, "s": 1.0, "ms": 0.001, "us": 0.000001}


class Port(NamedTuple):
    host_ip: str | None  # None: the entry names no address, so Docker binds every interface
    published: str | None
    target: str


class Mount(NamedTuple):
    source: str
    target: str
    read_only: bool


def parse_port(entry: str | int | dict[str, Any]) -> Port:
    """Read one `ports` entry, written in the short or the long syntax."""
    if isinstance(entry, dict):
        published = entry.get("published")
        return Port(
            entry.get("host_ip"),
            None if published is None else str(published),
            str(entry["target"]),
        )
    spec = str(entry).partition("/")[0]  # drop /tcp or /udp
    host_ip = None
    if spec.startswith("["):  # an IPv6 address, as in [::1]:8000:8000
        host_ip, _, spec = spec[1:].partition("]")
        spec = spec.removeprefix(":")
    fields = spec.split(":")
    target = fields.pop()
    published = (fields.pop() or None) if fields else None
    if fields and host_ip is None:
        host_ip = fields.pop()
    return Port(host_ip, published, target)


def parse_mount(entry: str | dict[str, Any]) -> Mount:
    """Read one `volumes` entry, written in the short or the long syntax."""
    if isinstance(entry, dict):
        source = posixpath.normpath(entry["source"])
        return Mount(source, entry["target"], bool(entry.get("read_only")))
    source, target, *modes = entry.split(":")
    read_only = "ro" in (modes[0].split(",") if modes else [])
    return Mount(posixpath.normpath(source), target, read_only)


def dependencies(service: dict[str, Any]) -> dict[str, str]:
    """Map each service that `service` waits for to the condition it waits for."""
    depends_on = service.get("depends_on", {})
    if isinstance(depends_on, list):  # the short syntax only waits for the container to start
        return dict.fromkeys(depends_on, "service_started")
    return {
        name: options.get("condition", "service_started") for name, options in depends_on.items()
    }


def published_ports(services: dict[str, Any]) -> dict[str, list[Port]]:
    return {
        name: [parse_port(entry) for entry in service.get("ports", [])]
        for name, service in services.items()
    }


def seconds(duration: str) -> float:
    """Read a Compose duration such as `30s` or `5m0s`."""
    parts = re.findall(r"(\d+)(ms|us|h|m|s)", duration)
    return sum(int(number) * DURATION_UNITS[unit] for number, unit in parts)


def env_example_values() -> dict[str, str]:
    lines = ENV_EXAMPLE.read_text(encoding="utf-8").splitlines()
    return dict(line.split("=", 1) for line in lines if line and not line.startswith("#"))


@pytest.fixture(scope="module")
def services() -> dict[str, Any]:
    return yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8"))["services"]


@pytest.mark.parametrize(
    ("entry", "expected"),
    [
        ("127.0.0.1:8000:8000", Port("127.0.0.1", "8000", "8000")),
        ("127.0.0.1:8080:80/tcp", Port("127.0.0.1", "8080", "80")),
        ("[::1]:8000:8000", Port("::1", "8000", "8000")),
        ("0.0.0.0:8000:8000", Port("0.0.0.0", "8000", "8000")),
        ("8000:8000", Port(None, "8000", "8000")),
        ("8000", Port(None, None, "8000")),
        (8000, Port(None, None, "8000")),
        ({"target": 80, "published": 8080, "host_ip": LOOPBACK}, Port(LOOPBACK, "8080", "80")),
        ({"target": 80, "published": 8080}, Port(None, "8080", "80")),
    ],
)
def test_ports_are_read_in_the_short_and_the_long_syntax(
    entry: str | int | dict[str, Any], expected: Port
) -> None:
    assert parse_port(entry) == expected


def test_compose_defines_the_three_services(services: dict[str, Any]) -> None:
    assert set(services) == set(SERVICES)


def test_the_services_publish_the_host_ports_the_urls_use(services: dict[str, Any]) -> None:
    published = {
        name: [port.published for port in ports]
        for name, ports in published_ports(services).items()
    }

    assert published == {"db": ["1433"], "backend": ["8000"], "frontend": ["8080"]}


def test_every_published_port_binds_to_loopback_only(services: dict[str, Any]) -> None:
    # No address, 0.0.0.0 and :: all reach the network (Req 14.16).
    open_to_the_network = [
        (name, port)
        for name, ports in published_ports(services).items()
        for port in ports
        if port.host_ip != LOOPBACK
    ]

    assert open_to_the_network == []


def test_backend_waits_for_a_healthy_db(services: dict[str, Any]) -> None:
    assert dependencies(services["backend"]) == {"db": "service_healthy"}


def test_frontend_waits_for_a_healthy_backend(services: dict[str, Any]) -> None:
    assert dependencies(services["frontend"]) == {"backend": "service_healthy"}


@pytest.mark.parametrize("name", SERVICES)
def test_every_service_has_a_healthcheck(services: dict[str, Any], name: str) -> None:
    assert services[name].get("healthcheck", {}).get("test")


def test_no_service_restarts_after_a_failure(services: dict[str, Any]) -> None:
    # A service that fails at startup has to exit, so `docker compose up` shows why (Req 14.15).
    restarting = [
        name
        for name, service in services.items()
        if service.get("restart", "no") not in ("no", False)
        or "restart_policy" in service.get("deploy", {})
    ]

    assert restarting == []


def test_the_db_healthcheck_gives_up_within_120_seconds(services: dict[str, Any]) -> None:
    # Failures in start_period do not count, then `retries` failed checks mark db unhealthy.
    check = services["db"]["healthcheck"]

    budget = seconds(check["start_period"]) + check["retries"] * seconds(check["interval"])

    assert budget <= 120


def test_the_backend_healthcheck_waits_out_the_first_import(services: dict[str, Any]) -> None:
    # The first start imports the dataset, 1.5 to 3.5 minutes, before the app accepts a connection.
    assert seconds(services["backend"]["healthcheck"]["start_period"]) >= 300


@pytest.mark.parametrize("folder", ["config", "data"])
def test_backend_mounts_the_folder_read_only(services: dict[str, Any], folder: str) -> None:
    mounts = {mount.source: mount for mount in map(parse_mount, services["backend"]["volumes"])}

    assert mounts.get(folder) == Mount(folder, f"/app/{folder}", read_only=True)


def test_the_paths_in_env_example_resolve_to_the_read_only_mounts(
    services: dict[str, Any],
) -> None:
    dockerfile = BACKEND_DOCKERFILE.read_text(encoding="utf-8")
    workdir = re.search(r"^WORKDIR\s+(\S+)", dockerfile, re.MULTILINE)
    assert workdir
    mounts = map(parse_mount, services["backend"]["volumes"])
    read_only_targets = [mount.target for mount in mounts if mount.read_only]
    paths = {name: value for name, value in env_example_values().items() if name.endswith("_PATH")}
    resolved = {
        name: posixpath.normpath(posixpath.join(workdir.group(1), value))
        for name, value in paths.items()
    }

    unmounted = {
        name: path
        for name, path in resolved.items()
        if not any(path.startswith(f"{target}/") for target in read_only_targets)
    }

    assert resolved
    assert unmounted == {}


def test_nginx_listens_on_the_port_that_compose_maps_for_the_frontend(
    services: dict[str, Any],
) -> None:
    listen = re.findall(
        r"^\s*listen\s+(\d+)[^;]*;", NGINX_CONF.read_text(encoding="utf-8"), re.MULTILINE
    )
    mapped = [port.target for port in published_ports(services)["frontend"]]

    assert listen == mapped


@pytest.mark.parametrize("name", ["backend", "frontend"])
def test_the_container_port_agrees_in_compose_the_dockerfile_and_the_healthcheck(
    services: dict[str, Any], name: str
) -> None:
    dockerfile = (ROOT / name / "Dockerfile").read_text(encoding="utf-8")
    mapped = [port.target for port in published_ports(services)[name]]
    exposed = re.findall(r"^EXPOSE\s+(\d+)\s*$", dockerfile, re.MULTILINE)
    checked = re.findall(r"127\.0\.0\.1:(\d+)", " ".join(services[name]["healthcheck"]["test"]))

    assert mapped == exposed == checked
