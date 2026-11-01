# Women’s street-audit data and route ranking

NAVIRA uses a route audit index as its primary safety-oriented route ordering when there is enough audited data. The nine inputs are lighting, openness, visibility, crowd/activity, security, walk path, public transport, gender diversity, and feeling of safety. The values are 0–3, collected by an audit method, and tied to reviewed pedestrian-network segments. This parameter set is published by [Safetipin](https://safetipin.com/our-apps/) and is consistent with participatory women’s safety-audit practice described by [UN-Habitat](https://unhabitat.org/womens-safety-audit-what-works-and-where).

NAVIRA assigns each of the nine inputs equal weight. It averages segment observations across sampled route points and scales the mean to 0–100. The result is a provisional description of observed conditions. It is not the Safetipin score, a probability of assault or harm, or a guarantee. Traffic, weather, citywide crime totals, unverified news candidates, and uninspected OpenStreetMap tags do not increase or decrease the index.

The score is shown only when all returned options meet the same minimum: reviewed observations from the last 180 days, within 50 m of at least 60% of route samples, and at least five distinct audited segments. If any option does not meet it, NAVIRA withholds the safety ordering and labels the fastest route as a navigation fallback. Missing audits never become a zero score or a positive safety signal.

## Loading a licensed audit dataset

The importer is `python -m backend.scripts.import_womens_safety_audits`. It expects:

- `source`: `name`, canonical `reference`, `permission_basis`, `geographic_precision`, `license`; optional `version`, `retrieved_at`, and `notes`.
- `observations`: a stable unique `id`, existing `segment_id`, timezone-qualified `observed_at`, `review_status: "verified"`, `audit_method`, and `ratings`.
- `ratings`: integer values from 0 to 3 for `lighting`, `openness`, `visibility`, `crowd_or_activity`, `observed_security`, `sidewalk_presence`, `transit_access`, `gender_diversity`, and `feeling`.

Map each audit to a reviewed pedestrian segment before import. The source must allow this storage and use; document the permission and license in the dataset. Validate first with `--dry-run`. Safetipin’s published description of its parameters does not grant access to its underlying audit records. A 2013–2016 Delhi NCR dataset described by the World Bank is explicitly not included for redistribution and was supplied to the study authors under a data-use agreement; NAVIRA must obtain its own authorization before using it. Do not use scraped or synthetic observations to populate production route scores.
