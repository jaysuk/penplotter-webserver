"""Backups of the web plotter: its settings, its history and (if wanted) the uploaded files, in a zip.

Restoring takes a zip from outside, so everything in it is checked before anything is used: the
names of its members (no folders, no `..`, only the known files), their sizes, and the database
(integrity, the table of plots, no triggers or views). Nothing here touches the running app: the
functions return what to apply.
"""
import configparser
import json
import os
import re
import shutil
import sqlite3
import time
import zipfile

import plotters
import vpype_devices

FORMAT = 1
MANIFEST = 'manifest.json'
CONFIG = 'config.ini'
DATABASE = 'history.db'
PLOTTERS = 'plotters.json'
DEVICES = 'vpype_devices.toml'

UPLOAD_NAME_RE = re.compile(r'uploads/([A-Za-z0-9._-]{1,200}\.(?:svg|hpgl|cal))', re.IGNORECASE)
MAX_MEMBERS = 5000
MAX_MEMBER_BYTES = 200 * 1024 * 1024
MAX_TOTAL_BYTES = 1024 * 1024 * 1024
MAX_CONFIG_BYTES = 1024 * 1024
UPLOAD_EXTENSIONS = ('.svg', '.hpgl', '.cal')


class BackupError(ValueError):
    """The backup is not usable. The message is for the user."""


def create(dest, config_path, database, uploads_dir=None, plotters_path=None, devices_path=None):
    """Write a backup zip to `dest`. `database` is a function returning a context manager that
    gives a locked sqlite3 connection (history.database). `plotters_path` is the user's plotter
    profiles and `devices_path` the user's vpype devices, if there are any. Returns what the zip contains."""
    contains = []
    with zipfile.ZipFile(dest, 'w', zipfile.ZIP_DEFLATED) as archive:
        if os.path.isfile(config_path):
            archive.write(config_path, CONFIG)
            contains.append('config')

        # The history is copied with sqlite's backup, so a plot being recorded cannot tear it
        snapshot = dest + '.db'
        try:
            copy = sqlite3.connect(snapshot)
            try:
                with database() as conn:
                    conn.backup(copy)
            finally:
                copy.close()
            archive.write(snapshot, DATABASE)
            contains.append('history')
        except sqlite3.Error as e:
            print('Backup: the history could not be copied:', repr(e))
        finally:
            try:
                os.remove(snapshot)
            except OSError:
                pass

        if plotters_path and os.path.isfile(plotters_path):
            archive.write(plotters_path, PLOTTERS)
            contains.append('plotters')
        if devices_path and os.path.isfile(devices_path):
            archive.write(devices_path, DEVICES)
            contains.append('devices')

        if uploads_dir:
            count = 0
            for name in sorted(os.listdir(uploads_dir)):
                path = os.path.join(uploads_dir, name)
                if os.path.isfile(path) and name.lower().endswith(UPLOAD_EXTENSIONS) and UPLOAD_NAME_RE.fullmatch('uploads/' + name):
                    archive.write(path, 'uploads/' + name)
                    count += 1
            contains.append('uploads')
        archive.writestr(MANIFEST, json.dumps({'app': 'webplotter', 'format': FORMAT, 'created': int(time.time()),
                                               'contains': contains}))
    return contains


def inspect(archive):
    """Check the members of an open zipfile. Returns {name: ZipInfo}; raises BackupError."""
    infos = archive.infolist()
    if len(infos) > MAX_MEMBERS:
        raise BackupError('The backup has too many files')
    members = {}
    total = 0
    for info in infos:
        name = info.filename
        if name in members:
            raise BackupError('The backup lists {} twice'.format(name))
        if name not in (MANIFEST, CONFIG, DATABASE, PLOTTERS, DEVICES) and not UPLOAD_NAME_RE.fullmatch(name):
            raise BackupError('The backup holds something that is not part of a web plotter backup')
        if info.file_size > MAX_MEMBER_BYTES:
            raise BackupError('{} is too large'.format(name))
        total += info.file_size
        members[name] = info
    if total > MAX_TOTAL_BYTES:
        raise BackupError('The backup is too large')
    if MANIFEST not in members:
        raise BackupError('This is not a web plotter backup')
    try:
        manifest = json.loads(_read(archive, MANIFEST, MAX_CONFIG_BYTES))
    except (ValueError, UnicodeDecodeError):
        raise BackupError('This is not a web plotter backup')
    if not isinstance(manifest, dict) or manifest.get('app') != 'webplotter' or manifest.get('format') != FORMAT:
        raise BackupError('This is not a web plotter backup, or a newer version made it')
    return members


def _read(archive, name, limit):
    """The bytes of a member, never more than `limit` (a zip can lie about its sizes)."""
    with archive.open(name) as member:
        data = member.read(limit + 1)
    if len(data) > limit:
        raise BackupError('{} is too large'.format(name))
    return data


def read_config(archive):
    """The settings in the backup as a ConfigParser."""
    parser = configparser.ConfigParser()
    try:
        parser.read_string(_read(archive, CONFIG, MAX_CONFIG_BYTES).decode('utf-8'))
    except (UnicodeDecodeError, configparser.Error):
        raise BackupError('The settings in the backup cannot be read')
    return parser


def read_plotters(archive):
    """The plotter profiles in the backup, checked: a list. Raises BackupError."""
    try:
        return plotters.parse_export(_read(archive, PLOTTERS, plotters.MAX_FILE_BYTES))
    except plotters.PlotterError as e:
        raise BackupError('The plotters in the backup cannot be used: ' + str(e))


def read_devices(archive):
    """The user's vpype devices in the backup, checked: {id: device}. Raises BackupError."""
    try:
        # Not checked against vpype's own names: the backup may come from another vpype version
        return vpype_devices.parse(_read(archive, DEVICES, vpype_devices.MAX_BYTES))
    except vpype_devices.DeviceError as e:
        raise BackupError('The vpype devices in the backup cannot be used: ' + str(e))


def extract_database(archive, folder, max_schema=None):
    """Copy the history out of the zip into `folder` and check it. Returns the path.

    `max_schema` is the newest layout of the database this version knows (history.SCHEMA_VERSION)."""
    path = os.path.join(folder, 'restore.db')
    written = 0
    with archive.open(DATABASE) as member, open(path, 'wb') as out:
        while True:
            chunk = member.read(1024 * 1024)
            if not chunk:
                break
            written += len(chunk)
            if written > MAX_MEMBER_BYTES:
                raise BackupError('The history in the backup is too large')
            out.write(chunk)
    try:
        conn = sqlite3.connect(path)
        try:
            if conn.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise BackupError('The history in the backup is damaged')
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            if max_schema is not None and conn.execute('PRAGMA user_version').fetchone()[0] > max_schema:
                raise BackupError('The history in the backup was made by a newer web plotter: update this one first')
            if 'jobs' not in tables:
                raise BackupError('The history in the backup is not a web plotter history')
            # Triggers and views would run inside this app's own queries
            if conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type IN ('trigger', 'view')").fetchone()[0]:
                raise BackupError('The history in the backup holds more than a web plotter keeps')
        finally:
            conn.close()
    except sqlite3.Error:
        raise BackupError('The history in the backup is damaged')
    return path


def extract_upload(archive, name, dest):
    """Write the uploaded file `name` ('uploads/x.hpgl') of the backup to `dest`, atomically."""
    temp = dest + '.restoring'
    try:
        with archive.open(name) as member, open(temp, 'wb') as out:
            shutil.copyfileobj(member, out, 1024 * 1024)
        os.replace(temp, dest)
    finally:
        try:
            os.remove(temp)
        except OSError:
            pass
