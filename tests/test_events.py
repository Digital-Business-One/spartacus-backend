"""Tests for backoffice calendar events (reuse of the posts pipeline)."""

from unittest.mock import MagicMock, patch

from app.models.post import PostCreate
from app.services.event_service import EventService, can_create_event


class TestPermission:
    def test_staff_plus_social_allowed(self):
        assert can_create_event(["teacher", "social"]) is True
        assert can_create_event(["owner", "social"]) is True

    def test_social_without_staff_denied(self):
        assert can_create_event(["social"]) is False
        assert can_create_event(["student", "social"]) is False

    def test_staff_without_social_denied(self):
        assert can_create_event(["owner"]) is False
        assert can_create_event(["assistant", "teacher"]) is False

    def test_empty_denied(self):
        assert can_create_event([]) is False


class TestPostServiceEventFields:
    def test_create_persists_event_category_and_payload(self):
        from app.services.post_service import PostService

        db = MagicMock()
        ref = MagicMock()
        ref.id = "post-1"
        db.collection.return_value.add.return_value = (None, ref)

        data = PostCreate(
            type="event",
            title="Defesa pessoal na praça",
            description="Aula aberta",
            eventDate="2026-06-17T19:00:00",
            eventLocation="Praça Central",
            eventCategory="own",
            modalityId="spartacus_jiu-jitsu",
        )
        with patch("app.services.post_service.firestore") as fs:
            fs.client.return_value = db
            out, event = PostService().create(
                project_id="spartacus", data=data,
                author_uid="staff-1", author_name="Prof", author_roles=["social"],
            )

        doc = db.collection.return_value.add.call_args[0][0]
        assert doc["eventCategory"] == "own"
        assert doc["modalityId"] == "spartacus_jiu-jitsu"
        assert out.event_category == "own"
        p = event.payload.personalization()
        assert p["event_category"] == "own"
        assert p["modality_id"] == "spartacus_jiu-jitsu"

    def test_external_event_carries_organizer_and_link(self):
        from app.services.post_service import PostService

        db = MagicMock()
        ref = MagicMock()
        ref.id = "post-2"
        db.collection.return_value.add.return_value = (None, ref)

        data = PostCreate(
            type="event",
            title="Campeonato estadual de JJ",
            description="Sapezal",
            eventCategory="external",
            organizer="Federação MT",
            registrationLink="https://inscricoes.example/jj",
        )
        with patch("app.services.post_service.firestore") as fs:
            fs.client.return_value = db
            _, event = PostService().create(
                project_id="spartacus", data=data,
                author_uid="staff-1", author_name="Prof", author_roles=["social"],
            )
        p = event.payload.personalization()
        assert p["event_category"] == "external"
        assert p["organizer"] == "Federação MT"
        assert p["registration_link"] == "https://inscricoes.example/jj"


class TestEventServiceRead:
    def test_list_exposes_event_extras(self):
        doc = MagicMock()
        doc.id = "post_abc"
        doc.to_dict.return_value = {
            "projectId": "spartacus",
            "type": "event",
            "title": "Aulão MMA",
            "startDate": "2026-06-19T20:00:00",
            "endDate": "2026-06-19T21:30:00",
            "eventCategory": "guest_class",
            "modalityId": "spartacus_mma",
        }
        db = MagicMock()
        db.collection.return_value.where.return_value.stream.return_value = [doc]
        with patch("app.services.event_service.firestore") as fs:
            fs.client.return_value = db
            events = EventService().list_by_project("spartacus")

        assert len(events) == 1
        assert events[0].event_category == "guest_class"
        assert events[0].modality_id == "spartacus_mma"
        assert events[0].start_date == "2026-06-19T20:00:00"
