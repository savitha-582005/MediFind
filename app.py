from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash
from werkzeug.utils import secure_filename
from werkzeug.security import check_password_hash, generate_password_hash
from flask_wtf.csrf import CSRFProtect
import certifi
from pymongo import MongoClient
from pymongo.errors import PyMongoError
from bson.objectid import ObjectId
from urllib.parse import quote_plus, urlencode
from urllib.request import Request, urlopen
from dotenv import load_dotenv
from functools import lru_cache
from functools import wraps
import json
import math
import re
import threading
import time
import os
import secrets
from datetime import datetime, timedelta, timezone

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

# --- Configuration ---
username = os.getenv('MONGO_USERNAME')
password = os.getenv('MONGO_PASSWORD')
cluster = os.getenv('MONGO_CLUSTER')

def require_mongo_config(username_value, password_value, cluster_value):
    if not all([username_value, password_value, cluster_value]):
        raise ValueError('MongoDB configuration missing in .env')
    return username_value, password_value, cluster_value

username, password, cluster = require_mongo_config(username, password, cluster)

MONGO_URI = (
    f"mongodb+srv://{quote_plus(username)}:{quote_plus(password)}@{cluster}/"
    '?appName=MediFindCluster&compressors=zlib'
)
UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), 'static', 'uploads')
ALLOWED_EXT = {'png','jpg','jpeg','gif'}
MAX_SEARCH_LENGTH = 120
MAX_LOCATION_LENGTH = 200

app = Flask(__name__)

def require_secret_key(value):
    placeholders = {
        'change_this_to_secure_value',
        'generate_a_long_random_value_locally',
        'replace_with_a_random_secret_generated_locally'
    }
    if not value or value in placeholders or len(value) < 32:
        raise RuntimeError('Set a persistent random SECRET_KEY of at least 32 characters in the project .env before starting MediFind.')
    return value

app.secret_key = require_secret_key(os.getenv('SECRET_KEY'))
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 8 * 1024 * 1024
app.permanent_session_lifetime = timedelta(days=7)
app.config['WTF_CSRF_TIME_LIMIT'] = 3600
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = os.getenv('SESSION_COOKIE_SECURE', 'false').strip().lower() == 'true'
csrf = CSRFProtect(app)

@app.context_processor
def inject_public_config():
    return {'google_maps_api_key': os.getenv('GOOGLE_MAPS_API_KEY', '').strip()}

def log_mongodb_error(context, error):
    details = str(error)
    for secret in (MONGO_URI, username, password, quote_plus(username), quote_plus(password)):
        if secret:
            details = details.replace(secret, '[redacted]')
    app.logger.error('%s (%s): %s', context, type(error).__name__, details)

@app.errorhandler(PyMongoError)
def handle_mongodb_unavailable(error):
    log_mongodb_error('MongoDB request failed', error)
    return (
        'MediFind cannot access the database right now. Please try again later.',
        503
    )

@app.errorhandler(500)
def handle_internal_error(error):
    original = getattr(error, 'original_exception', None)
    app.logger.error(
        'Unhandled server error on endpoint %s (%s)',
        request.endpoint or 'unknown',
        type(original).__name__ if original else type(error).__name__
    )
    message = 'MediFind could not complete this request. Please try again.'
    if request.path.startswith('/api/'):
        return jsonify({'error': message}), 500
    return render_template('error.html'), 500

# --- DB ---
client = MongoClient(
    MONGO_URI,
    serverSelectionTimeoutMS=10000,
    tlsCAFile=certifi.where()
)
try:
    client.admin.command('ping')
except PyMongoError as error:
    log_mongodb_error('MongoDB startup ping failed', error)
    raise RuntimeError('MediFind could not connect to MongoDB Atlas. See the terminal for details.') from None
db = client['medifinddb']
users = db.users
owners = db.owners
pharmacies = db.pharmacies
medicines = db.medicines
search_history = db.search_history
medicine_catalog = db.medicine_catalog

# --- Helpers ---
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.',1)[1].lower() in ALLOWED_EXT

def haversine(lat1, lon1, lat2, lon2):
    # returns distance in kilometers
    R = 6371.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlambda/2)**2
    return 2*R*math.asin(math.sqrt(a))

def valid_coordinates(latitude, longitude):
    return (
        math.isfinite(latitude)
        and math.isfinite(longitude)
        and -90 <= latitude <= 90
        and -180 <= longitude <= 180
    )

def coarse_area_label(address, fallback=''):
    if not isinstance(address, dict):
        return fallback
    locality = (
        address.get('city') or address.get('town') or address.get('village')
        or address.get('municipality') or address.get('suburb') or address.get('county')
    )
    pieces = [locality, address.get('state'), address.get('country')]
    return ', '.join(dict.fromkeys(piece.strip() for piece in pieces if isinstance(piece, str) and piece.strip())) or fallback

