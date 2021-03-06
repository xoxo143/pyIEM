# -*- coding: utf-8 -*-
# pylint: disable=import-outside-toplevel,unbalanced-tuple-unpacking
"""Utility functions for pyIEM package

This module contains utility functions used by various parts of the codebase.
"""
import os
import sys
import time
import random
import logging
from datetime import timezone, datetime
import re
import warnings
import getpass
from socket import error as socket_error

from six import string_types

# NB: some third party stuff is expensive to import, so let us be lazy

# NB: We shall not be importing other parts of pyIEM here as we then get
# circular references.

SEQNUM = re.compile(r"^[0-9]{3}\s?$")
# Setup a default logging instance for this module
LOG = logging.getLogger("pyiem")
LOG.addHandler(logging.NullHandler())


class CustomFormatter(logging.Formatter):
    """A custom log formatter class."""

    def format(self, record):
        """Return a string!"""
        return "[%s %6.3f %s:%s %s] %s" % (
            time.strftime("%H:%M:%S", time.localtime(record.created)),
            record.relativeCreated / 1000.0,
            record.filename,
            record.lineno,
            record.funcName,
            record.getMessage(),
        )


def html_escape(val):
    """Wrapper around cgi.escape depreciation."""
    from html import escape

    return escape(val)


def get_test_file(name, fponly=False):
    """Helper to get data for test usage."""
    basedir = os.path.dirname(__file__)
    fn = f"{basedir}/../../data/product_examples/{name}"
    fp = open(fn, "rb")
    if fponly:
        return fp
    return fp.read().decode("utf-8")


def logger(name="pyiem", level=None):
    """Get pyiem's logger with a stream handler attached.

    Args:
      name (str): The name of the logger to get, default pyiem
      level (logging.LEVEL): The log level for this pyiem logget, default is
        INFO for non interactive sessions, DEBUG otherwise

    Returns:
      logger instance
    """
    ch = logging.StreamHandler()
    ch.setFormatter(CustomFormatter())
    log = logging.getLogger(name)
    log.addHandler(ch)
    if level is None and sys.stdout.isatty():
        level = logging.DEBUG
    log.setLevel(level if level is not None else logging.INFO)
    return log


def find_ij(lons, lats, lon, lat):
    """Compute the i,j closest cell."""
    import numpy as np

    dist = ((lons - lon) ** 2 + (lats - lat) ** 2) ** 0.5
    (xidx, yidx) = np.unravel_index(dist.argmin(), dist.shape)
    return xidx, yidx


def get_twitter(screen_name):
    """Provide an authorized Twitter API Client

    Args:
      screen_name (str): The twitter user we are fetching creds for
    """
    import twython

    dbconn = get_dbconn("mesosite")
    cursor = dbconn.cursor()
    props = get_properties(cursor)
    # fetch the oauth saved creds
    cursor.execute(
        "select access_token, access_token_secret from iembot_twitter_oauth "
        "WHERE screen_name = %s",
        (screen_name,),
    )
    row = cursor.fetchone()
    dbconn.close()
    return twython.Twython(
        props["bot.twitter.consumerkey"],
        props["bot.twitter.consumersecret"],
        row[0],
        row[1],
    )


def ssw(mixedobj):
    """python23 wrapper for sys.stdout.write

    Args:
      mixedobj (str or bytes): what content we want to send
    """
    stdout = getattr(sys.stdout, "buffer", sys.stdout)
    if isinstance(mixedobj, string_types):
        stdout.write(mixedobj.encode("utf-8"))
    else:
        stdout.write(mixedobj)


def ncopen(ncfn, mode="r", timeout=60):
    """Safely open netcdf files

    The issue here is that we can only have the following situation for a
    given NetCDF file.
    1.  Only 1 or more readers
    2.  Only 1 appender

    The netcdf is being accessed over NFS and perhaps local disk, so writing
    lock files is problematic.

    Args:
      ncfn (str): The netCDF filename
      mode (str,optional): The netCDF4.Dataset open mode, default 'r'
      timeout (int): The total time in seconds to attempt a read, default 60

    Returns:
      `netCDF4.Dataset` or `None`
    """
    import netCDF4

    if mode != "w" and not os.path.isfile(ncfn):
        raise IOError("No such file %s" % (ncfn,))
    sts = datetime.utcnow()
    nc = None
    while (datetime.utcnow() - sts).total_seconds() < timeout:
        try:
            nc = netCDF4.Dataset(ncfn, mode)
            nc.set_auto_scale(True)
            break
        except (OSError, IOError):
            pass
        time.sleep(5)
    return nc


