"""AccountHistoryService — writes to users/{uid}/historic/{eventId}.

Coeso, desacoplado, separado. Cada doc carrega projectId para multi-tenancy
(um usuário pode pertencer a múltiplos projetos; queries filtram por projectId).

Two write APIs:
    - record(): standalone write (most common)
    - record_in_transaction(transaction=...): inside a Firestore transaction,
      to keep history atomic with the state mutation that triggered it
"""

from datetime import datetime, timezone
from typing import Optional

from firebase_admin import firestore
from google.cloud.firestore import Transaction

from app.logging.decorator import log
from app.models.account_history import AccountHistoryEntry, AccountHistoryPage


class AccountHistoryService:
    _USERS = "users"
    _SUBCOLLECTION = "historic"

    @log
    def record(
        self,
        uid: str,
        project_id: str,
        event_type: str,
        actor_uid: str,
        actor_name: str,
        description: str,
        event_subtype: Optional[str] = None,
        actor_roles: Optional[list[str]] = None,
        db=None,
    ) -> str:
        """Record a history entry. Returns the new doc id.

        Best-effort: failures are swallowed and logged. The history layer
        must never break the operation that triggered it.

        Pass `db` to reuse an existing Firestore client (e.g. from a mocked
        test or from a service that already initialized one).
        """
        try:
            client = db if db is not None else firestore.client()
            doc_ref = (
                client.collection(self._USERS)
                .document(uid)
                .collection(self._SUBCOLLECTION)
                .document()
            )
            doc_ref.set(
                self._build_payload(
                    project_id=project_id,
                    event_type=event_type,
                    event_subtype=event_subtype,
                    actor_uid=actor_uid,
                    actor_name=actor_name,
                    actor_roles=actor_roles or [],
                    description=description,
                )
            )
            return doc_ref.id
        except Exception:
            # History writes must never break the parent operation.
            # The failure is logged by @log decorator.
            return ""

    @log
    def record_in_transaction(
        self,
        transaction: Transaction,
        uid: str,
        project_id: str,
        event_type: str,
        actor_uid: str,
        actor_name: str,
        description: str,
        event_subtype: Optional[str] = None,
        actor_roles: Optional[list[str]] = None,
    ) -> str:
        """Record a history entry as part of an existing Firestore transaction.

        Use this when the history must be atomic with another write
        (e.g. account state transition + history entry in one shot).
        """
        db = firestore.client()
        doc_ref = (
            db.collection(self._USERS)
            .document(uid)
            .collection(self._SUBCOLLECTION)
            .document()
        )
        transaction.set(
            doc_ref,
            self._build_payload(
                project_id=project_id,
                event_type=event_type,
                event_subtype=event_subtype,
                actor_uid=actor_uid,
                actor_name=actor_name,
                actor_roles=actor_roles or [],
                description=description,
            ),
        )
        return doc_ref.id

    @log
    def query(
        self,
        uid: str,
        project_id: str,
        year: Optional[int] = None,
        months: Optional[list[int]] = None,
        types: Optional[list[str]] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> AccountHistoryPage:
        """Query history entries for a user, filtered by projectId.

        Filters:
            - year: only entries from this year (int, e.g. 2026)
            - months: only entries from these months (list of 1-12)
            - types: only these event types
        Pagination is in-memory (slice after fetch). Adequate for typical
        account history sizes (dozens to low hundreds of entries per user).
        """
        db = firestore.client()
        col = (
            db.collection(self._USERS)
            .document(uid)
            .collection(self._SUBCOLLECTION)
        )

        query = col.where("projectId", "==", project_id)
        if types:
            query = query.where("eventType", "in", types)

        # Stream all then filter by date in memory (Firestore can't filter
        # ISO strings by year/month efficiently without composite indexes
        # we'd rather not maintain).
        docs = list(query.stream())
        entries: list[AccountHistoryEntry] = []
        for doc in docs:
            data = doc.to_dict()
            created_at = data.get("createdAt", "")
            if year is not None or months:
                try:
                    dt = datetime.fromisoformat(created_at)
                except (ValueError, TypeError):
                    continue
                if year is not None and dt.year != year:
                    continue
                if months and dt.month not in months:
                    continue
            entries.append(
                AccountHistoryEntry(
                    id=doc.id,
                    project_id=data.get("projectId", ""),
                    event_type=data.get("eventType", ""),
                    event_subtype=data.get("eventSubtype"),
                    actor_uid=data.get("actorUid", ""),
                    actor_name=data.get("actorName", ""),
                    actor_roles=data.get("actorRoles", []),
                    description=data.get("description", ""),
                    created_at=created_at,
                )
            )

        # Sort newest first
        entries.sort(key=lambda e: e.created_at, reverse=True)

        total = len(entries)
        total_pages = (total + page_size - 1) // page_size if page_size else 1
        start = (page - 1) * page_size
        end = start + page_size
        return AccountHistoryPage(
            items=entries[start:end],
            total=total,
            page=page,
            page_size=page_size,
            total_pages=total_pages,
        )

    @staticmethod
    def _build_payload(
        project_id: str,
        event_type: str,
        event_subtype: Optional[str],
        actor_uid: str,
        actor_name: str,
        actor_roles: list[str],
        description: str,
    ) -> dict:
        return {
            "projectId": project_id,
            "eventType": event_type,
            "eventSubtype": event_subtype,
            "actorUid": actor_uid,
            "actorName": actor_name,
            "actorRoles": actor_roles,
            "description": description,
            "createdAt": datetime.now(timezone.utc).isoformat(),
        }