def user_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if 'user' not in session or session.get('role') != 'user':
            if request.path.startswith('/api/'):
                return jsonify({'error': 'Please sign in as a user.'}), 401
            return redirect(url_for('user_login'))
        try:
            user_id = ObjectId(session['user'])
        except Exception:
            session.clear()
            return redirect(url_for('user_login'))
        if users.find_one({'_id': user_id}, {'_id': 1}) is None:
            session.clear()
            return redirect(url_for('user_login'))
        return view(*args, **kwargs)
    return wrapped

def owner_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if 'owner' not in session or session.get('role') != 'owner':
            if request.path.startswith('/api/'):
                return jsonify({'error': 'Please sign in as a pharmacy owner.'}), 401
            return redirect(url_for('owner_login'))
        try:
            owner_id = ObjectId(session['owner'])
        except Exception:
            session.clear()
            return redirect(url_for('owner_login'))
        if owners.find_one({'_id': owner_id}, {'_id': 1}) is None:
            session.clear()
            return redirect(url_for('owner_login'))
        return view(*args, **kwargs)
    return wrapped

def owner_pharmacy():
    owner_id = ObjectId(session['owner'])
    return pharmacies.find_one({'owner_id': owner_id})

def verify_password_and_upgrade(account, collection, supplied_password):
    if not isinstance(supplied_password, str) or not supplied_password:
        return False
    stored_password = account.get('password', '')
    if stored_password.startswith(('pbkdf2:', 'scrypt:')):
        return check_password_hash(stored_password, supplied_password)
    if stored_password and stored_password == supplied_password:
        collection.update_one(
            {'_id': account['_id'], 'password': stored_password},
            {'$set': {'password': generate_password_hash(supplied_password)}}
        )
        return True
    return False

def record_user_search(user_id, query, radius_km, location_source, area, matches):
    search_history.insert_one({
        'user_id': ObjectId(user_id),
        'query': query,
        'created_at': datetime.now(timezone.utc),
        'radius_km': radius_km,
        'location_source': location_source,
        'area': area if location_source == 'manual_area' else None,
        'matching_pharmacies': matches
    })
    excess = search_history.count_documents({'user_id': ObjectId(user_id)}) - 100
    if excess > 0:
        oldest = list(search_history.find(
            {'user_id': ObjectId(user_id)}, {'_id': 1}
        ).sort('created_at', 1).limit(excess))
        if oldest:
            search_history.delete_many({'_id': {'$in': [item['_id'] for item in oldest]}})

_geocode_lock = threading.Lock()
_last_geocode_request = 0.0

@lru_cache(maxsize=128)
def geocode_location(location):
    global _last_geocode_request
    contact = os.getenv('GEOCODER_CONTACT', '').strip()
    user_agent = 'MediFind/1.0' + (f' ({contact})' if contact else '')
    url = 'https://nominatim.openstreetmap.org/search?' + urlencode({
        'q': location,
        'format': 'jsonv2',
        'limit': 1
    })
    with _geocode_lock:
        wait_seconds = 1.0 - (time.monotonic() - _last_geocode_request)
        if wait_seconds > 0:
            time.sleep(wait_seconds)
        _last_geocode_request = time.monotonic()
        try:
            request = Request(url, headers={'User-Agent': user_agent})
            with urlopen(request, timeout=8) as response:
                results = json.loads(response.read().decode('utf-8'))
        except Exception as error:
            app.logger.warning('Location lookup failed (%s)', type(error).__name__)
            raise RuntimeError('Location lookup is temporarily unavailable. Please try again or use browser location.') from None

    if not results:
        raise ValueError('We could not find that location. Try a more specific city, area, or PIN code.')

    result = results[0]
    latitude = float(result['lat'])
    longitude = float(result['lon'])
    if not valid_coordinates(latitude, longitude):
        raise ValueError('The location service returned invalid coordinates. Try another location.')
    label = coarse_area_label(result.get('address'), location)
    return latitude, longitude, label

@lru_cache(maxsize=128)
def reverse_geocode_location(latitude, longitude):
    global _last_geocode_request
    contact = os.getenv('GEOCODER_CONTACT', '').strip()
    user_agent = 'MediFind/1.0' + (f' ({contact})' if contact else '')
    url = 'https://nominatim.openstreetmap.org/reverse?' + urlencode({
        'lat': latitude,
        'lon': longitude,
        'format': 'jsonv2',
        'zoom': 14
    })
    with _geocode_lock:
        wait_seconds = 1.0 - (time.monotonic() - _last_geocode_request)
        if wait_seconds > 0:
            time.sleep(wait_seconds)
        _last_geocode_request = time.monotonic()
        try:
            request = Request(url, headers={'User-Agent': user_agent})
            with urlopen(request, timeout=8) as response:
                result = json.loads(response.read().decode('utf-8'))
        except Exception as error:
            app.logger.info('Reverse geocoding unavailable (%s)', type(error).__name__)
            return None
    return coarse_area_label(result.get('address'))

