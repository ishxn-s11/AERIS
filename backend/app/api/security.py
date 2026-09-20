"""Security & provisioning endpoints: device credentials and notification channels.

Every endpoint here is admin-only except the read-only status views, which
safety officers may need when auditing why a notification did or did not fire.
Plaintext credentials are returned exactly once (register / rotate) and are
never logged.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_roles
from app.database.session import get_db
from app.models.models import Device, User, UserRole
from app.schemas.schemas import (
    DeviceCredentialIssued,
    DeviceCredentialOut,
    DeviceRegisterRequest,
)
from app.services import device_auth_service
from app.services.notifications import notification_status

logger = logging.getLogger("aeris.api.security")

security_router = APIRouter(prefix="/security", tags=["security"])


@security_router.get("/notifications")
def notifications_status(_: User = Depends(get_current_user)) -> dict:
    """Which notification channels exist, whether they are enabled, and delivery counters."""
    return notification_status()


@security_router.get("/device-credentials", response_model=list[DeviceCredentialOut])
def list_device_credentials(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Credential status for every node — hashes and plaintext are never exposed."""
    devices = db.scalars(select(Device).order_by(Device.id)).all()
    return [DeviceCredentialOut(**device_auth_service.credential_status(db, d)) for d in devices]


@security_router.post("/devices/register", response_model=DeviceCredentialIssued, status_code=201)
def register_device(
    payload: DeviceRegisterRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.ADMIN)),
):
    """Pre-register a node and issue its broker credential (admin only)."""
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

    logger.info("Admin %s registered device %s", user.email, device.device_id)
    if plaintext is None:
        # Already provisioned and no password supplied: the existing credential
        # stands. Say so instead of pretending a new secret was created.
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Device '{device.device_id}' already has a credential — rotate it to get a new secret",
        )
    return DeviceCredentialIssued(
        device_id=device.device_id,
        username=device.credential.username,
        password=plaintext,
        mqtt_topic=device.mqtt_topic,
    )


@security_router.post("/devices/{device_db_id}/credential/rotate", response_model=DeviceCredentialIssued)
def rotate_credential(
    device_db_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.ADMIN)),
):
    """Issue a new password for a device; the old one stops working immediately."""
    device = db.get(Device, device_db_id)
    if device is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Device not found")
    plaintext = device_auth_service.issue_credential(db, device, rotate=True)
    db.commit()
    db.refresh(device)
    logger.info("Admin %s rotated the credential for %s", user.email, device.device_id)
    return DeviceCredentialIssued(
        device_id=device.device_id,
        username=device.credential.username,
        password=plaintext,
        mqtt_topic=device.mqtt_topic,
        warning=(
            "Store this password now — it is not recoverable. Re-run "
            "backend/scripts/sync_mqtt_credentials.py and reload the broker so the new "
            "secret is accepted."
        ),
    )


@security_router.post("/devices/{device_db_id}/enabled")
def set_device_enabled(
    device_db_id: int,
    enabled: bool,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.ADMIN)),
) -> dict:
    """Enable/disable a node. A disabled node's telemetry is rejected on arrival.

    Disabling is the revocation primitive: it takes effect in the backend even
    before the broker password file is regenerated.
    """
    device = db.get(Device, device_db_id)
    if device is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Device not found")
    device.enabled = enabled
    if device.credential is not None:
        device.credential.enabled = enabled
    db.commit()
    logger.info("Admin %s set device %s enabled=%s", user.email, device.device_id, enabled)
    return {"device_id": device.device_id, "enabled": device.enabled}
