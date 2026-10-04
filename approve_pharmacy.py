import argparse
import sys
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bson import ObjectId
from app import owners, pharmacies, valid_coordinates


def review(pharmacy_id, evidence_reference, confirm, decision, reason=None, phone_verified=False):
    if not confirm:
        print('No review was applied. Complete the manual verification first, then rerun with --confirm.')
        return False
    if not evidence_reference or not evidence_reference.strip():
        print('A non-sensitive evidence reference is required for every review decision.')
        return False
    try:
        object_id = ObjectId(pharmacy_id)
    except Exception:
        print('Invalid pharmacy ObjectId.')
        return False
    pharmacy = pharmacies.find_one({'_id': object_id})
    if not pharmacy or not pharmacy.get('owner_id') or pharmacy.get('is_demo'):
        print('Refusing review: pharmacy is missing, demo-only, or not linked to an owner.')
        return False
    if pharmacy.get('verification_status') not in {None, 'pending', 'pending_verification'}:
        print('Refusing review: listing is not pending review.')
        return False
    owner = owners.find_one({'_id': pharmacy['owner_id']})
    if not owner:
        print('Refusing review: owner account was not found.')
        return False
    if decision == 'rejected' and not (reason and reason.strip()):
        print('A clear --reason is required when rejecting a submission.')
        return False
    if decision == 'approved':
        try:
            latitude = float(pharmacy.get('latitude'))
            longitude = float(pharmacy.get('longitude'))
        except (TypeError, ValueError):
            print('Refusing approval: valid latitude and longitude are required.')
            return False
        if not valid_coordinates(latitude, longitude):
            print('Refusing approval: coordinates are outside valid ranges.')
            return False

    listing_fields = {
        'verification_status': decision,
        'verification_evidence_reference': evidence_reference,
        'verification_reviewed_at': datetime.now(timezone.utc)
    }
    if decision == 'approved':
        listing_fields.update({
            'public_listing': True,
            'public_phone_verified': bool(phone_verified and pharmacy.get('public_phone'))
        })
    else:
        listing_fields.update({
            'public_listing': False,
            'public_phone_verified': False,
            'verification_rejection_reason': reason.strip()
        })
    if decision == 'approved':
        pharmacies.update_one(
            {'_id': object_id, 'owner_id': owner['_id'], 'verification_status': pharmacy.get('verification_status')},
            {'$set': listing_fields, '$unset': {'verification_rejection_reason': ''}}
        )
    else:
        pharmacies.update_one(
            {'_id': object_id, 'owner_id': owner['_id'], 'verification_status': pharmacy.get('verification_status')},
            {'$set': listing_fields}
        )
    owner_fields = {'status': decision}
    if decision == 'rejected':
        owner_fields['verification_rejection_reason'] = reason.strip()
        owners.update_one({'_id': owner['_id']}, {'$set': owner_fields})
    else:
        owners.update_one({'_id': owner['_id']}, {'$set': owner_fields, '$unset': {'verification_rejection_reason': ''}})
    if decision == 'approved':
        print('Pharmacy approved for public listing. Stock is still hidden until the owner confirms each inventory item.')
    else:
        print('Submission rejected with a reason. The owner can correct details and request review again.')
    return True


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Admin-only manual review for an owner-submitted pharmacy.')
    parser.add_argument('--pharmacy-id', required=True)
    parser.add_argument('--evidence-reference', required=True, help='Non-sensitive reference to the manual verification record.')
    parser.add_argument('--decision', choices=('approved', 'rejected'), required=True)
    parser.add_argument('--reason', help='Required for a rejection; shown to the owner.')
    parser.add_argument('--confirm', action='store_true', help='Confirm you personally completed the manual verification.')
    parser.add_argument('--phone-verified', action='store_true', help='Confirm the optional public phone was separately verified.')
    args = parser.parse_args()
    success = review(args.pharmacy_id, args.evidence_reference, args.confirm, args.decision, args.reason, args.phone_verified)
    raise SystemExit(0 if success or not args.confirm else 1)
