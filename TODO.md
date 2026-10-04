# MediFind Remaining Manual Work

- [x] Enforce stable environment-backed session secrets and secure cookie defaults; local app startup now refuses missing/sample secrets.
- [x] Add sanitized Atlas connectivity diagnostics, explicit Certifi TLS roots, and safe debug/error behavior.
- [x] Support owner approval and rejection decisions, requiring evidence references and recording owner-visible rejection reasons.
- [x] Keep demo/public listings separate from approved owner participation and confirmed stock.
- [x] Verify search radius, authorization, history privacy, and review paths with isolated tests.
- [ ] Generate and set a persistent random `SECRET_KEY` in local `.env` (the app now fails fast if it is missing or still the sample placeholder).
- [ ] Add a restricted Google Maps JavaScript browser key after enabling the API, billing, referrer restrictions, API restrictions, quotas, and budget alerts.
- [ ] Research and verify Ballari pharmacy identities, public addresses, coordinates, and source URLs; do not populate `data/public_pharmacies.example.json` until evidence is checked.
- [ ] Verify participating owner authority and pharmacy location manually before using `scripts/approve_pharmacy.py`.
- [ ] Have participating owners enter and maintain their own stock; never present demo or imported directory data as verified availability.
- [ ] Test browser permission prompts and owner review/stock workflows with consenting test accounts before presenting the project.
