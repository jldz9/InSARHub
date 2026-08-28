# -*- coding: utf-8 -*-
"""Earthdata-authenticated IONEX (TEC) download for ISCE3_Burst.

COMPASS's ``compass.utils.iono.download_ionex`` fetches IONEX maps from CDDIS
(``cddis.nasa.gov``) with a bare, UNauthenticated ``requests.get``. CDDIS
requires NASA Earthdata Login (URS), so that request is redirected to the URS
login page and the "download" saves an HTML page instead of the file -- the
ionospheric timing correction then silently goes missing from every geocoded
burst (a missing TEC file is non-fatal in COMPASS, so nothing errors).

This module reuses COMPASS's exact filename/URL logic (``get_ionex_filename``),
so the files land at the identical paths the cslc runconfigs point at, but
performs the fetch through an Earthdata-authenticated session built from
``~/.netrc``'s ``machine urs.earthdata.nasa.gov`` entry -- the same credential
store the ASF downloader already populates and uses.
"""

from __future__ import annotations

import datetime as dt
import netrc
import os
import subprocess
from pathlib import Path
from urllib.parse import urlparse

import requests

_URS_HOST = "urs.earthdata.nasa.gov"

# CDDIS switched IONEX file naming ~2023-10-18; COMPASS tries both formats and
# so do we (via get_ionex_filename's is_new_filename_format flag).
_NEW_FILENAME_FORMAT_FROM = dt.datetime.fromisoformat("2023-10-18")


class _EarthdataSession(requests.Session):
    """Carry Earthdata Basic auth through the CDDIS -> URS OAuth redirect chain,
    but strip it on any cross-host hop that is neither URS nor the original host
    (NASA's documented pattern), so credentials never leak to a third-party
    redirect target."""

    def __init__(self, username: str, password: str):
        super().__init__()
        self.auth = (username, password)

    def rebuild_auth(self, prepared_request, response):  # noqa: D401
        headers = prepared_request.headers
        if "Authorization" in headers:
            orig = urlparse(response.request.url).hostname
            redir = urlparse(prepared_request.url).hostname
            if orig != redir and redir != _URS_HOST and orig != _URS_HOST:
                del headers["Authorization"]
        return


def _earthdata_creds() -> tuple[str, str] | None:
    """(login, password) for urs.earthdata.nasa.gov, from ~/.netrc, or, failing
    that, the EARTHDATA_USERNAME / EARTHDATA_PASSWORD environment variables.

    The env fallback matters in the container: the processor is re-invoked with
    HOME set to the (bind-mounted) workdir, so ~/.netrc there is the workdir's,
    not the host user's -- passing the two env vars into `docker run` is then the
    way to hand Earthdata credentials to the TEC stage.
    """
    try:
        auth = netrc.netrc().authenticators(_URS_HOST)
    except (FileNotFoundError, netrc.NetrcParseError):
        auth = None
    if auth:
        login, _, password = auth
        if login and password:
            return (login, password)

    user = os.environ.get("EARTHDATA_USERNAME")
    pw = os.environ.get("EARTHDATA_PASSWORD")
    return (user, pw) if user and pw else None


def have_earthdata_creds() -> bool:
    """Whether ~/.netrc carries a usable urs.earthdata.nasa.gov entry."""
    return _earthdata_creds() is not None


# Real global IONEX maps are hundreds of KB uncompressed; a URS login/error page
# is ~11 KB, and a truncated download is smaller still. The size floor sits well
# between them.
_MIN_IONEX_BYTES = 100_000