def resolve_search_location(data):
    latitude = data.get('lat', '').strip()
    longitude = data.get('lon', '').strip()
    if latitude or longitude:
        if not latitude or not longitude:
            raise ValueError('Both location coordinates are required.')
        try:
            coordinates = float(latitude), float(longitude)
        except ValueError:
            raise ValueError('The location coordinates are invalid.') from None
        if not valid_coordinates(*coordinates):
            raise ValueError('The location coordinates are outside valid latitude/longitude ranges.')
        return coordinates[0], coordinates[1], data.get('location_label', 'Your current location')

    location = data.get('location', '').strip()
    if not location:
        raise ValueError('Allow browser location or enter a city, area, or PIN code.')
    if len(location) > MAX_LOCATION_LENGTH:
        raise ValueError('Location text must be 200 characters or fewer.')
    return geocode_location(location)

def parse_search_radius(data):
    try:
        radius = int(data.get('radius_km', '10'))
    except (TypeError, ValueError):
        raise ValueError('Choose a valid search radius.') from None
    if radius not in {1, 3, 5, 10, 20}:
        raise ValueError('Choose a radius of 1, 3, 5, 10, or 20 km.')
    return radius

def find_nearby_medicine(query, user_lat, user_lon, radius_km):
    regex = {'$regex': re.escape(query), '$options': 'i'}
    matched_meds = list(medicines.find({'$or': [
        {'name': regex}, {'brand': regex}, {'search_keywords': regex}
    ]}))

    pharmacies_by_id = {}
    missing_pharmacy_location = 0
    out_of_stock_count = 0
    unverified_inventory_count = 0
    for medicine in matched_meds:
        if medicine.get('availability') == 'unavailable' or medicine.get('stock', 0) <= 0:
            out_of_stock_count += 1
            continue
        pharmacy_id = medicine.get('pharmacy_id')
        if not pharmacy_id:
            missing_pharmacy_location += 1
            continue
        pharmacy_key = str(pharmacy_id)
        if pharmacy_key not in pharmacies_by_id:
            pharmacy = pharmacies.find_one({'_id': pharmacy_id})
            if not pharmacy:
                missing_pharmacy_location += 1
                continue
            try:
                pharmacy_lat = float(pharmacy.get('latitude'))
                pharmacy_lon = float(pharmacy.get('longitude'))
            except (TypeError, ValueError):
                missing_pharmacy_location += 1
                continue
            if not valid_coordinates(pharmacy_lat, pharmacy_lon):
                missing_pharmacy_location += 1
                continue
            if not (pharmacy.get('is_demo') or pharmacy.get('verification_status') == 'approved'):
                continue
            distance = haversine(user_lat, user_lon, pharmacy_lat, pharmacy_lon)
            pharmacies_by_id[pharmacy_key] = {
                'pharmacy_name': pharmacy.get('name'),
                'pharmacy_address': pharmacy.get('address'),
                'pharmacy_phone': pharmacy.get('public_phone') if pharmacy.get('public_phone_verified') else None,
                'pharmacy_latitude': pharmacy_lat,
                'pharmacy_longitude': pharmacy_lon,
                'location_accuracy': pharmacy.get('location_accuracy'),
                'distance_km': round(distance, 2),
                'is_demo': pharmacy.get('is_demo', False) or pharmacy.get('name') == 'Demo Pharmacy',
                'verification_status': pharmacy.get('verification_status', 'demo' if pharmacy.get('is_demo') else 'pending'),
                'offers': [],
                '_offer_keys': set()
            }
        result = pharmacies_by_id[pharmacy_key]
        inventory_status = medicine.get('inventory_status')
        if not result['is_demo'] and inventory_status != 'owner_confirmed':
            unverified_inventory_count += 1
            continue
        offer_key = (
            (medicine.get('name') or '').strip().casefold(),
            (medicine.get('brand') or '').strip().casefold()
        )
        if offer_key in result['_offer_keys']:
            continue
        result['_offer_keys'].add(offer_key)
        result['offers'].append({
            'medicine_id': str(medicine['_id']),
            'medicine_name': medicine.get('name'),
            'brand': medicine.get('brand'),
            'price': medicine.get('price'),
            'stock': medicine.get('stock'),
            'availability': medicine.get('availability', 'available'),
            'inventory_status': (
                'demo_unverified' if medicine.get('inventory_status') == 'demo_unverified'
                or result['is_demo'] else inventory_status
            ),
            'last_updated_at': medicine.get('last_updated_at'),
            'availability_label': 'DEMO DATA — NOT VERIFIED LIVE STOCK' if result['is_demo'] or medicine.get('inventory_status') == 'demo_unverified' else 'Owner-confirmed inventory'
        })

    all_candidates = [pharmacy for pharmacy in pharmacies_by_id.values() if pharmacy['offers']]
    for pharmacy in all_candidates:
        pharmacy.pop('_offer_keys', None)
    nearby = [item for item in all_candidates if item['distance_km'] <= radius_km]
    nearby.sort(key=lambda item: item['distance_km'])
    return {
        'results': nearby,
        'matched_count': len(matched_meds),
        'candidate_pharmacy_count': len(all_candidates),
        'outside_radius_count': len(all_candidates) - len(nearby),
        'missing_pharmacy_location': missing_pharmacy_location,
        'out_of_stock_count': out_of_stock_count,
        'unverified_inventory_count': unverified_inventory_count,
        'inventory_empty': medicines.count_documents({}) == 0,
        'owner_pending_count': owners.count_documents({'status': 'pending_verification'})
    }

