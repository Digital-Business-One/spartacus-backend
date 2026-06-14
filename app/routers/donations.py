"""Back-compat shim for the legacy /donations endpoints.

The donations feature was generalized into "Apoio" (support). These thin
routes keep the previously-shipped mobile app working until it is rebuilt to
use /support/*. New code should use the support router.
"""

from typing import Optional

from fastapi import APIRouter, Header, HTTPException

from app.events import publisher
from app.logging.decorator import log
from app.models.support import SupportCreate, SupportOut
from app.security.context import auth_ctx
from app.services.support_service import SupportService

router = APIRouter(tags=["donations(compat)"])


@log
@router.post("/donations", status_code=201)
def create_donation_compat(
    data: SupportCreate,
    x_acting_as: Optional[str] = Header(None),
) -> SupportOut:
    """Legacy app registers a donation → delegates to support (donation)."""
    ctx = auth_ctx.get()
    data.support_type = "donation"
    result, event = SupportService().create(
        project_id=ctx.project_id,
        uid=ctx.user_id,
        acting_as=x_acting_as,
        data=data,
    )
    publisher.publish(event, project_id=ctx.project_id, source="support_service")
    return result


@log
@router.get("/donations/current")
def get_current_donation_compat(
    x_acting_as: Optional[str] = Header(None),
) -> SupportOut:
    """No monthly lock anymore — always 'none', so the app shows the wizard."""
    raise HTTPException(status_code=404, detail="Nenhuma doação registrada")


@log
@router.get("/donations/history")
def get_donation_history_compat(
    x_acting_as: Optional[str] = Header(None),
) -> dict:
    """Map support history to the legacy {donations:[...]} shape."""
    ctx = auth_ctx.get()
    hist = SupportService().get_history(
        project_id=ctx.project_id, uid=ctx.user_id, acting_as=x_acting_as,
    )
    return {
        "donations": [
            {
                "id": i.id,
                "month": i.month,
                "monthLabel": i.month_label,
                "item": i.item,
                "itemLabel": i.item_label,
                "itemDescription": i.item_description,
                "status": i.status,
                "statusLabel": i.status_label,
                "createdAt": i.created_at,
                "receivedBy": i.received_by,
                "receivedByName": i.received_by_name,
                "receivedAt": i.received_at,
            }
            for i in hist.items
        ],
    }