def is_valid_ionex(path) -> bool:
    """True only for a real, usable (uncompressed) IONEX map on disk.

    An IONEX filename is trivial to fake: when CDDIS is hit without Earthdata
    auth it returns an HTML login page, and COMPASS happily saved that under the
    correct ``*.INX``/``*.INX.gz`` name -- so a filename match alone is not
    evidence the map is present. This looks at the bytes:

    - a still-compressed file (``.gz`` / ``.Z``) is not usable as-is (COMPASS
      and the geocoder read the uncompressed text), so it does not count;
    - an HTML page (login/error) is rejected;
    - the content must carry the IONEX header label;
    - and the file must be at least :data:`_MIN_IONEX_BYTES` (catches truncated
      downloads and the ~11 KB login page).
    """
    p = Path(path)
    name = p.name.lower()
    if name.endswith((".gz", ".z")):
        return False
    try:
        if p.stat().st_size < _MIN_IONEX_BYTES:
            return False
        with open(p, "rb") as fh:
            head = fh.read(1024)
    except OSError:
        return False
    if head.lstrip().startswith(b"<"):        # HTML login/error page
        return False
    return b"IONEX" in head                    # the "IONEX VERSION / TYPE" header


def download_ionex_earthdata(date_str: str, tec_dir, sol_code: str = "jpl",
                             date_fmt: str = "%Y%m%d") -> str:
    """Earthdata-authenticated drop-in for ``compass.utils.iono.download_ionex``.

    Downloads the IONEX map for ``date_str``/``sol_code`` from CDDIS through an
    Earthdata-authenticated session and decompresses it in place.

    Returns the path to the local uncompressed IONEX file. Raises on failure
    (no credentials, no map from CDDIS for this date/centre, or a login page
    served instead of the file).
    """
    from compass.utils.iono import get_ionex_filename

    creds = _earthdata_creds()
    if creds is None:
        raise RuntimeError(
            "No Earthdata credentials for CDDIS IONEX download. Add a "
            "'machine urs.earthdata.nasa.gov' entry to ~/.netrc (the same one "
            "the ASF/S1 downloader uses).")

    tec_dir = Path(tec_dir)
    tec_dir.mkdir(parents=True, exist_ok=True)
    session = _EarthdataSession(*creds)

    date_tec = dt.datetime.strptime(date_str, date_fmt)
    use_new = date_tec >= _NEW_FILENAME_FORMAT_FROM

    last_err: Exception | None = None
    # Try the era-appropriate filename format first, then the other one.
    for is_new in (use_new, not use_new):
        kwargs = dict(sol_code=sol_code, date_fmt=date_fmt,
                      is_new_filename_format=is_new, check_if_exists=False)
        src = get_ionex_filename(date_str, tec_dir=None, **kwargs)
        dst_uncomp = get_ionex_filename(date_str, tec_dir=str(tec_dir), **kwargs)
        ext = src[src.rfind("."):]
        dst_comp = dst_uncomp + ext
        try:
            r = session.get(src, stream=True, timeout=120, allow_redirects=True)
            r.raise_for_status()
            # A URS login page comes back as 200 text/html, not the compressed
            # IONEX file -- reject it rather than saving a bogus file that the
            # decompress step would then choke on.
            if "html" in r.headers.get("Content-Type", "").lower():
                raise RuntimeError(f"got an HTML page (likely URS login) from {src}")
            with open(dst_comp, "wb") as f:
                for chunk in r.iter_content(chunk_size=1 << 16):
                    if chunk:
                        f.write(chunk)
            # Decompress via the gzip CLI (as COMPASS does) -- it handles both
            # the newer .gz and the legacy .Z (LZW) files, unlike Python's gzip
            # module which cannot read .Z.
            subprocess.run(["gzip", "--force", "--decompress", dst_comp],
                           capture_output=True, text=True, check=True)
            if not is_valid_ionex(dst_uncomp):
                raise RuntimeError(
                    f"downloaded file is not a valid IONEX map: {dst_uncomp}")
            return dst_uncomp
        except Exception as exc:  # noqa: BLE001  -- try the other format
            last_err = exc
            for p in (dst_comp, dst_uncomp):
                try:
                    os.remove(p)
                except OSError:
                    pass
            continue

    raise RuntimeError(
        f"IONEX download failed for {date_str} ({sol_code}): {last_err}")