def seed_demo_inventory():
    owner_email = 'owner@demo.com'
    owners.update_one(
        {'email': owner_email},
        {'$setOnInsert': {
            'name': 'Demo Owner',
            'email': owner_email,
            'password': generate_password_hash(secrets.token_urlsafe(32)),
            'status': 'demo'
        }},
        upsert=True
    )
    owner = owners.find_one({'email': owner_email}, {'_id': 1})

    pharmacies.update_one(
        {'owner_id': owner['_id']},
        {'$setOnInsert': {
            'owner_id': owner['_id'],
            'name': 'Demo Pharmacy',
            'address': 'Demo St, Ballari',
            'latitude': 15.1450,
            'longitude': 76.9214,
            'distance_managed_km': 10.0,
            'is_demo': True
        }},
        upsert=True
    )
    pharmacy = pharmacies.find_one({'owner_id': owner['_id']}, {'_id': 1})

    sample_medicines = [
        {'name': 'Dolo 650', 'brand': 'Micro Labs', 'price': 30.0, 'stock': 50},
        {'name': 'Paracetamol 650 mg', 'brand': None, 'price': 12.0, 'stock': 50},
        {'name': 'Cetirizine 10 mg', 'brand': None, 'price': 20.0, 'stock': 30},
        {'name': 'Oral Rehydration Salts (ORS)', 'brand': None, 'price': 18.0, 'stock': 25},
        {'name': 'Antacid tablets', 'brand': None, 'price': 25.0, 'stock': 20},
        {'name': 'Ibuprofen 200 mg', 'brand': None, 'price': 22.0, 'stock': 15},
        {'name': 'Vitamin C tablets', 'brand': None, 'price': 35.0, 'stock': 18}
    ]
    inserted = 0
    for sample in sample_medicines:
        result = medicines.update_one(
            {'pharmacy_id': pharmacy['_id'], 'name': sample['name']},
            {'$setOnInsert': {
                'pharmacy_id': pharmacy['_id'], **sample,
                'availability': 'available',
                'inventory_status': 'demo_unverified',
                'last_updated_at': datetime.now(timezone.utc)
            }},
            upsert=True
        )
        inserted += result.upserted_id is not None
    return inserted

# --- Routes ---
@app.route('/')
def home():
    return render_template('index.html')

# --- User auth ---
@app.route('/user/register', methods=['GET','POST'])
def user_register():
    if request.method == 'POST':
        data = request.form
        email = data.get('email', '').strip().lower()
        role = data.get('role', 'user')
        supplied_password = data.get('password', '')
        name = data.get('name', '').strip()
        if (
            role not in {'user', 'owner'}
            or not name or len(name) > 120
            or not email or len(email) > 254 or '@' not in email
            or not isinstance(supplied_password, str)
            or not 8 <= len(supplied_password) <= 256
        ):
            flash('Enter a valid name and email, choose a role, and use a password between 8 and 256 characters.')
            return render_template('user_register.html'), 400

        try:
            # Check if email exists in either collection
            if users.find_one({'email': email}) or owners.find_one({'email': email}):
                flash('Email already registered')
                return render_template('user_register.html')

            user_data = {
                'name': name,
                'email': email,
                'password': generate_password_hash(supplied_password),
                'created_at': datetime.now(timezone.utc)
            }

            if role == 'owner':
                user_data['status'] = 'pending_verification'
                owners.insert_one(user_data)
                flash('Owner account submitted. Sign in to add pharmacy details; public listing remains pending verification.')
                return redirect(url_for('owner_login'))
            else:
                users.insert_one(user_data)
                flash('Registered successfully as user. Please login.')
                return redirect(url_for('user_login'))
        except PyMongoError as error:
            log_mongodb_error('MongoDB registration failed', error)
            return (
                'MediFind could not access the database. Please try again later.',
                503
            )
        except Exception:
            flash('An error occurred during registration. Please try again.')
            return render_template('user_register.html')
            
    return render_template('user_register.html')

