import importlib.metadata
import os
import socket
import ssl
import sys
from pathlib import Path
from urllib.parse import quote_plus

import certifi
import dns.resolver
from dotenv import load_dotenv
from pymongo import MongoClient
from pymongo.errors import PyMongoError

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / '.env')


def classify_failure(error):
    message = str(error).lower()
    if 'tlsv1_alert' in message or 'ssl handshake' in message:
        return 'TLS handshake failed; check network inspection/firewall/VPN and Atlas availability.'
    if 'certificate_verify_failed' in message or 'certificate verify failed' in message:
        return 'Certificate verification failed; check Python/OpenSSL and trusted CA bundle.'
    if 'authentication failed' in message or 'auth failed' in message:
        return 'Atlas rejected authentication; check the Atlas database user and password.'
    if 'serverselectiontimeouterror' in type(error).__name__.lower() or 'timed out' in message:
        return 'Server selection timed out; check DNS, outbound TCP 27017, Atlas IP access list, and network.'
    return 'MongoDB operation failed; inspect Atlas status, access list, and driver configuration.'


def main():
    print('Python executable:', sys.executable)
    print('Python version:', sys.version.split()[0])
    print('OpenSSL:', ssl.OPENSSL_VERSION)
    for package in ('pymongo', 'dnspython', 'certifi', 'python-dotenv'):
        try:
            print(f'{package}:', importlib.metadata.version(package))
        except importlib.metadata.PackageNotFoundError:
            print(f'{package}: not installed')

    username = os.getenv('MONGO_USERNAME')
    password = os.getenv('MONGO_PASSWORD')
    cluster = os.getenv('MONGO_CLUSTER')
    missing = [key for key, value in (
        ('MONGO_USERNAME', username),
        ('MONGO_PASSWORD', password),
        ('MONGO_CLUSTER', cluster)
    ) if not value]
    if missing:
        print('Configuration missing:', ', '.join(missing))
        return 2
    print('Atlas environment variables: present (values suppressed)')

    try:
        records = list(dns.resolver.resolve(f'_mongodb._tcp.{cluster}', 'SRV', lifetime=6))
        nodes = [(str(record.target).rstrip('.'), int(record.port)) for record in records]
        print('Atlas SRV DNS: PASS; node count:', len(nodes))
    except Exception as error:
        print('Atlas SRV DNS: FAIL;', type(error).__name__)
        print('Next: check DNS resolver/network; credentials and URI were not displayed.')
        return 1

    tcp_passes = 0
    tls_passes = 0
    tls_errors = []
    context = ssl.create_default_context(cafile=certifi.where())
    for host, port in nodes:
        try:
            raw_socket = socket.create_connection((host, port), timeout=5)
            tcp_passes += 1
        except OSError:
            continue
        try:
            with context.wrap_socket(raw_socket, server_hostname=host):
                tls_passes += 1
        except ssl.SSLError as error:
            tls_errors.append(getattr(error, 'reason', 'SSL error'))
            raw_socket.close()
        except OSError as error:
            tls_errors.append(type(error).__name__)
            raw_socket.close()
    print(f'Atlas TCP port 27017: {tcp_passes}/{len(nodes)} nodes reachable')
    print(f'Atlas verified TLS handshake: {tls_passes}/{len(nodes)} nodes succeeded')
    if tls_errors:
        print('TLS failure category:', tls_errors[0])

    uri = (
        f'mongodb+srv://{quote_plus(username)}:{quote_plus(password)}@{cluster}/'
        '?appName=MediFindCluster&compressors=zlib'
    )
    client = MongoClient(uri, serverSelectionTimeoutMS=10000, tlsCAFile=certifi.where())
    try:
        result = client.admin.command('ping')
        print('MongoDB Atlas ping:', 'PASS' if result.get('ok') == 1 else 'FAIL')
        return 0 if result.get('ok') == 1 else 1
    except PyMongoError as error:
        print('MongoDB Atlas ping: FAIL;', type(error).__name__)
        print('Diagnosis:', classify_failure(error))
        print('Raw exception, credentials, and full URI suppressed.')
        return 1
    finally:
        client.close()


if __name__ == '__main__':
    raise SystemExit(main())
