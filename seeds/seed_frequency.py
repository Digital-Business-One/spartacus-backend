#!/usr/bin/env python3
"""Seed frequency (attendance analytics) test data for local dev.

Produces everything the "Frequência Analítica" + "Justificativa de Faltas"
features need to be exercised end-to-end:

  1. Turns the attendance engine ON for two Jiu-Jitsu turmas
     (`attendanceEngineEnabled` + `attendanceStartDate`).
  2. Approves and enrolls a set of students into those turmas
     (membership `status=active`, user `classIds`), backdating their
     `created_at` so the counting window `max(createdAt, startDate)` opens
     before the seeded aulas.
  3. Gives a subset of them an approved Jiu-Jitsu graduation
     (`users/{uid}.graduation["jiu-jitsu"]`) so the by-graduation breakdown
     has data.
  4. Generates aulas + attendance (presenças) across the window with a
     graduation snapshot per record — a mix of confirmed and absent, so the
     history/analytics screens show numbers and there are real absences to
     justify.

Idempotent: deterministic doc ids + a fixed RNG seed, so re-running
overwrites rather than duplicates.

Prerequisites (run these seeds first):
    seed_root_project → seed_admin_account → seed_modalities_and_classes
    → seed_classes → seed_modalities_and_classes (again, to migrate) →
    seed_graduation_systems → seed_test_accounts / seed_families

Usage (local dev with emulators — no ADC needed):
    FIRESTORE_EMULATOR_HOST=localhost:8080 \
    GOOGLE_CLOUD_PROJECT=spartacus-artes-marciais \
    ROOT_PROJECT_ID=spartacus-artes-marciais \
    uv run python seeds/seed_frequency.py

Usage (production — requires ADC; NOT recommended, this is test data):
    ROOT_PROJECT_ID=spartacus-artes-marciais \
    uv run python seeds/seed_frequency.py
"""
import os
import random
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

load_dotenv()

import firebase_admin
from firebase_admin import firestore

# Brasnorte is UTC-4; the backend normalises timestamps to this offset.
_TZ = timezone(timedelta(hours=-4))

# Turmas that get the engine turned on (must exist + be migrated to the
# new model by seed_classes + seed_modalities_and_classes).
_TARGET_TURMAS = [
    "spartacus-artes-marciais_jiu-jitsu-adultos",
    "spartacus-artes-marciais_jiu-jitsu-kids-matutino",
]
_MODALITY_SLUG = "jiu-jitsu"

_WINDOW_DAYS = 14          # attendanceStartDate = today - 14
_ACCOUNT_AGE_DAYS = 25     # backdate enrolled students' createdAt
_MAX_STUDENTS = 8          # how many pending students to approve + enroll

# Jiu-Jitsu graduations handed out (belt label + degree), one per student in
# order; students beyond this list are enrolled without a graduation
# (they land in the "Sem graduação" bucket, which is also worth testing).
_BELTS = [
    ("Branca", 0),
    ("Azul", 1),
    ("Azul", 2),
    ("Azul", 3),
    ("Branca", 0),
    ("Azul", 4),
]

# ~22% absence rate, deterministic via the seeded RNG.
_STATUS_POOL = ["confirmed"] * 7 + ["absent"] * 2


def _init_firebase() -> None:
    """Emulator-aware init: use anonymous creds against the emulator so the
    seed runs locally without ADC; fall back to ADC in production."""
    if firebase_admin._apps:
        return
    use_emulator = bool(
        os.getenv("FIRESTORE_EMULATOR_HOST")
        or os.getenv("FIREBASE_AUTH_EMULATOR_HOST")
    )
    if use_emulator:
        from firebase_admin import credentials as fb_credentials
        from google.auth.credentials import AnonymousCredentials

        class _EmulatorCredential(fb_credentials.Base):
            def get_credential(self):
                return AnonymousCredentials()

        firebase_admin.initialize_app(
            credential=_EmulatorCredential(),
            options={
                "projectId": os.getenv(
                    "GOOGLE_CLOUD_PROJECT", "spartacus-artes-marciais"
                )
            },
        )
    else:
        firebase_admin.initialize_app()


def _enable_engine(db, start_date: str) -> dict[str, dict]:
    info: dict[str, dict] = {}
    for tid in _TARGET_TURMAS:
        ref = db.collection("classes").document(tid)
        data = ref.get().to_dict()
        if not data:
            print(f"  ! turma {tid} não existe — pulei (rode seed_classes antes)")
            continue
        ref.update(
            {"attendanceEngineEnabled": True, "attendanceStartDate": start_date}
        )
        info[tid] = {
            "name": data.get("name") or tid,
            "teacherName": data.get("teacherName") or "Professor",
        }
    return info