def utc(year=None, month=1, day=1, hour=0, minute=0, second=0, microsecond=0):
    """Create a datetime instance with tzinfo=timezone.utc

    When no arguments are provided, returns `datetime.utcnow()`.

    Returns:
      datetime with tzinfo set
    """
    if year is None:
        return datetime.utcnow().replace(tzinfo=timezone.utc)
    return datetime(
        year, month, day, hour, minute, second, microsecond
    ).replace(tzinfo=timezone.utc)


def get_dbconn(database="mesosite", user=None, host=None, port=5432, **kwargs):
    """Helper function with business logic to get a database connection

    Note that this helper could return a read-only database connection if the
    connection to the primary server fails.

    Args:
      database (str,optional): the database name to connect to.
        default: mesosite
      user (str,optional): hard coded user to connect as, default: current user
      host (str,optional): hard coded hostname to connect as,
        default: iemdb.local
      port (int,optional): the TCP port that PostgreSQL is listening
        defaults to 5432
      password (str,optional): the password to use.
      allow_failover (bool,optional): Should this method attempt to connect to
        a failover host (hard coded as iemdb2.local), default is `True`.

    Returns:
      psycopg2 database connection
    """
    import psycopg2

    if user is None:
        user = getpass.getuser()
        # We hard code the apache user back to nobody, www-data is travis-ci
        if user in ["apache", "www-data"]:
            user = "nobody"
        elif user == "akrherz":  # HACK for daryl's development, sigh
            user = "mesonet"
        elif user == "meteor_ldm":  # Another HACK
            user = "ldm"
    if host is None:
        host = "iemdb-%s.local" % (database,)
    conn_kwargs = {
        "database": database,
        "host": host,
        "user": user,
        "password": kwargs.get("password"),
        "port": port,
        "connect_timeout": kwargs.get("connect_timeout", 15),
        "gssencmode": kwargs.get("gssencmode", "disable"),
    }
    allow_failover = kwargs.pop("allow_failover", True)
    conn_kwargs.update(kwargs)
    attempt = 0
    while attempt < 3:
        attempt += 1
        try:
            return psycopg2.connect(**conn_kwargs)
        except psycopg2.ProgrammingError as exp:
            # Likely gssencmode is not permitted
            if "gssencmode" in conn_kwargs:
                conn_kwargs.pop("gssencmode")
            else:
                warnings.warn(
                    f"database connection failure: {exp}", stacklevel=2
                )
            if attempt == 3:
                raise exp
        except psycopg2.OperationalError as exp:
            # as a stop-gap, lets try connecting to iemdb2
            host2 = "iemdb2.local" if allow_failover else host
            conn_kwargs["host"] = host2
            if attempt == 3:
                raise exp
            warnings.warn(
                f"database connection failure: {exp}, trying {host2}",
                stacklevel=2,
            )


def noaaport_text(text):
    """Make whatever text look like it is NOAAPort Pristine

    Args:
      text (string): the inbound text
    Returns:
      text that looks noaaportish
    """
    # Rectify the text to remove any stray stuff
    text = text.replace("\003", "").replace("\001", "").replace("\r", "")
    # trim any right hand space
    lines = [x.rstrip() for x in text.split("\n")]
    # remove any beginning empty lines
    for pos in [0, -1]:
        while lines and lines[pos].strip() == "":
            lines.pop(pos)

    # lime 0 should be start of product sequence
    lines.insert(0, "\001")
    # line 1 should be the LDM sequence number 4 chars
    if not SEQNUM.match(lines[1]):
        if len(lines[1]) > 5:
            lines.insert(1, "000 ")
    else:
        lines[1] = f"{lines[1][:3]} "
    # last line should be the control-c, by itself
    lines.append("\003")

    return "\r\r\n".join(lines)


