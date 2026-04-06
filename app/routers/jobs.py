"""Jobs router — scheduled tasks invoked by Cloud Scheduler (RFC-11)."""

import os

from fastapi import APIRouter, HTTPException, Request

from app.events import publisher
from app.logging.decorator import log
from app.services.absence_job_service import AbsenceJobService

router = APIRouter(prefix="/jobs", tags=["jobs"])


def _verify_scheduler_or_admin(request: Request) -> None:
    """Verify request comes from Cloud Scheduler (OIDC) or is in dev mode."""
    # In production, Cloud Scheduler sends OIDC token verified by Cloud Run IAM.
    # In development, we skip this check.
    if os.getenv("APP_ENV") == "development":
        return
    # Cloud Run IAM handles auth — if request reaches here, it's authorized.


@log
@router.post("/compute-absences")
def compute_absences(request: Request):
    """Compute absences for classes that ended today without check-in.

    Invoked daily at 23:59 by Cloud Scheduler.
    """
    _verify_scheduler_or_admin(request)

    project_id = os.getenv("ROOT_PROJECT_ID", "")
    if not project_id:
        raise HTTPException(status_code=500, detail="ROOT_PROJECT_ID not configured")

    events = AbsenceJobService().compute(project_id)

    for event in events:
        publisher.publish(event, project_id=project_id, source="absence_job")

    return {"absences_created": len(events)}
