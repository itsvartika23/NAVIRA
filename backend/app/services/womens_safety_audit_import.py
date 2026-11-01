"""Importer for licensed, reviewed women’s street-safety audit observations."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from backend.app.services.agents import WOMENS_SAFETY_AUDIT_DIMENSIONS


def validate_dataset(dataset: dict[str, Any]) -> list[dict[str, Any]]:
    source = dataset.get("source") or {}
    required = ("name", "reference", "permission_basis", "geographic_precision", "license")
    missing = [name for name in required if not str(source.get(name, "")).strip()]
    if missing:
        raise ValueError("source is missing required fields: " + ", ".join(missing))
    if not str(source["reference"]).startswith(("https://", "http://")):
        raise ValueError("source.reference must be a canonical https:// or http:// URL")
    observations = dataset.get("observations")
    if not isinstance(observations, list) or not observations:
        raise ValueError("observations must be a non-empty list")
    seen: set[str] = set()
    clean: list[dict[str, Any]] = []
    for index, item in enumerate(observations):
        if not isinstance(item, dict):
            raise ValueError(f"observation {index + 1} must be an object")
        oid = str(item.get("id", "")).strip()
        if not oid or len(oid) > 200 or oid in seen:
            raise ValueError(f"observation {index + 1} needs a unique stable id (maximum 200 characters)")
        seen.add(oid)
        segment_id = str(item.get("segment_id", "")).strip()
        if not segment_id:
            raise ValueError(f"observation {oid} needs an existing pedestrian segment_id")
        try:
            observed_at = datetime.fromisoformat(str(item.get("observed_at", "")).replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"observation {oid} has an invalid observed_at timestamp") from exc
        if observed_at.tzinfo is None:
            raise ValueError(f"observation {oid} observed_at must include a timezone")
        if item.get("review_status") != "verified":
            raise ValueError(f"observation {oid} must be review_status='verified' before it can affect route ranking")
        if not str(item.get("audit_method", "")).strip() or not str(item.get("license", source["license"])).strip():
            raise ValueError(f"observation {oid} needs an audit_method and license")
        ratings = item.get("ratings") or {}
        invalid = []
        for dimension in WOMENS_SAFETY_AUDIT_DIMENSIONS:
            try:
                rating = float(ratings[dimension])
            except (KeyError, TypeError, ValueError):
                invalid.append(dimension)
                continue
            if rating < 0 or rating > 3 or rating % 1:
                invalid.append(dimension)
        if invalid:
            raise ValueError(f"observation {oid} ratings must include integer values from 0 to 3 for: " + ", ".join(invalid))
        clean.append({**item, "observed_at": observed_at.astimezone(timezone.utc).isoformat(), "ratings": {key: int(ratings[key]) for key in WOMENS_SAFETY_AUDIT_DIMENSIONS}})
    return clean


def import_dataset(db, dataset: dict[str, Any], *, dry_run: bool = False) -> dict[str, Any]:
    observations = validate_dataset(dataset)
    source = dataset["source"]
    absent = sorted({item["segment_id"] for item in observations if not db.execute("select 1 from pedestrian_segments where id=?", (item["segment_id"],)).fetchone()})
    if absent:
        raise ValueError("unknown pedestrian segment_id(s): " + ", ".join(absent[:10]))
    if dry_run:
        return {"observations_seen": len(observations), "observations_imported": 0, "observations_skipped": 0, "dry_run": True}
    now = datetime.now(timezone.utc).isoformat()
    existing = db.execute("select id from sources where source_type='safety_audit' and name=? and reference=?", (source["name"], source["reference"])).fetchone()
    source_id = existing["id"] if existing else db.execute(
        "insert into sources(source_type,name,reference,permission_basis,retrieved_at,version,geographic_precision,notes) values('safety_audit',?,?,?,?,?,?,?)",
        (source["name"], source["reference"], source["permission_basis"], source.get("retrieved_at", now), source.get("version"), source["geographic_precision"], source.get("notes")),
    ).lastrowid
    run_id = db.execute("insert into route_safety_import_runs(source_id,source_version,started_at,status,records_seen) values(?,?,?,'running',?)", (source_id, source.get("version"), now, len(observations))).lastrowid
    rating_columns = {
        "lighting": "lighting", "openness": "openness", "walkpath": "sidewalk_presence",
        "visibility": "visibility", "public_transport": "transit_access", "crowd": "crowd_or_activity",
        "security": "observed_security", "gender_diversity": "gender_diversity", "feeling": "feeling",
    }
    imported = skipped = 0
    for item in observations:
        if db.execute("select 1 from street_context_observations where id=?", (item["id"],)).fetchone():
            skipped += 1
            continue
        values = [item["ratings"][key] for key in rating_columns]
        db.execute(
            "insert into street_context_observations(id,segment_id,source_id,observed_at,lighting,openness,sidewalk_presence,visibility,transit_access,crowd_or_activity,observed_security,gender_diversity,feeling,audit_method,review_status,missingness,license) values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (item["id"], item["segment_id"], source_id, item["observed_at"], *values, item["audit_method"], "verified", item.get("missingness"), item.get("license", source["license"])),
        )
        imported += 1
    db.execute("update route_safety_import_runs set finished_at=?,status='complete',records_imported=?,records_skipped=? where id=?", (datetime.now(timezone.utc).isoformat(), imported, skipped, run_id))
    db.commit()
    return {"observations_seen": len(observations), "observations_imported": imported, "observations_skipped": skipped, "dry_run": False}