def get_autoplot_context(fdict, cfg):
    """Get the variables out of a dict of strings

    This helper for IEM autoplot gets values out of a dictionary of strings,
    as provided by CGI.  It does some magic to get types right, defaults right
    and so on.  The typical way this is called

        ctx = iemutils.get_context(fdict, get_description())

    Args:
      fdict (dictionary): what was likely provided by `cgi.FieldStorage()`
      cfg (dictionary): autoplot value of get_description
    Returns:
      dictionary of variable names and values, with proper types!
    """
    ctx = {}
    for opt in cfg.get("arguments", []):
        name = opt.get("name")
        default = opt.get("default")
        typ = opt.get("type")
        minval = opt.get("min")
        maxval = opt.get("max")
        optional = opt.get("optional", False)
        value = fdict.get(name)
        if optional and value is None and typ not in ["vtec_ps"]:
            continue
        if typ in ["station", "zstation", "sid", "networkselect"]:
            # A bit of hackery here if we have a name ending in a number
            netname = "network%s" % (
                name[-1] if name[-1] in ["1", "2", "3", "4", "5"] else "",
            )
            # The network variable tags along and within a non-PHP context,
            # this variable is unset, so we do some more hackery here
            ctx[netname] = fdict.get(netname, opt.get("network"))
            # Convience we load up the network metadata
            ntname = "_nt%s" % (
                name[-1] if name[-1] in ["1", "2", "3", "4", "5"] else "",
            )
            from pyiem.network import Table as NetworkTable
            from pyiem.exceptions import NoDataFound

            ctx[ntname] = NetworkTable(ctx[netname], only_online=False)
            # stations starting with _ are virtual and should not error
            if value is None:
                value = default
            if not value.startswith("_") and value not in ctx[ntname].sts:
                raise NoDataFound("Station metadata unavailable.")

        elif typ in ["int", "month", "zhour", "hour", "day", "year"]:
            if value is not None:
                value = int(value)
            if default is not None:
                default = int(default)
        elif typ == "float":
            if value is not None:
                value = float(value)
            if default is not None:
                default = float(default)
        elif typ == "select":
            options = opt.get("options", dict())
            # in case of multi, value could be a list
            if value is None:
                value = default
            elif isinstance(value, string_types):
                if value not in options:
                    value = default
                if opt.get("multiple"):
                    value = [value]
            else:
                res = []
                for subval in value:
                    if subval in options:
                        res.append(subval)
                value = res
        elif typ == "datetime":
            # tricky here, php has YYYY/mm/dd and CGI has YYYY-mm-dd
            if default is not None:
                default = datetime.strptime(default, "%Y/%m/%d %H%M")
            if minval is not None:
                minval = datetime.strptime(minval, "%Y/%m/%d %H%M")
            if maxval is not None:
                maxval = datetime.strptime(maxval, "%Y/%m/%d %H%M")
            if value is not None:
                if value.find(" ") == -1:
                    value += " 0000"
                value = datetime.strptime(value, "%Y-%m-%d %H%M")
        elif typ == "date":
            # tricky here, php has YYYY/mm/dd and CGI has YYYY-mm-dd
            if default is not None:
                default = datetime.strptime(default, "%Y/%m/%d").date()
            if minval is not None:
                minval = datetime.strptime(minval, "%Y/%m/%d").date()
            if maxval is not None:
                maxval = datetime.strptime(maxval, "%Y/%m/%d").date()
            if value is not None:
                value = datetime.strptime(value, "%Y-%m-%d").date()
        elif typ == "vtec_ps":
            # VTEC phenomena and significance
            defaults = {}
            # Only set a default value when the field is not optional
            if default is not None and not optional:
                tokens = default.split(".")
                if (
                    len(tokens) == 2
                    and len(tokens[0]) == 2
                    and len(tokens[1]) == 1
                ):
                    defaults["phenomena"] = tokens[0]
                    defaults["significance"] = tokens[1]
            for label in ["phenomena", "significance"]:
                label2 = label + name
                ctx[label2] = fdict.get(label2, defaults.get(label))
            continue
        # validation
        if minval is not None and value is not None and value < minval:
            value = default
        if maxval is not None and value is not None and value > maxval:
            value = default
        ctx[name] = value if value is not None else default
    return ctx


