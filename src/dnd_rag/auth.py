"""Who is asking: verify Clerk session tokens, or fall back to the client's IP.

The site signs users in with Clerk entirely in the browser and sends the
session token as `Authorization: Bearer <jwt>`. This module verifies that
token against Clerk's public signing keys (JWKS), so the API needs only the
publishable key, never Clerk's secret key. Requests without a token are
anonymous and identified by IP address, read from `X-Forwarded-For` when the
API runs behind proxies (see `client_ip`).
"""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass

import jwt

# Shared with the site (Astro exposes PUBLIC_* variables to the browser).
PUBLISHABLE_KEY_ENV = "PUBLIC_CLERK_PUBLISHABLE_KEY"
# Optional comma-separated origins allowed to mint tokens, e.g.
# "http://localhost:4321,https://warlock.example". Checked against the
# token's `azp` claim when set.
AUTHORIZED_PARTIES_ENV = "CLERK_AUTHORIZED_PARTIES"
# Tolerate small clock differences between Clerk and this server.
CLOCK_LEEWAY_SECONDS = 5
# How many proxies in front of the API append to X-Forwarded-For: 0 when run
# directly, 1 on Cloud Run, 2 behind Firebase Hosting and then Cloud Run.
TRUSTED_PROXY_HOPS_ENV = "TRUSTED_PROXY_HOPS"


class AuthError(Exception):
    """The request carried a token, but it is not a valid Clerk session token."""


@dataclass(frozen=True)
class Identity:
    """The caller of one request.

    `key` identifies whose budget a request draws on: `user:<clerk id>` or
    `ip:<address>`. Sessions and usage are both keyed by it.
    """

    key: str
    user_id: str | None

    @property
    def is_signed_in(self) -> bool:
        return self.user_id is not None


def anonymous(ip: str) -> Identity:
    return Identity(key=f"ip:{ip}", user_id=None)


def client_ip(peer: str, forwarded_for: str | None, trusted_hops: int) -> str:
    """The caller's IP address, for telling anonymous callers apart.

    Each trusted proxy appends the address it received the request from, so
    the entry `trusted_hops` from the right is the one the outermost proxy
    saw: the caller. Entries left of it come from the caller and can be
    forged, so they are ignored. With no trusted proxies, or a header too
    short to hold that entry, the TCP peer is the best we have.
    """
    if trusted_hops == 0 or forwarded_for is None:
        return peer
    addresses = []
    for address in forwarded_for.split(","):
        if address.strip() != "":
            addresses.append(address.strip())
    if len(addresses) < trusted_hops:
        return peer
    return addresses[-trusted_hops]


def trusted_hops_from_env() -> int:
    """The `TRUSTED_PROXY_HOPS` setting, or 0 if unset."""
    setting = os.environ.get(TRUSTED_PROXY_HOPS_ENV, "").strip()
    if setting == "":
        return 0
    hops = int(setting)
    if hops < 0:
        raise ValueError(f"{TRUSTED_PROXY_HOPS_ENV} must not be negative")
    return hops


def frontend_api_from_publishable_key(publishable_key: str) -> str:
    """Decode the Clerk Frontend API host embedded in a publishable key.

    A key looks like `pk_test_<base64("<host>$")>`; the host serves the JWKS.
    """
    parts = publishable_key.split("_", 2)
    if len(parts) != 3 or parts[0] != "pk":
        raise ValueError(f"{PUBLISHABLE_KEY_ENV} is not a Clerk publishable key")
    encoded = parts[2]
    padding = "=" * (-len(encoded) % 4)
    decoded = base64.b64decode(encoded + padding).decode()
    if not decoded.endswith("$"):
        raise ValueError(f"{PUBLISHABLE_KEY_ENV} is not a Clerk publishable key")
    return decoded.removesuffix("$")


class ClerkVerifier:
    """Verifies Clerk session JWTs and returns the signed-in user's id."""

    def __init__(self, publishable_key: str, authorized_parties: list[str]):
        host = frontend_api_from_publishable_key(publishable_key)
        # PyJWKClient caches the keys and refetches when it sees a new key id.
        self._jwks = jwt.PyJWKClient(f"https://{host}/.well-known/jwks.json")
        self._authorized_parties = authorized_parties

    def verify(self, token: str) -> str:
        try:
            signing_key = self._jwks.get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                leeway=CLOCK_LEEWAY_SECONDS,
                options={"require": ["exp", "nbf", "sub"]},
            )
        except jwt.PyJWTError as error:
            raise AuthError(str(error)) from error

        if len(self._authorized_parties) > 0:
            if claims.get("azp") not in self._authorized_parties:
                raise AuthError("Token was issued for an unknown origin")
        return claims["sub"]


def verifier_from_env() -> ClerkVerifier | None:
    """A verifier configured from the environment, or None if Clerk is not set up."""
    publishable_key = os.environ.get(PUBLISHABLE_KEY_ENV, "").strip()
    if publishable_key == "":
        return None
    parties_setting = os.environ.get(AUTHORIZED_PARTIES_ENV, "")
    authorized_parties = []
    for party in parties_setting.split(","):
        if party.strip() != "":
            authorized_parties.append(party.strip())
    return ClerkVerifier(publishable_key, authorized_parties)


def bearer_token(authorization: str | None) -> str | None:
    """The token from an `Authorization: Bearer <token>` header, if present."""
    if authorization is None:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or token.strip() == "":
        return None
    return token.strip()