@app.route('/user/login', methods=['GET','POST'])
def user_login():
    if request.method=='POST':
        email = request.form.get('email', '').strip().lower()
        pwd = request.form.get('password')
        if not email or len(email) > 254 or not isinstance(pwd, str) or len(pwd) > 256:
            flash('Invalid credentials')
            return render_template('user_login.html'), 400
        u = users.find_one({'email': email})
        if u and verify_password_and_upgrade(u, users, pwd):
            session.permanent = True
            session['user'] = str(u['_id'])
            session['role'] = 'user'
            return redirect(url_for('user_search'))
        flash('Invalid credentials')
    return render_template('user_login.html')

@app.route('/user/logout')
def user_logout():
    session.clear()
    return redirect(url_for('home'))

# --- Owner auth ---
@app.route('/owner/login', methods=['GET','POST'])
def owner_login():
    if request.method=='POST':
        email = request.form.get('email', '').strip().lower()
        pwd = request.form.get('password')
        if not email or len(email) > 254 or not isinstance(pwd, str) or len(pwd) > 256:
            flash('Invalid credentials')
            return render_template('owner_login.html'), 400
        o = owners.find_one({'email': email})
        if o and verify_password_and_upgrade(o, owners, pwd):
            session['owner'] = str(o['_id'])
            session['role'] = 'owner'
            return redirect(url_for('owner_dashboard'))
        flash('Invalid credentials')
    return render_template('owner_login.html')

@app.route('/owner/logout')
def owner_logout():
    session.clear()
    return redirect(url_for('home'))

@app.route('/owner/dashboard')
@owner_required
def owner_dashboard():
    o = owners.find_one({'_id': ObjectId(session['owner'])})
    my_pharm = pharmacies.find_one({'owner_id': o['_id']})
    meds = []
    if my_pharm:
        meds = list(medicines.find({'pharmacy_id': my_pharm['_id']}))
    return render_template('owner_dashboard.html', owner=o, pharmacy=my_pharm, medicines=meds)

@app.route('/owner/add_pharmacy', methods=['GET','POST'])
@owner_required
def owner_add_pharmacy():
    owner_obj = owners.find_one({'_id': ObjectId(session['owner'])})
    existing_pharmacy = pharmacies.find_one({'owner_id': owner_obj['_id']})
    if existing_pharmacy and not existing_pharmacy.get('is_demo'):
        flash('Your pharmacy is already registered. Use the dashboard to update its details.')
        return redirect(url_for('owner_dashboard'))
    if request.method=='POST':
        data = request.form
        pharmacy_name = data.get('name', '').strip()
        address = data.get('address', '').strip()
        if not pharmacy_name or len(pharmacy_name) > 160 or not address or len(address) > MAX_LOCATION_LENGTH:
            flash('Enter a pharmacy name (160 characters or fewer) and an address (200 characters or fewer).')
            return render_template('add_pharmacy.html', form_data=data), 400
        latitude = data.get('latitude', '').strip()
        longitude = data.get('longitude', '').strip()
        location_accuracy = 'owner-provided'
        if not latitude and not longitude:
            try:
                latitude, longitude, _ = geocode_location(address)
                location_accuracy = 'address-geocoded-approximate'
            except (ValueError, RuntimeError) as error:
                return render_template(
                    'add_pharmacy.html', form_data=data, location_error=str(error)
                ), 400
        else:
            try:
                latitude, longitude = float(latitude), float(longitude)
            except ValueError:
                flash('Enter valid latitude and longitude, or leave both blank to locate the address.')
                return render_template('add_pharmacy.html', form_data=data), 400
            if not valid_coordinates(latitude, longitude):
                flash('Coordinates must be valid latitude (-90 to 90) and longitude (-180 to 180).')
                return render_template('add_pharmacy.html', form_data=data), 400
        try:
            service_radius = float(data.get('distance_managed_km', 5.0) or 5.0)
        except (TypeError, ValueError):
            flash('Service radius must be a number from 1 to 100 km.')
            return render_template('add_pharmacy.html', form_data=data), 400
        if not math.isfinite(service_radius) or not 1 <= service_radius <= 100:
            flash('Service radius must be between 1 and 100 km.')
            return render_template('add_pharmacy.html', form_data=data), 400
        ph = {
            'owner_id': owner_obj['_id'],
            'name': pharmacy_name,
            'address': address,
            'public_phone': data.get('public_phone', '').strip() or None,
            'public_phone_verified': False,
            'latitude': latitude,
            'longitude': longitude,
            'location_accuracy': location_accuracy,
            'distance_managed_km': service_radius,
            'verification_status': 'pending',
            'public_listing': False,
            'is_demo': False,
            'created_at': datetime.now(timezone.utc)
        }
        if existing_pharmacy:
            pharmacies.update_one(
                {'_id': existing_pharmacy['_id'], 'owner_id': owner_obj['_id']},
                {'$set': ph, '$unset': {'verification_rejection_reason': ''}}
            )
        else:
            pharmacies.insert_one(ph)
        owners.update_one(
            {'_id': owner_obj['_id']},
            {'$set': {'status': 'pending_verification'}, '$unset': {'verification_rejection_reason': ''}}
        )
        flash('Pharmacy details saved for manual verification. It will not appear in public search until approved.')
        return redirect(url_for('owner_dashboard'))
    return render_template('add_pharmacy.html')

