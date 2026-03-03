from dotenv import load_dotenv
import os
import uuid

load_dotenv()

headers = {
    "x-api-key": os.getenv("ETORO_API_KEY"),
    "x-user-key": os.getenv("ETORO_USER_KEY"),
    "x-request-id": str(uuid.uuid4()),
}
