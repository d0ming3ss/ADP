"""Testy pobierania i walidacji danych (sieć jest atrapą — bez dostępu do Internetu)."""

import gzip
import json

import pytest
import requests

from georent import data_source as ds
from georent.config import INSIDE_AIRBNB_INDEX_URL, get_city
from tests.conftest import make_grid, make_listings, write_geojson, write_listings_gz

RELEASE = "2026-09-17"


class FakeResponse:
    def __init__(self, content: bytes = b"", status: int = 200, text: str = ""):
        self.content, self.status_code, self.text = content, status, text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def iter_content(self, chunk_size=1):
        yield self.content

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeSession:
    """Zwraca zadane odpowiedzi wg końcówki URL; zapisuje listę zapytań."""

    def __init__(self, files: dict[str, bytes], index_html: str | None = None, status: dict[str, int] | None = None,
                 error: Exception | None = None):
        self.files, self.index_html, self.status, self.error, self.calls = files, index_html, status or {}, error, []

    def get(self, url, stream=False, timeout=None, **kwargs):
        self.calls.append(url)
        if self.error:
            raise self.error
        if url == INSIDE_AIRBNB_INDEX_URL:
            if self.index_html is None:
                raise requests.ConnectionError("offline")
            return FakeResponse(text=self.index_html)
        for suffix, code in self.status.items():
            if url.endswith(suffix):
                return FakeResponse(status=code)
        for suffix, content in self.files.items():
            if url.endswith(suffix):
                return FakeResponse(content)
        return FakeResponse(status=404)


@pytest.fixture
def good_files(tmp_path_factory):
    d = tmp_path_factory.mktemp("src")
    grid = make_grid(3)
    write_listings_gz(d / "l.csv.gz", make_listings(grid, per_district=3))
    write_geojson(d / "n.geojson", grid)
    (d / "n.csv").write_text("neighbourhood_group,neighbourhood\nA,D00\n", encoding="utf-8")
    return {"listings.csv.gz": (d / "l.csv.gz").read_bytes(), "neighbourhoods.geojson": (d / "n.geojson").read_bytes(),
            "neighbourhoods.csv": (d / "n.csv").read_bytes()}


def index_for(date):
    city = get_city("barcelona")
    return f'<a href="https://data.insideairbnb.com/{city.path}/{date}/data/listings.csv.gz">x</a>'


def test_discover_latest_release():
    html = index_for("2026-03-01") + index_for("2026-09-17") + "<a href='https://data.insideairbnb.com/portugal/x/'>"
    assert ds.discover_latest_release(get_city("barcelona"), FakeSession({}, html)) == "2026-09-17"


def test_discover_returns_none_when_index_offline_or_city_missing():
    assert ds.discover_latest_release(get_city("barcelona"), FakeSession({}, None)) is None
    assert ds.discover_latest_release(get_city("lisbon"), FakeSession({}, index_for("2026-09-17"))) is None


def test_download_saves_files_and_metadata(settings, good_files):
    session = FakeSession(good_files, index_for(RELEASE))
    meta = ds.download_dataset(settings, session=session)
    assert meta.release == RELEASE and meta.listings_rows == 27 and meta.neighbourhood_features == 9
    for name in ds.REMOTE_FILES:
        assert meta.path(name).exists()
        assert len(meta.files[name]["sha256"]) == 64
    assert meta.downloaded_at.endswith("+00:00") and "CC BY" in meta.license
    assert ds.get_local_dataset(settings).release == RELEASE
    assert ds.local_cities(settings) == ["barcelona"]
    assert not list((settings.raw_dir / "barcelona").glob(".*partial"))


def test_download_is_cached_and_force_redownloads(settings, good_files):
    session = FakeSession(good_files, index_for(RELEASE))
    ds.download_dataset(settings, session=session)
    n_first = len(session.calls)
    ds.download_dataset(settings, session=session)
    assert len(session.calls) == n_first + 1  # tylko zapytanie o indeks, bez pobierania plików
    ds.download_dataset(settings, session=session, force=True)
    assert len(session.calls) > n_first + 1 + 3


