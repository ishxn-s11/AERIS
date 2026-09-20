"""IoT connection onboarding: broker health, new device connections, recipes.

Three jobs, in the order an engineer does them:

  1. Verify the transport we are already on (`GET /overview`, `POST /broker/test`).
  2. Add a node (`POST /devices`) and get back a copy-paste connection recipe
     for the transport it was onboarded on.
  3. Audit or revoke what is connected (`GET /overview` lists every node with
     its protocol, endpoint, liveness and credential state).
"""
from __future__ import annotations

import logging

from pydantic import BaseModel, Field, field_validator
from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_roles
from app.config.settings import settings
from app.database.session import get_db
from app.models.models import Device, DeviceProtocol, Factory, User, UserRole, Zone
from app.schemas.schemas import (
    BrokerProbeRequest,
    ConnectionOverview,
    DeviceConnectionOut,
    DeviceConnectionRecipe,
    DeviceConnectionRequest,
)
from app.services import connection_probe, device_auth_service, telemetry_service

logger = logging.getLogger("aeris.api.connections")

connections_router = APIRouter(prefix="/connections", tags=["connections"])

# Sample frame offered in the HTTP recipe. Values sit in the "normal" band so a
# copy-pasted test does not raise a false alarm in the demo plant.
SAMPLE_FRAME = {
    "device_id": "<DEVICE_ID>",
    "factory_id": "<FACTORY_ID>",
    "temperature": 27.5,
    "humidity": 44.0,
    "co": 12.0,
    "methane": 180.0,
    "smoke": 60.0,
    "flame": False,
}


def _connection_rows(db: Session) -> list[DeviceConnectionOut]:
    """Every node as a connection record — one query per table, not per row."""
    devices = db.scalars(select(Device).order_by(Device.id)).all()
    zones = {z.id: z for z in db.scalars(select(Zone)).all()}
    factories = {f.id: f for f in db.scalars(select(Factory)).all()}

    rows: list[DeviceConnectionOut] = []
    for device in devices:
        zone = zones.get(device.zone_id)
        factory = factories.get(zone.factory_id) if zone else None
        credential = device.credential
        is_mqtt = device.protocol == DeviceProtocol.MQTT.value
        rows.append(
            DeviceConnectionOut(
                id=device.id,
                device_id=device.device_id,
                protocol=device.protocol,
                status=device.status,
                enabled=device.enabled,
                last_seen=device.last_seen,
                firmware_version=device.firmware_version,
                zone_id=device.zone_id,
                zone_name=zone.name if zone else None,
                factory_id=factory.id if factory else None,
                factory_name=factory.name if factory else None,
                # The endpoint a node uses depends on its transport.
                endpoint=device.mqtt_topic if is_mqtt else "/api/telemetry",
                username=credential.username if credential else None,
                has_credential=credential is not None,
                credential_enabled=bool(credential and credential.enabled),
                issued_at=credential.issued_at if credential else None,
                rotated_at=credential.rotated_at if credential else None,
                last_authenticated_at=credential.last_authenticated_at if credential else None,
            )
        )
    return rows


