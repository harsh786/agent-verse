"""Public-key sealed-bid envelopes for registry-backed auctions (AUCTION-KEYS).

Each auction has an X25519 key pair: the public key is published with the
announcement, the private key never leaves the server (persisted only as vault
ciphertext). A bidder seals its ``BidPayload`` with ECIES — an ephemeral X25519
exchange, HKDF-SHA256 and AES-256-GCM bound to ``(auction, bidder, version)`` —
so nobody, other bidders included, can read a bid before the auctioneer unseals
it at close. Each bid is also signed with the bidder's registry-issued HMAC
secret, so a bid cannot be forged or replayed under another bidder's name.

Envelope wire format (all base64):
* ``ciphertext`` = ephemeral public key (32 bytes) || AES-GCM ciphertext+tag
* ``nonce``      = 12-byte AES-GCM nonce
* ``signature``  = hex HMAC-SHA256 over ``auction:bidder:version:nonce:ciphertext``
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.coordination.auction.models import BidPayload


class BidEnvelopeError(ValueError):
    """The envelope is malformed, forged or cannot be opened."""


def _context(auction_id: str, bidder_id: str, version: int) -> bytes:
    return f"auction-bid-v1:{auction_id}:{bidder_id}:{version}".encode()


def _derive(shared: bytes, context: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=context).derive(shared)


def generate_key_pair() -> tuple[str, str]:
    """(private key, public key), each raw 32 bytes base64-encoded."""
    private = X25519PrivateKey.generate()
    raw_private = private.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )
    raw_public = private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    return base64.b64encode(raw_private).decode(), base64.b64encode(raw_public).decode()


def new_signing_secret() -> str:
    return base64.b64encode(secrets.token_bytes(32)).decode()


def sign_envelope(
    signing_secret: str,
    *,
    auction_id: str,
    bidder_id: str,
    version: int,
    nonce: str,
    ciphertext: str,
) -> str:
    message = f"{auction_id}:{bidder_id}:{version}:{nonce}:{ciphertext}".encode()
    return hmac.new(base64.b64decode(signing_secret), message, hashlib.sha256).hexdigest()


def verify_signature(
    signing_secret: str,
    *,
    auction_id: str,
    bidder_id: str,
    version: int,
    nonce: str,
    ciphertext: str,
    signature: str,
) -> bool:
    expected = sign_envelope(
        signing_secret,
        auction_id=auction_id,
        bidder_id=bidder_id,
        version=version,
        nonce=nonce,
        ciphertext=ciphertext,
    )
    return hmac.compare_digest(expected, signature)


def seal_bid(
    public_key: str,
    payload: BidPayload,
    *,
    auction_id: str,
    bidder_id: str,
    version: int,
    signing_secret: str,
) -> tuple[str, str, str]:
    """Bidder side: (ciphertext, nonce, signature) for the public bid API."""
    ephemeral = X25519PrivateKey.generate()
    peer = X25519PublicKey.from_public_bytes(base64.b64decode(public_key))
    context = _context(auction_id, bidder_id, version)
    key = _derive(ephemeral.exchange(peer), context)
    nonce_bytes = secrets.token_bytes(12)
    sealed = AESGCM(key).encrypt(nonce_bytes, payload.model_dump_json().encode(), context)
    ephemeral_public = ephemeral.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    ciphertext = base64.b64encode(ephemeral_public + sealed).decode()
    nonce = base64.b64encode(nonce_bytes).decode()
    signature = sign_envelope(
        signing_secret,
        auction_id=auction_id,
        bidder_id=bidder_id,
        version=version,
        nonce=nonce,
        ciphertext=ciphertext,
    )
    return ciphertext, nonce, signature


def open_bid(
    private_key: str,
    *,
    auction_id: str,
    bidder_id: str,
    version: int,
    nonce: str,
    ciphertext: str,
) -> BidPayload:
    """Auctioneer side (after close): decrypt and validate one envelope."""
    try:
        raw = base64.b64decode(ciphertext, validate=True)
        nonce_bytes = base64.b64decode(nonce, validate=True)
        if len(raw) <= 32 or len(nonce_bytes) != 12:
            raise BidEnvelopeError("malformed bid envelope")
        private = X25519PrivateKey.from_private_bytes(base64.b64decode(private_key))
        peer = X25519PublicKey.from_public_bytes(raw[:32])
        context = _context(auction_id, bidder_id, version)
        key = _derive(private.exchange(peer), context)
        plaintext = AESGCM(key).decrypt(nonce_bytes, raw[32:], context)
        return BidPayload.model_validate_json(plaintext)
    except BidEnvelopeError:
        raise
    except Exception as exc:
        raise BidEnvelopeError(f"bid envelope cannot be opened: {type(exc).__name__}") from exc


__all__ = [
    "BidEnvelopeError",
    "generate_key_pair",
    "new_signing_secret",
    "open_bid",
    "seal_bid",
    "sign_envelope",
    "verify_signature",
]
