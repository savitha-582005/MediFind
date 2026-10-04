
import os
import certifi
from urllib.parse import quote_plus
from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

username = os.getenv("MONGO_USERNAME")
password = os.getenv("MONGO_PASSWORD")
cluster = os.getenv("MONGO_CLUSTER")

if not all([username, password, cluster]):
    raise ValueError("MongoDB credentials are missing from .env")

uri = (
    f"mongodb+srv://{quote_plus(username)}:"
    f"{quote_plus(password)}@{cluster}/"
    "?appName=MediFindCluster&compressors=zlib"
)

try:
    client = MongoClient(
        uri,
        serverSelectionTimeoutMS=10000,
        tlsCAFile=certifi.where()
    )
    print(client.admin.command("ping"))
    print("MongoDB Atlas connected successfully!")
except Exception as e:
    details = str(e)
    for secret in (uri, username, password, quote_plus(username), quote_plus(password)):
        if secret:
            details = details.replace(secret, "[redacted]")
    print("MongoDB connection failed:", type(e).__name__, details)
    raise SystemExit(1)
finally:
    if "client" in globals():
        client.close()