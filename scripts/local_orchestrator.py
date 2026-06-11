#!/usr/bin/env python3
"""Local event orchestrator — polls `events` collection and processes them.

Replaces the Cloud Function in local development. Runs as a long-lived
process that watches for unprocessed events and dispatches them through
the same orchestrator logic used in production.

Usage:
    OPENSSL_CONF="" uv run python scripts/local_orchestrator.py

Requires:
    - Firebase emulators running (Firestore on :8080)
    - Backend .env loaded (FIRESTORE_EMULATOR_HOST, etc.)

The script polls every 2 seconds for events with status != "processed"
and status != "failed", processes them, and marks them accordingly.
"""

import importlib
import os
import sys
import time

# Ensure the project root is in the path so we can import the function
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def main() -> None:
    # Load .env
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

    import firebase_admin
    from firebase_admin import credentials, firestore

    if not firebase_admin._apps:
        firebase_admin.initialize_app(credentials.ApplicationDefault())

    db = firestore.client()

    def _load_function_module(name: str) -> object:
        """Load a Cloud Function module without re-initializing firebase."""
        spec = importlib.util.spec_from_file_location(
            name,
            os.path.join(
                os.path.dirname(__file__), "..", "functions", name, "main.py",
            ),
        )
        mod = importlib.util.module_from_spec(spec)
        original_init = firebase_admin.initialize_app
        firebase_admin.initialize_app = lambda *a, **kw: None
        try:
            spec.loader.exec_module(mod)
        finally:
            firebase_admin.initialize_app = original_init
        return mod

    mod = _load_function_module("orchestrator")
    push_mod = _load_function_module("send_push")

    EVENT_RULES = mod.EVENT_RULES
    _now = mod._now
    _handle_email = mod._handle_email
    _handle_timeline = mod._handle_timeline
    _handle_calendar = mod._handle_calendar
    _handle_push = mod._handle_push
    _get_user_tokens = push_mod._get_user_tokens
    _send_to_expo = push_mod._send_to_expo

    def process_push_queue() -> None:
        """Send pending push_queue docs via Expo (replaces send_push fn)."""
        pending = (
            db.collection("push_queue")
            .where("status", "==", "pending")
            .stream()
        )
        for doc in pending:
            fields = doc.to_dict()
            to_uid = fields.get("to_uid", "")
            title = fields.get("title", "")
            body = fields.get("body", "")
            data = fields.get("data", {}) or {}
            source_event_ref = fields.get("source_event_ref", "")

            if not to_uid or not title:
                doc.reference.update({"status": "skipped"})
                continue

            tokens = _get_user_tokens(db, to_uid)
            if not tokens:
                doc.reference.update({"status": "no_token"})
                print(f"  PUSH no_token: to_uid={to_uid}")
                continue

            messages = [
                {
                    "to": token,
                    "title": title,
                    "body": body,
                    "sound": "default",
                    "priority": "high",
                    "data": {
                        "event_id": data.get("event_id", ""),
                        "entity_type": data.get("entity_type", ""),
                        "entity_id": data.get("entity_id", ""),
                        "source_event_ref": source_event_ref,
                    },
                }
                for token in tokens
            ]
            try:
                response = _send_to_expo(messages)
                doc.reference.update({
                    "status": "sent",
                    "expo_response": str(response),
                })
                print(f"  PUSH sent: to_uid={to_uid} tokens={len(tokens)}")
            except Exception as e:
                doc.reference.update({"status": "error", "error": str(e)})
                print(f"  PUSH error: to_uid={to_uid} — {e}")

    print(
        f"\n{'=' * 60}\n"
        f"  Local Event Orchestrator\n"
        f"  Polling events collection every 2s\n"
        f"  {len(EVENT_RULES)} event rules loaded\n"
        f"{'=' * 60}\n"
    )

    processed_ids: set[str] = set()

    while True:
        try:
            docs = list(
                db.collection("events")
                .order_by("occurredAt")
                .stream()
            )

            for doc in docs:
                if doc.id in processed_ids:
                    continue

                data = doc.to_dict()
                status = data.get("status", "")
                if status in ("processed", "failed"):
                    processed_ids.add(doc.id)
                    continue

                event_id = data.get("eventId", "")
                rule = EVENT_RULES.get(event_id)
                doc_path = f"events/{doc.id}"

                if not rule or not rule.get("channels"):
                    doc.reference.update({
                        "status": "processed",
                        "processedAt": _now(),
                    })
                    processed_ids.add(doc.id)
                    print(f"  SKIP: {event_id} — no channels")
                    continue

                payload = data.get("payload", {})
                print(f"  Processing: {event_id} (doc={doc.id})")

                try:
                    for channel in rule["channels"]:
                        if channel == "email":
                            _handle_email(
                                rule, event_id, payload, doc_path, db,
                            )
                        elif channel == "timeline":
                            _handle_timeline(
                                rule, data, payload, doc_path, db,
                            )
                        elif channel == "calendar":
                            _handle_calendar(
                                rule, data, payload, doc_path, db,
                            )
                        elif channel == "push":
                            _handle_push(
                                rule, data, payload, doc_path, db,
                            )

                    doc.reference.update({
                        "status": "processed",
                        "processedAt": _now(),
                    })
                    print(f"  OK: {event_id}")
                except Exception as e:
                    print(f"  ERROR: {event_id} — {e}")
                    doc.reference.update({
                        "status": "failed",
                        "processedAt": _now(),
                        "error": str(e),
                    })

                processed_ids.add(doc.id)

            process_push_queue()

        except KeyboardInterrupt:
            print("\nStopping.")
            break
        except Exception as e:
            print(f"  Poll error: {e}")

        time.sleep(2)


if __name__ == "__main__":
    main()
