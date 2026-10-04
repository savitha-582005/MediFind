import argparse
import json
import re
from datetime import date, datetime, timezone
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import pharmacies

REQUIRED = ('public_listing_id', 'name', 'address', 'locality', 'latitude', 'longitude', 'source_url', 'verified_at')


def validate_record(record):
    missing = [field for field in REQUIRED if not record.get(field)]
    if missing:
        return f'missing required fields: {", ".join(missing)}'
    if not str(record['source_url']).startswith('https://'):
        return 'source_url must be an HTTPS public source'
    if record.get('public_phone') and not str(record.get('public_phone_source_url', '')).startswith('https://'):
        return 'a public_phone requires its own HTTPS verification source'
    try:
        verified_at = date.fromisoformat(record['verified_at'])
        latitude = float(record['latitude'])
        longitude = float(record['longitude'])
    except (TypeError, ValueError):
        return 'date or coordinates are invalid'
    if verified_at > date.today():
        return 'verified_at cannot be in the future'
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        return 'coordinates are outside valid latitude/longitude ranges'
    stable_id = str(record['public_listing_id']).strip()
    if len(stable_id) < 6 or not re.fullmatch(r'[A-Za-z0-9._:-]+', stable_id):
        return 'public_listing_id must be a stable verified Place ID or source identity'
    return None


def load_records(path):
    records = json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(records, list):
        raise ValueError('Input JSON must contain a list of pharmacy records.')
    return records


def run(path, apply=False):
    records = load_records(path)
    valid = []
    already_present = 0
    print('Public pharmacy import preview; public listings are not MediFind participants and have no verified stock.')
    for number, record in enumerate(records, 1):
        problem = validate_record(record)
        if problem:
            print(f'[{number}] SKIP: {problem}')
            continue
        if pharmacies.find_one({'public_listing_id': str(record['public_listing_id']).strip()}, {'_id': 1}):
            already_present += 1
            print(f'[{number}] PRESENT: {record["name"]}; existing owner-managed data will not be changed')
            continue
        print(f'[{number}] READY: {record["name"]} — {record["locality"]}; source={record["source_url"]}; verified={record["verified_at"]}')
        valid.append(record)
    print(f'Preview summary: {len(valid)} new, {already_present} already present, {len(records) - len(valid) - already_present} skipped.')
    if not apply:
        print('Dry run only. Use --apply after checking every name, address, coordinate, source and verification date.')
        return

    inserted = 0
    existing = 0
    for record in valid:
        now = datetime.now(timezone.utc)
        public_data = {
            'public_listing_id': str(record['public_listing_id']).strip(),
            'name': str(record['name']).strip(),
            'address': str(record['address']).strip(),
            'locality': str(record['locality']).strip(),
            'latitude': float(record['latitude']),
            'longitude': float(record['longitude']),
            'public_phone': str(record.get('public_phone', '')).strip() or None,
            'public_phone_verified': bool(record.get('public_phone')),
            'public_phone_source_url': record.get('public_phone_source_url'),
            'public_source_url': record['source_url'],
            'public_source_verified_at': date.fromisoformat(record['verified_at']),
            'public_listing': True,
            'verification_status': 'public_listing_unverified_participation',
            'owner_id': None,
            'is_demo': False,
            'imported_at': now
        }
        result = pharmacies.update_one(
            {'public_listing_id': public_data['public_listing_id']},
            {'$setOnInsert': public_data},
            upsert=True
        )
        if result.upserted_id is not None:
            inserted += 1
        else:
            existing += 1
    print(f'Import complete. Added {inserted}; already present and left unchanged {existing + already_present}; skipped {len(records) - len(valid) - already_present}.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Preview or import source-verified public pharmacy listings.')
    parser.add_argument('json_file', help='JSON list prepared from checked public sources; no credentials belong in this file.')
    parser.add_argument('--apply', action='store_true', help='Insert missing public listing records; never updates existing owner-managed records.')
    arguments = parser.parse_args()
    run(arguments.json_file, arguments.apply)