@connections_router.get("/overview", response_model=ConnectionOverview)
def connection_overview(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> ConnectionOverview:
    """Broker state, transport summary and the full connection register."""
    broker = connection_probe.active_broker_report()
    rows = _connection_rows(db)

    by_protocol: dict[str, int] = {}
    for row in rows:
        by_protocol[row.protocol] = by_protocol.get(row.protocol, 0) + 1

    telemetry_url = f"{settings.api_base_url.rstrip('/')}/api/telemetry"
    return ConnectionOverview(
        broker=broker,
        http_ingest={
            "endpoint": "/api/telemetry",
            "url": telemetry_url,
            "method": "POST",
            "auth_required": settings.device_auth_required,
            "auth_header": "auth_token in the JSON body",
            "note": (
                "HTTP nodes authenticate with the same issued credential, passed as "
                "auth_token. Set DEVICE_AUTH_REQUIRED=true to enforce it."
            ),
        },
        devices=rows,
        total=len(rows),
        by_protocol=by_protocol,
        online=sum(1 for r in rows if r.status == "online"),
        revoked=sum(1 for r in rows if not r.enabled),
        unprovisioned=sum(1 for r in rows if not r.has_credential),
        auto_provision=settings.device_auto_provision,
    )


@connections_router.post("/broker/test")
def test_broker_connection(
    payload: BrokerProbeRequest,
    _: User = Depends(require_roles(UserRole.ADMIN, UserRole.SAFETY_OFFICER)),
) -> dict:
    """Probe a broker and report the first failing stage.

    The target may be the configured broker or a candidate one, so a new site
    can be validated before its details are put into the environment.
    """
    host = payload.host or settings.mqtt_host
    port = payload.port or settings.mqtt_port
    username = payload.username if payload.username is not None else settings.mqtt_username
    password = payload.password if payload.password is not None else settings.mqtt_password

    result = connection_probe.probe_mqtt_broker(
        host,
        port,
        username=username,
        password=password,
        use_tls=payload.use_tls,
        timeout=payload.timeout_seconds,
    )
    # Never echo a secret back, even on failure.
    logger.info(
        "Broker probe %s:%s -> ok=%s (stage=%s)", host, port, result.ok, result.stage
    )
    return result.to_dict()


def _recipe_for(
    device: Device,
    zone: Zone,
    factory: Factory | None,
    plaintext: str | None,
) -> DeviceConnectionRecipe:
    """Build the copy-paste connection details for a freshly registered node."""
    factory_name = factory.name if factory else "FACTORY_01"
    http_url = f"{settings.api_base_url.rstrip('/')}/api/telemetry"
    sample = {
        **SAMPLE_FRAME,
        "device_id": device.device_id,
        "factory_id": factory_name,
    }
    if plaintext:
        sample["auth_token"] = plaintext

    is_mqtt = device.protocol == DeviceProtocol.MQTT.value
    topic = device.mqtt_topic if is_mqtt else None

    return DeviceConnectionRecipe(
        device_id=device.device_id,
        protocol=device.protocol,
        factory_name=factory_name,
        zone_name=zone.name,
        username=device.credential.username if device.credential else None,
        password=plaintext,
        mqtt_host=settings.mqtt_host,
        mqtt_port=settings.mqtt_port,
        mqtt_topic=topic,
        http_url=http_url,
        sample_payload=sample if not is_mqtt or plaintext else {**SAMPLE_FRAME, "device_id": device.device_id},
        publish_command=(
            f"mosquitto_pub -h {settings.mqtt_host} -p {settings.mqtt_port}"
            f" -u {device.credential.username if device.credential else '<username>'}"
            f" -P '<password>' -t {topic} -m '<json frame>'"
            if is_mqtt
            else None
        ),
        curl_command=(
            f"curl -X POST {http_url} -H 'Content-Type: application/json' -d '<json frame>'"
            if not is_mqtt
            else None
        ),
        acl_line=(
            f"user {device.credential.username}\ntopic write {topic}"
            if is_mqtt and device.credential and topic
            else None
        ),
        warning=(
            "Store this password now — it is shown once and is not recoverable. "
            + (
                "Run backend/scripts/sync_mqtt_credentials.py and reload the broker so "
                "the new username/ACL is accepted."
                if is_mqtt
                else "Send it as auth_token on every frame once DEVICE_AUTH_REQUIRED is on."
            )
        ) if plaintext else "Existing credential retained — rotate to issue a new secret.",
    )


@connections_router.post(
    "/devices", response_model=DeviceConnectionRecipe, status_code=status.HTTP_201_CREATED
)
def add_device_connection(
    payload: DeviceConnectionRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.ADMIN, UserRole.SAFETY_OFFICER)),
) -> DeviceConnectionRecipe:
    """Register a node and return its connection recipe.

    Idempotent: registering an existing node updates it (zone, protocol) rather
    than wiping the credential already flashed onto the hardware. Supply
    `password` to rotate deliberately.
    """
    try:
        device, plaintext = device_auth_service.register_device(
            db,
            device_id=payload.device_id,
            zone_id=payload.zone_id,
            firmware_version=payload.firmware_version,
            mqtt_topic=payload.mqtt_topic,
            password=payload.password,
            protocol=payload.protocol,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from None

    zone = db.get(Zone, device.zone_id)
    factory = db.get(Factory, zone.factory_id) if zone else None
    logger.info(
        "%s added %s connection '%s' in zone %s",
        user.email, payload.protocol, device.device_id, zone.name if zone else "?",
    )
    return _recipe_for(device, zone, factory, plaintext)


@connections_router.get("/devices/{device_db_id}/recipe", response_model=DeviceConnectionRecipe)
def device_recipe(
    device_db_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> DeviceConnectionRecipe:
    """Re-display a node's connection details (without its password)."""
    device = db.get(Device, device_db_id)
    if device is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Device not found")
    zone = db.get(Zone, device.zone_id)
    factory = db.get(Factory, zone.factory_id) if zone else None
    return _recipe_for(device, zone, factory, plaintext=None)


@connections_router.post("/devices/{device_db_id}/verify")
def verify_device_connection(
    device_db_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> dict:
    """Check whether a node is actually delivering frames.

    Reports the last stored reading and, for MQTT nodes, whether the broker is
    currently reachable — the two facts that separate "configured" from
    "working".
    """
    device = db.get(Device, device_db_id)
    if device is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Device not found")

    latest = telemetry_service.latest_reading(db, device.id)
    broker = connection_probe.active_broker_report() if device.protocol == "mqtt" else None

    if not device.enabled:
        verdict = "revoked — frames from this node are rejected on arrival"
    elif latest is None:
        verdict = "no frames received yet"
    else:
        verdict = "delivering telemetry"

    return {
        "device_id": device.device_id,
        "protocol": device.protocol,
        "status": device.status,
        "enabled": device.enabled,
        "last_seen": device.last_seen,
        "last_reading_at": latest.timestamp if latest else None,
        "broker_connected": broker["connected"] if broker else None,
        "verdict": verdict,
    }


# ---------------------------------------------------------------------------
# CSV import / export
# ---------------------------------------------------------------------------

import csv
import io
from fastapi.responses import StreamingResponse


@connections_router.get("/devices/export", response_class=StreamingResponse)
def export_connections_csv(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> StreamingResponse:
    """Export every device's connection record as a CSV file for audit.

    Columns mirror the register table on the Connections page: device id,
    protocol, zone, factory, status, credential state, last seen.
    """
    rows = _connection_rows(db)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "device_id", "protocol", "zone", "factory", "status", "enabled",
        "username", "has_credential", "credential_enabled",
        "firmware_version", "endpoint", "last_seen", "last_authenticated_at",
    ])
    for row in rows:
        writer.writerow([
            row.device_id, row.protocol, row.zone_name or "",
            row.factory_name or "", row.status, row.enabled,
            row.username or "", row.has_credential, row.credential_enabled,
            row.firmware_version, row.endpoint or "", row.last_seen or "",
            row.last_authenticated_at or "",
        ])
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=aeris_connections.csv"},
    )


class CsvImportRow(BaseModel):
    """One row of an uploaded CSV — validated before touching the DB."""
    device_id: str = Field(min_length=1, max_length=64)
    zone_id: int | None = None
    zone_name: str | None = None
    protocol: str = "mqtt"
    firmware_version: str = "unknown"
    password: str | None = None

    @field_validator("protocol")
    @classmethod
    def _known(cls, v: str) -> str:
        if v not in {"mqtt", "http"}:
            raise ValueError("protocol must be 'mqtt' or 'http'")
        return v


class CsvImportResult(BaseModel):
    """Summary of a bulk CSV import."""
    total_rows: int
    registered: int
    skipped: int
    errors: list[dict]


def _resolve_zone_id(db: Session, row: CsvImportRow) -> int | None:
    """Resolve zone_id from either an explicit id or a zone name."""
    if row.zone_id is not None:
        return row.zone_id
    if row.zone_name:
        zone = db.scalar(select(Zone).where(Zone.name == row.zone_name))
        return zone.id if zone else None
    return None


@connections_router.post("/devices/import", response_model=CsvImportResult)
async def import_connections_csv(
    file: UploadFile,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.ADMIN, UserRole.SAFETY_OFFICER)),
) -> CsvImportResult:
    """Bulk-register devices from a CSV upload.

    Expected columns (header row required): device_id, zone_id OR zone_name,
    protocol, firmware_version, password.  Rows that fail validation are
    skipped with an error note; valid rows are registered idempotently.
    """
    raw = await file.read()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")

    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "CSV has no header row")

    results: list[dict] = []
    registered = 0
    skipped = 0

    for i, raw_row in enumerate(reader, start=2):  # row 1 = header
        try:
            row = CsvImportRow(**{k.strip(): v.strip() for k, v in raw_row.items() if v})
        except Exception as exc:
            results.append({"row": i, "error": str(exc)})
            skipped += 1
            continue

        zone_id = _resolve_zone_id(db, row)
        if zone_id is None:
            results.append({"row": i, "error": f"zone '{row.zone_name or row.zone_id}' not found"})
            skipped += 1
            continue

        try:
            device_auth_service.register_device(
                db,
                device_id=row.device_id,
                zone_id=zone_id,
                firmware_version=row.firmware_version,
                protocol=row.protocol,
                password=row.password,
            )
            registered += 1
        except Exception as exc:
            results.append({"row": i, "error": str(exc)})
            skipped += 1

    logger.info(
        "CSV import by %s: %d registered, %d skipped (out of %d rows)",
        user.email, registered, skipped, registered + skipped,
    )
    return CsvImportResult(
        total_rows=registered + skipped,
        registered=registered,
        skipped=skipped,
        errors=results,
    )


