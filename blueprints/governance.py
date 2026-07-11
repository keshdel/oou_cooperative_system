"""
governance.py — cooperative governance content:
  * Events / announcements (AGM, meeting dates) shown on the members' banner.
  * Minutes-of-meeting repository (upload → stored in the DB → browse/download).
"""
import io
from datetime import datetime

from flask import (Blueprint, render_template, redirect, url_for, request, flash,
                   send_file, abort)
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename

from database import get_db
from utils import role_required, audit

governance = Blueprint('governance', __name__)

_ALLOWED = {'pdf', 'doc', 'docx', 'txt', 'jpg', 'jpeg', 'png'}
_MIME = {
    'pdf': 'application/pdf', 'doc': 'application/msword',
    'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'txt': 'text/plain', 'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'png': 'image/png',
}


def upcoming_events(db, limit=5):
    """Active events dated today or later — used for the members' banner."""
    try:
        today = datetime.now().strftime('%Y-%m-%d')
        return db.execute(
            "SELECT * FROM events WHERE is_active = 1 AND (event_date IS NULL OR event_date >= ?) "
            "ORDER BY event_date ASC LIMIT ?", (today, limit)
        ).fetchall()
    except Exception:
        return []


# ── Member / all-user views ──────────────────────────────────────────────────

@governance.route('/events')
@login_required
def events_list():
    db = get_db()
    today = datetime.now().strftime('%Y-%m-%d')
    upcoming = db.execute(
        "SELECT * FROM events WHERE is_active = 1 AND (event_date IS NULL OR event_date >= ?) "
        "ORDER BY event_date ASC", (today,)).fetchall()
    past = db.execute(
        "SELECT * FROM events WHERE is_active = 1 AND event_date < ? "
        "ORDER BY event_date DESC LIMIT 30", (today,)).fetchall()
    return render_template('governance/events.html', upcoming=upcoming, past=past)


@governance.route('/minutes')
@login_required
def minutes_list():
    db = get_db()
    minutes = db.execute(
        "SELECT id, title, meeting_type, meeting_date, file_name, notes, uploaded_at "
        "FROM meeting_minutes ORDER BY meeting_date DESC, id DESC").fetchall()
    return render_template('governance/minutes.html', minutes=minutes)


@governance.route('/minutes/<int:minute_id>/download')
@login_required
def minutes_download(minute_id):
    db = get_db()
    m = db.execute("SELECT file_name, file_mime, file_data FROM meeting_minutes WHERE id = ?",
                   (minute_id,)).fetchone()
    if not m or not m['file_data']:
        abort(404)
    data = bytes(m['file_data'])
    return send_file(io.BytesIO(data), mimetype=m['file_mime'] or 'application/octet-stream',
                     as_attachment=True, download_name=m['file_name'] or f'minutes-{minute_id}')


# ── Admin / secretary management ─────────────────────────────────────────────

@governance.route('/governance')
@login_required
@role_required('admin', 'secretary')
def manage():
    db = get_db()
    events = db.execute("SELECT * FROM events ORDER BY event_date DESC, id DESC").fetchall()
    minutes = db.execute(
        "SELECT id, title, meeting_type, meeting_date, file_name, notes, uploaded_at "
        "FROM meeting_minutes ORDER BY meeting_date DESC, id DESC").fetchall()
    return render_template('governance/manage.html', events=events, minutes=minutes,
                           today=datetime.now().strftime('%Y-%m-%d'))


@governance.route('/governance/events/add', methods=['POST'])
@login_required
@role_required('admin', 'secretary')
def add_event():
    db = get_db()
    title = request.form.get('title', '').strip()
    if not title:
        flash('Event title is required.', 'danger')
        return redirect(url_for('governance.manage'))
    db.execute('''INSERT INTO events (title, event_type, event_date, location, description, created_by)
                  VALUES (?, ?, ?, ?, ?, ?)''',
               (title, request.form.get('event_type', 'announcement'),
                request.form.get('event_date') or None,
                request.form.get('location', '').strip(),
                request.form.get('description', '').strip(), current_user.id))
    db.commit()
    audit(db, 'ADD_EVENT', 'governance', f'Added event: {title}')
    flash('Event published.', 'success')
    return redirect(url_for('governance.manage'))


@governance.route('/governance/events/<int:event_id>/toggle', methods=['POST'])
@login_required
@role_required('admin', 'secretary')
def toggle_event(event_id):
    db = get_db()
    e = db.execute("SELECT is_active FROM events WHERE id = ?", (event_id,)).fetchone()
    if e:
        db.execute("UPDATE events SET is_active = ? WHERE id = ?",
                   (0 if e['is_active'] else 1, event_id))
        db.commit()
    return redirect(url_for('governance.manage'))


@governance.route('/governance/events/<int:event_id>/delete', methods=['POST'])
@login_required
@role_required('admin', 'secretary')
def delete_event(event_id):
    db = get_db()
    db.execute("DELETE FROM events WHERE id = ?", (event_id,))
    db.commit()
    flash('Event deleted.', 'info')
    return redirect(url_for('governance.manage'))


@governance.route('/governance/minutes/upload', methods=['POST'])
@login_required
@role_required('admin', 'secretary')
def upload_minutes():
    db = get_db()
    title = request.form.get('title', '').strip()
    if not title:
        flash('Minutes title is required.', 'danger')
        return redirect(url_for('governance.manage'))
    f = request.files.get('file')
    file_name = file_mime = None
    data = None
    if f and f.filename:
        ext = f.filename.rsplit('.', 1)[-1].lower() if '.' in f.filename else ''
        if ext not in _ALLOWED:
            flash(f'File type .{ext} is not allowed. Use PDF, DOC, DOCX, TXT, or an image.', 'danger')
            return redirect(url_for('governance.manage'))
        data = f.read()
        if len(data) > 10 * 1024 * 1024:
            flash('File too large (max 10 MB).', 'danger')
            return redirect(url_for('governance.manage'))
        file_name = secure_filename(f.filename)
        file_mime = _MIME.get(ext, 'application/octet-stream')
    db.execute('''INSERT INTO meeting_minutes
                  (title, meeting_type, meeting_date, file_name, file_mime, file_data, notes, uploaded_by)
                  VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
               (title, request.form.get('meeting_type', 'general'),
                request.form.get('meeting_date') or None, file_name, file_mime, data,
                request.form.get('notes', '').strip(), current_user.id))
    db.commit()
    audit(db, 'UPLOAD_MINUTES', 'governance', f'Uploaded minutes: {title}')
    flash('Minutes saved to the repository.', 'success')
    return redirect(url_for('governance.manage'))


@governance.route('/governance/minutes/<int:minute_id>/delete', methods=['POST'])
@login_required
@role_required('admin', 'secretary')
def delete_minutes(minute_id):
    db = get_db()
    db.execute("DELETE FROM meeting_minutes WHERE id = ?", (minute_id,))
    db.commit()
    flash('Minutes deleted.', 'info')
    return redirect(url_for('governance.manage'))
