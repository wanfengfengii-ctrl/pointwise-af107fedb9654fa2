import sqlite3

from measurement.profiles import CalibrationProfile, build_profile


def initialize_calibrations(db: sqlite3.Connection) -> None:
    db.execute('''
        CREATE TABLE IF NOT EXISTS calibrations (
            profile_id TEXT PRIMARY KEY NOT NULL,
            lower_code TEXT NOT NULL,
            upper_code TEXT NOT NULL,
            gain TEXT NOT NULL,
            offset TEXT NOT NULL,
            unit TEXT NOT NULL
        )
    ''')


def install_calibration(db: sqlite3.Connection, profile: CalibrationProfile) -> CalibrationProfile:
    profile = build_profile(profile.profile_id, profile.limits.minimum, profile.limits.maximum,
                            profile.gain, profile.offset, profile.unit)
    with db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('''
            INSERT INTO calibrations VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(profile_id) DO NOTHING
        ''', (profile.profile_id, str(profile.limits.minimum), str(profile.limits.maximum),
              str(profile.gain), str(profile.offset), profile.unit))
        original = load_calibration(db, profile.profile_id)
        if original != profile:
            raise ValueError(f'calibration {profile.profile_id!r} is already fixed')
    return original


def load_calibration(db: sqlite3.Connection, profile_id: str) -> CalibrationProfile:
    if not isinstance(profile_id, str) or not profile_id.strip():
        raise ValueError('calibration ID must be nonblank text')
    row = db.execute('''
        SELECT lower_code, upper_code, gain, offset, unit FROM calibrations WHERE profile_id = ?
    ''', (profile_id,)).fetchone()
    if row is None:
        raise ValueError(f'unknown calibration {profile_id!r}')
    minimum, maximum, gain, offset, unit = row
    return build_profile(profile_id, int(minimum), int(maximum), gain, offset, unit)
