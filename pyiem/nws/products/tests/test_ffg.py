"""Testing FFG parsing."""

import psycopg2.extras
import pytest
from pyiem.nws.products.ffg import parser as ffgparser
from pyiem.util import get_dbconn, get_test_file


@pytest.fixture
def dbcursor():
    """Return a database cursor."""
    return get_dbconn("postgis").cursor(cursor_factory=psycopg2.extras.DictCursor)


def test_ffg(dbcursor):
    """FFG"""
    prod = ffgparser(get_test_file("FFGJAN.txt"))
    prod.sql(dbcursor)
    assert len(prod.data.index) == 53


def test_ffg2(dbcursor):
    """FFGKY"""
    prod = ffgparser(get_test_file("FFGKY.txt"))
    prod.sql(dbcursor)
    assert len(prod.data.index) == 113


def test_ffgama(dbcursor):
    """FFGAMA"""
    prod = ffgparser(get_test_file("FFGAMA.txt"))
    prod.sql(dbcursor)
    assert len(prod.data.index) == 23
