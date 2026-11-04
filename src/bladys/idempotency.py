import hashlib
import json


def canonicalize_order_intent(quantities):
    # Keep the original serialization for flavorless orders, including existing keys.
    lines = []
    for identity, quantity in quantities.items():
        variant_id, flavors = (identity, ()) if isinstance(identity, int) else identity
        flavors = tuple(sorted(flavors))
        lines.append((variant_id, quantity) if not flavors else (variant_id, quantity, flavors))
    return sorted(lines)


def build_request_hash(quantities):
    canonical = canonicalize_order_intent(quantities)
    serialized = json.dumps(canonical)
    encoded = serialized.encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()