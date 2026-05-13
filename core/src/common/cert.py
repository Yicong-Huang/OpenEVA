"""Cert / auth-token framework.

Eva tracks the remaining validity of one or more credentials (OAuth
tokens, SSH certs, signed-runtime certs, ...) and surfaces them as
status pills + auto-renew attempts. The framework here owns the
abstract `CertProvider` contract + the in-memory registry +
`get_certs()` / `renew_cert()` dispatchers; concrete providers live
in extension namespaces:

    core/src/certs/                    # OSS defaults (currently empty)
    <extension>/src/certs/*.py         # per-extension providers

Each provider module self-registers via `register_cert(provider)` at
import time. `server.py` (and `conftest.py` for tests) call
`discover_certs(*dirs)` once at startup to import every `.py` file
under each `certs/` directory -- identical shape to the Agent
layer's discovery (see `agent`).

Why a separate registry from plugins / agents:
  - plugins are UI widgets (FastAPI routes + scheduled jobs)
  - agents are CLI binaries Eva shells out to
  - certs are credentials whose lifecycle Eva tracks + nudges
Each kind has its own protocol, its own registry, its own
discovery. No `kind` enum that the framework has to special-case.
"""

from __future__ import annotations

import importlib
from typing import Protocol, runtime_checkable

import app_state


# Shared default: 16h warning threshold. Providers override via
# class attribute when their renewal cadence differs.
CERT_WARNING_SECS = 16 * 3600


@runtime_checkable
class CertProvider(Protocol):
    """Minimum surface every cert provider implements.

    Concrete classes typically subclass `CertProviderBase` for the
    default `status()` / `to_dict()` helpers, then override `check`
    + (optionally) `renew`.

    Attributes:
      key            stable identifier used in the routes (`/api/certs/renew/<key>`)
                     and as the dedup id in the registry. Lowercase
                     ASCII; no spaces.
      name           human-readable display name shown in the
                     AuthStatus dropdown.
      warning_secs   threshold below which the cert is "warning"
                     instead of "ok". Default: 16h.
      auto_renewable when True, the cert checker tries `renew()`
                     once any time the cert dips into the warning
                     window. Providers that need interactive
                     browser-based SSO opt out (`False`).
    """

    key: str
    name: str
    warning_secs: int
    auto_renewable: bool

    def check(self) -> int:
        """Return remaining seconds, -1 on error. Should NOT raise --
        the framework catches exceptions, but cleaner errors come
        from a typed `RuntimeError` with a user-actionable hint
        (e.g. 'Run: <auth-tool> login --host X')."""
        ...

    def renew(self) -> bool:
        """Attempt auto-renew. Returns True on success.

        Providers that can't auto-renew (interactive SSO required)
        either return False or raise `RuntimeError` with a hint."""
        ...


class CertProviderBase:
    """Convenience base providing status mapping + to_dict shape.

    Concrete providers override `check()` and `renew()`. The class
    attributes (`key`, `name`, ...) MUST be set by the subclass so
    `register_cert()` can dedup correctly.
    """

    key: str = ""
    name: str = ""
    warning_secs: int = CERT_WARNING_SECS
    auto_renewable: bool = True

    def check(self) -> int:
        raise NotImplementedError

    def renew(self) -> bool:
        return False

    def status(self, remaining: int) -> str:
        if remaining < 0:
            return "error"
        if remaining == 0:
            return "expired"
        return "ok" if remaining > self.warning_secs else "warning"

    def to_dict(self, remaining: int) -> dict:
        # `key` is the route-side identifier; the frontend's "Renew"
        # button must POST to /api/certs/renew/<key>, NOT /<name>
        # (display name has spaces, base-URL-host names get URL-
        # encoded, and the route handler does an exact-equality
        # lookup on the provider's `key`).
        return {
            "key": self.key,
            "name": self.name,
            "remaining_seconds": remaining,
            "status": self.status(remaining),
        }


# ---------------------------------------------------------------
# Registry
# ---------------------------------------------------------------

_registered: list = []
_seen_keys: set[str] = set()
# Per-key last status, used to emit events only on transition.
_last_cert_status: dict[str, str] = {}


def register_cert(provider) -> None:
    """Register a cert provider. Idempotent on `provider.key` so
    re-imports during tests / hot reloads don't double-fire."""
    pk = getattr(provider, "key", "") or provider.__class__.__name__
    if pk in _seen_keys:
        return
    _seen_keys.add(pk)
    _registered.append(provider)


def all_certs() -> list:
    """Snapshot of registered providers, in registration order."""
    return list(_registered)


def reset_for_tests() -> None:
    """Clear the cert registry + last-status cache so per-test
    fixtures see a clean slate."""
    _registered.clear()
    _seen_keys.clear()
    _last_cert_status.clear()


# ---------------------------------------------------------------
# Discovery (filesystem scan, mirrors agent.discover_agents)
# ---------------------------------------------------------------