def exponential_backoff(func, *args, **kwargs):
    """Exponentially backoff some function until it stops erroring

    Args:
      _ebfactor (int,optional): Optional scale factor, allowing for faster test
    """
    ebfactor = float(kwargs.pop("_ebfactor", 2))
    msgs = []
    for i in range(5):
        try:
            return func(*args, **kwargs)
        except socket_error as serr:
            msgs.append("%s/5 %s traceback: %s" % (i + 1, func.__name__, serr))
            time.sleep((ebfactor ** i) + (random.randint(0, 1000) / 1000))
        except Exception as exp:
            msgs.append("%s/5 %s traceback: %s" % (i + 1, func.__name__, exp))
            time.sleep((ebfactor ** i) + (random.randint(0, 1000) / 1000))
    logging.error("%s failure", func.__name__)
    logging.error("\n".join(msgs))
    return None


def get_properties(cursor=None):
    """Fetch the properties set

    Returns:
      dict: a dictionary of property names and values (both str)
    """
    if cursor is None:
        pgconn = get_dbconn("mesosite", user="nobody")
        cursor = pgconn.cursor()
    cursor.execute("SELECT propname, propvalue from properties")
    res = {}
    for row in cursor:
        res[row[0]] = row[1]
    return res


def drct2text(drct):
    """Convert an degree value to text representation of direction.

    Args:
      drct (int or float): Value in degrees to convert to text

    Returns:
      str: String representation of the direction, could be `None`

    """
    if drct is None:
        return None
    # Convert the value into a float
    drct = float(drct)
    if drct > 360 or drct < 0:
        return None
    text = None
    if drct >= 350 or drct < 13:
        text = "N"
    elif drct < 35:
        text = "NNE"
    elif drct < 57:
        text = "NE"
    elif drct < 80:
        text = "ENE"
    elif drct < 102:
        text = "E"
    elif drct < 127:
        text = "ESE"
    elif drct < 143:
        text = "SE"
    elif drct < 166:
        text = "SSE"
    elif drct < 190:
        text = "S"
    elif drct < 215:
        text = "SSW"
    elif drct < 237:
        text = "SW"
    elif drct < 260:
        text = "WSW"
    elif drct < 281:
        text = "W"
    elif drct < 304:
        text = "WNW"
    elif drct < 324:
        text = "NW"
    else:
        text = "NNW"
    return text


def grid_bounds(lons, lats, bounds):
    """Figure out indices that we can truncate big grid

    Args:
      lons (np.array): grid lons
      lats (np.array): grid lats
      bounds (list): [x0, y0, x1, y1]

    Returns:
      [x0, y0, x1, y1]
    """
    import numpy as np

    if len(lons.shape) == 1:
        # Do 1-d work
        (x0, x1) = np.digitize([bounds[0], bounds[2]], lons)
        (y0, y1) = np.digitize([bounds[1], bounds[3]], lats)
        szx = len(lons)
        szy = len(lats)
    else:
        # Do 2-d work
        diff = ((lons - bounds[0]) ** 2 + (lats - bounds[1]) ** 2) ** 0.5
        (lly, llx) = np.unravel_index(np.argmin(diff), lons.shape)
        diff = ((lons - bounds[2]) ** 2 + (lats - bounds[3]) ** 2) ** 0.5
        (ury, urx) = np.unravel_index(np.argmin(diff), lons.shape)
        diff = ((lons - bounds[0]) ** 2 + (lats - bounds[3]) ** 2) ** 0.5
        (uly, ulx) = np.unravel_index(np.argmin(diff), lons.shape)
        diff = ((lons - bounds[2]) ** 2 + (lats - bounds[1]) ** 2) ** 0.5
        (lry, lrx) = np.unravel_index(np.argmin(diff), lons.shape)
        x0 = min([llx, ulx])
        x1 = max([lrx, urx])
        y0 = min([lry, lly])
        y1 = max([uly, ury])
        (szy, szx) = lons.shape

    return [
        int(i)
        for i in [max([0, x0]), max([0, y0]), min([szx, x1]), min([szy, y1])]
    ]