def _approve_and_enroll(db, turmas: list[str], now: datetime) -> list[dict]:
    """Approve + enroll pending students; return enrolled student dicts."""
    picked: list[tuple[str, str]] = []  # (membership_id, user_id)
    # Any student membership not yet active (seed data uses "pending_approval").
    for m in db.collection("memberships").stream():
        md = m.to_dict()
        if "student" in (md.get("roles") or []) and md.get("status") != "active":
            picked.append((m.id, md.get("userId")))
        if len(picked) >= _MAX_STUDENTS:
            break

    backdated = (now - timedelta(days=_ACCOUNT_AGE_DAYS)).isoformat()
    enrolled: list[dict] = []
    for i, (mid, uid) in enumerate(picked):
        tid = turmas[i % len(turmas)]
        uref = db.collection("users").document(uid)
        ud = uref.get().to_dict() or {}
        update = {
            "classIds": sorted(set((ud.get("classIds") or []) + [tid])),
            # set both spellings defensively (window reads created_at)
            "created_at": backdated,
            "createdAt": backdated,
        }
        grad = None
        if i < len(_BELTS):
            belt, degree = _BELTS[i]
            grad = {
                "belt": belt,
                "degree": degree,
                "status": "approved",
                "lockedByStudent": False,
            }
            update["graduation"] = {**(ud.get("graduation") or {}), _MODALITY_SLUG: grad}
        uref.update(update)
        db.collection("memberships").document(mid).update({"status": "active"})
        enrolled.append(
            {"uid": uid, "name": ud.get("name") or "Aluno", "tid": tid, "grad": grad}
        )
    return enrolled


def _seed_attendance(db, enrolled: list[dict], tinfo: dict[str, dict],
                     project_id: str, now: datetime) -> tuple[int, int]:
    rng = random.Random(7)
    total = absences = 0
    for tid, meta in tinfo.items():
        roster = [e for e in enrolled if e["tid"] == tid]
        for day_offset in range(1, _WINDOW_DAYS, 3):  # ~5 aulas per turma
            adt = (now - timedelta(days=day_offset)).replace(
                hour=18, minute=0, second=0, microsecond=0
            )
            aula_id = f"{tid}_{adt.strftime('%Y%m%d_%H%M')}"
            db.collection("aulas").document(aula_id).set(
                {
                    "projectId": project_id,
                    "turmaId": tid,
                    "startTime": adt.isoformat(),
                    "endTime": (adt + timedelta(hours=1)).isoformat(),
                    "createdAt": adt.isoformat(),
                }
            )
            for e in roster:
                status = rng.choice(_STATUS_POOL)
                snapshot = (
                    {
                        "belt": e["grad"]["belt"],
                        "degree": e["grad"]["degree"],
                        "status": "approved",
                    }
                    if e["grad"]
                    else None
                )
                db.collection("attendance").document(f"{aula_id}__{e['uid']}").set(
                    {
                        "projectId": project_id,
                        "userId": e["uid"],
                        "userName": e["name"],
                        "aulaId": aula_id,
                        "turmaId": tid,
                        "turmaName": meta["name"],
                        "teacherName": meta["teacherName"],
                        "timestamp": adt.isoformat(),
                        "status": status,
                        "validatedBy": "system" if status == "absent" else "seed",
                        "validatedAt": adt.isoformat(),
                        "source": "seed",
                        "createdAt": adt.isoformat(),
                        "modalitySlug": _MODALITY_SLUG,
                        "graduationSnapshot": snapshot,
                    }
                )
                total += 1
                absences += status == "absent"
    return total, absences


def run() -> None:
    _init_firebase()
    project_id = os.getenv("ROOT_PROJECT_ID", "spartacus-artes-marciais")
    db = firestore.client()
    now = datetime.now(_TZ)
    start_date = (now - timedelta(days=_WINDOW_DAYS)).date().isoformat()

    print(f"Seeding frequency data for project {project_id}...")

    tinfo = _enable_engine(db, start_date)
    if not tinfo:
        print("Nenhuma turma alvo encontrada — abortando.")
        return
    print(f"  motor LIGADO em {len(tinfo)} turma(s) (data-base {start_date}): "
          + ", ".join(m["name"] for m in tinfo.values()))

    enrolled = _approve_and_enroll(db, list(tinfo.keys()), now)
    graded = sum(1 for e in enrolled if e["grad"])
    print(f"  {len(enrolled)} aluno(s) aprovados + matriculados ({graded} com "
          f"graduação {_MODALITY_SLUG}, {len(enrolled) - graded} sem graduação)")

    total, absences = _seed_attendance(db, enrolled, tinfo, project_id, now)
    print(f"  {total} presença(s) criadas, sendo {absences} falta(s) — há faltas "
          "reais para testar o fluxo de justificativa")
    print("Done.")


if __name__ == "__main__":
    run()