# ---------------------------------------------------------------------------
# HTTP gateway simulator controls
# ---------------------------------------------------------------------------

@connections_router.get("/simulator")
def simulator_status(
    _: User = Depends(get_current_user),
) -> dict:
    """Report whether the HTTP gateway simulator is running."""
    from app.services.http_gateway import gateway_simulator  # noqa: PLC0415
    return {
        "running": gateway_simulator.running,
        "note": (
            "The HTTP simulator POSTs telemetry for every HTTP-registered "
            "device every 4-7 seconds.  It is a prototype convenience."
        ),
    }


@connections_router.post("/simulator/start")
def simulator_start(
    user: User = Depends(require_roles(UserRole.ADMIN, UserRole.SAFETY_OFFICER)),
) -> dict:
    """Start the HTTP gateway simulator (admin / safety officer only)."""
    from app.services.http_gateway import gateway_simulator  # noqa: PLC0415
    gateway_simulator.start()
    logger.info("HTTP gateway simulator started by %s", user.email)
    return {"running": True}


@connections_router.post("/simulator/stop")
def simulator_stop(
    user: User = Depends(require_roles(UserRole.ADMIN, UserRole.SAFETY_OFFICER)),
) -> dict:
    """Stop the HTTP gateway simulator."""
    from app.services.http_gateway import gateway_simulator  # noqa: PLC0415
    gateway_simulator.stop()
    logger.info("HTTP gateway simulator stopped by %s", user.email)
    return {"running": False}