def test_download_uses_fallback_release_when_index_unavailable(settings, good_files):
    session = FakeSession(good_files, None)
    meta = ds.download_dataset(settings, session=session)
    assert meta.release == get_city("barcelona").fallback_release


def test_download_http_404_raises_and_leaves_no_partial(settings, good_files):
    session = FakeSession({k: v for k, v in good_files.items() if k != "neighbourhoods.geojson"}, index_for(RELEASE))
    with pytest.raises(ds.DataSourceError, match="404"):
        ds.download_dataset(settings, session=session)
    assert ds.get_local_dataset(settings) is None
    assert not list((settings.raw_dir / "barcelona").glob("*"))


def test_download_server_error(settings, good_files):
    session = FakeSession(good_files, index_for(RELEASE), status={"listings.csv.gz": 503})
    with pytest.raises(ds.DataSourceError, match="503"):
        ds.download_dataset(settings, session=session)


@pytest.mark.parametrize("error, text", [(requests.Timeout("t"), "limit czasu"), (requests.ConnectionError("c"), "połączenia")])
def test_download_network_errors(settings, good_files, error, text):
    session = FakeSession(good_files, index_for(RELEASE))
    session.error = None
    original_get = session.get

    def get(url, **kw):
        if url == INSIDE_AIRBNB_INDEX_URL:
            return original_get(url, **kw)
        raise error
    session.get = get
    with pytest.raises(ds.DataSourceError, match=text):
        ds.download_dataset(settings, session=session)


def test_download_corrupted_gzip(settings, good_files):
    files = dict(good_files, **{"listings.csv.gz": b"to nie jest gzip"})
    with pytest.raises(ds.DataValidationError):
        ds.download_dataset(settings, session=FakeSession(files, index_for(RELEASE)))
    assert ds.get_local_dataset(settings) is None


def test_download_missing_columns(settings, good_files):
    import io
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb") as fh:
        fh.write(b"id,latitude\n1,41.3\n")
    files = dict(good_files, **{"listings.csv.gz": buf.getvalue()})
    with pytest.raises(ds.DataValidationError, match="brakuje wymaganych kolumn"):
        ds.download_dataset(settings, session=FakeSession(files, index_for(RELEASE)))


def test_download_invalid_geojson(settings, good_files):
    for bad in (b"{nie json", json.dumps({"type": "FeatureCollection", "features": []}).encode(),
                json.dumps({"type": "Point", "coordinates": [0, 0]}).encode()):
        files = dict(good_files, **{"neighbourhoods.geojson": bad})
        with pytest.raises(ds.DataValidationError):
            ds.download_dataset(settings, session=FakeSession(files, index_for(RELEASE)))


def test_validate_empty_listings(tmp_path, listings):
    p = tmp_path / "l.csv.gz"
    write_listings_gz(p, listings.iloc[0:0])
    with pytest.raises(ds.DataValidationError, match="nie zawiera żadnych ofert"):
        ds.validate_listings_file(p)


def test_validate_missing_file(tmp_path):
    with pytest.raises(ds.DataValidationError):
        ds.validate_listings_file(tmp_path / "brak.csv.gz")
    with pytest.raises(ds.DataValidationError):
        ds.validate_geojson_file(tmp_path / "brak.geojson")


def test_check_for_update(settings, good_files, monkeypatch):
    ds.download_dataset(settings, session=FakeSession(good_files, index_for("2026-03-01")))
    monkeypatch.setattr(ds, "_session", lambda: FakeSession(good_files, index_for("2026-09-17")))
    remote, local = ds.check_for_update(settings)
    assert (remote, local) == ("2026-09-17", "2026-03-01")


def test_unknown_city_is_error(settings):
    with pytest.raises(ValueError, match="Nieznane miasto"):
        ds.download_dataset(settings, "atlantyda", session=FakeSession({}, None))
