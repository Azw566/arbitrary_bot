"""
flashbots_relay.py — Optional Flashbots MEV-protection relay submission.

Provides bundle-based transaction submission to the Flashbots relay endpoint
so that trades are not visible in the public mempool and are therefore protected
from front-running / sandwich attacks.

Usage
-----
Set the FLASHBOTS_AUTH_KEY environment variable to a hex private key that will
be used *only* to sign bundles for the relay.  It does NOT need to hold ETH; it
is purely an identity key.

If FLASHBOTS_AUTH_KEY is not set the helper functions return None/False and the
caller falls back to normal RPC submission.

No external Flashbots library is required — bundles are submitted via a plain
HTTP POST using the standard eth_account and requests libraries that are already
in requirements.txt.

Relay endpoints
---------------
Mainnet : https://relay.flashbots.net
Sepolia  : https://relay-sepolia.flashbots.net
"""

import json
import logging
import os
from typing import Optional

import requests
from eth_account import Account
from web3 import Web3

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Relay endpoints
# ---------------------------------------------------------------------------

FLASHBOTS_RELAY_MAINNET = "https://relay.flashbots.net"
FLASHBOTS_RELAY_SEPOLIA = "https://relay-sepolia.flashbots.net"

# Override via environment variable if desired
FLASHBOTS_RELAY_URL = os.getenv("FLASHBOTS_RELAY_URL", FLASHBOTS_RELAY_MAINNET)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_auth_key() -> Optional[str]:
    """Return the Flashbots auth key from environment, or None if not set."""
    return os.getenv("FLASHBOTS_AUTH_KEY")


def _sign_flashbots_payload(body: bytes, auth_key: str) -> str:
    """
    Sign a Flashbots relay payload.

    The Flashbots relay expects the header:
        X-Flashbots-Signature: <address>:<hex_signature>

    The signature is over keccak256(body) with the auth key.
    """
    body_hash = Web3.keccak(body)
    # eth_account sign_hash produces a SignedMessage
    signed = Account.sign_message(
        # Flashbots uses the Ethereum signed-message prefix
        _eth_signed_message_hash(body_hash),
        private_key=auth_key,
    )
    address = Account.from_key(auth_key).address
    return f"{address}:{signed.signature.hex()}"


def _eth_signed_message_hash(message_hash: bytes):
    """
    Wrap a 32-byte hash in the Ethereum personal_sign prefix so that
    eth_account.sign_message can consume it.
    """
    from eth_account.messages import encode_defunct
    # encode_defunct expects the raw bytes that will be prefixed
    return encode_defunct(message_hash)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_flashbots_provider(w3: Web3) -> Optional[object]:
    """
    Returns a lightweight Flashbots context object if FLASHBOTS_AUTH_KEY is set,
    otherwise returns None (signals: use normal submission).

    The returned object is currently just a dict carrying config; the actual
    submission goes through submit_bundle().
    """
    auth_key = _get_auth_key()
    if not auth_key:
        return None
    try:
        address = Account.from_key(auth_key).address
        relay_url = FLASHBOTS_RELAY_URL
        chain_id = w3.eth.chain_id
        # Pick correct relay URL based on chain if not overridden by env
        if not os.getenv("FLASHBOTS_RELAY_URL"):
            relay_url = (FLASHBOTS_RELAY_SEPOLIA
                         if chain_id == 11155111
                         else FLASHBOTS_RELAY_MAINNET)
        logger.info(
            "Flashbots provider initialised | auth_address=%s | relay=%s | chain=%s",
            address, relay_url, chain_id,
        )
        return {
            "auth_key":  auth_key,
            "address":   address,
            "relay_url": relay_url,
            "chain_id":  chain_id,
        }
    except Exception as exc:
        logger.error("Failed to initialise Flashbots provider: %s", exc)
        return None


def build_flashbots_bundle(signed_tx: bytes, block_number: int) -> dict:
    """
    Build a Flashbots bundle dict from a single signed raw transaction.

    Parameters
    ----------
    signed_tx    : raw signed transaction bytes (from w3.eth.account.sign_transaction)
    block_number : the target block number for inclusion

    Returns
    -------
    bundle dict suitable for passing to submit_bundle()
    """
    return {
        "txs":              [signed_tx.hex() if isinstance(signed_tx, (bytes, bytearray))
                             else signed_tx],
        "blockNumber":      hex(block_number),
        "minTimestamp":     0,
        "maxTimestamp":     0,
        "revertingTxHashes": [],
    }


def submit_bundle(bundle: dict, fb_provider: dict, dry_run: bool = True) -> bool:
    """
    Submit a Flashbots bundle to the relay endpoint.

    Parameters
    ----------
    bundle      : dict produced by build_flashbots_bundle()
    fb_provider : dict produced by get_flashbots_provider()
    dry_run     : if True, logs the bundle but does NOT post to the relay

    Returns
    -------
    True on success (or dry-run), False on failure.
    """
    if fb_provider is None:
        logger.warning("submit_bundle called without a valid Flashbots provider.")
        return False

    auth_key  = fb_provider["auth_key"]
    relay_url = fb_provider["relay_url"]

    payload = {
        "jsonrpc": "2.0",
        "id":      1,
        "method":  "eth_sendBundle",
        "params":  [bundle],
    }

    body = json.dumps(payload).encode("utf-8")

    if dry_run:
        logger.info(
            "[FLASHBOTS DRY RUN] Would POST to %s | block=%s | txs=%d",
            relay_url,
            bundle.get("blockNumber", "?"),
            len(bundle.get("txs", [])),
        )
        return True

    try:
        signature_header = _sign_flashbots_payload(body, auth_key)
        headers = {
            "Content-Type":           "application/json",
            "X-Flashbots-Signature":  signature_header,
        }
        resp = requests.post(relay_url, data=body, headers=headers, timeout=5)
        resp_json = resp.json()

        if "error" in resp_json:
            logger.error(
                "Flashbots relay error: code=%s message=%s",
                resp_json["error"].get("code"),
                resp_json["error"].get("message"),
            )
            return False

        bundle_hash = resp_json.get("result", {}).get("bundleHash", "unknown")
        logger.info(
            "[FLASHBOTS] Bundle submitted | block=%s | bundleHash=%s",
            bundle.get("blockNumber"), bundle_hash,
        )
        return True

    except requests.RequestException as exc:
        logger.error("Flashbots HTTP error: %s", exc)
        return False
    except Exception as exc:
        logger.error("Unexpected error in submit_bundle: %s", exc)
        return False