@app.route('/owner/pharmacy/update', methods=['POST'])
@owner_required
def owner_update_pharmacy():
    pharmacy = owner_pharmacy()
    if not pharmacy:
        return redirect(url_for('owner_add_pharmacy'))
    name = request.form.get('name', '').strip()
    address = request.form.get('address', '').strip()
    latitude_value = request.form.get('latitude', '').strip()
    longitude_value = request.form.get('longitude', '').strip()
    if not name or len(name) > 160 or not address or len(address) > MAX_LOCATION_LENGTH:
        flash('Enter a valid pharmacy name and address.')
        return redirect(url_for('owner_dashboard'))
    try:
        if latitude_value and longitude_value:
            latitude, longitude = float(latitude_value), float(longitude_value)
            accuracy = 'owner-provided'
        elif not latitude_value and not longitude_value:
            latitude, longitude, _ = geocode_location(address)
            accuracy = 'address-geocoded-approximate'
        else:
            raise ValueError('Provide both coordinates or leave both blank to geocode the address.')
    except (ValueError, RuntimeError) as error:
        flash(str(error))
        return redirect(url_for('owner_dashboard'))
    if not valid_coordinates(latitude, longitude):
        flash('Coordinates must be valid latitude (-90 to 90) and longitude (-180 to 180).')
        return redirect(url_for('owner_dashboard'))
    pharmacies.update_one(
        {'_id': pharmacy['_id'], 'owner_id': ObjectId(session['owner'])},
        {'$set': {
            'name': name,
            'address': address,
            'public_phone': request.form.get('public_phone', '').strip() or None,
            'public_phone_verified': False,
            'latitude': latitude,
            'longitude': longitude,
            'location_accuracy': accuracy,
            'verification_status': 'pending',
            'public_listing': False,
            'is_demo': False,
            'updated_at': datetime.now(timezone.utc)
        }, '$unset': {'verification_rejection_reason': ''}}
    )
    owners.update_one(
        {'_id': ObjectId(session['owner'])},
        {'$set': {'status': 'pending_verification'}, '$unset': {'verification_rejection_reason': ''}}
    )
    flash('Pharmacy details saved. Any location change requires verification before public display.')
    return redirect(url_for('owner_dashboard'))

@app.route('/owner/add_medicine', methods=['GET','POST'])
@owner_required
def owner_add_medicine():
    owner_obj = owners.find_one({'_id': ObjectId(session['owner'])})
    my_pharm = pharmacies.find_one({'owner_id': owner_obj['_id']})
    if not my_pharm:
        flash('Please add a pharmacy first.')
        return redirect(url_for('owner_add_pharmacy'))
    if request.method=='POST':
        data = request.form
        name = data.get('name', '').strip()
        brand = data.get('brand', '').strip()
        try:
            price_value = data.get('price', '').strip()
            price = float(price_value) if price_value else None
            stock = int(data.get('stock', ''))
        except (ValueError, TypeError):
            flash('Enter a valid non-negative price and whole-number stock quantity.')
            return render_template('add_medicine.html', pharmacy=my_pharm), 400
        if not name or len(name) > 160 or len(brand) > 120 or (price is not None and (price < 0 or not math.isfinite(price))) or stock < 0:
            flash('Medicine name is required; price and stock cannot be negative.')
            return render_template('add_medicine.html', pharmacy=my_pharm), 400
        med = {
            'pharmacy_id': my_pharm['_id'],
            'name': name,
            'brand': brand or None,
            'price': price,
            'stock': stock,
            'availability': 'available' if stock > 0 else 'unavailable',
            'inventory_status': 'owner_confirmed',
            'last_updated_at': datetime.now(timezone.utc),
            'notes': data.get('notes','').strip()
        }
        catalog_item = medicine_catalog.find_one({'name': name})
        if catalog_item:
            med.update({
                'catalog_id': catalog_item['_id'],
                'generic_name': catalog_item.get('generic_name'),
                'strength': catalog_item.get('strength'),
                'form': catalog_item.get('form'),
                'category': catalog_item.get('category'),
                'search_keywords': catalog_item.get('search_keywords', []),
                'prescription_required': catalog_item.get('prescription_required'),
                'prescription_status': catalog_item.get('prescription_status', 'not_assessed')
            })
        medicines.update_one(
            {
                'pharmacy_id': my_pharm['_id'],
                'name': {'$regex': f'^{re.escape(name)}$', '$options': 'i'},
                'brand': brand or None
            },
            {'$set': med},
            upsert=True
        )
        flash('Inventory saved. Any matching item at your pharmacy was updated instead of duplicated.')
        return redirect(url_for('owner_dashboard'))
    catalog = list(medicine_catalog.find({}, {'_id': 0, 'name': 1}).sort('name', 1))
    return render_template('add_medicine.html', pharmacy=my_pharm, catalog=catalog)

