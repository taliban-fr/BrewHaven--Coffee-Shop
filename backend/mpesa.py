"""Safaricom Daraja helpers: M-Pesa Express (STK Push)."""
import base64
import os
import time
from datetime import datetime, timedelta, timezone

import requests

ENV = os.getenv("MPESA_ENV", "sandbox")  # "sandbox" or "production"
BASE_URL = (
    "https://api.safaricom.co.ke" if ENV == "production" else "https://sandbox.safaricom.co.ke"
)
CONSUMER_KEY = os.getenv("MPESA_CONSUMER_KEY", "")
CONSUMER_SECRET = os.getenv("MPESA_CONSUMER_SECRET", "")
SHORTCODE = os.getenv("MPESA_SHORTCODE", "")
PASSKEY = os.getenv("MPESA_PASSKEY", "")
CALLBACK_URL = os.getenv("MPESA_CALLBACK_URL", "")
CALLBACK_SECRET = os.getenv("MPESA_CALLBACK_SECRET", "dev-secret")
TRANSACTION_TYPE = os.getenv("MPESA_TRANSACTION_TYPE", "CustomerPayBillOnline")
PARTY_B = os.getenv("MPESA_PARTY_B", SHORTCODE)

ENABLED = all([CONSUMER_KEY, CONSUMER_SECRET, SHORTCODE, PASSKEY, CALLBACK_URL])

NAIROBI = timezone(timedelta(hours=3))
_token = {"value": "", "expires": 0.0}


class MpesaError(Exception):
    """Something went wrong talking to M-Pesa. The message is safe to show customers."""


def _access_token() -> str:
    if _token["value"] and time.time() < _token["expires"]:
        return _token["value"]
    try:
        res = requests.get(
            f"{BASE_URL}/oauth/v1/generate",
            params={"grant_type": "client_credentials"},
            auth=(CONSUMER_KEY, CONSUMER_SECRET),
            timeout=20,
        )
        data = res.json()
        token = data["access_token"]
    except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
        print(f"[MPESA] Could not get an access token: {exc!r}")
        raise MpesaError("Could not connect to M-Pesa. Please try again.") from exc
    _token["value"] = token
    _token["expires"] = time.time() + int(data.get("expires_in", 3599)) - 60
    return token


def _post(path: str, payload: dict) -> dict:
    try:
        res = requests.post(
            f"{BASE_URL}{path}",
            json=payload,
            headers={"Authorization": f"Bearer {_access_token()}"},
            timeout=20,
        )
        return res.json()
    except (requests.RequestException, ValueError) as exc:
        print(f"[MPESA] Request to {path} failed: {exc!r}")
        raise MpesaError("Could not connect to M-Pesa. Please try again.") from exc


def _password() -> tuple[str, str]:
    timestamp = datetime.now(NAIROBI).strftime("%Y%m%d%H%M%S")
    raw = f"{SHORTCODE}{PASSKEY}{timestamp}"
    return base64.b64encode(raw.encode("utf-8")).decode("utf-8"), timestamp


def stk_push(order_id: int, phone: str, amount_kes: int) -> str:
    """Send the payment prompt to the customer's phone. Returns the CheckoutRequestID."""
    password, timestamp = _password()
    data = _post(
        "/mpesa/stkpush/v1/processrequest",
        {
            "BusinessShortCode": SHORTCODE,
            "Password": password,
            "Timestamp": timestamp,
            "TransactionType": TRANSACTION_TYPE,
            "Amount": amount_kes,
            "PartyA": phone,
            "PartyB": PARTY_B,
            "PhoneNumber": phone,
            "CallBackURL": CALLBACK_URL,
            "AccountReference": f"ORD{order_id}",
            "TransactionDesc": "BrewHaven",
        },
    )
    if str(data.get("ResponseCode")) != "0" or "CheckoutRequestID" not in data:
        print(f"[MPESA] STK push rejected: {data}")  # details stay in your terminal
        raise MpesaError("M-Pesa could not start the payment. Please try again.")
    return data["CheckoutRequestID"]


def stk_query(checkout_id: str) -> str:
    """Ask Safaricom how a payment went. Returns 'paid', 'failed' or 'pending'."""
    password, timestamp = _password()
    data = _post(
        "/mpesa/stkpushquery/v1/query",
        {
            "BusinessShortCode": SHORTCODE,
            "Password": password,
            "Timestamp": timestamp,
            "CheckoutRequestID": checkout_id,
        },
    )
    if "ResultCode" in data:
        return "paid" if str(data["ResultCode"]) == "0" else "failed"
    return "pending"  # Safaricom replies with an error while the customer is still deciding