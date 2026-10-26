import hashlib
import json


def canonicalize_order_intent(quantities):
    return sorted(quantities.items())


def build_request_hash(quantities):
    canonical = canonicalize_order_intent(quantities)
    serialized = json.dumps(canonical)
    encoded = serialized.encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()