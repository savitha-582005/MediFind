import argparse
from datetime import datetime, timezone
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import db, medicine_catalog, medicines, owners, pharmacies

MEDICINE_TYPES = [
    {
        'slug': 'paracetamol-650-mg',
        'name': 'Paracetamol 650 mg',
        'generic_name': 'Paracetamol',
        'strength': '650 mg',
        'form': 'tablet',
        'category': 'medicine',
        'search_keywords': ['paracetamol', '650 mg'],
        'prescription_required': None,
        'prescription_status': 'not_assessed'
    },
    {
        'slug': 'cetirizine-10-mg',
        'name': 'Cetirizine 10 mg',
        'generic_name': 'Cetirizine',
        'strength': '10 mg',
        'form': 'tablet',
        'category': 'medicine',
        'search_keywords': ['cetirizine', '10 mg'],
        'prescription_required': None,
        'prescription_status': 'not_assessed'
    },
    {
        'slug': 'oral-rehydration-salts-ors',
        'name': 'Oral Rehydration Salts (ORS)',
        'generic_name': 'Oral rehydration salts',
        'strength': None,
        'form': 'oral rehydration salts',
        'category': 'medicine',
        'search_keywords': ['oral rehydration salts', 'ors'],
        'prescription_required': None,
        'prescription_status': 'not_assessed'
    },
    {
        'slug': 'antacid-tablets',
        'name': 'Antacid tablets',
        'generic_name': None,
        'strength': None,
        'form': 'tablet',
        'category': 'medicine',
        'search_keywords': ['antacid', 'antacid tablets'],
        'prescription_required': None,
        'prescription_status': 'not_assessed'
    },
    {
        'slug': 'ibuprofen-200-mg',
        'name': 'Ibuprofen 200 mg',
        'generic_name': 'Ibuprofen',
        'strength': '200 mg',
        'form': 'tablet',
        'category': 'medicine',
        'search_keywords': ['ibuprofen', '200 mg'],
        'prescription_required': None,
        'prescription_status': 'not_assessed'
    },
    {
        'slug': 'vitamin-c-tablets',
        'name': 'Vitamin C tablets',
        'generic_name': 'Vitamin C',
        'strength': None,
        'form': 'tablet',
        'category': 'medicine',
        'search_keywords': ['vitamin c', 'ascorbic acid'],
        'prescription_required': None,
        'prescription_status': 'not_assessed'
    }
]

INDEXES = [
    ('users', [('email', 1)], {'unique': True, 'name': 'users_email_unique'}),
    ('owners', [('email', 1)], {'unique': True, 'name': 'owners_email_unique'}),
    ('pharmacies', [('owner_id', 1)], {'name': 'pharmacies_owner_id'}),
    ('pharmacies', [('public_listing_id', 1)], {
        'unique': True,
        'sparse': True,
        'name': 'pharmacies_public_listing_id_unique'
    }),
    ('medicines', [('name', 1), ('brand', 1)], {'name': 'medicines_name_brand'}),
    ('medicines', [('search_keywords', 1)], {'name': 'medicines_search_keywords'}),
    ('medicines', [('pharmacy_id', 1), ('stock', 1)], {'name': 'medicines_pharmacy_stock'}),
    ('medicine_catalog', [('slug', 1)], {'unique': True, 'name': 'medicine_catalog_slug_unique'}),
    ('search_history', [('user_id', 1), ('created_at', -1)], {'name': 'search_history_user_date'})
]


def run(apply=False):
    print('Database setup preview:')
    print('  Database:', db.name)
    print('  Existing collections/documents will not be deleted or rewritten.')
    for collection_name, fields, options in INDEXES:
        print(f'  Index: {collection_name} {fields} {options.get("name")}')
    print('  Medicine catalog entries:', len(MEDICINE_TYPES))
    demo_owner = owners.find_one({'email': 'owner@demo.com'}, {'_id': 1})
    legacy_demo_pharmacy = pharmacies.find_one(
        {'owner_id': demo_owner['_id'], 'name': 'Demo Pharmacy'}, {'_id': 1}
    ) if demo_owner else None
    legacy_demo_stock_count = medicines.count_documents({
        'pharmacy_id': legacy_demo_pharmacy['_id'],
        'inventory_status': {'$exists': False}
    }) if legacy_demo_pharmacy else 0
    print('  Existing known demo pharmacy records to label demo-only:', int(bool(legacy_demo_pharmacy)))
    print('  Existing unmarked inventory records to label demo-unverified:', legacy_demo_stock_count)
    if not apply:
        print('Dry run only. Use --apply after reviewing the plan.')
        return

    if legacy_demo_pharmacy:
        pharmacies.update_one(
            {'_id': legacy_demo_pharmacy['_id'], 'owner_id': demo_owner['_id']},
            {'$set': {
                'is_demo': True,
                'public_listing': False,
                'verification_status': 'demo'
            }}
        )
        medicines.update_many(
            {
                'pharmacy_id': legacy_demo_pharmacy['_id'],
                'inventory_status': {'$exists': False}
            },
            {'$set': {'inventory_status': 'demo_unverified'}}
        )
        print('Known legacy demo records labeled; stock counts and prices were not modified.')

    for collection_name, fields, options in INDEXES:
        try:
            db[collection_name].create_index(fields, **options)
            print('Index ready:', options['name'])
        except Exception as error:
            print('Index skipped:', options['name'], type(error).__name__)
            print('Resolve duplicate legacy data before retrying this unique index; no records were deleted.')

    inserted = 0
    for medicine in MEDICINE_TYPES:
        now = datetime.now(timezone.utc)
        result = medicine_catalog.update_one(
            {'slug': medicine['slug']},
            {'$setOnInsert': {**medicine, 'created_at': now, 'updated_at': now}},
            upsert=True
        )
        inserted += result.upserted_id is not None
    print(f'Catalog setup complete. Added {inserted}; already present {len(MEDICINE_TYPES) - inserted}.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Safely initialize MediFind indexes and medicine catalog.')
    parser.add_argument('--apply', action='store_true', help='Create indexes and add missing catalog records.')
    run(parser.parse_args().apply)
