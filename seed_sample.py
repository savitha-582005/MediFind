import argparse

from app import medicines, owners, pharmacies, seed_demo_inventory


if __name__ == '__main__':
	parser = argparse.ArgumentParser(description='Preview or add explicitly marked MediFind demo inventory.')
	parser.add_argument('--apply', action='store_true', help='Write demo records to the configured database.')
	args = parser.parse_args()
	owner = owners.find_one({'email': 'owner@demo.com'}, {'_id': 1})
	pharmacy = pharmacies.find_one({'owner_id': owner['_id']}, {'_id': 1}) if owner else None
	current = medicines.count_documents({'pharmacy_id': pharmacy['_id']}) if pharmacy else 0
	print('Demo inventory preview: 7 sample medicine types, all labeled DEMO DATA — NOT VERIFIED LIVE STOCK.')
	print('Existing demo pharmacy inventory entries:', current)
	if not args.apply:
		print('Dry run only. Review this preview, then rerun with --apply to add missing sample records.')
	else:
		inserted = seed_demo_inventory()
		print(f'Demo seed complete. Added {inserted} new record(s); existing stock values were left unchanged.')