"""Support library for the IEM Reanalysis code

.. data:: SOUTH

    Latitude of the southern edge of the IEM Reanalysis.


"""
from __future__ import print_function
import datetime

from affine import Affine
import numpy as np
import pytz
import xarray as xr
from six import string_types
from pyiem.util import get_dbconn

# 1/8 degree grid, grid cell is the lower left corner
SOUTH = 23.0
WEST = -126.0
NORTH = 50.0
EAST = -65.0

DX = 0.125
DY = 0.125
# hard coding these to prevent flakey behaviour with dynamic computation
NX = 488  # int((EAST - WEST) / DX)
NY = 216  # int((NORTH - SOUTH) / DY)
XAXIS = np.arange(WEST, EAST, DX)
YAXIS = np.arange(SOUTH, NORTH, DY)
AFFINE = Affine(
    DX, 0., WEST, 0., 0 - DY, NORTH
)
MRMS_AFFINE = Affine(
    0.01, 0., WEST, 0., -0.01, NORTH
)


def get_table(valid):
    """Figure out which table should be used for given valid.

    Args:
      valid (datetime or date):  which time is of interest

    Returns:
      str tablename
    """
    # careful here, a datetime is not an instance of date
    if isinstance(valid, datetime.datetime):
        table = "iemre_hourly_%s" % (
            valid.astimezone(pytz.UTC).strftime("%Y%m"), )
    else:
        table = "iemre_daily_%s" % (valid.year, )
    return table


def set_grids(valid, ds, cursor=None):
    """Update the database with a given ``xarray.Dataset``.

    Args:
      valid (datetime or date): If datetime, save hourly, if date, save daily
      ds (xarray.Dataset): The xarray dataset to save
      cursor (database cursor, optional): cursor to use for queries
    """
    table = get_table(valid)
    commit = (cursor is None)
    if cursor is None:
        pgconn = get_dbconn('iemre')
        cursor = pgconn.cursor()
    # see that we have database entries, otherwise create them
    cursor.execute("""
        SELECT valid from """ + table + """ WHERE valid = %s LIMIT 1
    """, (valid, ))
    if cursor.rowcount != 1:
        cursor.execute("""
            INSERT into """ + table + """ (gid, valid)
            SELECT gid, %s from iemre_grid
        """, (valid, ))
    update_cols = ", ".join(["%s = %%s" % (v, ) for v in ds])

    def _n(val):
        """Prevent nan"""
        return None if np.isnan(val) else float(val)

    # Implementation notes: Dataset iteration here is ~25 secs, total ~60s
    for y in range(ds.dims['y']):
        for x in range(ds.dims['x']):
            # needed for python2.7 support as (*[list], arg, arg) no worky
            arr = [_n(ds[v].values[y, x]) for v in ds]
            arr.extend([y * NX + x, valid])
            cursor.execute("""
                UPDATE """ + table + """ SET """ + update_cols + """
                WHERE gid = %s and valid = %s
            """, arr)
    # If we generate a cursor, we should save it
    if commit:
        cursor.close()
        pgconn.commit()


def get_grids(valid, varnames=None, cursor=None):
    """Fetch grid(s) from the database, returning xarray.

    Args:
      valid (datetime or date): If datetime, load hourly, if date, load daily
      varnames (str or list,optional): Which variables to fetch from database,
        defaults to all available
      cursor (database cursor,optional): cursor to use for query

    Returns:
      ``xarray.Dataset``"""
    table = get_table(valid)
    if cursor is None:
        pgconn = get_dbconn('iemre')
        cursor = pgconn.cursor()
    # rectify varnames
    if isinstance(varnames, string_types):
        varnames = [varnames, ]
    # Compute variable names
    cursor.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = %s and
        column_name not in ('gid', 'valid')
    """, (table, ))
    use_columns = []
    for row in cursor:
        if not varnames or row[0] in varnames:
            use_columns.append(row[0])
    colsql = ",".join(use_columns)
    cursor.execute("""
        SELECT (gid / %s)::int as y, gid %% %s as x,
        """ + colsql + """ from """ + table + """ WHERE valid = %s
    """, (NX, NX, valid))
    data = dict(
        (key, np.full((NY, NX), np.nan)) for key in use_columns
    )
    for row in cursor:
        for i, col in enumerate(use_columns):
            data[col][row[0], row[1]] = row[2 + i]
    ds = xr.Dataset(
        dict((key, (['y', 'x'], data[key])) for key in data),
        coords={'lon': (['x'], XAXIS), 'lat': (['y'], YAXIS)}
    )
    return ds


def get_dailyc_ncname():
    """Return the filename of the daily climatology netcdf file"""
    return "/mesonet/data/iemre/iemre_dailyc.nc"


def get_daily_ncname(year):
    """Get the daily netcdf filename for the given year"""
    return "/mesonet/data/iemre/%s_iemre_daily.nc" % (year, )


def get_dailyc_mrms_ncname():
    """Get the MRMS daily climatology filename"""
    return "/mesonet/data/iemre/iemre_mrms_dailyc.nc"


def get_daily_mrms_ncname(year):
    """Get the daily netcdf MRMS filename for the given year"""
    return "/mesonet/data/iemre/%s_iemre_mrms_daily.nc" % (year, )


def get_hourly_ncname(year):
    """Get the daily netcdf filename for the given year"""
    return "/mesonet/data/iemre/%s_iemre_hourly.nc" % (year, )


def daily_offset(ts):
    """ Compute the timestamp index in the netcdf file """
    # In case ts is passed here as a datetime.date object
    ts = datetime.datetime(ts.year, ts.month, ts.day)
    base = ts.replace(month=1, day=1, hour=0, minute=0,
                      second=0, microsecond=0)
    days = (ts - base).days
    return int(days)


def hourly_offset(dtobj):
    """Return time index for given timestamp

    Args:
      dtobj (datetime): datetime, if no tzinfo, we assume it is UTC

    Returns:
      int time index in the netcdf file
    """
    if dtobj.tzinfo and dtobj.tzinfo != pytz.utc:
        dtobj = dtobj.astimezone(pytz.utc)
    base = dtobj.replace(month=1, day=1, hour=0, minute=0,
                         second=0, microsecond=0)
    seconds = (dtobj - base).total_seconds()
    return int(seconds / 3600.)


def find_ij(lon, lat):
    """ Compute which grid cell this lon, lat resides within
    """
    if lon < WEST or lon >= EAST or lat < SOUTH or lat >= NORTH:
        return None, None

    i = np.digitize([lon, ], XAXIS)[0] - 1
    j = np.digitize([lat, ], YAXIS)[0] - 1

    return i, j


def get_gid(lon, lat):
    """Compute the grid id for the given location."""
    i, j = find_ij(lon, lat)
    if i is None:
        return None
    return j * NX + i
