#!/usr/bin/env python3

import hmac
from hashlib import sha256

def get_signature(key, value):
    return hmac.new(key, value.encode(), sha256).hexdigest()

def make_token(key, value):
    return "-".join((get_signature(key, value), value))

def check_token(key, token):
    "Return the value if the token is valid, otherwise None."
    mac, value = token.split('-', 1)
    if hmac.compare_digest(mac, get_signature(key, value)):
        return value