def discover_certs(*directories) -> int:
    """Load every `*.py` file directly under each directory so its
    `register_cert(...)` call fires.

    Mirrors `agent.discover_agents()`: files are loaded by
    absolute path via `importlib.util.spec_from_file_location` so
    cert modules under `<extension>/src/certs/` don't need to be
    importable via sys.path (which would collide with an
    extension's other top-level modules).

    Files starting with `_` are skipped. Missing directories are
    a no-op so an OSS-only install ships without any cert
    implementations and `get_certs()` simply returns `{}`.
    """
    import importlib.util
    import sys
    from pathlib import Path
    n = 0
    for d in directories:
        p = Path(d)
        if not p.is_dir():
            continue
        for py_path in sorted(p.glob("*.py")):
            if py_path.name.startswith("_"):
                continue
            unique_name = f"_eva_cert_{p.parent.name}_{py_path.stem}"
            try:
                existing = sys.modules.get(unique_name)
                spec = (getattr(existing, "__spec__", None)
                        if existing is not None else None)
                if spec is None or spec.loader is None:
                    spec = importlib.util.spec_from_file_location(
                        unique_name, py_path,
                    )
                if spec is None or spec.loader is None:
                    continue
                if existing is not None:
                    mod = existing
                else:
                    mod = importlib.util.module_from_spec(spec)
                    sys.modules[unique_name] = mod
                # Friendly alias = file stem (e.g. `mycert`).
                # Lets tests grab the provider class via
                # `import <stem>; <stem>.<MyProviderClass>`.
                # Only set when no unrelated top-level module of the
                # same name already exists, so a real package
                # collision surfaces instead of being silently
                # shadowed.
                alias_existing = sys.modules.get(py_path.stem)
                if (alias_existing is None
                        or getattr(alias_existing, "__file__", "")
                        == str(py_path)):
                    sys.modules[py_path.stem] = mod
                spec.loader.exec_module(mod)
                n += 1
            except Exception as e:
                print(f"[cert] load {py_path} failed: {e}", flush=True)
    return n


# ---------------------------------------------------------------
# get_certs / renew_cert -- the dispatchers consumed by routes +
# services. Moved here from `system` so cert wiring is fully
# contained in the framework.
# ---------------------------------------------------------------

def get_certs() -> dict:
    """Check every registered cert provider; return `{key: dict}`.

    For each cert that just transitioned status (ok -> warning /
    expired, or warning/expired -> ok), emit a deduped
    `auth.cert_*` event so the frontend notifications surface the
    change without polling.

    For each `auto_renewable` provider currently in the warning
    window, attempt `renew()` once. Renewal failures log but never
    take peer providers down -- a broken OAuth flow shouldn't
    shadow a healthy SSH cert."""
    certs: dict = {}

    for provider in _registered:
        check_error = ""
        try:
            remaining = provider.check()
        except Exception as e:
            remaining = -1
            check_error = str(e)
        entry = provider.to_dict(remaining)
        # Surface the check-time error as `note` so the AuthStatus
        # dropdown shows actionable text ("Run: <auth-tool> login")
        # instead of an opaque red dot. Distinct from the auto-renew
        # `note` below; either populates the same slot.
        if check_error:
            entry["note"] = check_error
        certs[provider.key] = entry

    # Emit events only on status transitions.
    for key, cert_data in certs.items():
        new_status = cert_data.get("status", "ok")
        prev_status = _last_cert_status.get(key)
        if new_status == prev_status:
            continue
        _last_cert_status[key] = new_status
        name = cert_data.get("name", key)
        if new_status == "expired":
            app_state.emit_event("auth.cert_expired", {
                "title": f"{name} expired",
                "severity": "error",
                "source_id": f"cert-{key}",
            })
        elif new_status == "warning":
            hours = cert_data.get("remaining_seconds", 0) // 3600
            app_state.emit_event("auth.cert_expiring", {
                "title": f"{name} expiring in {hours}h",
                "severity": "warning",
                "source_id": f"cert-{key}",
            })
        elif new_status == "ok" and prev_status in ("expired", "warning"):
            app_state.emit_event("auth.cert_renewed", {
                "title": f"{name} renewed",
                "severity": "info",
                "source_id": f"cert-{key}",
            })

    # Auto-renew: try to refresh any cert below warning threshold.
    # Providers that require human interaction (browser-based SSO)
    # opt out via `auto_renewable = False`; otherwise we'd hang
    # their subprocess waiting for an OAuth callback and leak
    # zombie listeners on subsequent polls.
    for provider in _registered:
        if not getattr(provider, "auto_renewable", True):
            continue
        secs = certs.get(provider.key, {}).get(
            "remaining_seconds", provider.warning_secs + 1,
        )
        if 0 < secs < provider.warning_secs:
            try:
                if provider.renew():
                    certs[provider.key]["note"] = "auto-renewed"
                    certs[provider.key]["status"] = "ok"
                    certs[provider.key]["remaining_seconds"] = \
                        provider.warning_secs + 1
                    print(f"[cert] {provider.name} auto-renewed", flush=True)
            except Exception as e:
                print(f"[cert] {provider.name} auto-renew failed: {e}",
                      flush=True)

    return certs


def renew_cert(cert_id: str) -> dict | None:
    """Manually renew a cert by key. Returns result dict or None if
    no provider with that key is registered."""
    for provider in _registered:
        if provider.key == cert_id:
            try:
                ok = provider.renew()
                return {"ok": ok, "output": "renewed" if ok else "renew failed"}
            except Exception as e:
                return {"ok": False, "output": str(e)}
    return None