@app.route('/owner/inventory/<medicine_id>/update', methods=['POST'])
@owner_required
def owner_update_medicine(medicine_id):
    pharmacy = owner_pharmacy()
    if not pharmacy:
        return redirect(url_for('owner_add_pharmacy'))
    try:
        stock = int(request.form.get('stock', ''))
        price_value = request.form.get('price', '').strip()
        price = float(price_value) if price_value else None
        medicine_object_id = ObjectId(medicine_id)
    except (ValueError, TypeError):
        flash('Enter a valid stock quantity and price.')
        return redirect(url_for('owner_dashboard'))
    if stock < 0 or (price is not None and (price < 0 or not math.isfinite(price))):
        flash('Stock and price cannot be negative.')
        return redirect(url_for('owner_dashboard'))
    result = medicines.update_one(
        {'_id': medicine_object_id, 'pharmacy_id': pharmacy['_id']},
        {'$set': {
            'stock': stock,
            'price': price,
            'availability': 'available' if stock > 0 else 'unavailable',
            'inventory_status': 'owner_confirmed',
            'last_updated_at': datetime.now(timezone.utc)
        }}
    )
    flash('Inventory updated.' if result.matched_count else 'That inventory item does not belong to your pharmacy.')
    return redirect(url_for('owner_dashboard'))

@app.route('/owner/inventory/<medicine_id>/delete', methods=['POST'])
@owner_required
def owner_delete_medicine(medicine_id):
    pharmacy = owner_pharmacy()
    if not pharmacy:
        return redirect(url_for('owner_add_pharmacy'))
    try:
        medicine_object_id = ObjectId(medicine_id)
    except Exception:
        return 'Invalid inventory item.', 400
    result = medicines.delete_one({'_id': medicine_object_id, 'pharmacy_id': pharmacy['_id']})
    flash('Inventory item removed.' if result.deleted_count else 'That inventory item does not belong to your pharmacy.')
    return redirect(url_for('owner_dashboard'))

# --- Search ---
@app.route('/user/search')
@user_required
def user_search():
    return render_template('search.html', query=request.args.get('query', ''), location='')

@app.route('/api/geocode', methods=['POST'])
@user_required
def api_geocode():
    location = request.form.get('location', '').strip()
    if not location:
        return jsonify({'error': 'Enter a city, area, or PIN code.'}), 400
    try:
        latitude, longitude, display_name = geocode_location(location)
    except ValueError as error:
        return jsonify({'error': str(error)}), 404
    except RuntimeError as error:
        return jsonify({'error': str(error)}), 502
    return jsonify({
        'latitude': latitude,
        'longitude': longitude,
        'display_name': display_name,
        'approximate': True
    })

@app.route('/api/reverse-geocode', methods=['POST'])
@user_required
def api_reverse_geocode():
    try:
        latitude = float(request.form.get('lat', ''))
        longitude = float(request.form.get('lon', ''))
    except (TypeError, ValueError):
        return jsonify({'error': 'Valid coordinates are required.'}), 400
    if not valid_coordinates(latitude, longitude):
        return jsonify({'error': 'Coordinates are outside valid latitude/longitude ranges.'}), 400
    label = reverse_geocode_location(latitude, longitude)
    return jsonify({'label': label})

@app.route('/search_results', methods=['POST'])
@user_required
def search_results():
    data = request.form
    query = data.get('query', '').strip()
    try:
        user_lat, user_lon, location_label = resolve_search_location(data)
        radius_km = parse_search_radius(data)
    except ValueError as error:
        return render_template(
            'search.html', query=query, location=data.get('location', ''),
            location_error=str(error)
        ), 400
    except RuntimeError as error:
        return render_template(
            'search.html', query=query, location=data.get('location', ''),
            location_error=str(error)
        ), 502

    # if image uploaded
    if 'image' in request.files and request.files['image'] and allowed_file(request.files['image'].filename):
        img = request.files['image']
        filename = secure_filename(img.filename)
        saved_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        img.save(saved_path)
        # NOTE: Image-to-text (OCR) can be integrated here (tesseract/pytesseract).
        # For now we will not rely on OCR in the server; front-end can show uploaded image.
        # This prototype will prioritise 'query' text if present.
        # If you want OCR, install tesseract and pytesseract and uncomment integration.

    if query == '':
        return render_template('search.html', query='', location=data.get('location', ''), location_error='Enter a medicine name to search.'), 400

    search = find_nearby_medicine(query, user_lat, user_lon, radius_km)
    location_source = 'manual_area' if data.get('location', '').strip() else 'browser'
    record_user_search(
        session['user'], query, radius_km, location_source,
        location_label if location_source == 'manual_area' else None,
        len(search['results'])
    )
    app.logger.info(
        'Medicine search: matched=%d, pharmacies=%d, radius_km=%d, missing_coordinates=%d',
        search['matched_count'], len(search['results']), radius_km,
        search['missing_pharmacy_location']
    )
    return render_template(
        'search_results.html',
        results=search['results'],
        query=query,
        inventory_empty=search['inventory_empty'],
        matched_count=search['matched_count'],
        candidate_pharmacy_count=search['candidate_pharmacy_count'],
        outside_radius_count=search['outside_radius_count'],
        missing_pharmacy_location=search['missing_pharmacy_location'],
        unverified_inventory_count=search['unverified_inventory_count'],
        radius_km=radius_km,
        user_lat=user_lat,
        user_lon=user_lon,
        map_data=[{
            'pharmacy_name': item['pharmacy_name'],
            'pharmacy_address': item['pharmacy_address'],
            'pharmacy_phone': item['pharmacy_phone'],
            'pharmacy_latitude': item['pharmacy_latitude'],
            'pharmacy_longitude': item['pharmacy_longitude'],
            'distance_km': item['distance_km'],
            'is_demo': item['is_demo'],
            'offers': [{
                'medicine_name': offer['medicine_name'],
                'brand': offer['brand'],
                'price': offer['price'],
                'stock': offer['stock'],
                'availability_label': offer['availability_label']
            } for offer in item['offers']]
        } for item in search['results']],
        location_label=location_label,
        location_approximate=data.get('location_approximate') == 'true' or bool(data.get('location', '').strip())
    )

@app.route('/api/search', methods=['POST'])
@user_required
def api_search():
    data = request.form.to_dict()
    query = data.get('query', '').strip()
    try:
        user_lat, user_lon, location_label = resolve_search_location(data)
        radius_km = parse_search_radius(data)
    except ValueError as error:
        return jsonify({'error': str(error)}), 400
    except RuntimeError as error:
        return jsonify({'error': str(error)}), 502

    # if image uploaded
    if 'image' in request.files and request.files['image'] and allowed_file(request.files['image'].filename):
        img = request.files['image']
        filename = secure_filename(img.filename)
        saved_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        img.save(saved_path)
        # NOTE: Image-to-text (OCR) can be integrated here (tesseract/pytesseract).
        # For now we will not rely on OCR in the server; front-end can show uploaded image.
        # This prototype will prioritise 'query' text if present.
        # If you want OCR, install tesseract and pytesseract and uncomment integration.

    if query == '':
        return jsonify({'error':'Empty query'}), 400

    search = find_nearby_medicine(query, user_lat, user_lon, radius_km)
    location_source = 'manual_area' if data.get('location', '').strip() else 'browser'
    record_user_search(
        session['user'], query, radius_km, location_source,
        location_label if location_source == 'manual_area' else None,
        len(search['results'])
    )
    app.logger.info(
        'Medicine API search: matched=%d, pharmacies=%d, radius_km=%d, missing_coordinates=%d',
        search['matched_count'], len(search['results']), radius_km,
        search['missing_pharmacy_location']
    )
    return jsonify({
        **search,
        'location_label': location_label,
        'radius_km': radius_km
    })

@app.route('/user/history')
@user_required
def user_search_history():
    query_filter = request.args.get('q', '').strip()
    user_id = ObjectId(session['user'])
    criteria = {'user_id': user_id}
    if query_filter:
        criteria['query'] = {'$regex': re.escape(query_filter), '$options': 'i'}
    entries = list(search_history.find(criteria).sort('created_at', -1).limit(100))
    return render_template('search_history.html', entries=entries, query_filter=query_filter)

@app.route('/user/profile')
@user_required
def user_profile():
    user = users.find_one({'_id': ObjectId(session['user'])}, {'name': 1, 'email': 1, 'created_at': 1})
    return render_template('user_profile.html', user=user)

@app.route('/user/history/<history_id>/delete', methods=['POST'])
@user_required
def delete_search_history_item(history_id):
    try:
        entry_id = ObjectId(history_id)
    except Exception:
        return 'Invalid search history entry.', 400
    search_history.delete_one({'_id': entry_id, 'user_id': ObjectId(session['user'])})
    return redirect(url_for('user_search_history'))

@app.route('/user/history/clear', methods=['POST'])
@user_required
def clear_search_history():
    search_history.delete_many({'user_id': ObjectId(session['user'])})
    return redirect(url_for('user_search_history'))

# --- Simple seed endpoint to add a sample owner and pharmacy (for first-time demo) ---
@app.route('/seed_demo')
def seed_demo():
    return (
        'Demo data is not added automatically by a browser request. '
        'Run python seed_sample.py --apply from the project virtual environment; '
        'the records are marked DEMO DATA — NOT VERIFIED LIVE STOCK.'
    ), 405

if __name__ == '__main__':
    debug_enabled = os.getenv('FLASK_DEBUG', 'false').strip().lower() == 'true'
    app.run(debug=debug_enabled, host='127.0.0.1', port=5000)
