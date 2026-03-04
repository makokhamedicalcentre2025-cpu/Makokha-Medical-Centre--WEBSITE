
from flask import Flask, render_template, request, session, redirect, url_for, jsonify, flash, has_request_context
from flask_sqlalchemy import SQLAlchemy
from flask_socketio import SocketIO, emit, join_room, leave_room
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from werkzeug.middleware.proxy_fix import ProxyFix
from flask_wtf.csrf import CSRFProtect, CSRFError
from functools import wraps
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
import os
import re
import json
import importlib
import threading
import hashlib
import smtplib
import ssl
from uuid import uuid4
from email.message import EmailMessage
from sqlalchemy import desc, inspect, text, or_, func
from urllib.parse import urlparse, urlencode
from urllib import error as urllib_error
from urllib import request as urllib_request
from xml.sax.saxutils import escape as xml_escape


def _load_optional_pillow_modules():
    """Load Pillow modules lazily without hard import-time dependency."""
    try:
        pil_image = importlib.import_module('PIL.Image')
        pil_filter = importlib.import_module('PIL.ImageFilter')
        pil_ops = importlib.import_module('PIL.ImageOps')
        return pil_image, pil_filter, pil_ops
    except ImportError:
        return None, None, None


Image, ImageFilter, ImageOps = _load_optional_pillow_modules()

# Load environment variables
load_dotenv()


def _env_flag(env_name, default=False):
    """Read boolean-like environment variable values."""
    raw_value = os.getenv(env_name)
    if raw_value is None:
        return bool(default)
    return str(raw_value).strip().lower() in {'1', 'true', 'yes', 'on'}


def _env_int(env_name, default_value):
    """Read integer environment variable with safe fallback."""
    raw_value = os.getenv(env_name)
    if raw_value is None or not str(raw_value).strip():
        return int(default_value)
    try:
        return int(str(raw_value).strip())
    except (TypeError, ValueError):
        return int(default_value)


def _normalize_database_uri(raw_database_uri):
    """Normalize SQLAlchemy database URI, including legacy postgres:// scheme."""
    candidate = (raw_database_uri or '').strip()
    if not candidate:
        return ''
    if candidate.startswith('postgres://'):
        candidate = 'postgresql://' + candidate[len('postgres://'):]
    return candidate


# Initialize Flask app
app = Flask(__name__)
APP_ENV = (os.getenv('APP_ENV') or os.getenv('FLASK_ENV') or ('production' if os.getenv('RENDER') else 'development')).strip().lower()
IS_PRODUCTION = APP_ENV == 'production'

secret_key = (os.getenv('SECRET_KEY') or '').strip()
if not secret_key:
    if IS_PRODUCTION:
        raise RuntimeError('SECRET_KEY environment variable is required in production.')
    secret_key = os.urandom(24).hex()

database_uri = _normalize_database_uri(os.getenv('DATABASE_URL'))
if not database_uri:
    if IS_PRODUCTION:
        raise RuntimeError('DATABASE_URL environment variable is required in production.')
    database_uri = 'sqlite:///makokha_medical.db'
if IS_PRODUCTION and database_uri.startswith('sqlite'):
    raise RuntimeError('Production deployment must use PostgreSQL. Configure DATABASE_URL accordingly.')

app.config['SECRET_KEY'] = secret_key
app.config['SQLALCHEMY_DATABASE_URI'] = database_uri
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = os.getenv('UPLOAD_FOLDER', 'static/uploads')
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size
app.config['PREFERRED_URL_SCHEME'] = 'https' if IS_PRODUCTION else 'http'
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = IS_PRODUCTION
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=_env_int('SESSION_LIFETIME_HOURS', 12))

engine_options = {'pool_pre_ping': True}
if database_uri.startswith('postgresql://'):
    engine_options.update({
        'pool_recycle': _env_int('DB_POOL_RECYCLE', 280),
        'pool_size': max(1, _env_int('DB_POOL_SIZE', 5)),
        'max_overflow': max(0, _env_int('DB_POOL_MAX_OVERFLOW', 10)),
        'pool_timeout': max(5, _env_int('DB_POOL_TIMEOUT', 30))
    })
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = engine_options

# Create upload folder if it doesn't exist
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# Respect reverse-proxy forwarding headers on Render and similar platforms.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)

# Initialize SQLAlchemy
db = SQLAlchemy(app)
csrf = CSRFProtect(app)
socketio_options = {'async_mode': 'threading'}
socketio_cors = (os.getenv('SOCKETIO_CORS_ALLOWED_ORIGINS') or '').strip()
if socketio_cors:
    if socketio_cors == '*':
        socketio_options['cors_allowed_origins'] = '*'
    else:
        socketio_options['cors_allowed_origins'] = [
            origin.strip() for origin in socketio_cors.split(',') if origin.strip()
        ]
socketio = SocketIO(app, **socketio_options)

# Call/voice runtime configuration
def _read_csv_env(primary_key, aliases=(), default=''):
    """Read comma-separated env values with backward-compatible aliases."""
    raw_value = os.getenv(primary_key)
    if raw_value is None or not str(raw_value).strip():
        for alias_key in aliases:
            alias_value = os.getenv(alias_key)
            if alias_value is not None and str(alias_value).strip():
                raw_value = alias_value
                break
    raw_value = raw_value or default
    return [value.strip() for value in str(raw_value).split(',') if value.strip()]


TURN_SERVER_URLS = [
    value for value in _read_csv_env('TURN_SERVER_URLS', aliases=('TURN_SERVER_URL',))
]
TURN_USERNAME = (os.getenv('TURN_USERNAME') or '').strip()
TURN_CREDENTIAL = (os.getenv('TURN_CREDENTIAL') or '').strip()
STUN_SERVER_URLS = _read_csv_env(
    'STUN_SERVER_URLS',
    aliases=('STUN_SERVER_URL',),
    default='stun:stun.l.google.com:19302'
)

# Public website content
FACILITY_SERVICES = [
    {
        'name': 'Outpatient Consultation',
        'description': 'General and specialist consultations for everyday health concerns.',
        'icon': 'fa-user-doctor'
    },
    {
        'name': 'Emergency Care',
        'description': '24/7 emergency response and urgent stabilization services.',
        'icon': 'fa-ambulance'
    },
    {
        'name': 'Maternal and Child Health',
        'description': 'Antenatal, postnatal, family planning, and child wellness services.',
        'icon': 'fa-baby'
    },
    {
        'name': 'Laboratory Services',
        'description': 'Diagnostic laboratory tests for accurate and timely treatment planning.',
        'icon': 'fa-vials'
    },
    {
        'name': 'Pharmacy Services',
        'description': 'On-site dispensing of prescribed medication and treatment counseling.',
        'icon': 'fa-pills'
    },
    {
        'name': 'Immunization Clinic',
        'description': 'Routine and campaign-based vaccination for children and adults.',
        'icon': 'fa-syringe'
    },
    {
        'name': 'Chronic Disease Management',
        'description': 'Follow-up care for hypertension, diabetes, asthma, and long-term conditions.',
        'icon': 'fa-heart-pulse'
    },
    {
        'name': 'Minor Procedures',
        'description': 'Wound care, dressing, and selected outpatient procedures.',
        'icon': 'fa-notes-medical'
    }
]

UPCOMING_TELEMEDICINE = {
    'title': 'Upcoming Telemedicine',
    'subtitle': 'Launching soon at Makokha Medical Centre',
    'description': (
        'Virtual doctor consultations, digital follow-up reviews, and remote triage support '
        'will be available through our telemedicine service.'
    ),
    'launch_window': 'Target rollout: 2026'
}

EVENT_TYPE_FILTER_OPTIONS = [
    {'value': 'all', 'label': 'All Event Types'},
    {'value': 'health_camp', 'label': 'Free Health Camps'},
    {'value': 'seminar', 'label': 'Seminars'},
    {'value': 'workshop', 'label': 'Workshops'},
    {'value': 'vaccination', 'label': 'Vaccination Drives'},
    {'value': 'general', 'label': 'General Programs'}
]
VALID_EVENT_TYPE_FILTERS = {
    option['value'] for option in EVENT_TYPE_FILTER_OPTIONS if option['value'] != 'all'
}

DEFAULT_SITE_SETTINGS = {
    'about_heading': 'Your Trusted Healthcare Partner',
    'about_intro_primary': (
        'Makokha Medical Centre is a leading healthcare facility committed to providing the highest '
        'quality medical services to the community. With a team of experienced physicians, modern '
        'medical equipment, and patient-centric care, we strive to improve the health and well-being '
        'of every patient who walks through our doors.'
    ),
    'about_intro_secondary': (
        'Since our establishment, we have been dedicated to offering comprehensive healthcare solutions '
        'with a focus on excellence, integrity, and compassion. Our mission is to remain the preferred '
        'medical institution in the region through continuous improvement and innovation.'
    ),
    'mission_text': (
        'To provide accessible, affordable, and quality healthcare services that promote the physical, '
        'mental, and social well-being of individuals and the community. We are committed to treating '
        'every patient with dignity, respect, and compassion.'
    ),
    'vision_text': (
        'To be the leading healthcare provider in the region, recognized for excellence in medical care, '
        'patient satisfaction, and positive health outcomes. We envision a community where everyone has '
        'access to high-quality healthcare.'
    ),
    'footer_about_text': (
        'Makokha Medical Centre is committed to providing quality healthcare services with modern '
        'facilities and experienced medical professionals.'
    ),
    'contact_address': 'Makokha Medical Centre\nMaliki, Kenya',
    'contact_phones': '+254 (0) 123 456 789\n+254 (0) 987 654 321',
    'contact_emails': 'info@makokhamedical.com\nappointments@makokhamedical.com',
    'opening_hours': (
        'Monday - Friday: 9:00 AM - 5:00 PM\n'
        'Saturday: 9:00 AM - 1:00 PM\n'
        'Sunday: Emergency Only'
    ),
    'services_json': json.dumps(FACILITY_SERVICES),
    'services_banner_image': '',
    'services_banner_position_json': '{"x": 50.0, "y": 50.0}',
    'hero_background_image': '',
    'hero_background_images_json': '[]',
    'hero_background_positions_json': '{}',
    'telemedicine_title': UPCOMING_TELEMEDICINE['title'],
    'telemedicine_subtitle': UPCOMING_TELEMEDICINE['subtitle'],
    'telemedicine_description': UPCOMING_TELEMEDICINE['description'],
    'telemedicine_launch_window': UPCOMING_TELEMEDICINE['launch_window'],
    'telemedicine_image': '',
    'emergency_call_title': 'Emergency System Call',
    'emergency_call_description': (
        'This emergency contact channel does not require a phone number. '
        'Press the button below to alert customer care immediately.'
    )
}

ALLOWED_IMAGE_EXTENSIONS = {'png', 'jpg', 'jpeg', 'webp', 'gif'}
MAX_HERO_IMAGES_PER_UPLOAD = 10
HERO_IMAGE_ASPECT_RATIO = (16, 9)
UPLOAD_IMAGE_LONG_EDGE_TARGET = int(os.getenv('UPLOAD_IMAGE_LONG_EDGE_TARGET', '3840'))
UPLOAD_IMAGE_SHARPEN_PERCENT = int(os.getenv('UPLOAD_IMAGE_SHARPEN_PERCENT', '65'))
UPLOAD_MIN_LONG_EDGE = int(os.getenv('UPLOAD_MIN_LONG_EDGE', '1280'))
UPLOAD_MIN_SHORT_EDGE = int(os.getenv('UPLOAD_MIN_SHORT_EDGE', '720'))

ACTIVE_CALL_STATUSES = ['initiated', 'dialing', 'ringing', 'busy', 'connected', 'on_hold']
MUTATING_HTTP_METHODS = {'POST', 'PUT', 'PATCH', 'DELETE'}
SAME_ORIGIN_EXEMPT_PATH_PREFIXES = ['/socket.io', '/socket.io/']
LOGIN_RATE_LIMIT_WINDOW_SECONDS = _env_int('LOGIN_RATE_LIMIT_WINDOW_SECONDS', 600)
LOGIN_RATE_LIMIT_MAX_ATTEMPTS = _env_int('LOGIN_RATE_LIMIT_MAX_ATTEMPTS', 8)
LOGIN_RATE_LIMIT_LOCK_SECONDS = _env_int('LOGIN_RATE_LIMIT_LOCK_SECONDS', 900)
LOGIN_ATTEMPT_TRACKER = {}
FOUNDER_TABLE_READY = False
PARTNER_TABLE_READY = False
DOCTOR_SCHEMA_READY = False
COMMUNICATION_SCHEMA_READY = False
COMMUNICATION_THREAD_TABLE_READY = False
SITE_SETTINGS_TABLE_READY = False
RUNTIME_SCHEMA_READY = False
RUNTIME_SCHEMA_LOCK = threading.Lock()
PASSWORD_RESET_TOKEN_TTL_MINUTES = max(10, _env_int('PASSWORD_RESET_TOKEN_TTL_MINUTES', 30))
TELEMEDICINE_LINK_TTL_HOURS = max(1, _env_int('TELEMEDICINE_LINK_TTL_HOURS', 72))

# ==================== DATABASE MODELS ====================

class Admin(db.Model):
    """Admin user model for authentication"""
    __tablename__ = 'admin'
    
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(255), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    full_name = db.Column(db.String(120), nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    is_active = db.Column(db.Boolean, default=True)
    
    def set_password(self, password):
        """Hash and set password"""
        self.password = generate_password_hash(password)
    
    def check_password(self, password):
        """Check if provided password matches hash"""
        return check_password_hash(self.password, password)
    
    def __repr__(self):
        return f'<Admin {self.username}>'


class Doctor(db.Model):
    """Doctor model for medical staff information"""
    __tablename__ = 'doctor'
    
    id = db.Column(db.Integer, primary_key=True)
    first_name = db.Column(db.String(80), nullable=False)
    last_name = db.Column(db.String(80), nullable=False)
    specialty = db.Column(db.String(120), nullable=False)
    qualification = db.Column(db.String(255), nullable=True)
    bio = db.Column(db.Text, nullable=True)
    phone = db.Column(db.String(20), nullable=True)
    email = db.Column(db.String(120), nullable=True)
    image_filename = db.Column(db.String(255), nullable=True)
    image_focus_x = db.Column(db.Float, default=50.0)
    image_focus_y = db.Column(db.Float, default=50.0)
    available_days = db.Column(db.String(255), nullable=True)  # JSON string of days
    consulting_hours = db.Column(db.String(100), nullable=True)  # e.g., "9:00 AM - 5:00 PM"
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    
    def full_name(self):
        """Get full name"""
        return f"{self.first_name} {self.last_name}"
    
    def __repr__(self):
        return f'<Doctor {self.full_name()}>'


class Founder(db.Model):
    """Founder profile model for public website leadership section."""
    __tablename__ = 'founder'

    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(120), nullable=False)
    title = db.Column(db.String(160), nullable=False)
    bio = db.Column(db.Text, nullable=True)
    image_filename = db.Column(db.String(255), nullable=True)
    image_focus_x = db.Column(db.Float, default=50.0)
    image_focus_y = db.Column(db.Float, default=50.0)
    display_order = db.Column(db.Integer, default=0)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    def __repr__(self):
        return f'<Founder {self.full_name}>'


class Partner(db.Model):
    """Partner profile model for public website partnership section."""
    __tablename__ = 'partner'

    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(120), nullable=False)
    title = db.Column(db.String(160), nullable=False)
    bio = db.Column(db.Text, nullable=True)
    image_filename = db.Column(db.String(255), nullable=True)
    display_order = db.Column(db.Integer, default=0)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    def __repr__(self):
        return f'<Partner {self.full_name}>'


class Event(db.Model):
    """Event model for upcoming and past events"""
    __tablename__ = 'event'
    
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=False)
    event_date = db.Column(db.DateTime, nullable=False)
    location = db.Column(db.String(200), nullable=True)
    image_filename = db.Column(db.String(255), nullable=True)
    image_focus_x = db.Column(db.Float, default=50.0)
    image_focus_y = db.Column(db.Float, default=50.0)
    event_type = db.Column(db.String(50), default='general')  # 'health_camp', 'workshop', 'general', etc.
    status = db.Column(db.String(50), default='upcoming')  # 'upcoming', 'ongoing', 'completed'
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    
    def is_upcoming(self):
        """Check if event is upcoming"""
        status_value = (self.status or '').strip().lower()
        if status_value == 'completed':
            return False
        if not self.event_date:
            return False
        now = datetime.now(timezone.utc) if self.event_date.tzinfo else datetime.now()
        return self.event_date > now
    
    def is_past(self):
        """Check if event is in the past"""
        status_value = (self.status or '').strip().lower()
        if status_value == 'completed':
            return True
        if not self.event_date:
            return False
        now = datetime.now(timezone.utc) if self.event_date.tzinfo else datetime.now()
        return self.event_date < now

    def normalized_status(self):
        """Return normalized status value for display and filtering."""
        status_value = (self.status or '').strip().lower()
        if status_value in {'upcoming', 'ongoing', 'completed'}:
            return status_value
        return 'completed' if self.is_past() else 'upcoming'
    
    def __repr__(self):
        return f'<Event {self.title}>'


class EventPhoto(db.Model):
    """Additional photos for an event gallery."""
    __tablename__ = 'event_photo'

    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.Integer, db.ForeignKey('event.id'), nullable=False)
    filename = db.Column(db.String(255), nullable=False)
    display_order = db.Column(db.Integer, default=0)
    focus_x = db.Column(db.Float, default=50.0)
    focus_y = db.Column(db.Float, default=50.0)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def __repr__(self):
        return f'<EventPhoto {self.filename}>'


class Photo(db.Model):
    """Photo model for managing medical center photos"""
    __tablename__ = 'photo'
    
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(255), nullable=False)
    title = db.Column(db.String(200), nullable=True)
    description = db.Column(db.Text, nullable=True)
    category = db.Column(db.String(100), nullable=True)  # 'facility', 'team', 'event', etc.
    uploaded_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    is_active = db.Column(db.Boolean, default=True)
    
    def __repr__(self):
        return f'<Photo {self.filename}>'


class Review(db.Model):
    """Review model for patient reviews"""
    __tablename__ = 'review'
    
    id = db.Column(db.Integer, primary_key=True)
    patient_name = db.Column(db.String(120), nullable=False)
    patient_email = db.Column(db.String(120), nullable=False)
    review_text = db.Column(db.Text, nullable=False)
    rating = db.Column(db.Integer, nullable=False)  # 1-5 stars
    doctor_id = db.Column(db.Integer, db.ForeignKey('doctor.id'), nullable=True)
    verified = db.Column(db.Boolean, default=False)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    
    def __repr__(self):
        return f'<Review {self.patient_name}>'

class Reception(db.Model):
    """Reception staff model for customer service"""
    __tablename__ = 'reception'
    
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(255), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    full_name = db.Column(db.String(120), nullable=False)
    phone = db.Column(db.String(20), nullable=True)
    department = db.Column(db.String(100), nullable=True)  # e.g., 'calls', 'emails', 'appointments'
    shift = db.Column(db.String(50), nullable=True)  # e.g., 'morning', 'evening', 'night'
    is_available = db.Column(db.Boolean, default=True)  # Online status
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    
    def set_password(self, password):
        """Hash and set password"""
        self.password = generate_password_hash(password)
    
    def check_password(self, password):
        """Check if provided password matches hash"""
        return check_password_hash(self.password, password)
    
    def __repr__(self):
        return f'<Reception {self.username}>'


class Communication(db.Model):
    """Communication model for patient-reception messaging"""
    __tablename__ = 'communication'
    
    id = db.Column(db.Integer, primary_key=True)
    patient_name = db.Column(db.String(120), nullable=False)
    patient_email = db.Column(db.String(120), nullable=False)
    patient_phone = db.Column(db.String(20), nullable=True)
    reception_id = db.Column(db.Integer, db.ForeignKey('reception.id'), nullable=True)
    message_type = db.Column(db.String(50), default='message')  # 'message', 'call', 'email', 'appointment'
    message_content = db.Column(db.Text, nullable=False)
    public_token = db.Column(db.String(64), nullable=True)
    attachments = db.Column(db.String(500), nullable=True)  # JSON string of file names
    is_read = db.Column(db.Boolean, default=False)
    reply_content = db.Column(db.Text, nullable=True)
    replied_at = db.Column(db.DateTime, nullable=True)
    is_resolved = db.Column(db.Boolean, default=False)
    priority = db.Column(db.String(20), default='normal')  # 'low', 'normal', 'high', 'urgent'
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    
    def __repr__(self):
        return f'<Communication {self.patient_email}>'


class CommunicationMessage(db.Model):
    """Threaded private conversation entries between patient and receptionist."""
    __tablename__ = 'communication_message'

    id = db.Column(db.Integer, primary_key=True)
    communication_id = db.Column(db.Integer, db.ForeignKey('communication.id'), nullable=False)
    sender_type = db.Column(db.String(20), nullable=False, default='patient')  # 'patient' or 'reception'
    sender_name = db.Column(db.String(120), nullable=True)
    sender_email = db.Column(db.String(120), nullable=True)
    message_content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def __repr__(self):
        return f'<CommunicationMessage {self.communication_id} {self.sender_type}>'


class Appointment(db.Model):
    """Appointment model for booking management"""
    __tablename__ = 'appointment'
    
    id = db.Column(db.Integer, primary_key=True)
    patient_name = db.Column(db.String(120), nullable=False)
    patient_email = db.Column(db.String(120), nullable=False)
    patient_phone = db.Column(db.String(20), nullable=False)
    doctor_id = db.Column(db.Integer, db.ForeignKey('doctor.id'), nullable=True)
    appointment_date = db.Column(db.DateTime, nullable=False)
    reason = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(20), default='pending')  # 'pending', 'confirmed', 'completed', 'cancelled'
    notes = db.Column(db.Text, nullable=True)
    reception_id = db.Column(db.Integer, db.ForeignKey('reception.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    
    def __repr__(self):
        return f'<Appointment {self.patient_name}>'


class Notification(db.Model):
    """Notification model for reception alerts"""
    __tablename__ = 'notification'
    
    id = db.Column(db.Integer, primary_key=True)
    reception_id = db.Column(db.Integer, db.ForeignKey('reception.id'), nullable=False)
    communication_id = db.Column(db.Integer, db.ForeignKey('communication.id'), nullable=True)
    appointment_id = db.Column(db.Integer, db.ForeignKey('appointment.id'), nullable=True)
    notification_type = db.Column(db.String(50), default='message')  # 'message', 'appointment', 'call', 'email'
    title = db.Column(db.String(200), nullable=False)
    message = db.Column(db.Text, nullable=False)
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    
    def __repr__(self):
        return f'<Notification {self.notification_type}>'


class Call(db.Model):
    """Model to track patient-receptionist calls"""
    __tablename__ = 'call'
    
    id = db.Column(db.Integer, primary_key=True)
    call_id = db.Column(db.String(50), unique=True, nullable=False)
    patient_name = db.Column(db.String(120), nullable=False)
    patient_phone = db.Column(db.String(20), nullable=True)
    call_type = db.Column(db.String(20), default='customer_care')
    status = db.Column(
        db.String(20),
        default='initiated'
    )  # initiated, dialing, ringing, busy, on_hold, connected, message_left, ended, rejected, failed
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    answered_at = db.Column(db.DateTime, nullable=True)
    ended_at = db.Column(db.DateTime, nullable=True)
    duration = db.Column(db.Integer, default=0)  # Duration in seconds
    reception_user_id = db.Column(db.Integer, db.ForeignKey('reception.id'), nullable=True)
    twilio_patient_call_sid = db.Column(db.String(64), nullable=True)
    twilio_staff_call_sid = db.Column(db.String(64), nullable=True)
    patient_leg_status = db.Column(db.String(30), nullable=True)
    staff_leg_status = db.Column(db.String(30), nullable=True)
    conference_name = db.Column(db.String(120), nullable=True)
    hold_requested_at = db.Column(db.DateTime, nullable=True)
    last_error = db.Column(db.Text, nullable=True)
    
    def __repr__(self):
        return f'<Call {self.call_id}>'
    
    def get_duration_formatted(self):
        """Return formatted duration"""
        if self.duration:
            hours = self.duration // 3600
            minutes = (self.duration % 3600) // 60
            secs = self.duration % 60
            if hours > 0:
                return f'{hours}:{minutes:02d}:{secs:02d}'
            return f'{minutes}:{secs:02d}'
        return '0:00'


class SiteSetting(db.Model):
    """Key-value settings for editable public website content."""
    __tablename__ = 'site_setting'

    id = db.Column(db.Integer, primary_key=True)
    setting_key = db.Column(db.String(120), unique=True, nullable=False)
    setting_value = db.Column(db.Text, nullable=True)
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc)
    )

    def __repr__(self):
        return f'<SiteSetting {self.setting_key}>'


class PasswordResetToken(db.Model):
    """One-time password reset tokens for admin and reception accounts."""
    __tablename__ = 'password_reset_token'

    id = db.Column(db.Integer, primary_key=True)
    user_type = db.Column(db.String(20), nullable=False)  # 'admin' or 'reception'
    user_id = db.Column(db.Integer, nullable=False)
    email = db.Column(db.String(120), nullable=False)
    token_hash = db.Column(db.String(64), unique=True, nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)
    used_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def __repr__(self):
        return f'<PasswordResetToken {self.user_type}:{self.user_id}>'


class TelemedicineSessionLink(db.Model):
    """Secure telemedicine access links sent to patients by email."""
    __tablename__ = 'telemedicine_session_link'

    id = db.Column(db.Integer, primary_key=True)
    appointment_id = db.Column(db.Integer, db.ForeignKey('appointment.id'), nullable=False)
    patient_email = db.Column(db.String(120), nullable=False)
    token_hash = db.Column(db.String(64), unique=True, nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)
    created_by_user_type = db.Column(db.String(20), nullable=True)  # 'admin'/'reception'
    created_by_user_id = db.Column(db.Integer, nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def __repr__(self):
        return f'<TelemedicineSessionLink appointment={self.appointment_id}>'


# ==================== AUTHENTICATION DECORATORS ====================

def login_required(f):
    """Decorator to require admin login"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash('Please log in first.', 'error')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function


def admin_required(f):
    """Decorator to require admin login"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if session.get('user_type') != 'admin':
            flash('Admin access required.', 'error')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function


def reception_required(f):
    """Decorator to require reception login"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if session.get('user_type') != 'reception':
            flash('Reception access required.', 'error')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function


# ==================== SECURITY HELPERS ====================

def _get_client_ip():
    """Resolve client IP with proxy support."""
    forwarded_for = (request.headers.get('X-Forwarded-For') or '').strip()
    if forwarded_for:
        return forwarded_for.split(',')[0].strip()
    return (request.remote_addr or 'unknown').strip()


def _current_utc():
    return datetime.now(timezone.utc)


def _same_origin_request():
    """Best-effort CSRF mitigation by enforcing same-origin for authenticated mutations."""
    origin_header = (request.headers.get('Origin') or '').strip()
    referer_header = (request.headers.get('Referer') or '').strip()
    host_url = (request.host_url or '').strip().rstrip('/')
    host_parts = urlparse(host_url)
    host_origin = f'{host_parts.scheme}://{host_parts.netloc}'.rstrip('/')

    if origin_header:
        return origin_header.rstrip('/') == host_origin
    if referer_header:
        referer_parts = urlparse(referer_header)
        referer_origin = f'{referer_parts.scheme}://{referer_parts.netloc}'.rstrip('/')
        return referer_origin == host_origin
    # Some user agents and clients omit both headers. Do not hard-fail those requests.
    return True


def _login_attempt_key(username):
    username_part = (username or '').strip().lower() or 'unknown'
    return f'{_get_client_ip()}::{username_part}'


def _get_login_attempt_state(username):
    tracker_key = _login_attempt_key(username)
    state = LOGIN_ATTEMPT_TRACKER.get(tracker_key)
    if not state:
        state = {
            'attempts': [],
            'locked_until': None
        }
        LOGIN_ATTEMPT_TRACKER[tracker_key] = state
    return state


def _prune_login_attempts(state):
    now = _current_utc()
    cutoff = now - timedelta(seconds=LOGIN_RATE_LIMIT_WINDOW_SECONDS)
    state['attempts'] = [timestamp for timestamp in state.get('attempts', []) if timestamp >= cutoff]

    locked_until = state.get('locked_until')
    if locked_until and locked_until <= now:
        state['locked_until'] = None


def _is_login_locked(username):
    state = _get_login_attempt_state(username)
    _prune_login_attempts(state)
    locked_until = state.get('locked_until')
    if not locked_until:
        return False, 0
    remaining_seconds = int((locked_until - _current_utc()).total_seconds())
    return remaining_seconds > 0, max(0, remaining_seconds)


def _record_login_failure(username):
    state = _get_login_attempt_state(username)
    _prune_login_attempts(state)
    state['attempts'].append(_current_utc())
    if len(state['attempts']) >= LOGIN_RATE_LIMIT_MAX_ATTEMPTS:
        state['locked_until'] = _current_utc() + timedelta(seconds=LOGIN_RATE_LIMIT_LOCK_SECONDS)


def _clear_login_failures(username):
    tracker_key = _login_attempt_key(username)
    if tracker_key in LOGIN_ATTEMPT_TRACKER:
        LOGIN_ATTEMPT_TRACKER.pop(tracker_key, None)


# ==================== ROUTES - PUBLIC ====================

def _copy_default_services():
    return [{**service} for service in FACILITY_SERVICES]


def _split_non_empty_lines(value):
    return [line.strip() for line in (value or '').splitlines() if line.strip()]


def _parse_services_json(raw_value):
    """Decode JSON service list with safe fallback to defaults."""
    if not raw_value:
        return _copy_default_services()

    try:
        parsed = json.loads(raw_value)
    except (TypeError, ValueError):
        return _copy_default_services()

    if not isinstance(parsed, list):
        return _copy_default_services()

    services = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        name = (item.get('name') or '').strip()
        description = (item.get('description') or '').strip()
        icon = (item.get('icon') or '').strip()
        if not name or not description:
            continue
        if not icon.startswith('fa-'):
            icon = 'fa-stethoscope'
        services.append({
            'name': name,
            'description': description,
            'icon': icon
        })

    return services if services else _copy_default_services()


def _services_to_editor_text(services):
    lines = []
    for service in services:
        lines.append(
            f"{service.get('name', '').strip()} | "
            f"{service.get('description', '').strip()} | "
            f"{service.get('icon', 'fa-stethoscope').strip()}"
        )
    return '\n'.join(lines)


def _parse_services_editor_text(editor_text):
    """Parse admin services text area lines into structured service records."""
    services = []
    for raw_line in (editor_text or '').splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = [part.strip() for part in line.split('|')]
        if len(parts) < 2:
            continue

        name = parts[0]
        description = parts[1]
        icon = parts[2] if len(parts) >= 3 and parts[2] else 'fa-stethoscope'

        if not name or not description:
            continue
        if not icon.startswith('fa-'):
            icon = 'fa-stethoscope'

        services.append({
            'name': name,
            'description': description,
            'icon': icon
        })

    return services if services else _copy_default_services()


def _load_site_setting_map():
    ensure_site_settings()
    rows = SiteSetting.query.all()
    return {row.setting_key: (row.setting_value or '') for row in rows}


def _resolve_site_setting(raw_map, key):
    raw_value = raw_map.get(key)
    if raw_value is None or not str(raw_value).strip():
        return DEFAULT_SITE_SETTINGS.get(key, '')
    return str(raw_value).strip()


def _is_allowed_image_filename(filename):
    if not filename or '.' not in filename:
        return False
    ext = filename.rsplit('.', 1)[1].lower()
    return ext in ALLOWED_IMAGE_EXTENSIONS


def _uploaded_image_url(filename):
    safe_name = secure_filename(str(filename or '').strip())
    if not safe_name:
        return ''

    image_path = os.path.join(app.config['UPLOAD_FOLDER'], safe_name)
    if not os.path.isfile(image_path):
        return ''

    return url_for('static', filename='uploads/' + safe_name)


def _rewind_upload_stream(file_obj):
    """Best-effort rewind for repeated reads of an uploaded file."""
    try:
        file_obj.stream.seek(0)
    except Exception:
        pass


def _validate_uploaded_image_resolution(file_obj, image_label='Image'):
    """Resolution restrictions are intentionally disabled."""
    _rewind_upload_stream(file_obj)
    return


def _save_uploaded_image(file_obj, filename, target_aspect_ratio=None, image_label='Image'):
    """
    Save uploaded image with best-effort high-quality processing.
    Falls back to raw save if Pillow is unavailable or processing fails.
    """
    _validate_uploaded_image_resolution(file_obj, image_label=image_label)

    output_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    ext = filename.rsplit('.', 1)[1].lower() if '.' in filename else ''

    if Image is None or ext == 'gif':
        _rewind_upload_stream(file_obj)
        file_obj.save(output_path)
        return False

    if ext not in ALLOWED_IMAGE_EXTENSIONS:
        _rewind_upload_stream(file_obj)
        file_obj.save(output_path)
        return False

    try:
        _rewind_upload_stream(file_obj)
        with Image.open(file_obj.stream) as opened_image:
            image = ImageOps.exif_transpose(opened_image) if ImageOps else opened_image.copy()
            image.load()

        if image.mode == 'P' and ext in {'jpg', 'jpeg', 'webp'}:
            image = image.convert('RGB')

        if ext in {'jpg', 'jpeg'} and image.mode != 'RGB':
            image = image.convert('RGB')
        elif ext == 'png' and image.mode not in {'RGB', 'RGBA'}:
            image = image.convert('RGBA')
        elif ext == 'webp' and image.mode not in {'RGB', 'RGBA'}:
            image = image.convert('RGB')

        width, height = image.size
        if target_aspect_ratio and isinstance(target_aspect_ratio, (tuple, list)) and len(target_aspect_ratio) == 2:
            try:
                ratio_w = float(target_aspect_ratio[0])
                ratio_h = float(target_aspect_ratio[1])
            except (TypeError, ValueError):
                ratio_w = 0.0
                ratio_h = 0.0

            if ratio_w > 0.0 and ratio_h > 0.0 and width > 0 and height > 0:
                desired_ratio = ratio_w / ratio_h
                current_ratio = float(width) / float(height)

                # Enforce a consistent frame by center-cropping to target ratio.
                if current_ratio > desired_ratio:
                    crop_width = max(1, int(round(height * desired_ratio)))
                    crop_left = max(0, int(round((width - crop_width) / 2.0)))
                    crop_right = min(width, crop_left + crop_width)
                    image = image.crop((crop_left, 0, crop_right, height))
                elif current_ratio < desired_ratio:
                    crop_height = max(1, int(round(width / desired_ratio)))
                    crop_top = max(0, int(round((height - crop_height) / 2.0)))
                    crop_bottom = min(height, crop_top + crop_height)
                    image = image.crop((0, crop_top, width, crop_bottom))

                width, height = image.size

        long_edge = max(width, height)
        target_long_edge = max(1, int(UPLOAD_IMAGE_LONG_EDGE_TARGET))
        if long_edge > 0 and long_edge != target_long_edge:
            # Normalize all uploaded images to a consistent 4K-class long edge.
            scale = target_long_edge / float(long_edge)
            target_size = (
                max(1, int(round(width * scale))),
                max(1, int(round(height * scale)))
            )
            if hasattr(Image, 'Resampling'):
                resample_method = Image.Resampling.BICUBIC if scale > 1.0 else Image.Resampling.LANCZOS
            else:
                resample_method = Image.BICUBIC if scale > 1.0 else Image.LANCZOS
            image = image.resize(target_size, resample=resample_method)
            if ImageFilter and scale > 1.0:
                image = image.filter(
                    ImageFilter.UnsharpMask(
                        radius=0.9,
                        percent=max(0, min(UPLOAD_IMAGE_SHARPEN_PERCENT, 120)),
                        threshold=4
                    )
                )

        if ext in {'jpg', 'jpeg'}:
            if image.mode != 'RGB':
                image = image.convert('RGB')
            image.save(
                output_path,
                format='JPEG',
                quality=98,
                subsampling=0,
                optimize=True
            )
        elif ext == 'png':
            image.save(
                output_path,
                format='PNG',
                optimize=True,
                compress_level=1
            )
        elif ext == 'webp':
            image.save(
                output_path,
                format='WEBP',
                quality=98,
                method=6
            )
        else:
            _rewind_upload_stream(file_obj)
            file_obj.save(output_path)
            return False

        _rewind_upload_stream(file_obj)
        return True
    except Exception:
        _rewind_upload_stream(file_obj)
        file_obj.save(output_path)
        return False


def _parse_uploaded_image_list(raw_value):
    """Decode stored JSON filename list for uploaded images."""
    if not raw_value:
        return []
    try:
        parsed = json.loads(raw_value)
    except (TypeError, ValueError):
        return []
    if not isinstance(parsed, list):
        return []

    filenames = []
    seen = set()
    for item in parsed:
        safe_name = secure_filename(str(item or '').strip())
        if not safe_name or safe_name in seen:
            continue
        if not _is_allowed_image_filename(safe_name):
            continue
        seen.add(safe_name)
        filenames.append(safe_name)
    return filenames


def _parse_uploaded_image_order(raw_value):
    """Decode JSON ordered list of uploaded image filenames."""
    if not raw_value:
        return []
    try:
        parsed = json.loads(raw_value)
    except (TypeError, ValueError):
        return []
    if not isinstance(parsed, list):
        return []

    ordered = []
    seen = set()
    for item in parsed:
        safe_name = secure_filename(str(item or '').strip())
        if not safe_name or safe_name in seen:
            continue
        seen.add(safe_name)
        ordered.append(safe_name)
    return ordered


def _clamp_percent(value, default=50.0):
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return float(default)
    return max(0.0, min(100.0, numeric))


def _parse_hero_background_positions(raw_value):
    """Decode stored hero image position map (filename -> {x, y})."""
    if not raw_value:
        return {}
    try:
        parsed = json.loads(raw_value)
    except (TypeError, ValueError):
        return {}
    if not isinstance(parsed, dict):
        return {}

    positions = {}
    for raw_name, raw_pos in parsed.items():
        safe_name = secure_filename(str(raw_name or '').strip())
        if not safe_name:
            continue
        if isinstance(raw_pos, dict):
            x = _clamp_percent(raw_pos.get('x'), 50.0)
            y = _clamp_percent(raw_pos.get('y'), 50.0)
        else:
            x = 50.0
            y = 50.0
        positions[safe_name] = {'x': x, 'y': y}
    return positions


def _resolve_hero_background_filenames(raw_map):
    """Return hero image filename list with legacy single-image fallback."""
    hero_filenames = _parse_uploaded_image_list(raw_map.get('hero_background_images_json'))
    legacy_name = secure_filename(str(raw_map.get('hero_background_image') or '').strip())
    if legacy_name and legacy_name not in hero_filenames and _is_allowed_image_filename(legacy_name):
        hero_filenames.insert(0, legacy_name)
    return hero_filenames


def get_site_content():
    """Get editable public content merged with defaults."""
    raw_map = _load_site_setting_map()
    data = {}
    for key in DEFAULT_SITE_SETTINGS:
        data[key] = _resolve_site_setting(raw_map, key)

    services = _parse_services_json(data.get('services_json'))
    data['services'] = services
    data['services_editor_text'] = _services_to_editor_text(services)
    hero_filenames = _resolve_hero_background_filenames(raw_map)
    hero_positions = _parse_hero_background_positions(raw_map.get('hero_background_positions_json'))
    hero_image_urls = []
    resolved_hero_filenames = []
    hero_slides = []
    resolved_positions = {}
    for image_name in hero_filenames:
        image_url = _uploaded_image_url(image_name)
        if not image_url:
            continue
        position = hero_positions.get(image_name, {'x': 50.0, 'y': 50.0})
        pos_x = _clamp_percent(position.get('x'), 50.0)
        pos_y = _clamp_percent(position.get('y'), 50.0)
        resolved_hero_filenames.append(image_name)
        hero_image_urls.append(image_url)
        resolved_positions[image_name] = {'x': pos_x, 'y': pos_y}
        hero_slides.append({
            'filename': image_name,
            'url': image_url,
            'position_x': pos_x,
            'position_y': pos_y
        })

    data['hero_background_images'] = resolved_hero_filenames
    data['hero_background_image_urls'] = hero_image_urls
    data['hero_background_slides'] = hero_slides
    data['hero_background_positions'] = resolved_positions
    data['hero_background_positions_json'] = json.dumps(resolved_positions)
    data['hero_background_image_url'] = hero_image_urls[0] if hero_image_urls else ''
    data['services_banner_image_url'] = _uploaded_image_url(data.get('services_banner_image'))
    data['services_banner_position'] = _parse_focus_position_json(data.get('services_banner_position_json'))
    data['telemedicine'] = _build_telemedicine_content(data)
    data['telemedicine_image_url'] = data['telemedicine'].get('image_url', '')
    data['contact_address_lines'] = _split_non_empty_lines(data.get('contact_address'))
    data['contact_phone_lines'] = _split_non_empty_lines(data.get('contact_phones'))
    data['contact_email_lines'] = _split_non_empty_lines(data.get('contact_emails'))
    data['opening_hours_lines'] = _split_non_empty_lines(data.get('opening_hours'))

    return data


def _upsert_site_settings(updates):
    keys = list(updates.keys())
    existing = {
        row.setting_key: row
        for row in SiteSetting.query.filter(SiteSetting.setting_key.in_(keys)).all()
    }

    for key, value in updates.items():
        normalized = (value or '').strip() if isinstance(value, str) else str(value)
        if key in existing:
            existing[key].setting_value = normalized
        else:
            db.session.add(SiteSetting(setting_key=key, setting_value=normalized))


def _normalize_rating_value(raw_rating):
    """Coerce rating to integer in range 1..5."""
    try:
        rating_value = int(raw_rating)
    except (TypeError, ValueError):
        raise ValueError('Rating must be a whole number between 1 and 5.')

    if rating_value < 1 or rating_value > 5:
        raise ValueError('Rating must be between 1 and 5.')
    return rating_value


def _get_public_rating_stats():
    """
    Public rating summary sourced from Review ratings only.
    """
    review_count = Review.query.filter_by(is_active=True).count()
    review_avg = db.session.query(db.func.avg(Review.rating)).filter(
        Review.is_active.is_(True)
    ).scalar()

    if review_count <= 0 or review_avg is None:
        return 0, 0
    return round(float(review_avg), 1), review_count


def _build_public_live_stats():
    """Real-time public metrics sourced from live database records."""
    _ensure_doctor_schema()
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    avg_rating, _rating_count = _get_public_rating_stats()
    patient_satisfaction_percent = int(round((avg_rating / 5) * 100)) if avg_rating > 0 else 0

    emergency_base_query = Call.query.filter_by(call_type='emergency')
    emergency_total_calls = emergency_base_query.count()
    emergency_today_calls = emergency_base_query.filter(Call.created_at >= today_start).count()
    emergency_active_calls = emergency_base_query.filter(Call.status.in_(ACTIVE_CALL_STATUSES)).count()
    emergency_answered_calls = emergency_base_query.filter(Call.answered_at.isnot(None)).count()

    total_calls = Call.query.count()
    total_messages = Communication.query.count()
    total_appointments = Appointment.query.count()
    total_interactions = total_calls + total_messages + total_appointments

    return {
        'total_doctors': Doctor.query.filter_by(is_active=True).count(),
        'total_reviews': Review.query.filter_by(is_active=True).count(),
        'avg_rating': avg_rating,
        'patient_satisfaction_percent': patient_satisfaction_percent,
        'total_interactions': total_interactions,
        'emergency_total_calls': emergency_total_calls,
        'emergency_today_calls': emergency_today_calls,
        'emergency_active_calls': emergency_active_calls,
        'emergency_answered_calls': emergency_answered_calls
    }


@app.context_processor
def inject_site_content():
    """Expose editable public site content to all templates."""
    nav_event_categories = [option for option in EVENT_TYPE_FILTER_OPTIONS if option['value'] != 'all']
    try:
        return {
            'site_content': get_site_content(),
            'event_type_nav_options': nav_event_categories
        }
    except Exception:
        fallback = dict(DEFAULT_SITE_SETTINGS)
        fallback['services'] = _copy_default_services()
        fallback['services_editor_text'] = _services_to_editor_text(fallback['services'])
        fallback['hero_background_images'] = []
        fallback['hero_background_image_urls'] = []
        fallback['hero_background_slides'] = []
        fallback['hero_background_positions'] = {}
        fallback['hero_background_positions_json'] = '{}'
        fallback['hero_background_image_url'] = ''
        fallback['services_banner_image_url'] = ''
        fallback['services_banner_position'] = _parse_focus_position_json(fallback.get('services_banner_position_json'))
        fallback['telemedicine'] = _build_telemedicine_content(fallback)
        fallback['telemedicine_image_url'] = fallback['telemedicine'].get('image_url', '')
        fallback['contact_address_lines'] = _split_non_empty_lines(fallback.get('contact_address'))
        fallback['contact_phone_lines'] = _split_non_empty_lines(fallback.get('contact_phones'))
        fallback['contact_email_lines'] = _split_non_empty_lines(fallback.get('contact_emails'))
        fallback['opening_hours_lines'] = _split_non_empty_lines(fallback.get('opening_hours'))
        return {
            'site_content': fallback,
            'event_type_nav_options': nav_event_categories
        }


def _completed_event_filter():
    """SQL filter for completed events, case-insensitive."""
    return db.func.lower(db.func.coalesce(Event.status, '')) == 'completed'


def _upcoming_events_query():
    """Upcoming event query for public pages."""
    now = datetime.now(timezone.utc)
    return Event.query.filter(
        Event.event_date > now,
        ~_completed_event_filter()
    ).order_by(Event.event_date)


def _past_events_query():
    """Past/completed event query for public pages."""
    now = datetime.now(timezone.utc)
    return Event.query.filter(
        or_(Event.event_date < now, _completed_event_filter())
    ).order_by(desc(Event.event_date), desc(Event.created_at))


def _normalize_event_status(status_value, event_date):
    """Normalize incoming event status from admin form data."""
    normalized = (status_value or '').strip().lower()
    if normalized in {'upcoming', 'ongoing', 'completed'}:
        return normalized

    now = datetime.now(timezone.utc) if event_date.tzinfo else datetime.now()
    return 'completed' if event_date < now else 'upcoming'


def _normalize_event_type_filter(raw_value):
    """Normalize selected event type filter from query params."""
    normalized = (raw_value or '').strip().lower()
    if normalized in VALID_EVENT_TYPE_FILTERS:
        return normalized
    return 'all'


def _apply_event_type_filter(query, selected_event_type):
    """Apply selected event type to a base events query."""
    if selected_event_type != 'all':
        return query.filter(Event.event_type == selected_event_type)
    return query


def _build_telemedicine_content(content_map=None):
    """Build telemedicine content from editable site settings with constant fallback."""
    source = content_map or {}
    return {
        'title': (source.get('telemedicine_title') or '').strip() or UPCOMING_TELEMEDICINE['title'],
        'subtitle': (source.get('telemedicine_subtitle') or '').strip() or UPCOMING_TELEMEDICINE['subtitle'],
        'description': (source.get('telemedicine_description') or '').strip() or UPCOMING_TELEMEDICINE['description'],
        'launch_window': (source.get('telemedicine_launch_window') or '').strip() or UPCOMING_TELEMEDICINE['launch_window'],
        'image_url': _uploaded_image_url(source.get('telemedicine_image'))
    }


def _build_event_slides_map(events):
    """Build image slide payload for events (cover image + gallery images)."""
    event_list = [event for event in (events or []) if getattr(event, 'id', None) is not None]
    if not event_list:
        return {}

    event_ids = [event.id for event in event_list]
    slide_map = {event_id: [] for event_id in event_ids}

    for event in event_list:
        if not event.image_filename:
            continue
        cover_url = _uploaded_image_url(event.image_filename)
        if not cover_url:
            cover_url = url_for('static', filename='uploads/' + event.image_filename)
        slide_map[event.id].append({
            'url': cover_url,
            'x': _clamp_focus_percent(event.image_focus_x, 50.0),
            'y': _clamp_focus_percent(event.image_focus_y, 50.0)
        })

    extra_photos = EventPhoto.query.filter(EventPhoto.event_id.in_(event_ids)).order_by(
        EventPhoto.event_id.asc(),
        EventPhoto.display_order.asc(),
        EventPhoto.created_at.asc()
    ).all()
    for photo in extra_photos:
        photo_url = _uploaded_image_url(photo.filename)
        if not photo_url:
            photo_url = url_for('static', filename='uploads/' + photo.filename)
        slide_map.setdefault(photo.event_id, []).append({
            'url': photo_url,
            'x': _clamp_focus_percent(photo.focus_x, 50.0),
            'y': _clamp_focus_percent(photo.focus_y, 50.0)
        })

    for event in event_list:
        setattr(event, 'media_slides', slide_map.get(event.id, []))

    return slide_map


def _clamp_focus_percent(value, default=50.0):
    """Clamp a focus percentage to the valid object-position range."""
    try:
        numeric_value = float(value)
    except (TypeError, ValueError):
        numeric_value = float(default)
    return max(0.0, min(100.0, numeric_value))


def _parse_focus_position_json(raw_value):
    """Parse one focus position payload ({x, y})."""
    fallback = {'x': 50.0, 'y': 50.0}
    if not raw_value:
        return fallback

    try:
        parsed = json.loads(raw_value)
    except (TypeError, ValueError):
        return fallback

    if not isinstance(parsed, dict):
        return fallback

    return {
        'x': _clamp_focus_percent(parsed.get('x'), 50.0),
        'y': _clamp_focus_percent(parsed.get('y'), 50.0)
    }


def _parse_focus_position_map_json(raw_value):
    """Parse a map payload: {id: {x, y}}."""
    if not raw_value:
        return {}

    try:
        parsed = json.loads(raw_value)
    except (TypeError, ValueError):
        return {}

    if not isinstance(parsed, dict):
        return {}

    position_map = {}
    for key, value in parsed.items():
        safe_key = str(key or '').strip()
        if not safe_key or not isinstance(value, dict):
            continue
        position_map[safe_key] = {
            'x': _clamp_focus_percent(value.get('x'), 50.0),
            'y': _clamp_focus_percent(value.get('y'), 50.0)
        }
    return position_map


def _collect_event_upload_files():
    """Collect uploaded event files from form data (supports multiple and legacy single-file posts)."""
    uploaded_files = request.files.getlist('image')
    if not uploaded_files:
        legacy_single_file = request.files.get('image')
        if legacy_single_file:
            uploaded_files = [legacy_single_file]

    valid_files = []
    for file_obj in uploaded_files:
        if not file_obj or not file_obj.filename:
            continue
        original_name = secure_filename(file_obj.filename)
        if not original_name:
            continue
        valid_files.append((file_obj, original_name))
    return valid_files


def _attach_uploaded_event_images(event, uploaded_files):
    """Persist uploaded event images; first image is the event cover, remaining images become gallery photos."""
    if not uploaded_files:
        return

    last_photo = EventPhoto.query.filter_by(event_id=event.id).order_by(desc(EventPhoto.display_order)).first()
    next_display_order = (last_photo.display_order + 1) if last_photo else 0

    for index, (file_obj, original_name) in enumerate(uploaded_files):
        timestamp = datetime.now().strftime('%Y%m%d%H%M%S%f')
        filename = f"event_{timestamp}_{index}_{original_name}"
        _save_uploaded_image(file_obj, filename, image_label='Event image')

        if index == 0:
            event.image_filename = filename
            event.image_focus_x = 50.0
            event.image_focus_y = 50.0
            continue

        db.session.add(
            EventPhoto(
                event_id=event.id,
                filename=filename,
                display_order=next_display_order,
                focus_x=50.0,
                focus_y=50.0
            )
        )
        next_display_order += 1


def _ensure_founder_table():
    """Ensure founder table and image-focus columns exist for older DB files."""
    global FOUNDER_TABLE_READY
    if FOUNDER_TABLE_READY:
        return
    inspector = inspect(db.engine)
    if 'founder' not in inspector.get_table_names():
        Founder.__table__.create(db.engine, checkfirst=True)
        FOUNDER_TABLE_READY = True
        return

    existing_columns = {column['name'] for column in inspector.get_columns('founder')}
    schema_changed = False
    if 'image_focus_x' not in existing_columns:
        db.session.execute(text("ALTER TABLE founder ADD COLUMN image_focus_x FLOAT DEFAULT 50.0"))
        schema_changed = True
    if 'image_focus_y' not in existing_columns:
        db.session.execute(text("ALTER TABLE founder ADD COLUMN image_focus_y FLOAT DEFAULT 50.0"))
        schema_changed = True

    if schema_changed:
        db.session.commit()

    db.session.execute(text("UPDATE founder SET image_focus_x = 50.0 WHERE image_focus_x IS NULL"))
    db.session.execute(text("UPDATE founder SET image_focus_y = 50.0 WHERE image_focus_y IS NULL"))
    db.session.commit()
    FOUNDER_TABLE_READY = True


def _ensure_partner_table():
    """Ensure partner table exists for deployments on older DB files."""
    global PARTNER_TABLE_READY
    if PARTNER_TABLE_READY:
        return
    inspector = inspect(db.engine)
    if 'partner' not in inspector.get_table_names():
        Partner.__table__.create(db.engine, checkfirst=True)
    PARTNER_TABLE_READY = True


def _ensure_doctor_schema():
    """Ensure doctor image-focus columns exist for drag-position support."""
    global DOCTOR_SCHEMA_READY
    if DOCTOR_SCHEMA_READY:
        return

    inspector = inspect(db.engine)
    if 'doctor' not in inspector.get_table_names():
        DOCTOR_SCHEMA_READY = True
        return

    existing_columns = {column['name'] for column in inspector.get_columns('doctor')}
    schema_changed = False
    if 'image_focus_x' not in existing_columns:
        db.session.execute(text("ALTER TABLE doctor ADD COLUMN image_focus_x FLOAT DEFAULT 50.0"))
        schema_changed = True
    if 'image_focus_y' not in existing_columns:
        db.session.execute(text("ALTER TABLE doctor ADD COLUMN image_focus_y FLOAT DEFAULT 50.0"))
        schema_changed = True

    if schema_changed:
        db.session.commit()

    db.session.execute(text("UPDATE doctor SET image_focus_x = 50.0 WHERE image_focus_x IS NULL"))
    db.session.execute(text("UPDATE doctor SET image_focus_y = 50.0 WHERE image_focus_y IS NULL"))
    db.session.commit()
    DOCTOR_SCHEMA_READY = True


def _ensure_communication_schema():
    """Ensure communication table has token column used for private patient thread access."""
    global COMMUNICATION_SCHEMA_READY
    if COMMUNICATION_SCHEMA_READY:
        return

    inspector = inspect(db.engine)
    table_names = set(inspector.get_table_names())
    if 'communication' not in table_names:
        # Base schema is not ready yet; retry on a later request.
        return

    existing_columns = {column['name'] for column in inspector.get_columns('communication')}
    schema_changed = False
    if 'public_token' not in existing_columns:
        db.session.execute(text("ALTER TABLE communication ADD COLUMN public_token VARCHAR(64)"))
        schema_changed = True

    if schema_changed:
        db.session.commit()

    tokenless_rows = Communication.query.filter(
        or_(Communication.public_token.is_(None), Communication.public_token == '')
    ).all()
    for communication in tokenless_rows:
        communication.public_token = uuid4().hex
    if tokenless_rows:
        db.session.commit()

    COMMUNICATION_SCHEMA_READY = True


def _ensure_communication_thread_table():
    """Ensure communication_message table exists for two-way private conversations."""
    global COMMUNICATION_THREAD_TABLE_READY
    if COMMUNICATION_THREAD_TABLE_READY:
        return

    inspector = inspect(db.engine)
    table_names = set(inspector.get_table_names())
    if 'communication' not in table_names:
        # Parent table has not been created yet.
        return
    if 'communication_message' not in table_names:
        CommunicationMessage.__table__.create(db.engine, checkfirst=True)
    COMMUNICATION_THREAD_TABLE_READY = True


def _seed_conversation_thread_if_needed(communication):
    """Backfill one-time thread entries for old rows that predate threaded messaging."""
    _ensure_communication_thread_table()
    existing_count = CommunicationMessage.query.filter_by(communication_id=communication.id).count()
    if existing_count > 0:
        return

    patient_message = (communication.message_content or '').strip()
    if patient_message:
        db.session.add(
            CommunicationMessage(
                communication_id=communication.id,
                sender_type='patient',
                sender_name=communication.patient_name,
                sender_email=communication.patient_email,
                message_content=patient_message,
                created_at=communication.created_at or datetime.now(timezone.utc)
            )
        )

    legacy_reply = (communication.reply_content or '').strip()
    if legacy_reply:
        db.session.add(
            CommunicationMessage(
                communication_id=communication.id,
                sender_type='reception',
                sender_name='Reception Team',
                sender_email=None,
                message_content=legacy_reply,
                created_at=communication.replied_at or datetime.now(timezone.utc)
            )
        )

    db.session.commit()


def _serialize_conversation_thread(communication):
    """Return normalized thread payload for public and receptionist conversation views."""
    _seed_conversation_thread_if_needed(communication)
    rows = CommunicationMessage.query.filter_by(
        communication_id=communication.id
    ).order_by(CommunicationMessage.created_at.asc(), CommunicationMessage.id.asc()).all()

    return [{
        'id': row.id,
        'sender_type': row.sender_type,
        'sender_name': row.sender_name or ('Patient' if row.sender_type == 'patient' else 'Reception'),
        'sender_email': row.sender_email,
        'message_content': row.message_content,
        'created_at': row.created_at.isoformat() if row.created_at else None
    } for row in rows]


def _hash_token(raw_token):
    """Create fixed-length hash for token persistence."""
    return hashlib.sha256((raw_token or '').encode('utf-8')).hexdigest()


def _looks_like_email(value):
    candidate = (value or '').strip()
    return bool(candidate and re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', candidate))


def _normalize_email(value):
    return (value or '').strip().lower()


def _unique_email_recipients(values):
    recipients = []
    seen = set()
    for raw_value in values:
        candidate = (raw_value or '').strip()
        candidate_key = candidate.lower()
        if not _looks_like_email(candidate):
            continue
        if candidate_key in seen:
            continue
        seen.add(candidate_key)
        recipients.append(candidate)
    return recipients


def _configured_staff_email_recipients():
    """Resolve staff alert inboxes (doctor@ / admin@) for Spaceship delivery."""
    recipients = []
    recipients.extend(_read_csv_env('STAFF_ALERT_EMAILS', aliases=('SPACESHIP_STAFF_EMAILS',)))

    for env_key in [
        'STAFF_DOCTOR_EMAIL',
        'DOCTOR_STAFF_EMAIL',
        'SPACESHIP_DOCTOR_EMAIL',
        'STAFF_ADMIN_EMAIL',
        'ADMIN_STAFF_EMAIL',
        'SPACESHIP_ADMIN_EMAIL'
    ]:
        env_value = (os.getenv(env_key) or '').strip()
        if env_value:
            recipients.append(env_value)

    if not recipients and IS_PRODUCTION:
        # Explicit fallback requested by project requirements.
        recipients.extend([
            'doctor@makokhamedicalcentre.top',
            'admin@makokhamedicalcentre.top'
        ])

    return _unique_email_recipients(recipients)


def _send_plain_email_via_resend(recipient_email, subject, plain_text):
    """Send plain-text email via Resend API."""
    resend_api_key = (os.getenv('RESEND_API_KEY') or '').strip()
    resend_from = (
        os.getenv('RESEND_FROM_EMAIL')
        or os.getenv('RESEND_FROM')
        or ''
    ).strip()
    resend_reply_to = (os.getenv('RESEND_REPLY_TO') or '').strip()
    resend_timeout = max(5, _env_int('RESEND_TIMEOUT_SECONDS', 20))

    if not recipient_email:
        return False, 'Recipient email is required.'
    if not resend_api_key or not resend_from:
        return False, 'Resend configuration is incomplete.'

    payload = {
        'from': resend_from,
        'to': [recipient_email],
        'subject': subject,
        'text': plain_text
    }
    if resend_reply_to:
        payload['reply_to'] = resend_reply_to

    request_body = json.dumps(payload).encode('utf-8')
    request_obj = urllib_request.Request(
        url='https://api.resend.com/emails',
        data=request_body,
        method='POST',
        headers={
            'Authorization': f'Bearer {resend_api_key}',
            'Content-Type': 'application/json'
        }
    )

    try:
        with urllib_request.urlopen(request_obj, timeout=resend_timeout) as response:
            status_code = getattr(response, 'status', response.getcode())
            response_text = response.read().decode('utf-8', errors='replace')
            if status_code < 200 or status_code >= 300:
                return False, f'Resend returned status {status_code}.'

            try:
                response_payload = json.loads(response_text) if response_text else {}
            except ValueError:
                response_payload = {}

            message_id = response_payload.get('id')
            if message_id:
                return True, f'Email sent successfully via Resend ({message_id}).'
            return True, 'Email sent successfully via Resend.'
    except urllib_error.HTTPError as exc:
        error_body = ''
        try:
            error_body = exc.read().decode('utf-8', errors='replace')
        except Exception:
            error_body = str(exc)
        app.logger.error('Resend HTTP error (%s): %s', getattr(exc, 'code', 'unknown'), error_body)
        return False, f'Resend HTTP error: {getattr(exc, "code", "unknown")}'
    except Exception as exc:
        app.logger.error('Resend send failed: %s', str(exc))
        return False, str(exc)


def _send_plain_email_via_spaceship(recipient_email, subject, plain_text):
    """Send plain-text email via Spaceship SMTP credentials."""
    smtp_host = (os.getenv('SPACESHIP_SMTP_HOST') or '').strip()
    smtp_port = max(1, _env_int('SPACESHIP_SMTP_PORT', 465))
    smtp_username = (os.getenv('SPACESHIP_SMTP_USERNAME') or '').strip()
    smtp_password = (os.getenv('SPACESHIP_SMTP_PASSWORD') or '').strip()
    smtp_from = (os.getenv('SPACESHIP_FROM_EMAIL') or smtp_username).strip()
    smtp_reply_to = (os.getenv('SPACESHIP_REPLY_TO') or '').strip()
    smtp_timeout = max(5, _env_int('SPACESHIP_SMTP_TIMEOUT_SECONDS', 20))
    smtp_use_ssl = _env_flag('SPACESHIP_SMTP_USE_SSL', default=(smtp_port == 465))
    smtp_use_starttls = _env_flag('SPACESHIP_SMTP_USE_STARTTLS', default=not smtp_use_ssl)

    if not recipient_email:
        return False, 'Recipient email is required.'
    if not smtp_host or not smtp_username or not smtp_password or not smtp_from:
        return False, 'Spaceship SMTP configuration is incomplete.'

    message = EmailMessage()
    message['From'] = smtp_from
    message['To'] = recipient_email
    message['Subject'] = subject
    if smtp_reply_to:
        message['Reply-To'] = smtp_reply_to
    message.set_content(plain_text)

    try:
        ssl_context = ssl.create_default_context()
        if smtp_use_ssl:
            with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=smtp_timeout, context=ssl_context) as server:
                server.login(smtp_username, smtp_password)
                server.send_message(message)
        else:
            with smtplib.SMTP(smtp_host, smtp_port, timeout=smtp_timeout) as server:
                server.ehlo()
                if smtp_use_starttls:
                    server.starttls(context=ssl_context)
                    server.ehlo()
                server.login(smtp_username, smtp_password)
                server.send_message(message)
        return True, 'Email sent successfully via Spaceship SMTP.'
    except Exception as exc:
        app.logger.error('Spaceship SMTP send failed: %s', str(exc))
        return False, str(exc)


def _send_plain_email(recipient_email, subject, plain_text, provider='resend'):
    """Send plain-text email through configured provider."""
    provider_name = (provider or 'resend').strip().lower()
    if provider_name == 'spaceship':
        return _send_plain_email_via_spaceship(recipient_email, subject, plain_text)
    return _send_plain_email_via_resend(recipient_email, subject, plain_text)


def _send_transactional_email(recipient_email, subject, plain_text):
    """Resend channel for appointment confirmations, password resets, telemedicine links."""
    return _send_plain_email(recipient_email, subject, plain_text, provider='resend')


def _send_staff_email(recipient_email, subject, plain_text):
    """Spaceship channel for internal staff mailboxes."""
    return _send_plain_email(recipient_email, subject, plain_text, provider='spaceship')


def _send_staff_alert_email(subject, plain_text):
    """Deliver internal alerts to staff inboxes using Spaceship SMTP."""
    recipients = _configured_staff_email_recipients()
    if not recipients:
        return False, 'No staff alert recipients are configured.'

    delivered = 0
    failures = []
    for recipient in recipients:
        sent, message = _send_staff_email(recipient, subject, plain_text)
        if sent:
            delivered += 1
            continue
        failures.append(f'{recipient}: {message}')

    if delivered > 0:
        if failures:
            return True, f'Staff email sent to {delivered}/{len(recipients)} recipients.'
        return True, f'Staff email sent to {delivered} recipient(s).'
    return False, '; '.join(failures) if failures else 'Failed to send staff email.'


def _appointment_reference(appointment_id):
    try:
        numeric_id = int(appointment_id)
    except (TypeError, ValueError):
        numeric_id = 0
    return f'MMC-APT-{numeric_id:06d}'


def _format_appointment_datetime(appointment_date):
    if not appointment_date:
        return 'TBD'
    return appointment_date.strftime('%B %d, %Y at %I:%M %p')


def _build_absolute_public_url(path):
    normalized_path = str(path or '').strip() or '/'
    if normalized_path.startswith('http://') or normalized_path.startswith('https://'):
        return normalized_path

    if not normalized_path.startswith('/'):
        normalized_path = '/' + normalized_path

    if has_request_context():
        return f'{_public_site_base_url()}{normalized_path}'

    configured_base_url = (
        os.getenv('PUBLIC_SITE_URL')
        or os.getenv('SITE_URL')
        or os.getenv('APP_BASE_URL')
        or ''
    ).strip().rstrip('/')
    if configured_base_url:
        return f'{configured_base_url}{normalized_path}'

    fallback_base = 'https://makokhamedicalcentre.top' if IS_PRODUCTION else 'http://localhost:5000'
    return f'{fallback_base}{normalized_path}'


def _issue_password_reset_token(user_type, user_id, email_address):
    """Create one-time password reset token and persist only its hash."""
    PasswordResetToken.query.filter_by(
        user_type=user_type,
        user_id=user_id,
        used_at=None
    ).delete(synchronize_session=False)
    db.session.commit()

    raw_token = f'{uuid4().hex}{uuid4().hex}'
    reset_token = PasswordResetToken(
        user_type=user_type,
        user_id=user_id,
        email=(email_address or '').strip(),
        token_hash=_hash_token(raw_token),
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=PASSWORD_RESET_TOKEN_TTL_MINUTES)
    )
    db.session.add(reset_token)
    db.session.commit()
    return raw_token, reset_token


def _resolve_password_reset_token(raw_token):
    """Resolve active password reset token record from raw token."""
    token_value = (raw_token or '').strip()
    if not token_value:
        return None
    token_hash = _hash_token(token_value)

    token_record = PasswordResetToken.query.filter_by(token_hash=token_hash).first()
    if not token_record:
        return None
    if token_record.used_at:
        return None

    expires_at = token_record.expires_at
    expires_at_utc = _coerce_utc(expires_at) if expires_at else None
    if not expires_at_utc or expires_at_utc < datetime.now(timezone.utc):
        return None
    return token_record


def _resolve_reset_account(token_record):
    """Fetch account referenced by a password reset token record."""
    if not token_record:
        return None
    if token_record.user_type == 'admin':
        return Admin.query.get(token_record.user_id)
    if token_record.user_type == 'reception':
        return Reception.query.get(token_record.user_id)
    return None


def _build_password_reset_url(raw_token):
    reset_path = url_for('password_reset', token=raw_token)
    return _build_absolute_public_url(reset_path)


def _create_telemedicine_session_link(appointment, created_by_user_type=None, created_by_user_id=None):
    """Generate and store a secure telemedicine link for an appointment."""
    if not appointment:
        raise ValueError('Appointment is required.')
    if not appointment.patient_email:
        raise ValueError('Appointment patient email is required.')

    raw_token = f'{uuid4().hex}{uuid4().hex}'
    link_record = TelemedicineSessionLink(
        appointment_id=appointment.id,
        patient_email=appointment.patient_email.strip(),
        token_hash=_hash_token(raw_token),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=TELEMEDICINE_LINK_TTL_HOURS),
        created_by_user_type=(created_by_user_type or '').strip() or None,
        created_by_user_id=created_by_user_id
    )
    db.session.add(link_record)
    db.session.commit()
    return _build_absolute_public_url(url_for('telemedicine_session', token=raw_token)), link_record


def _resolve_telemedicine_session(raw_token):
    """Validate telemedicine token and return session record + appointment."""
    token_value = (raw_token or '').strip()
    if not token_value:
        return None, None
    token_hash = _hash_token(token_value)

    link_record = TelemedicineSessionLink.query.filter_by(token_hash=token_hash).first()
    if not link_record:
        return None, None

    expires_at = _coerce_utc(link_record.expires_at) if link_record.expires_at else None
    if not expires_at or expires_at < datetime.now(timezone.utc):
        return link_record, None

    appointment = Appointment.query.get(link_record.appointment_id)
    if not appointment:
        return link_record, None
    return link_record, appointment


def _send_appointment_confirmation_email(appointment, doctor_name='To be assigned'):
    """Send booking/updated appointment confirmation to patient via Resend."""
    if not appointment or not appointment.patient_email:
        return False, 'Patient email is required for appointment confirmation.'

    appointment_ref = _appointment_reference(appointment.id)
    appointment_when = _format_appointment_datetime(appointment.appointment_date)
    subject = f'Appointment Confirmation ({appointment_ref}) - Makokha Medical Centre'
    body = (
        f'Dear {appointment.patient_name},\n\n'
        'Your appointment details are confirmed as below:\n'
        f'Reference: {appointment_ref}\n'
        f'Status: {str(appointment.status or "pending").title()}\n'
        f'Date and time: {appointment_when}\n'
        f'Assigned doctor: {doctor_name}\n'
        f'Reason: {appointment.reason or "N/A"}\n\n'
        'If you need to reschedule, please reply to this email or contact reception.\n\n'
        'Regards,\n'
        'Makokha Medical Centre'
    )
    return _send_transactional_email(appointment.patient_email, subject, body)


def _send_staff_new_appointment_alert(appointment, doctor_name='To be assigned'):
    """Send new-appointment alert to doctor/admin staff inboxes via Spaceship."""
    if not appointment:
        return False, 'Appointment payload is required.'

    appointment_ref = _appointment_reference(appointment.id)
    appointment_when = _format_appointment_datetime(appointment.appointment_date)
    subject = f'New Appointment Booking ({appointment_ref})'
    body = (
        'A new appointment has been booked on the website.\n\n'
        f'Reference: {appointment_ref}\n'
        f'Patient: {appointment.patient_name}\n'
        f'Email: {appointment.patient_email}\n'
        f'Phone: {appointment.patient_phone}\n'
        f'Date and time: {appointment_when}\n'
        f'Assigned doctor: {doctor_name}\n'
        f'Reason: {appointment.reason or "N/A"}\n'
        f'Status: {str(appointment.status or "pending").title()}\n\n'
        'Please follow up from the admin/reception dashboard.'
    )
    return _send_staff_alert_email(subject, body)


def _send_telemedicine_link_email(appointment, created_by_user_type=None, created_by_user_id=None):
    """Generate telemedicine session link and email it to patient via Resend."""
    if not appointment:
        return False, '', 'Appointment is required.'
    if not appointment.patient_email:
        return False, '', 'Patient email is required.'

    link_url, link_record = _create_telemedicine_session_link(
        appointment,
        created_by_user_type=created_by_user_type,
        created_by_user_id=created_by_user_id
    )
    doctor = Doctor.query.get(appointment.doctor_id) if appointment.doctor_id else None
    doctor_name = doctor.full_name() if doctor else 'To be assigned'

    subject = f'Telemedicine Session Link ({_appointment_reference(appointment.id)})'
    body = (
        f'Dear {appointment.patient_name},\n\n'
        'Your telemedicine access link is ready.\n'
        f'Join link: {link_url}\n\n'
        f'Appointment date/time: {_format_appointment_datetime(appointment.appointment_date)}\n'
        f'Assigned doctor: {doctor_name}\n\n'
        f'This link expires in {TELEMEDICINE_LINK_TTL_HOURS} hour(s).\n'
        'Please use a secure/private device when joining.\n\n'
        'Regards,\n'
        'Makokha Medical Centre'
    )
    sent, message = _send_transactional_email(appointment.patient_email, subject, body)
    if sent:
        return True, link_url, message

    try:
        db.session.delete(link_record)
        db.session.commit()
    except Exception:
        db.session.rollback()
    return False, '', message


def _ensure_runtime_schema_once():
    """Create all core tables and lightweight schema updates once per worker process."""
    global RUNTIME_SCHEMA_READY
    if RUNTIME_SCHEMA_READY:
        return

    with RUNTIME_SCHEMA_LOCK:
        if RUNTIME_SCHEMA_READY:
            return

        db.create_all()
        _ensure_founder_table()
        _ensure_partner_table()
        _ensure_doctor_schema()
        _ensure_communication_schema()
        _ensure_communication_thread_table()
        ensure_runtime_schema()
        ensure_event_schema()
        ensure_site_settings()
        RUNTIME_SCHEMA_READY = True


@app.before_request
def ensure_message_runtime_schema():
    """Ensure runtime schema exists before request handlers query tables."""
    _ensure_runtime_schema_once()


@app.before_request
def enforce_authenticated_request_safety():
    """Apply security checks for authenticated state-changing requests."""
    if session.get('user_id'):
        session.permanent = True

    if request.method not in MUTATING_HTTP_METHODS:
        return None

    request_path = request.path or ''
    for path_prefix in SAME_ORIGIN_EXEMPT_PATH_PREFIXES:
        if request_path.startswith(path_prefix):
            return None

    if session.get('user_type') in {'admin', 'reception'} and not _same_origin_request():
        if request_path.startswith('/api/'):
            return jsonify({'success': False, 'message': 'Invalid request origin.'}), 403
        flash('Security validation failed. Please try again from this website.', 'error')
        return redirect(url_for('login'))

    return None


@app.after_request
def apply_security_headers(response):
    """Attach baseline production-safe HTTP response headers."""
    response.headers.setdefault('X-Content-Type-Options', 'nosniff')
    response.headers.setdefault('X-Frame-Options', 'SAMEORIGIN')
    response.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
    response.headers.setdefault('Cross-Origin-Opener-Policy', 'same-origin')
    response.headers.setdefault('Permissions-Policy', 'geolocation=()')

    content_security_policy = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdn.socket.io https://code.jquery.com https://cdnjs.cloudflare.com; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdnjs.cloudflare.com; "
        "font-src 'self' data: https://fonts.gstatic.com https://cdnjs.cloudflare.com; "
        "img-src 'self' data: blob: https:; "
        "connect-src 'self' https: wss:; "
        "frame-ancestors 'self'; "
        "object-src 'none'; "
        "base-uri 'self'; "
        "form-action 'self';"
    )
    response.headers.setdefault('Content-Security-Policy', content_security_policy)

    if IS_PRODUCTION:
        response.headers.setdefault('Strict-Transport-Security', 'max-age=31536000; includeSubDomains')

    return response


def _cron_secret_authorized():
    """Validate cron secret header for protected keepalive routes."""
    configured_secret = (os.getenv('CRON_SECRET') or '').strip()
    provided_secret = (request.headers.get('X-CRON-SECRET') or '').strip()
    if not configured_secret:
        return False
    return provided_secret == configured_secret


@app.route('/health')
def health_check():
    """App-level keepalive endpoint for production cron pings."""
    if not _cron_secret_authorized():
        return jsonify({"error": "unauthorized"}), 401

    return jsonify({
        "app": "awake",
        "status": "ok",
        "utc": datetime.now(timezone.utc).isoformat()
    }), 200


@app.route('/healthz')
def healthz():
    """Public liveness endpoint for load balancers and Render health checks."""
    return jsonify({
        'status': 'ok',
        'app': 'awake',
        'utc': datetime.now(timezone.utc).isoformat()
    }), 200


@app.route('/db-keepalive')
def db_keepalive():
    """Database keepalive endpoint to prevent idle DB sleep on free tiers."""
    if not _cron_secret_authorized():
        return jsonify({"error": "unauthorized"}), 401

    try:
        db.session.execute(text("SELECT 1"))
        db.session.commit()
        return jsonify({"db": "awake"}), 200
    except Exception as e:
        app.logger.error("DB keepalive error: %s", str(e))
        db.session.rollback()
        return jsonify({"error": "db error"}), 500


def _public_site_base_url():
    """Resolve canonical base URL for SEO endpoints."""
    configured_base_url = (
        os.getenv('PUBLIC_SITE_URL')
        or os.getenv('SITE_URL')
        or os.getenv('APP_BASE_URL')
        or ''
    ).strip()
    if configured_base_url:
        parsed = urlparse(configured_base_url)
        if parsed.scheme and parsed.netloc:
            return configured_base_url.rstrip('/')

    request_base_url = (request.url_root or '').strip()
    if request_base_url:
        return request_base_url.rstrip('/')

    if IS_PRODUCTION:
        return 'https://makokhamedicalcentre.top'
    return 'http://localhost:5000'


def _build_public_sitemap_urls():
    """Public website pages that should be indexed by search engines."""
    return [
        {'path': '/', 'changefreq': 'daily', 'priority': '1.0'},
        {'path': '/about', 'changefreq': 'weekly', 'priority': '0.8'},
        {'path': '/doctors', 'changefreq': 'weekly', 'priority': '0.8'},
        {'path': '/events', 'changefreq': 'daily', 'priority': '0.9'},
        {'path': '/reviews', 'changefreq': 'weekly', 'priority': '0.7'},
        {'path': '/contact', 'changefreq': 'weekly', 'priority': '0.9'}
    ]


def _render_sitemap_xml():
    """Render sitemap XML for dynamic backend delivery."""
    base_url = _public_site_base_url()
    lastmod = datetime.now(timezone.utc).date().isoformat()
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
    ]

    for entry in _build_public_sitemap_urls():
        path = entry.get('path', '/')
        if path == '/':
            loc = f'{base_url}/'
        else:
            loc = f'{base_url}{path}'
        lines.extend([
            '  <url>',
            f'    <loc>{xml_escape(loc)}</loc>',
            f'    <lastmod>{lastmod}</lastmod>',
            f'    <changefreq>{entry.get("changefreq", "weekly")}</changefreq>',
            f'    <priority>{entry.get("priority", "0.5")}</priority>',
            '  </url>'
        ])

    lines.append('</urlset>')
    return '\n'.join(lines)


@app.route('/sitemap.xml')
def sitemap_xml():
    """Serve XML sitemap as a public dynamic endpoint."""
    response = app.response_class(_render_sitemap_xml(), mimetype='application/xml')
    response.headers['Cache-Control'] = 'public, max-age=3600'
    return response


@app.route('/robots.txt')
def robots_txt():
    """Serve robots.txt that points crawlers to the sitemap endpoint."""
    base_url = _public_site_base_url()
    robots_content = '\n'.join([
        'User-agent: *',
        'Allow: /',
        '',
        f'Sitemap: {base_url}/sitemap.xml'
    ])
    response = app.response_class(robots_content, mimetype='text/plain')
    response.headers['Cache-Control'] = 'public, max-age=3600'
    return response


@app.route('/')
def index():
    """Home page"""
    _ensure_founder_table()
    _ensure_partner_table()
    _ensure_doctor_schema()
    site_content = get_site_content()
    telemedicine_content = _build_telemedicine_content(site_content)
    live_stats = _build_public_live_stats()

    # Get featured doctors
    doctors = Doctor.query.filter_by(is_active=True).limit(3).all()
    founders = Founder.query.filter_by(is_active=True).order_by(Founder.display_order.asc(), Founder.created_at.asc()).all()
    partners = Partner.query.filter_by(is_active=True).order_by(Partner.display_order.asc(), Partner.created_at.asc()).all()
    
    # Get events for homepage sections
    upcoming_events = _upcoming_events_query().all()
    past_events = _past_events_query().all()
    event_slides_map = _build_event_slides_map(upcoming_events + past_events)
    
    # Get average hospital rating from active review ratings
    avg_rating, _rating_count = _get_public_rating_stats()
    
    # Get total reviews
    total_reviews = Review.query.filter_by(is_active=True).count()

    # Get admin-uploaded gallery photos for home page display
    gallery_rows = Photo.query.filter_by(is_active=True).order_by(desc(Photo.uploaded_at)).all()
    gallery_photos = []
    for photo in gallery_rows:
        photo_url = _uploaded_image_url(photo.filename)
        if not photo_url and photo.filename:
            photo_url = url_for('static', filename='uploads/' + photo.filename)
        if not photo_url:
            continue

        gallery_photos.append({
            'id': photo.id,
            'url': photo_url,
            'title': (photo.title or '').strip() or 'Gallery Photo',
            'description': (photo.description or '').strip(),
            'category': (photo.category or '').strip()
        })
    
    return render_template(
        'index.html',
        doctors=doctors,
        events=upcoming_events,
        upcoming_events=upcoming_events,
        past_events=past_events,
        avg_rating=avg_rating,
        total_reviews=total_reviews,
        total_doctors=live_stats.get('total_doctors', 0),
        services=site_content.get('services', _copy_default_services()),
        founders=founders,
        partners=partners,
        telemedicine=telemedicine_content,
        event_slides_map=event_slides_map,
        event_type_options=EVENT_TYPE_FILTER_OPTIONS,
        live_stats=live_stats,
        gallery_photos=gallery_photos
    )


@app.route('/about')
def about():
    """About page"""
    _ensure_doctor_schema()
    live_stats = _build_public_live_stats()
    doctors = Doctor.query.filter_by(is_active=True).all()
    total_doctors = live_stats.get('total_doctors', len(doctors))
    total_patients = live_stats.get('total_interactions', 0)
    
    return render_template(
        'about.html',
        doctors=doctors,
        total_doctors=total_doctors,
        total_patients=total_patients,
        live_stats=live_stats
    )


@app.route('/contact', methods=['GET', 'POST'])
def contact():
    """Contact page"""
    live_stats = _build_public_live_stats()
    if request.method == 'POST':
        # Handle contact form
        name = request.form.get('name')
        email = request.form.get('email')
        phone = request.form.get('phone')
        message = request.form.get('message')
        
        # In a real app, you'd save this or send an email
        flash('Thank you for your message. We will contact you soon!', 'success')
        return redirect(url_for('contact'))
    
    return render_template('contact.html', live_stats=live_stats)


@app.route('/doctors')
def doctors_page():
    """Doctors page"""
    _ensure_doctor_schema()
    doctors = Doctor.query.filter_by(is_active=True).all()
    return render_template('doctors.html', doctors=doctors)


@app.route('/events')
def events_page():
    """Events page"""
    selected_event_type = _normalize_event_type_filter(request.args.get('event_type'))
    selected_event_type_label = next(
        (option['label'] for option in EVENT_TYPE_FILTER_OPTIONS if option['value'] == selected_event_type),
        'All Event Types'
    )
    past_page_size = 6
    upcoming_query = _apply_event_type_filter(_upcoming_events_query(), selected_event_type)
    past_query = _apply_event_type_filter(_past_events_query(), selected_event_type)
    upcoming = upcoming_query.all()
    past = past_query.limit(past_page_size).all()
    has_more_past = past_query.count() > len(past)
    event_slides_map = _build_event_slides_map(upcoming + past)
    telemedicine_content = _build_telemedicine_content(get_site_content())
    
    return render_template(
        'events.html',
        upcoming=upcoming,
        past=past,
        has_more_past=has_more_past,
        past_page_size=past_page_size,
        selected_event_type=selected_event_type,
        selected_event_type_label=selected_event_type_label,
        telemedicine=telemedicine_content,
        event_slides_map=event_slides_map,
        event_type_options=EVENT_TYPE_FILTER_OPTIONS
    )


@app.route('/telemedicine/session/<string:token>')
def telemedicine_session(token):
    """Public telemedicine access landing page from emailed secure links."""
    link_record, appointment = _resolve_telemedicine_session(token)
    if not link_record:
        return render_template(
            'telemedicine_session.html',
            token_valid=False,
            token_status='invalid'
        ), 404

    if not appointment:
        expires_at = _coerce_utc(link_record.expires_at) if link_record.expires_at else None
        status_code = 410 if expires_at and expires_at < datetime.now(timezone.utc) else 404
        return render_template(
            'telemedicine_session.html',
            token_valid=False,
            token_status='expired' if status_code == 410 else 'invalid',
            expires_at=expires_at
        ), status_code

    assigned_doctor = Doctor.query.get(appointment.doctor_id) if appointment.doctor_id else None
    doctor_name = assigned_doctor.full_name() if assigned_doctor else 'To be assigned'
    prefill_params = urlencode({
        'open_comm': '1',
        'comm_tab': 'call',
        'name': appointment.patient_name or '',
        'email': appointment.patient_email or '',
        'phone': appointment.patient_phone or ''
    })

    return render_template(
        'telemedicine_session.html',
        token_valid=True,
        token_status='active',
        appointment=appointment,
        doctor_name=doctor_name,
        expires_at=_coerce_utc(link_record.expires_at),
        prefill_params=prefill_params
    )


@app.route('/api/events/past')
def load_past_events():
    """Load paginated past events for the public events page."""
    try:
        page = int(request.args.get('page', 1))
        page_size = int(request.args.get('page_size', 6))
    except (TypeError, ValueError):
        return jsonify({'success': False, 'message': 'Invalid pagination parameters.'}), 400

    page = max(page, 1)
    page_size = min(max(page_size, 1), 24)

    selected_event_type = _normalize_event_type_filter(request.args.get('event_type'))
    query = _apply_event_type_filter(_past_events_query(), selected_event_type)
    total = query.count()
    events = query.offset((page - 1) * page_size).limit(page_size).all()
    event_slides_map = _build_event_slides_map(events)

    payload = []
    for event in events:
        image_slides = event_slides_map.get(event.id, [])
        status_value = event.normalized_status()
        if status_value == 'ongoing':
            status_label = 'Ongoing'
        elif status_value == 'completed':
            status_label = 'Past'
        else:
            status_label = 'Upcoming'
        payload.append({
            'id': event.id,
            'title': event.title,
            'description': event.description or '',
            'event_type': (event.event_type or 'general').replace('_', ' ').title(),
            'display_date': event.event_date.strftime('%b %d, %Y'),
            'display_time': event.event_date.strftime('%I:%M %p'),
            'location': event.location or '',
            'status': status_value,
            'status_label': status_label,
            'image_url': image_slides[0]['url'] if image_slides else None,
            'image_focus_x': image_slides[0]['x'] if image_slides else _clamp_focus_percent(event.image_focus_x, 50.0),
            'image_focus_y': image_slides[0]['y'] if image_slides else _clamp_focus_percent(event.image_focus_y, 50.0),
            'image_slides': image_slides
        })

    has_more = page * page_size < total
    return jsonify({
        'success': True,
        'events': payload,
        'has_more': has_more,
        'next_page': page + 1 if has_more else None
    }), 200


@app.route('/api/public-live-stats', methods=['GET'])
def public_live_stats():
    """Expose real-time public stats to client pages."""
    try:
        stats = _build_public_live_stats()
        return jsonify({
            'success': True,
            'stats': stats,
            'generated_at': datetime.now(timezone.utc).isoformat()
        }), 200
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 400


@app.route('/reviews')
def reviews_page():
    """Reviews page"""
    reviews = Review.query.filter_by(is_active=True).order_by(desc(Review.created_at)).all()
    avg_rating, total_ratings = _get_public_rating_stats()
    
    return render_template('reviews.html', reviews=reviews, avg_rating=avg_rating,
                         total_ratings=total_ratings)


# ==================== ROUTES - PATIENT FUNCTION ====================

@app.route('/api/submit-review', methods=['POST'])
def submit_review():
    """API endpoint to submit a review"""
    try:
        data = request.get_json(silent=True) or {}
        patient_name = (data.get('name') or '').strip()
        patient_email = (data.get('email') or '').strip()
        review_text = (data.get('review') or '').strip()
        rating_value = _normalize_rating_value(data.get('rating'))
        doctor_id_raw = data.get('doctor_id')
        doctor_id = None
        if doctor_id_raw not in [None, '']:
            try:
                doctor_id = int(doctor_id_raw)
            except (TypeError, ValueError):
                doctor_id = None

        if not patient_name:
            return jsonify({'success': False, 'message': 'Name is required.'}), 400
        if not patient_email:
            return jsonify({'success': False, 'message': 'Email is required.'}), 400
        if not review_text:
            return jsonify({'success': False, 'message': 'Review text is required.'}), 400
        
        review = Review(
            patient_name=patient_name,
            patient_email=patient_email,
            review_text=review_text,
            rating=rating_value,
            doctor_id=doctor_id
        )
        
        db.session.add(review)
        db.session.commit()
        
        return jsonify({'success': True, 'message': 'Review submitted successfully!'}), 201
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 400


@app.route('/api/submit-rating', methods=['POST'])
def submit_rating():
    """Compatibility endpoint: stores rating as a Review entry."""
    try:
        data = request.get_json(silent=True) or {}
        patient_name = (data.get('name') or '').strip()
        patient_email = (data.get('email') or '').strip()
        rating_value = _normalize_rating_value(data.get('rating'))
        feedback_text = (data.get('feedback') or '').strip()

        if not patient_name:
            return jsonify({'success': False, 'message': 'Name is required.'}), 400
        if not patient_email:
            return jsonify({'success': False, 'message': 'Email is required.'}), 400

        review = Review(
            patient_name=patient_name,
            patient_email=patient_email,
            review_text=feedback_text if feedback_text else 'Hospital rating submitted without written review.',
            rating=rating_value,
            doctor_id=None
        )
        
        db.session.add(review)
        db.session.commit()
        
        # Return public average rating (Review-based source of truth)
        avg, _total_ratings = _get_public_rating_stats()
        
        return jsonify({
            'success': True,
            'message': 'Rating submitted successfully!',
            'average_rating': avg
        }), 201
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 400


# ==================== ROUTES - ADMIN & RECEPTION ====================

def _find_staff_account_by_email(email_address):
    """Resolve admin/reception account by linked account email."""
    normalized_email = _normalize_email(email_address)
    if not normalized_email:
        return '', None

    admin = Admin.query.filter(
        func.lower(Admin.email) == normalized_email
    ).first()
    if admin:
        return 'admin', admin

    reception = Reception.query.filter(
        func.lower(Reception.email) == normalized_email
    ).first()
    if reception:
        return 'reception', reception

    return '', None


def _find_staff_account_by_identity(identity):
    """Backward-compatible alias; password reset lookup now requires account email."""
    return _find_staff_account_by_email(identity)


@app.route('/login', methods=['GET', 'POST'])
def login():
    """Unified login page for admin and reception"""
    if request.method == 'POST':
        username = (request.form.get('username') or '').strip()
        password = request.form.get('password') or ''

        if not username or not password:
            flash('Username and password are required.', 'error')
            return render_template('login.html')

        locked, seconds_remaining = _is_login_locked(username)
        if locked:
            minutes_remaining = max(1, (seconds_remaining + 59) // 60)
            flash(
                f'Too many login attempts. Try again in about {minutes_remaining} minute(s).',
                'error'
            )
            return render_template('login.html')
        
        # Try admin login first
        admin = Admin.query.filter_by(username=username).first()
        if admin and admin.check_password(password):
            session.clear()
            session.permanent = True
            session['user_id'] = admin.id
            session['user_type'] = 'admin'
            session['username'] = admin.username
            session['full_name'] = admin.full_name
            _clear_login_failures(username)
            flash(f'Welcome, {admin.full_name}!', 'success')
            return redirect(url_for('admin_dashboard'))
        
        # Try reception login
        reception = Reception.query.filter_by(username=username).first()
        if reception and reception.check_password(password):
            if not reception.is_active:
                _record_login_failure(username)
                flash('Your account is inactive. Contact admin.', 'error')
                return redirect(url_for('login'))
            session.clear()
            session.permanent = True
            session['user_id'] = reception.id
            session['user_type'] = 'reception'
            session['username'] = reception.username
            session['full_name'] = reception.full_name
            _clear_login_failures(username)
            flash(f'Welcome, {reception.full_name}!', 'success')
            return redirect(url_for('reception_dashboard'))
        
        _record_login_failure(username)
        flash('Invalid username or password.', 'error')
    
    return render_template('login.html')


@app.route('/password/forgot', methods=['GET', 'POST'])
def password_forgot():
    """Request password reset link for staff accounts."""
    generic_success_message = (
        'If an account with that email exists, a reset link will be sent shortly.'
    )

    if request.method == 'POST':
        email_address = (request.form.get('email') or request.form.get('identifier') or '').strip()
        if not email_address:
            flash('Account email is required.', 'error')
            return render_template('password_forgot.html')
        if not _looks_like_email(email_address):
            flash('Enter a valid account email address.', 'error')
            return render_template('password_forgot.html')

        user_type, account = _find_staff_account_by_email(email_address)
        if not account:
            flash('No account found with that email address.', 'error')
            return render_template('password_forgot.html')
        if not _looks_like_email(getattr(account, 'email', '')):
            flash('This account does not have a valid email configured. Please contact admin.', 'error')
            return render_template('password_forgot.html')

        try:
            raw_token, _token_record = _issue_password_reset_token(
                user_type=user_type,
                user_id=account.id,
                email_address=account.email
            )
            reset_url = _build_password_reset_url(raw_token)
            subject = 'Password Reset Instructions - Makokha Medical Centre'
            body = (
                f'Dear {account.full_name},\n\n'
                'We received a request to reset your portal password.\n'
                f'Use this link to set a new password: {reset_url}\n\n'
                f'This link expires in {PASSWORD_RESET_TOKEN_TTL_MINUTES} minute(s).\n'
                'If you did not request this reset, you can ignore this email.\n\n'
                'Regards,\n'
                'Makokha Medical Centre'
            )
            email_sent, email_message = _send_transactional_email(account.email, subject, body)
            if email_sent:
                flash(generic_success_message, 'success')
            else:
                app.logger.warning(
                    'Password reset email failed for %s (%s): %s',
                    account.email,
                    user_type,
                    email_message
                )
                flash('Reset request recorded, but email delivery failed. Please contact admin.', 'warning')
        except Exception as exc:
            db.session.rollback()
            app.logger.error('Password reset request failed: %s', str(exc))
            flash('Unable to process reset request right now. Please try again shortly.', 'error')
        return redirect(url_for('password_forgot'))

    return render_template('password_forgot.html')


@app.route('/password/reset/<string:token>', methods=['GET', 'POST'])
def password_reset(token):
    """Apply password reset using one-time token."""
    raw_token = (token or '').strip()
    token_record = _resolve_password_reset_token(raw_token)
    account = _resolve_reset_account(token_record) if token_record else None
    token_valid = (
        token_record is not None
        and account is not None
        and _normalize_email(getattr(token_record, 'email', '')) == _normalize_email(getattr(account, 'email', ''))
        and bool(_normalize_email(getattr(account, 'email', '')))
    )

    if request.method == 'POST':
        if not token_valid:
            flash('This password reset link is invalid or expired.', 'error')
            return render_template('password_reset.html', token_valid=False)

        password = request.form.get('password') or ''
        confirm_password = request.form.get('confirm_password') or ''
        if len(password) < 8:
            flash('Password must be at least 8 characters.', 'error')
            return render_template('password_reset.html', token_valid=True)
        if password != confirm_password:
            flash('Passwords do not match.', 'error')
            return render_template('password_reset.html', token_valid=True)

        try:
            account.set_password(password)
            now_utc = datetime.now(timezone.utc)
            token_record.used_at = now_utc
            PasswordResetToken.query.filter(
                PasswordResetToken.user_type == token_record.user_type,
                PasswordResetToken.user_id == token_record.user_id,
                PasswordResetToken.used_at.is_(None),
                PasswordResetToken.id != token_record.id
            ).update({'used_at': now_utc}, synchronize_session=False)
            db.session.commit()
            flash('Password updated successfully. You can now log in.', 'success')
            return redirect(url_for('login'))
        except Exception as exc:
            db.session.rollback()
            app.logger.error('Password reset completion failed: %s', str(exc))
            flash('Unable to update password right now. Please try again.', 'error')
            return render_template('password_reset.html', token_valid=True)

    return render_template('password_reset.html', token_valid=token_valid)


@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    """Admin login page (redirects to unified login)"""
    return redirect(url_for('login'))


@app.route('/logout')
def logout():
    """Universal logout"""
    user_type = session.get('user_type')
    session.clear()
    flash('You have been logged out.', 'success')
    return redirect(url_for('login'))


@app.route('/admin/logout')
def admin_logout():
    """Admin logout (redirects to universal logout)"""
    return redirect(url_for('logout'))


@app.route('/admin/dashboard')
@admin_required
def admin_dashboard():
    """Admin dashboard"""
    _ensure_founder_table()
    _ensure_partner_table()
    _ensure_doctor_schema()
    total_doctors = Doctor.query.count()
    total_founders = Founder.query.count()
    total_partners = Partner.query.count()
    total_events = Event.query.count()
    total_reviews = Review.query.filter_by(is_active=True).count()
    total_photos = Photo.query.count()
    total_communications = Communication.query.count()
    active_communications = Communication.query.filter_by(is_resolved=False).count()
    
    recent_reviews = Review.query.order_by(desc(Review.created_at)).limit(5).all()
    recent_messages = Communication.query.order_by(desc(Communication.created_at)).limit(5).all()
    
    return render_template('admin/dashboard.html',
                         total_doctors=total_doctors,
                         total_founders=total_founders,
                         total_partners=total_partners,
                         total_events=total_events,
                         total_reviews=total_reviews,
                         total_photos=total_photos,
                         total_communications=total_communications,
                         active_communications=active_communications,
                         recent_reviews=recent_reviews,
                         recent_messages=recent_messages)


@app.route('/admin/site-content', methods=['GET', 'POST'])
@admin_required
def admin_site_content():
    """Manage public website content blocks from admin dashboard."""
    if request.method == 'POST':
        try:
            services = _parse_services_editor_text(request.form.get('services_editor', ''))
            raw_map = _load_site_setting_map()

            updates = {
                'about_heading': request.form.get('about_heading', ''),
                'about_intro_primary': request.form.get('about_intro_primary', ''),
                'about_intro_secondary': request.form.get('about_intro_secondary', ''),
                'mission_text': request.form.get('mission_text', ''),
                'vision_text': request.form.get('vision_text', ''),
                'footer_about_text': request.form.get('footer_about_text', ''),
                'contact_address': request.form.get('contact_address', ''),
                'contact_phones': request.form.get('contact_phones', ''),
                'contact_emails': request.form.get('contact_emails', ''),
                'opening_hours': request.form.get('opening_hours', ''),
                'emergency_call_title': request.form.get('emergency_call_title', ''),
                'emergency_call_description': request.form.get('emergency_call_description', ''),
                'telemedicine_title': request.form.get('telemedicine_title', ''),
                'telemedicine_subtitle': request.form.get('telemedicine_subtitle', ''),
                'telemedicine_description': request.form.get('telemedicine_description', ''),
                'telemedicine_launch_window': request.form.get('telemedicine_launch_window', ''),
                'services_json': json.dumps(services)
            }

            banner_position = _parse_focus_position_json(request.form.get('services_banner_position_json'))
            updates['services_banner_position_json'] = json.dumps(banner_position)

            services_banner_filename = secure_filename(str(raw_map.get('services_banner_image') or '').strip())
            if request.form.get('remove_services_banner') == 'on' and services_banner_filename:
                existing_banner_path = os.path.join(app.config['UPLOAD_FOLDER'], services_banner_filename)
                if os.path.isfile(existing_banner_path):
                    try:
                        os.remove(existing_banner_path)
                    except OSError:
                        pass
                services_banner_filename = ''

            services_banner_file = request.files.get('services_banner_image')
            if services_banner_file and services_banner_file.filename:
                original_name = secure_filename(services_banner_file.filename)
                if not _is_allowed_image_filename(original_name):
                    raise ValueError('Services banner image must be PNG, JPG, JPEG, WEBP, or GIF.')

                timestamp = datetime.now().strftime('%Y%m%d%H%M%S%f')
                new_banner_filename = f"services_banner_{timestamp}_{original_name}"
                _save_uploaded_image(services_banner_file, new_banner_filename, image_label='Services banner image')

                if services_banner_filename and services_banner_filename != new_banner_filename:
                    previous_path = os.path.join(app.config['UPLOAD_FOLDER'], services_banner_filename)
                    if os.path.isfile(previous_path):
                        try:
                            os.remove(previous_path)
                        except OSError:
                            pass
                services_banner_filename = new_banner_filename

            updates['services_banner_image'] = services_banner_filename

            telemedicine_image_filename = secure_filename(str(raw_map.get('telemedicine_image') or '').strip())
            if request.form.get('remove_telemedicine_image') in {'1', 'on', 'true'} and telemedicine_image_filename:
                existing_telemedicine_path = os.path.join(app.config['UPLOAD_FOLDER'], telemedicine_image_filename)
                if os.path.isfile(existing_telemedicine_path):
                    try:
                        os.remove(existing_telemedicine_path)
                    except OSError:
                        pass
                telemedicine_image_filename = ''

            telemedicine_file = request.files.get('telemedicine_image')
            if telemedicine_file and telemedicine_file.filename:
                original_name = secure_filename(telemedicine_file.filename)
                if not _is_allowed_image_filename(original_name):
                    raise ValueError('Telemedicine image must be PNG, JPG, JPEG, WEBP, or GIF.')

                timestamp = datetime.now().strftime('%Y%m%d%H%M%S%f')
                new_telemedicine_filename = f"telemedicine_{timestamp}_{original_name}"
                _save_uploaded_image(telemedicine_file, new_telemedicine_filename, image_label='Telemedicine image')

                if telemedicine_image_filename and telemedicine_image_filename != new_telemedicine_filename:
                    previous_telemedicine_path = os.path.join(app.config['UPLOAD_FOLDER'], telemedicine_image_filename)
                    if os.path.isfile(previous_telemedicine_path):
                        try:
                            os.remove(previous_telemedicine_path)
                        except OSError:
                            pass
                telemedicine_image_filename = new_telemedicine_filename

            updates['telemedicine_image'] = telemedicine_image_filename

            hero_filenames = _resolve_hero_background_filenames(raw_map)
            hero_positions = _parse_hero_background_positions(raw_map.get('hero_background_positions_json'))

            remove_names = {
                secure_filename(str(raw_name or '').strip())
                for raw_name in request.form.getlist('remove_hero_images')
            }
            remove_names = {name for name in remove_names if name}
            if remove_names:
                hero_filenames = [name for name in hero_filenames if name not in remove_names]
                for name in remove_names:
                    hero_positions.pop(name, None)
                    file_path = os.path.join(app.config['UPLOAD_FOLDER'], name)
                    if os.path.isfile(file_path):
                        try:
                            os.remove(file_path)
                        except OSError:
                            pass

            hero_files = [
                file_obj for file_obj in request.files.getlist('hero_background_images')
                if file_obj and file_obj.filename
            ]

            legacy_single_file = request.files.get('hero_background_image')
            if legacy_single_file and legacy_single_file.filename:
                hero_files.append(legacy_single_file)

            if len(hero_files) > MAX_HERO_IMAGES_PER_UPLOAD:
                raise ValueError(f'Please upload at most {MAX_HERO_IMAGES_PER_UPLOAD} hero images at a time.')

            for hero_file in hero_files:
                original_name = secure_filename(hero_file.filename)
                if not _is_allowed_image_filename(original_name):
                    raise ValueError('Hero background image must be PNG, JPG, JPEG, WEBP, or GIF.')

                timestamp = datetime.now().strftime('%Y%m%d%H%M%S%f')
                hero_filename = f"hero_bg_{timestamp}_{original_name}"
                _save_uploaded_image(
                    hero_file,
                    hero_filename,
                    target_aspect_ratio=HERO_IMAGE_ASPECT_RATIO,
                    image_label='Hero background image'
                )
                hero_filenames.append(hero_filename)
                hero_positions[hero_filename] = {'x': 50.0, 'y': 50.0}

            deduped = []
            seen = set()
            for image_name in hero_filenames:
                safe_name = secure_filename(str(image_name or '').strip())
                if not safe_name or safe_name in seen:
                    continue
                seen.add(safe_name)
                deduped.append(safe_name)
            hero_filenames = deduped

            posted_order = _parse_uploaded_image_order(request.form.get('hero_order_json'))
            if posted_order:
                ordered_filenames = []
                ordered_seen = set()
                for image_name in posted_order:
                    if image_name in hero_filenames and image_name not in ordered_seen:
                        ordered_filenames.append(image_name)
                        ordered_seen.add(image_name)

                for image_name in hero_filenames:
                    if image_name not in ordered_seen:
                        ordered_filenames.append(image_name)
                        ordered_seen.add(image_name)
                hero_filenames = ordered_filenames

            posted_positions = _parse_hero_background_positions(request.form.get('hero_positions_json'))
            for image_name in hero_filenames:
                if image_name in posted_positions:
                    hero_positions[image_name] = posted_positions[image_name]
                elif image_name not in hero_positions:
                    hero_positions[image_name] = {'x': 50.0, 'y': 50.0}

            hero_positions = {name: hero_positions[name] for name in hero_filenames if name in hero_positions}

            updates['hero_background_images_json'] = json.dumps(hero_filenames)
            updates['hero_background_positions_json'] = json.dumps(hero_positions)
            updates['hero_background_image'] = hero_filenames[0] if hero_filenames else ''

            _upsert_site_settings(updates)
            db.session.commit()
            flash('Website content updated successfully.', 'success')
            return redirect(url_for('admin_site_content'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error updating website content: {str(e)}', 'error')

    site_content = get_site_content()
    return render_template(
        'admin/site_content.html',
        site_content=site_content,
        services_editor_text=site_content.get('services_editor_text', '')
    )


@app.route('/admin/doctors')
@admin_required
def admin_doctors():
    """Manage doctors"""
    _ensure_doctor_schema()
    doctors = Doctor.query.all()
    return render_template('admin/doctors.html', doctors=doctors)


@app.route('/admin/founders')
@admin_required
def admin_founders():
    """Manage founder profiles."""
    _ensure_founder_table()
    founders = Founder.query.order_by(Founder.display_order.asc(), Founder.created_at.asc()).all()
    return render_template('admin/founders.html', founders=founders)


@app.route('/admin/founder/add', methods=['GET', 'POST'])
@admin_required
def admin_add_founder():
    """Add founder profile."""
    _ensure_founder_table()
    founder_focus = {'x': 50.0, 'y': 50.0}
    if request.method == 'POST':
        try:
            founder_focus = _parse_focus_position_json(request.form.get('founder_image_position_json'))
            founder = Founder(
                full_name=(request.form.get('full_name') or '').strip(),
                title=(request.form.get('title') or '').strip(),
                bio=(request.form.get('bio') or '').strip(),
                image_focus_x=founder_focus['x'],
                image_focus_y=founder_focus['y'],
                display_order=request.form.get('display_order', type=int) or 0,
                is_active=request.form.get('is_active') == 'on'
            )

            if not founder.full_name:
                raise ValueError('Founder name is required.')
            if not founder.title:
                raise ValueError('Founder title is required.')

            if 'image' in request.files:
                file = request.files['image']
                if file and file.filename:
                    original_name = secure_filename(file.filename)
                    if not _is_allowed_image_filename(original_name):
                        raise ValueError('Founder image must be PNG, JPG, JPEG, WEBP, or GIF.')
                    timestamp = datetime.now().strftime('%Y%m%d%H%M%S%f')
                    filename = f"founder_{timestamp}_{original_name}"
                    _save_uploaded_image(file, filename, image_label='Founder image')
                    founder.image_filename = filename

            db.session.add(founder)
            db.session.commit()
            flash('Founder added successfully!', 'success')
            return redirect(url_for('admin_founders'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error adding founder: {str(e)}', 'error')

    return render_template('admin/add_founder.html', founder_focus=founder_focus)


@app.route('/admin/founder/edit/<int:founder_id>', methods=['GET', 'POST'])
@admin_required
def admin_edit_founder(founder_id):
    """Edit founder profile."""
    _ensure_founder_table()
    founder = Founder.query.get_or_404(founder_id)
    founder_focus = {
        'x': _clamp_focus_percent(founder.image_focus_x, 50.0),
        'y': _clamp_focus_percent(founder.image_focus_y, 50.0)
    }

    if request.method == 'POST':
        try:
            founder_focus = _parse_focus_position_json(request.form.get('founder_image_position_json'))
            founder.full_name = (request.form.get('full_name') or '').strip()
            founder.title = (request.form.get('title') or '').strip()
            founder.bio = (request.form.get('bio') or '').strip()
            founder.image_focus_x = founder_focus['x']
            founder.image_focus_y = founder_focus['y']
            founder.display_order = request.form.get('display_order', type=int) or 0
            founder.is_active = request.form.get('is_active') == 'on'

            if not founder.full_name:
                raise ValueError('Founder name is required.')
            if not founder.title:
                raise ValueError('Founder title is required.')

            if 'image' in request.files:
                file = request.files['image']
                if file and file.filename:
                    original_name = secure_filename(file.filename)
                    if not _is_allowed_image_filename(original_name):
                        raise ValueError('Founder image must be PNG, JPG, JPEG, WEBP, or GIF.')
                    timestamp = datetime.now().strftime('%Y%m%d%H%M%S%f')
                    filename = f"founder_{timestamp}_{original_name}"
                    _save_uploaded_image(file, filename, image_label='Founder image')
                    founder.image_filename = filename

            db.session.commit()
            flash('Founder updated successfully!', 'success')
            return redirect(url_for('admin_founders'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error updating founder: {str(e)}', 'error')

    return render_template('admin/edit_founder.html', founder=founder, founder_focus=founder_focus)


@app.route('/admin/founder/delete/<int:founder_id>', methods=['POST'])
@admin_required
def admin_delete_founder(founder_id):
    """Delete founder profile."""
    _ensure_founder_table()
    try:
        founder = Founder.query.get_or_404(founder_id)
        if founder.image_filename:
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], founder.image_filename)
            if os.path.isfile(file_path):
                try:
                    os.remove(file_path)
                except OSError:
                    pass
        db.session.delete(founder)
        db.session.commit()
        flash('Founder deleted successfully!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting founder: {str(e)}', 'error')

    return redirect(url_for('admin_founders'))


@app.route('/admin/partners')
@admin_required
def admin_partners():
    """Manage partner profiles."""
    _ensure_partner_table()
    partners = Partner.query.order_by(Partner.display_order.asc(), Partner.created_at.asc()).all()
    return render_template('admin/partners.html', partners=partners)


@app.route('/admin/partner/add', methods=['GET', 'POST'])
@admin_required
def admin_add_partner():
    """Add partner profile."""
    _ensure_partner_table()
    if request.method == 'POST':
        try:
            partner = Partner(
                full_name=(request.form.get('full_name') or '').strip(),
                title=(request.form.get('title') or '').strip(),
                bio=(request.form.get('bio') or '').strip(),
                display_order=request.form.get('display_order', type=int) or 0,
                is_active=request.form.get('is_active') == 'on'
            )

            if not partner.full_name:
                raise ValueError('Partner name is required.')
            if not partner.title:
                raise ValueError('Partner title is required.')

            if 'image' in request.files:
                file = request.files['image']
                if file and file.filename:
                    original_name = secure_filename(file.filename)
                    if not _is_allowed_image_filename(original_name):
                        raise ValueError('Partner image must be PNG, JPG, JPEG, WEBP, or GIF.')
                    timestamp = datetime.now().strftime('%Y%m%d%H%M%S%f')
                    filename = f"partner_{timestamp}_{original_name}"
                    _save_uploaded_image(file, filename, image_label='Partner image')
                    partner.image_filename = filename

            db.session.add(partner)
            db.session.commit()
            flash('Partner added successfully!', 'success')
            return redirect(url_for('admin_partners'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error adding partner: {str(e)}', 'error')

    return render_template('admin/add_partner.html')


@app.route('/admin/partner/edit/<int:partner_id>', methods=['GET', 'POST'])
@admin_required
def admin_edit_partner(partner_id):
    """Edit partner profile."""
    _ensure_partner_table()
    partner = Partner.query.get_or_404(partner_id)

    if request.method == 'POST':
        try:
            partner.full_name = (request.form.get('full_name') or '').strip()
            partner.title = (request.form.get('title') or '').strip()
            partner.bio = (request.form.get('bio') or '').strip()
            partner.display_order = request.form.get('display_order', type=int) or 0
            partner.is_active = request.form.get('is_active') == 'on'

            if not partner.full_name:
                raise ValueError('Partner name is required.')
            if not partner.title:
                raise ValueError('Partner title is required.')

            if 'image' in request.files:
                file = request.files['image']
                if file and file.filename:
                    original_name = secure_filename(file.filename)
                    if not _is_allowed_image_filename(original_name):
                        raise ValueError('Partner image must be PNG, JPG, JPEG, WEBP, or GIF.')
                    timestamp = datetime.now().strftime('%Y%m%d%H%M%S%f')
                    filename = f"partner_{timestamp}_{original_name}"
                    _save_uploaded_image(file, filename, image_label='Partner image')
                    partner.image_filename = filename

            db.session.commit()
            flash('Partner updated successfully!', 'success')
            return redirect(url_for('admin_partners'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error updating partner: {str(e)}', 'error')

    return render_template('admin/edit_partner.html', partner=partner)


@app.route('/admin/partner/delete/<int:partner_id>', methods=['POST'])
@admin_required
def admin_delete_partner(partner_id):
    """Delete partner profile."""
    _ensure_partner_table()
    try:
        partner = Partner.query.get_or_404(partner_id)
        if partner.image_filename:
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], partner.image_filename)
            if os.path.isfile(file_path):
                try:
                    os.remove(file_path)
                except OSError:
                    pass
        db.session.delete(partner)
        db.session.commit()
        flash('Partner deleted successfully!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting partner: {str(e)}', 'error')

    return redirect(url_for('admin_partners'))


@app.route('/admin/doctor/add', methods=['GET', 'POST'])
@admin_required
def admin_add_doctor():
    """Add new doctor"""
    _ensure_doctor_schema()
    if request.method == 'POST':
        try:
            doctor_focus = _parse_focus_position_json(request.form.get('doctor_image_position_json'))
            doctor = Doctor(
                first_name=request.form.get('first_name'),
                last_name=request.form.get('last_name'),
                specialty=request.form.get('specialty'),
                qualification=request.form.get('qualification'),
                bio=request.form.get('bio'),
                phone=request.form.get('phone'),
                email=request.form.get('email'),
                available_days=request.form.get('available_days'),
                consulting_hours=request.form.get('consulting_hours'),
                image_focus_x=doctor_focus['x'],
                image_focus_y=doctor_focus['y']
            )
            
            # Handle image upload
            if 'image' in request.files:
                file = request.files['image']
                if file and file.filename:
                    filename = secure_filename(file.filename)
                    timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
                    filename = f"doctor_{timestamp}_{filename}"
                    _save_uploaded_image(file, filename, image_label='Doctor image')
                    doctor.image_filename = filename
            
            db.session.add(doctor)
            db.session.commit()
            
            flash('Doctor added successfully!', 'success')
            return redirect(url_for('admin_doctors'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error adding doctor: {str(e)}', 'error')
    
    return render_template('admin/add_doctor.html', doctor_focus={'x': 50.0, 'y': 50.0})


@app.route('/admin/doctor/edit/<int:doctor_id>', methods=['GET', 'POST'])
@admin_required
def admin_edit_doctor(doctor_id):
    """Edit doctor"""
    _ensure_doctor_schema()
    doctor = Doctor.query.get_or_404(doctor_id)
    
    if request.method == 'POST':
        try:
            doctor_focus = _parse_focus_position_json(request.form.get('doctor_image_position_json'))
            doctor.first_name = request.form.get('first_name')
            doctor.last_name = request.form.get('last_name')
            doctor.specialty = request.form.get('specialty')
            doctor.qualification = request.form.get('qualification')
            doctor.bio = request.form.get('bio')
            doctor.phone = request.form.get('phone')
            doctor.email = request.form.get('email')
            doctor.available_days = request.form.get('available_days')
            doctor.consulting_hours = request.form.get('consulting_hours')
            doctor.image_focus_x = doctor_focus['x']
            doctor.image_focus_y = doctor_focus['y']
            
            # Handle image upload
            if 'image' in request.files:
                file = request.files['image']
                if file and file.filename:
                    filename = secure_filename(file.filename)
                    timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
                    filename = f"doctor_{timestamp}_{filename}"
                    _save_uploaded_image(file, filename, image_label='Doctor image')
                    doctor.image_filename = filename
            
            db.session.commit()
            flash('Doctor updated successfully!', 'success')
            return redirect(url_for('admin_doctors'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error updating doctor: {str(e)}', 'error')
    
    doctor_focus = {
        'x': _clamp_focus_percent(doctor.image_focus_x, 50.0),
        'y': _clamp_focus_percent(doctor.image_focus_y, 50.0)
    }
    return render_template('admin/edit_doctor.html', doctor=doctor, doctor_focus=doctor_focus)


@app.route('/admin/doctor/delete/<int:doctor_id>', methods=['POST'])
@admin_required
def admin_delete_doctor(doctor_id):
    """Delete doctor"""
    _ensure_doctor_schema()
    try:
        doctor = Doctor.query.get_or_404(doctor_id)
        db.session.delete(doctor)
        db.session.commit()
        flash('Doctor deleted successfully!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting doctor: {str(e)}', 'error')
    
    return redirect(url_for('admin_doctors'))


@app.route('/admin/events')
@admin_required
def admin_events():
    """Manage events"""
    events = Event.query.order_by(desc(Event.event_date)).all()
    return render_template('admin/events.html', events=events)


@app.route('/admin/event/add', methods=['GET', 'POST'])
@admin_required
def admin_add_event():
    """Add new event"""
    if request.method == 'POST':
        try:
            event_date = datetime.strptime(
                f"{request.form.get('event_date')} {request.form.get('event_time')}",
                '%Y-%m-%d %H:%M'
            )
            
            event = Event(
                title=request.form.get('title'),
                description=request.form.get('description'),
                event_date=event_date,
                location=request.form.get('location'),
                event_type=request.form.get('event_type'),
                status=_normalize_event_status(request.form.get('status'), event_date)
            )

            db.session.add(event)
            db.session.flush()
            _attach_uploaded_event_images(event, _collect_event_upload_files())
            db.session.commit()
            
            flash('Event added successfully!', 'success')
            return redirect(url_for('admin_events'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error adding event: {str(e)}', 'error')
    
    return render_template('admin/add_event.html')


@app.route('/admin/event/edit/<int:event_id>', methods=['GET', 'POST'])
@admin_required
def admin_edit_event(event_id):
    """Edit event"""
    event = Event.query.get_or_404(event_id)
    event_photos = EventPhoto.query.filter_by(event_id=event.id).order_by(
        EventPhoto.display_order.asc(),
        EventPhoto.created_at.asc()
    ).all()
    
    if request.method == 'POST':
        try:
            event_date = datetime.strptime(
                f"{request.form.get('event_date')} {request.form.get('event_time')}",
                '%Y-%m-%d %H:%M'
            )
            
            event.title = request.form.get('title')
            event.description = request.form.get('description')
            event.event_date = event_date
            event.location = request.form.get('location')
            event.event_type = request.form.get('event_type')
            event.status = _normalize_event_status(request.form.get('status'), event_date)

            cover_focus = _parse_focus_position_json(request.form.get('event_cover_position_json'))
            event.image_focus_x = cover_focus['x']
            event.image_focus_y = cover_focus['y']

            if request.form.get('remove_event_cover') == 'on' and event.image_filename:
                cover_file_path = os.path.join(app.config['UPLOAD_FOLDER'], event.image_filename)
                if os.path.isfile(cover_file_path):
                    try:
                        os.remove(cover_file_path)
                    except OSError:
                        pass
                event.image_filename = None

            remove_photo_ids = set()
            for raw_id in request.form.getlist('remove_event_photo_ids'):
                try:
                    remove_photo_ids.add(int(raw_id))
                except (TypeError, ValueError):
                    continue

            if remove_photo_ids:
                removable_photos = EventPhoto.query.filter(
                    EventPhoto.event_id == event.id,
                    EventPhoto.id.in_(remove_photo_ids)
                ).all()
                for removable_photo in removable_photos:
                    file_path = os.path.join(app.config['UPLOAD_FOLDER'], removable_photo.filename)
                    if os.path.isfile(file_path):
                        try:
                            os.remove(file_path)
                        except OSError:
                            pass
                    db.session.delete(removable_photo)

            photo_position_map = _parse_focus_position_map_json(request.form.get('event_photo_positions_json'))
            if photo_position_map:
                editable_photos = EventPhoto.query.filter_by(event_id=event.id).all()
                for editable_photo in editable_photos:
                    position = photo_position_map.get(str(editable_photo.id))
                    if not position:
                        continue
                    editable_photo.focus_x = position['x']
                    editable_photo.focus_y = position['y']

            _attach_uploaded_event_images(event, _collect_event_upload_files())
            db.session.commit()
            flash('Event updated successfully!', 'success')
            return redirect(url_for('admin_events'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error updating event: {str(e)}', 'error')

        event_photos = EventPhoto.query.filter_by(event_id=event.id).order_by(
            EventPhoto.display_order.asc(),
            EventPhoto.created_at.asc()
        ).all()

    cover_focus = {
        'x': _clamp_focus_percent(event.image_focus_x, 50.0),
        'y': _clamp_focus_percent(event.image_focus_y, 50.0)
    }
    event_photo_positions = {
        str(photo.id): {
            'x': _clamp_focus_percent(photo.focus_x, 50.0),
            'y': _clamp_focus_percent(photo.focus_y, 50.0)
        }
        for photo in event_photos
    }
    return render_template(
        'admin/edit_event.html',
        event=event,
        event_photos=event_photos,
        cover_focus=cover_focus,
        event_photo_positions=event_photo_positions
    )


@app.route('/admin/event/delete/<int:event_id>', methods=['POST'])
@admin_required
def admin_delete_event(event_id):
    """Delete event"""
    try:
        event = Event.query.get_or_404(event_id)

        if event.image_filename:
            cover_path = os.path.join(app.config['UPLOAD_FOLDER'], event.image_filename)
            if os.path.isfile(cover_path):
                try:
                    os.remove(cover_path)
                except OSError:
                    pass

        event_photos = EventPhoto.query.filter_by(event_id=event.id).all()
        for event_photo in event_photos:
            photo_path = os.path.join(app.config['UPLOAD_FOLDER'], event_photo.filename)
            if os.path.isfile(photo_path):
                try:
                    os.remove(photo_path)
                except OSError:
                    pass
            db.session.delete(event_photo)

        db.session.delete(event)
        db.session.commit()
        flash('Event deleted successfully!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting event: {str(e)}', 'error')
    
    return redirect(url_for('admin_events'))


@app.route('/admin/photos')
@admin_required
def admin_photos():
    """Manage photos"""
    photos = Photo.query.order_by(desc(Photo.uploaded_at)).all()
    return render_template('admin/photos.html', photos=photos)


@app.route('/admin/photo/upload', methods=['GET', 'POST'])
@admin_required
def admin_upload_photo():
    """Upload photo"""
    if request.method == 'POST':
        try:
            uploaded_files = [
                file_obj for file_obj in request.files.getlist('image')
                if file_obj and file_obj.filename
            ]
            if not uploaded_files:
                legacy_single = request.files.get('image')
                if legacy_single and legacy_single.filename:
                    uploaded_files = [legacy_single]

            if not uploaded_files:
                flash('No image selected', 'error')
                return redirect(url_for('admin_upload_photo'))

            title_base = (request.form.get('title') or '').strip()
            description_value = request.form.get('description')
            category_value = request.form.get('category')
            created_count = 0

            for index, file_obj in enumerate(uploaded_files, start=1):
                original_name = secure_filename(file_obj.filename)
                if not original_name:
                    continue
                if not _is_allowed_image_filename(original_name):
                    raise ValueError('Photo image must be PNG, JPG, JPEG, WEBP, or GIF.')

                timestamp = datetime.now().strftime('%Y%m%d%H%M%S%f')
                filename = f"photo_{timestamp}_{index}_{original_name}"
                _save_uploaded_image(file_obj, filename, image_label='Photo')

                photo_title = title_base
                if photo_title and len(uploaded_files) > 1:
                    photo_title = f'{photo_title} ({index})'
                if not photo_title:
                    photo_title = original_name

                db.session.add(
                    Photo(
                        filename=filename,
                        title=photo_title,
                        description=description_value,
                        category=category_value
                    )
                )
                created_count += 1

            if created_count <= 0:
                raise ValueError('No valid images selected for upload.')
            db.session.commit()
            flash(f'{created_count} photo(s) uploaded successfully!', 'success')
            return redirect(url_for('admin_photos'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error uploading photo: {str(e)}', 'error')
    
    return render_template('admin/upload_photo.html')


@app.route('/admin/photo/delete/<int:photo_id>', methods=['POST'])
@admin_required
def admin_delete_photo(photo_id):
    """Delete photo"""
    try:
        photo = Photo.query.get_or_404(photo_id)
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], photo.filename)
        if os.path.exists(filepath):
            os.remove(filepath)
        db.session.delete(photo)
        db.session.commit()
        flash('Photo deleted successfully!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting photo: {str(e)}', 'error')
    
    return redirect(url_for('admin_photos'))


@app.route('/admin/reviews')
@admin_required
def admin_reviews():
    """View all reviews"""
    reviews = Review.query.order_by(desc(Review.created_at)).all()
    return render_template('admin/reviews.html', reviews=reviews)


@app.route('/admin/review/delete/<int:review_id>', methods=['POST'])
@admin_required
def admin_delete_review(review_id):
    """Delete review"""
    try:
        review = Review.query.get_or_404(review_id)
        db.session.delete(review)
        db.session.commit()
        flash('Review deleted successfully!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting review: {str(e)}', 'error')
    
    return redirect(url_for('admin_reviews'))


# ==================== ROUTES - RECEPTION ====================

@app.route('/reception/dashboard')
@reception_required
def reception_dashboard():
    """Reception dashboard"""
    reception_id = session.get('user_id')
    now = datetime.now(timezone.utc)
    active_window_start = now - timedelta(minutes=CALL_HOLD_STALE_MINUTES)
    
    # Get communications for this reception
    total_messages = Communication.query.filter_by(reception_id=reception_id).count()
    unread_messages = Communication.query.filter_by(reception_id=reception_id, is_read=False).count()
    pending_messages = Communication.query.filter_by(reception_id=reception_id, is_resolved=False).count()
    
    # Get recent communications
    recent_communications = Communication.query.filter_by(reception_id=reception_id).order_by(
        desc(Communication.created_at)
    ).limit(10).all()
    
    # Get pending appointments
    pending_appointments = Appointment.query.filter_by(
        reception_id=reception_id,
        status='pending'
    ).order_by(Appointment.appointment_date).limit(10).all()
    
    # Get unread notifications
    unread_notifications = Notification.query.filter_by(
        reception_id=reception_id,
        is_read=False
    ).order_by(desc(Notification.created_at)).limit(10).all()
    
    # Get pending calls assigned to this reception
    pending_calls = Call.query.filter(
        Call.reception_user_id == reception_id,
        Call.status.in_(['initiated', 'dialing', 'ringing', 'busy', 'on_hold']),
        Call.created_at >= active_window_start
    ).order_by(desc(Call.created_at)).limit(10).all()

    pending_emergency_calls = [call for call in pending_calls if call.call_type == 'emergency']
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    emergency_today_calls = Call.query.filter(
        Call.call_type == 'emergency',
        Call.created_at >= today_start
    ).count()
    emergency_active_calls = Call.query.filter(
        Call.call_type == 'emergency',
        Call.status.in_(ACTIVE_CALL_STATUSES)
    ).count()
    emergency_answered_calls = Call.query.filter(
        Call.call_type == 'emergency',
        Call.answered_at.isnot(None)
    ).count()
    
    return render_template('reception/dashboard.html',
                         total_messages=total_messages,
                         unread_messages=unread_messages,
                         pending_messages=pending_messages,
                         recent_communications=recent_communications,
                         pending_appointments=pending_appointments,
                         unread_notifications=unread_notifications,
                         pending_calls=pending_calls,
                         pending_emergency_calls=pending_emergency_calls,
                         emergency_today_calls=emergency_today_calls,
                         emergency_active_calls=emergency_active_calls,
                         emergency_answered_calls=emergency_answered_calls)


@app.route('/api/reception/emergency-receive', methods=['GET'])
@reception_required
def reception_emergency_receive():
    """Reception endpoint to fetch live emergency-call queue and stats."""
    reception_id = session.get('user_id')
    if not reception_id:
        return jsonify({'success': False, 'message': 'Reception session not found.'}), 401

    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    active_window_start = now - timedelta(minutes=CALL_HOLD_STALE_MINUTES)

    emergency_calls = Call.query.filter(
        Call.reception_user_id == reception_id,
        Call.call_type == 'emergency',
        Call.status.in_(['initiated', 'dialing', 'ringing', 'busy', 'on_hold']),
        Call.created_at >= active_window_start
    ).order_by(desc(Call.created_at)).limit(20).all()

    calls_payload = [{
        'call_id': call.call_id,
        'patient_name': call.patient_name,
        'patient_phone': call.patient_phone,
        'status': call.status,
        'created_at': call.created_at.strftime('%I:%M %p') if call.created_at else '',
        'call_type': call.call_type
    } for call in emergency_calls]

    stats = {
        'today_calls': Call.query.filter(
            Call.call_type == 'emergency',
            Call.created_at >= today_start
        ).count(),
        'active_calls': Call.query.filter(
            Call.call_type == 'emergency',
            Call.status.in_(ACTIVE_CALL_STATUSES)
        ).count(),
        'answered_calls': Call.query.filter(
            Call.call_type == 'emergency',
            Call.answered_at.isnot(None)
        ).count()
    }

    return jsonify({
        'success': True,
        'calls': calls_payload,
        'stats': stats,
        'generated_at': now.isoformat()
    }), 200


@app.route('/reception/messages')
@reception_required
def reception_messages():
    """View all communications"""
    reception_id = session.get('user_id')
    
    # Pagination
    page = request.args.get('page', 1, type=int)
    sort_by = request.args.get('sort', 'recent', type=str)
    filter_type = request.args.get('filter', 'all', type=str)
    
    query = Communication.query.filter_by(reception_id=reception_id)
    
    # Apply filters
    if filter_type == 'unread':
        query = query.filter_by(is_read=False)
    elif filter_type == 'resolved':
        query = query.filter_by(is_resolved=True)
    elif filter_type == 'pending':
        query = query.filter_by(is_resolved=False)
    
    # Apply sorting
    if sort_by == 'oldest':
        query = query.order_by(Communication.created_at)
    elif sort_by == 'urgent':
        query = query.order_by(Communication.priority.desc()).order_by(desc(Communication.created_at))
    else:  # recent
        query = query.order_by(desc(Communication.created_at))
    
    communications = query.paginate(page=page, per_page=20)
    
    return render_template('reception/messages.html', communications=communications)


@app.route('/reception/message/<int:message_id>')
@reception_required
def view_communication(message_id):
    """View single communication"""
    communication = Communication.query.get_or_404(message_id)
    
    # Verify ownership
    if communication.reception_id != session.get('user_id'):
        flash('Unauthorized access', 'error')
        return redirect(url_for('reception_dashboard'))
    
    # Mark as read
    if not communication.is_read:
        communication.is_read = True
        db.session.commit()
    
    return render_template('reception/view_message.html', communication=communication)


@app.route('/api/send-message', methods=['POST'])
def send_message():
    """API endpoint to send patient message (public)"""
    try:
        _ensure_communication_schema()
        _ensure_communication_thread_table()
        data = request.get_json()
        patient_name = (data.get('name') or '').strip()
        patient_email = (data.get('email') or '').strip()
        message_text = (data.get('message') or '').strip()

        if not patient_name:
            return jsonify({'success': False, 'message': 'Name is required.'}), 400
        if not patient_email:
            return jsonify({'success': False, 'message': 'Email is required.'}), 400
        if not message_text:
            return jsonify({'success': False, 'message': 'Message is required.'}), 400
        
        communication = Communication(
            patient_name=patient_name,
            patient_email=patient_email,
            patient_phone=(data.get('phone') or '').strip(),
            message_type=data.get('type', 'message'),
            message_content=message_text,
            priority=data.get('priority', 'normal'),
            public_token=uuid4().hex
        )
        
        # Assign to first available reception
        available_reception = Reception.query.filter_by(is_available=True).first()
        if available_reception:
            communication.reception_id = available_reception.id
        
        db.session.add(communication)
        db.session.flush()  # Get the ID before committing

        # Seed initial message into thread table for two-way private conversation.
        db.session.add(
            CommunicationMessage(
                communication_id=communication.id,
                sender_type='patient',
                sender_name=communication.patient_name,
                sender_email=communication.patient_email,
                message_content=message_text,
                created_at=communication.created_at or datetime.now(timezone.utc)
            )
        )
        
        # Create notification if reception assigned
        if communication.reception_id:
            notification = Notification(
                reception_id=communication.reception_id,
                communication_id=communication.id,
                notification_type='message',
                title=f'New {data.get("type", "message")} from {patient_name}',
                message=message_text[:100] + '...' if len(message_text) > 100 else message_text
            )
            db.session.add(notification)
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': 'Message sent successfully! Our team will respond soon.',
            'message_id': communication.id,
            'conversation_token': communication.public_token
        }), 201
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 400


@app.route('/api/communication-thread/<int:communication_id>', methods=['GET'])
def get_public_communication_thread(communication_id):
    """Fetch private conversation thread for a patient using per-thread token."""
    _ensure_communication_schema()
    _ensure_communication_thread_table()
    token = (
        request.headers.get('X-CONVERSATION-TOKEN')
        or request.args.get('token')
        or ''
    ).strip()
    if not token:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401

    communication = Communication.query.get_or_404(communication_id)
    if not communication.public_token or communication.public_token != token:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 403

    thread_messages = _serialize_conversation_thread(communication)
    return jsonify({
        'success': True,
        'communication': {
            'id': communication.id,
            'patient_name': communication.patient_name,
            'patient_email': communication.patient_email,
            'is_resolved': bool(communication.is_resolved),
            'updated_at': communication.updated_at.isoformat() if communication.updated_at else None
        },
        'messages': thread_messages
    }), 200


@app.route('/api/message-reply', methods=['POST'])
def patient_message_reply():
    """Allow patient to continue private two-way conversation from customer-care desk modal."""
    try:
        _ensure_communication_schema()
        _ensure_communication_thread_table()
        data = request.get_json() or {}
        communication_id = data.get('communication_id')
        token = (data.get('conversation_token') or '').strip()
        message_text = (data.get('message') or '').strip()

        if not communication_id:
            return jsonify({'success': False, 'message': 'Communication ID is required.'}), 400
        if not token:
            return jsonify({'success': False, 'message': 'Unauthorized'}), 401
        if not message_text:
            return jsonify({'success': False, 'message': 'Message is required.'}), 400

        communication = Communication.query.get_or_404(communication_id)
        if not communication.public_token or communication.public_token != token:
            return jsonify({'success': False, 'message': 'Unauthorized'}), 403

        patient_name = (data.get('name') or communication.patient_name or 'Patient').strip()
        patient_email = (data.get('email') or communication.patient_email or '').strip()
        patient_phone = (data.get('phone') or communication.patient_phone or '').strip()

        communication.patient_name = patient_name or communication.patient_name
        if patient_email:
            communication.patient_email = patient_email
        communication.patient_phone = patient_phone
        communication.is_read = False
        communication.is_resolved = False

        if not communication.reception_id:
            available_reception = Reception.query.filter_by(is_available=True).first()
            if available_reception:
                communication.reception_id = available_reception.id

        db.session.add(
            CommunicationMessage(
                communication_id=communication.id,
                sender_type='patient',
                sender_name=patient_name,
                sender_email=patient_email or None,
                message_content=message_text
            )
        )

        if communication.reception_id:
            notification = Notification(
                reception_id=communication.reception_id,
                communication_id=communication.id,
                notification_type='message',
                title=f'Patient reply from {communication.patient_name}',
                message=message_text[:100] + '...' if len(message_text) > 100 else message_text
            )
            db.session.add(notification)

        db.session.commit()

        return jsonify({
            'success': True,
            'message': 'Reply sent successfully.',
            'communication_id': communication.id
        }), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 400


@app.route('/api/reception/communication-thread/<int:communication_id>', methods=['GET'])
@reception_required
def reception_communication_thread(communication_id):
    """Fetch threaded conversation for reception message view."""
    _ensure_communication_schema()
    _ensure_communication_thread_table()
    communication = Communication.query.get_or_404(communication_id)

    if communication.reception_id != session.get('user_id'):
        return jsonify({'success': False, 'message': 'Unauthorized'}), 403

    thread_messages = _serialize_conversation_thread(communication)
    return jsonify({
        'success': True,
        'messages': thread_messages,
        'is_resolved': bool(communication.is_resolved)
    }), 200


@app.route('/api/reply-message', methods=['POST'])
@reception_required
def reply_message():
    """API endpoint for reception to reply to message"""
    try:
        _ensure_communication_schema()
        _ensure_communication_thread_table()
        data = request.get_json()
        communication_id = data.get('communication_id')
        reply = (data.get('reply') or '').strip()
        if not reply:
            return jsonify({'success': False, 'message': 'Reply is required.'}), 400
        
        communication = Communication.query.get_or_404(communication_id)
        
        # Verify ownership
        if communication.reception_id != session.get('user_id'):
            return jsonify({'success': False, 'message': 'Unauthorized'}), 403
        
        reception_name = (session.get('full_name') or 'Reception Team').strip()
        db.session.add(
            CommunicationMessage(
                communication_id=communication.id,
                sender_type='reception',
                sender_name=reception_name,
                sender_email=None,
                message_content=reply
            )
        )
        communication.reply_content = reply
        communication.replied_at = datetime.now(timezone.utc)
        if 'is_resolved' in data:
            communication.is_resolved = bool(data.get('is_resolved'))
        communication.is_read = True
        
        db.session.commit()

        send_email_requested = bool(data.get('send_email'))
        email_sent = False
        email_error = ''
        if send_email_requested and communication.patient_email:
            email_subject = 'Response from Makokha Medical Centre'
            email_body = (
                f'Dear {communication.patient_name},\n\n'
                'Our customer care team has replied to your message:\n\n'
                f'{reply}\n\n'
                'You can continue the conversation from the customer care desk on the website.\n\n'
                'Regards,\n'
                'Makokha Medical Centre'
            )
            email_sent, email_error = _send_transactional_email(
                communication.patient_email,
                email_subject,
                email_body
            )
        
        return jsonify({
            'success': True,
            'message': 'Reply sent successfully!',
            'replied_at': communication.replied_at.isoformat(),
            'email_sent': email_sent,
            'email_error': email_error
        }), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 400


@app.route('/api/book-appointment', methods=['POST'])
def book_appointment():
    """API endpoint to book appointment (public)"""
    try:
        data = request.get_json(silent=True) or {}
        patient_name = (data.get('name') or '').strip()
        patient_email = (data.get('email') or '').strip()
        patient_phone = (data.get('phone') or '').strip()
        reason = (data.get('reason') or '').strip()
        doctor_id_raw = data.get('doctor_id')
        doctor_id = None
        if doctor_id_raw not in [None, '']:
            try:
                doctor_id = int(doctor_id_raw)
            except (TypeError, ValueError):
                doctor_id = None

        if not patient_name:
            return jsonify({'success': False, 'message': 'Name is required.'}), 400
        if not patient_email:
            return jsonify({'success': False, 'message': 'Email is required.'}), 400
        if not patient_phone:
            return jsonify({'success': False, 'message': 'Phone is required.'}), 400

        appointment_raw = (data.get('appointment_date') or '').strip()
        if not appointment_raw:
            return jsonify({'success': False, 'message': 'Appointment date is required.'}), 400
        try:
            appointment_date = datetime.strptime(appointment_raw, '%Y-%m-%dT%H:%M')
        except ValueError:
            return jsonify({'success': False, 'message': 'Invalid appointment date format.'}), 400

        appointment = Appointment(
            patient_name=patient_name,
            patient_email=patient_email,
            patient_phone=patient_phone,
            doctor_id=doctor_id,
            appointment_date=appointment_date,
            reason=reason
        )
        
        # Assign to first available reception
        available_reception = Reception.query.filter_by(is_available=True).first()
        if available_reception:
            appointment.reception_id = available_reception.id
        
        db.session.add(appointment)
        db.session.flush()
        
        # Create notification
        if appointment.reception_id:
            notification = Notification(
                reception_id=appointment.reception_id,
                appointment_id=appointment.id,
                notification_type='appointment',
                title=f'New appointment booking from {patient_name}',
                message=f'Appointment: {appointment_date.strftime("%B %d, %Y at %I:%M %p")}'
            )
            db.session.add(notification)
        
        db.session.commit()

        assigned_doctor = Doctor.query.get(appointment.doctor_id) if appointment.doctor_id else None
        doctor_name = assigned_doctor.full_name() if assigned_doctor else 'To be assigned'

        confirmation_email_sent = False
        confirmation_email_error = ''
        if appointment.patient_email:
            confirmation_email_sent, confirmation_email_error = _send_appointment_confirmation_email(
                appointment,
                doctor_name=doctor_name
            )

        staff_email_sent, staff_email_result = _send_staff_new_appointment_alert(
            appointment,
            doctor_name=doctor_name
        )
        
        return jsonify({
            'success': True,
            'message': 'Appointment booked successfully!',
            'appointment_id': appointment.id,
            'appointment_reference': _appointment_reference(appointment.id),
            'confirmation_email_sent': confirmation_email_sent,
            'confirmation_email_error': confirmation_email_error,
            'staff_email_sent': staff_email_sent,
            'staff_email_result': staff_email_result
        }), 201
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 400


@app.route('/api/get-notifications', methods=['GET'])
@reception_required
def get_notifications():
    """Get unread notifications for reception"""
    reception_id = session.get('user_id')
    
    notifications = Notification.query.filter_by(
        reception_id=reception_id,
        is_read=False
    ).order_by(desc(Notification.created_at)).all()
    
    return jsonify({
        'count': len(notifications),
        'notifications': [{
            'id': n.id,
            'type': n.notification_type,
            'title': n.title,
            'message': n.message,
            'created_at': n.created_at.isoformat()
        } for n in notifications]
    }), 200


@app.route('/api/mark-notification-read/<int:notification_id>', methods=['POST'])
@reception_required
def mark_notification_read(notification_id):
    """Mark notification as read"""
    try:
        notification = Notification.query.get_or_404(notification_id)
        
        # Verify ownership
        if notification.reception_id != session.get('user_id'):
            return jsonify({'success': False, 'message': 'Unauthorized'}), 403
        
        notification.is_read = True
        db.session.commit()
        
        return jsonify({'success': True}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 400


@app.route('/reception/call-logs')
@reception_required
def reception_call_logs():
    """Reception view for call history and operational call status filtering."""
    reception_id = session.get('user_id')
    page = request.args.get('page', 1, type=int)
    status_filter = (request.args.get('status') or 'all').strip().lower()
    call_type_filter = (request.args.get('call_type') or 'all').strip().lower()

    query = Call.query.filter(Call.reception_user_id == reception_id)

    if status_filter and status_filter != 'all':
        query = query.filter(Call.status == status_filter)
    if call_type_filter and call_type_filter != 'all':
        query = query.filter(Call.call_type == call_type_filter)

    calls = query.order_by(desc(Call.created_at)).paginate(page=page, per_page=20)
    return render_template(
        'reception/call_logs.html',
        calls=calls,
        status_filter=status_filter,
        call_type_filter=call_type_filter
    )


@app.route('/reception/appointments')
@reception_required
def reception_appointments():
    """Reception appointment management page (assign doctor, confirm, complete, cancel)."""
    reception_id = session.get('user_id')
    page = request.args.get('page', 1, type=int)
    status_filter = (request.args.get('status') or 'all').strip().lower()

    query = Appointment.query.filter(
        or_(Appointment.reception_id == reception_id, Appointment.reception_id.is_(None))
    )
    if status_filter and status_filter != 'all':
        query = query.filter(Appointment.status == status_filter)

    appointments = query.order_by(
        Appointment.appointment_date.asc(),
        desc(Appointment.created_at)
    ).paginate(page=page, per_page=20)
    doctors = Doctor.query.filter_by(is_active=True).order_by(Doctor.first_name.asc(), Doctor.last_name.asc()).all()

    return render_template(
        'reception/appointments.html',
        appointments=appointments,
        doctors=doctors,
        status_filter=status_filter
    )


@app.route('/reception/appointment/<int:appointment_id>/update', methods=['POST'])
@reception_required
def reception_update_appointment(appointment_id):
    """Reception action to confirm/schedule appointments and optionally email patient."""
    reception_id = session.get('user_id')
    appointment = Appointment.query.get_or_404(appointment_id)

    if appointment.reception_id not in [None, reception_id]:
        flash('You are not allowed to manage this appointment.', 'error')
        return redirect(url_for('reception_appointments'))

    try:
        appointment.reception_id = reception_id
        previous_status = (appointment.status or '').strip().lower()

        requested_status = (request.form.get('status') or '').strip().lower()
        valid_statuses = {'pending', 'confirmed', 'completed', 'cancelled'}
        if requested_status in valid_statuses:
            appointment.status = requested_status

        doctor_id = request.form.get('doctor_id', type=int)
        if doctor_id:
            doctor = Doctor.query.get(doctor_id)
            if doctor:
                appointment.doctor_id = doctor.id
        else:
            appointment.doctor_id = None

        datetime_raw = (request.form.get('appointment_date') or '').strip()
        if datetime_raw:
            try:
                appointment.appointment_date = datetime.strptime(datetime_raw, '%Y-%m-%dT%H:%M')
            except ValueError:
                flash('Invalid appointment date format. Keep using the date-time picker.', 'error')
                return redirect(url_for('reception_appointments'))

        appointment.notes = (request.form.get('notes') or '').strip()
        db.session.commit()

        send_email_requested = request.form.get('send_email') == 'on'
        send_telemedicine_requested = request.form.get('send_telemedicine_link') == 'on'
        auto_confirmation_email = (
            (appointment.status or '').strip().lower() == 'confirmed'
            and previous_status != 'confirmed'
        )
        send_status_email = send_email_requested or auto_confirmation_email
        assigned_doctor = Doctor.query.get(appointment.doctor_id) if appointment.doctor_id else None
        doctor_name = assigned_doctor.full_name() if assigned_doctor else 'To be assigned'

        success_messages = []
        warning_messages = []

        if send_status_email:
            if not appointment.patient_email:
                warning_messages.append('Patient email is missing; status email was not sent.')
            elif auto_confirmation_email:
                email_sent, email_error = _send_appointment_confirmation_email(
                    appointment,
                    doctor_name=doctor_name
                )
                if email_sent:
                    success_messages.append('Confirmation email sent automatically to patient.')
                else:
                    warning_messages.append(f'Confirmation email not sent: {email_error}')
            else:
                email_subject = f'Appointment {appointment.status.title()} - Makokha Medical Centre'
                email_body = (
                    f'Dear {appointment.patient_name},\n\n'
                    f'Your appointment status is now: {appointment.status.title()}.\n'
                    f'Scheduled date/time: {_format_appointment_datetime(appointment.appointment_date)}.\n'
                    f'Assigned doctor: {doctor_name}.\n'
                    f'Notes: {appointment.notes or "N/A"}\n\n'
                    'Thank you,\n'
                    'Makokha Medical Centre Reception'
                )
                email_sent, email_error = _send_transactional_email(
                    appointment.patient_email,
                    email_subject,
                    email_body
                )
                if email_sent:
                    success_messages.append('Status email sent to patient.')
                else:
                    warning_messages.append(f'Status email not sent: {email_error}')

        if send_telemedicine_requested:
            if not appointment.patient_email:
                warning_messages.append('Patient email is missing; telemedicine link was not sent.')
            else:
                telemedicine_sent, _telemedicine_url, telemedicine_result = _send_telemedicine_link_email(
                    appointment,
                    created_by_user_type='reception',
                    created_by_user_id=reception_id
                )
                if telemedicine_sent:
                    success_messages.append('Telemedicine link sent to patient.')
                else:
                    warning_messages.append(f'Telemedicine link not sent: {telemedicine_result}')

        if not send_status_email and not send_telemedicine_requested:
            flash('Appointment updated successfully.', 'success')
        else:
            if success_messages:
                flash('Appointment updated. ' + ' '.join(success_messages), 'success')
            if warning_messages:
                flash('Appointment updated. ' + ' '.join(warning_messages), 'warning')
            if not success_messages and not warning_messages:
                flash('Appointment updated successfully.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error updating appointment: {str(e)}', 'error')

    return redirect(url_for('reception_appointments', status=request.args.get('status', 'all')))


@app.route('/admin/reception')
@admin_required
def admin_reception():
    """Admin - Manage reception staff"""
    receptionists = Reception.query.all()
    return render_template('admin/reception.html', receptionists=receptionists)


@app.route('/admin/reception/add', methods=['GET', 'POST'])
@admin_required
def admin_add_reception():
    """Admin - Add reception staff"""
    if request.method == 'POST':
        try:
            username = request.form.get('username')
            phone_raw = (request.form.get('phone') or '').strip()
            normalized_phone = _normalize_phone_number(phone_raw)
            
            # Check if username exists
            if Admin.query.filter_by(username=username).first():
                flash('Username already exists', 'error')
                return redirect(url_for('admin_add_reception'))
                
            if Reception.query.filter_by(username=username).first():
                flash('Username already exists', 'error')
                return redirect(url_for('admin_add_reception'))
            
            reception = Reception(
                username=username,
                email=request.form.get('email'),
                full_name=request.form.get('full_name'),
                phone=normalized_phone or phone_raw,
                department=request.form.get('department'),
                shift=request.form.get('shift')
            )
            
            reception.set_password(request.form.get('password'))
            
            db.session.add(reception)
            db.session.commit()
            
            flash('Reception staff added successfully!', 'success')
            return redirect(url_for('admin_reception'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error adding reception staff: {str(e)}', 'error')
    
    return render_template('admin/add_reception.html')


@app.route('/admin/reception/edit/<int:reception_id>', methods=['GET', 'POST'])
@admin_required
def admin_edit_reception(reception_id):
    """Admin - Edit reception staff"""
    reception = Reception.query.get_or_404(reception_id)
    
    if request.method == 'POST':
        try:
            phone_raw = (request.form.get('phone') or '').strip()
            normalized_phone = _normalize_phone_number(phone_raw)

            reception.full_name = request.form.get('full_name')
            reception.email = request.form.get('email')
            reception.phone = normalized_phone or phone_raw
            reception.department = request.form.get('department')
            reception.shift = request.form.get('shift')
            reception.is_available = request.form.get('is_available') == 'on'
            reception.is_active = request.form.get('is_active') == 'on'
            
            # Update password if provided
            password = request.form.get('password')
            if password:
                reception.set_password(password)
            
            db.session.commit()
            
            flash('Reception staff updated successfully!', 'success')
            return redirect(url_for('admin_reception'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error updating reception staff: {str(e)}', 'error')
    
    return render_template('admin/edit_reception.html', reception=reception)


@app.route('/admin/reception/delete/<int:reception_id>', methods=['POST'])
@admin_required
def admin_delete_reception(reception_id):
    """Admin - Delete reception staff"""
    try:
        reception = Reception.query.get_or_404(reception_id)
        db.session.delete(reception)
        db.session.commit()
        flash('Reception staff deleted successfully!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting reception staff: {str(e)}', 'error')
    
    return redirect(url_for('admin_reception'))


@app.route('/admin/communications')
@admin_required
def admin_communications():
    """Admin - View all communications"""
    page = request.args.get('page', 1, type=int)
    
    communications = Communication.query.order_by(
        desc(Communication.created_at)
    ).paginate(page=page, per_page=20)
    
    return render_template('admin/communications.html', communications=communications)


@app.route('/admin/appointments')
@admin_required
def admin_appointments():
    """Admin - View all appointments"""
    page = request.args.get('page', 1, type=int)
    
    appointments = Appointment.query.order_by(
        desc(Appointment.created_at)
    ).paginate(page=page, per_page=20)
    
    return render_template('admin/appointments.html', appointments=appointments)


# ==================== CALL / VOIP ROUTES ====================

RECEPTION_SOCKET_SIDS = {}
CALL_SOCKET_PARTICIPANTS = {}
CALL_RINGING_STALE_MINUTES = 6
CALL_HOLD_STALE_MINUTES = 20


def _normalize_phone_number(raw_phone):
    """Normalize phone for display/storage without enforcing E.164."""
    if raw_phone is None:
        return ''
    return re.sub(r'\s+', ' ', str(raw_phone).strip())[:32]


def _coerce_utc(dt_value):
    """Normalize naive/aware datetimes to UTC-aware values."""
    if not dt_value:
        return None
    if dt_value.tzinfo is None:
        return dt_value.replace(tzinfo=timezone.utc)
    return dt_value.astimezone(timezone.utc)


def _calculate_call_duration_seconds(answered_at, ended_at=None):
    """Safely compute call duration across mixed naive/aware datetime values."""
    start_utc = _coerce_utc(answered_at)
    end_utc = _coerce_utc(ended_at) or datetime.now(timezone.utc)
    if not start_utc or not end_utc:
        return 0
    return max(0, int((end_utc - start_utc).total_seconds()))


def _webrtc_configured():
    """TURN must be configured for real-time WebRTC calls."""
    return bool(TURN_SERVER_URLS and TURN_USERNAME and TURN_CREDENTIAL)


def _webrtc_configuration_error():
    return (
        'TURN server is not configured. Set TURN_SERVER_URLS (or TURN_SERVER_URL), '
        'TURN_USERNAME and TURN_CREDENTIAL in .env.'
    )


def _build_ice_servers():
    """Build ICE server list (TURN + optional STUN)."""
    ice_servers = []
    if STUN_SERVER_URLS:
        ice_servers.append({
            'urls': STUN_SERVER_URLS if len(STUN_SERVER_URLS) > 1 else STUN_SERVER_URLS[0]
        })
    if TURN_SERVER_URLS:
        ice_servers.append({
            'urls': TURN_SERVER_URLS if len(TURN_SERVER_URLS) > 1 else TURN_SERVER_URLS[0],
            'username': TURN_USERNAME,
            'credential': TURN_CREDENTIAL
        })
    return ice_servers


def _busy_voice_prompt():
    return 'The other user is on another call. Please hold, try again later, or send a message.'


def _get_call_room(call):
    """Create/read deterministic WebRTC signaling room for a call."""
    if not call.conference_name:
        call.conference_name = f"rtc-call-{call.call_id}"
    return call.conference_name


def _register_reception_socket(reception_id, sid):
    RECEPTION_SOCKET_SIDS.setdefault(reception_id, set()).add(sid)


def _unregister_reception_socket(reception_id, sid):
    if reception_id not in RECEPTION_SOCKET_SIDS:
        return
    RECEPTION_SOCKET_SIDS[reception_id].discard(sid)
    if not RECEPTION_SOCKET_SIDS[reception_id]:
        RECEPTION_SOCKET_SIDS.pop(reception_id, None)


def _is_reception_online(reception_id):
    return bool(RECEPTION_SOCKET_SIDS.get(reception_id))


def _emit_to_reception(reception_id, event, payload):
    for sid in RECEPTION_SOCKET_SIDS.get(reception_id, set()):
        socketio.emit(event, payload, room=sid)


def _ensure_call_registry(call_id):
    return CALL_SOCKET_PARTICIPANTS.setdefault(call_id, {'patient': set(), 'reception': set()})


def _cleanup_call_registry_sid(sid):
    stale_calls = []
    for call_id, participants in CALL_SOCKET_PARTICIPANTS.items():
        participants['patient'].discard(sid)
        participants['reception'].discard(sid)
        if not participants['patient'] and not participants['reception']:
            stale_calls.append(call_id)
    for call_id in stale_calls:
        CALL_SOCKET_PARTICIPANTS.pop(call_id, None)

def _is_reception_call_capable(receptionist):
    """Limit live call routing to call-capable departments."""
    department = (receptionist.department or '').strip().lower()
    return department in {'', 'general', 'calls', 'customer_care'}


def _is_call_record_active_for_capacity(call_record, now_utc=None):
    """
    Determine whether a call should still block receptionist capacity.
    Uses staleness windows to avoid zombie 'busy' states from abandoned calls.
    """
    now_utc = now_utc or datetime.now(timezone.utc)
    status = (call_record.status or '').strip().lower()

    if status == 'connected':
        if call_record.ended_at:
            return False

        answered_at = _coerce_utc(call_record.answered_at) or _coerce_utc(call_record.created_at)
        participants = CALL_SOCKET_PARTICIPANTS.get(call_record.call_id, {})
        has_live_participants = bool(participants.get('patient') or participants.get('reception'))

        # Guardrail: treat orphaned "connected" rows as stale so they do not block routing forever.
        if answered_at and (now_utc - answered_at) > timedelta(hours=2):
            return False
        if answered_at and not has_live_participants and (now_utc - answered_at) > timedelta(minutes=2):
            return False
        return True

    if status in {'initiated', 'dialing', 'ringing', 'busy'}:
        created_at = _coerce_utc(call_record.created_at)
        if not created_at:
            return False
        return (now_utc - created_at) <= timedelta(minutes=CALL_RINGING_STALE_MINUTES)

    if status == 'on_hold':
        hold_anchor = _coerce_utc(call_record.hold_requested_at) or _coerce_utc(call_record.created_at)
        if not hold_anchor:
            return False
        return (now_utc - hold_anchor) <= timedelta(minutes=CALL_HOLD_STALE_MINUTES)

    return False


def _has_active_customer_care_call(reception_id, exclude_call_id=None):
    """Return True if a customer care user currently has an active call."""
    candidate_statuses = ['initiated', 'dialing', 'ringing', 'busy', 'connected', 'on_hold']
    query = Call.query.filter(
        Call.reception_user_id == reception_id,
        Call.call_type.in_(['customer_care', 'emergency']),
        Call.status.in_(candidate_statuses)
    )
    if exclude_call_id:
        query = query.filter(Call.call_id != exclude_call_id)

    now_utc = datetime.now(timezone.utc)
    for record in query.all():
        if _is_call_record_active_for_capacity(record, now_utc=now_utc):
            return True
    return False


def _build_reception_presence_snapshot(receptionist, exclude_call_id=None):
    """Build live availability metadata for routing and public status cards."""
    is_online = _is_reception_online(receptionist.id)
    is_busy = _has_active_customer_care_call(receptionist.id, exclude_call_id=exclude_call_id)
    is_schedulable = bool(receptionist.is_available)
    can_receive = bool(is_online and is_schedulable and not is_busy)

    if can_receive:
        availability = 'available'
    elif is_busy:
        availability = 'busy'
    elif not is_online:
        availability = 'offline'
    else:
        availability = 'away'

    return {
        'is_online': is_online,
        'is_busy': is_busy,
        'is_schedulable': is_schedulable,
        'can_receive_calls': can_receive,
        'availability': availability
    }


def _select_customer_care_for_call(exclude_call_id=None, preferred_reception_id=None):
    """
    Select customer care user for a call.
    Returns: (reception_obj_or_none, is_busy, reason_code)
    reason_code: available, rerouted, busy, no_online, no_reception
    """
    active_reception = Reception.query.filter_by(is_active=True).order_by(Reception.id.asc()).all()
    candidates = [reception for reception in active_reception if _is_reception_call_capable(reception)]
    if not candidates:
        return None, True, 'no_reception'

    online_candidates = [reception for reception in candidates if _is_reception_online(reception.id)]
    if not online_candidates:
        return None, True, 'no_online'

    preferred = None
    if preferred_reception_id is not None:
        preferred = next((r for r in candidates if r.id == preferred_reception_id), None)

    def _first_available(candidate_pool):
        for receptionist in candidate_pool:
            if not _has_active_customer_care_call(receptionist.id, exclude_call_id=exclude_call_id) and receptionist.is_available:
                return receptionist
        for receptionist in candidate_pool:
            if not _has_active_customer_care_call(receptionist.id, exclude_call_id=exclude_call_id):
                return receptionist
        return None

    if preferred:
        if preferred in online_candidates and not _has_active_customer_care_call(preferred.id, exclude_call_id=exclude_call_id):
            return preferred, False, 'available'

        reroute_pool = [r for r in online_candidates if r.id != preferred.id]
        rerouted = _first_available(reroute_pool)
        if rerouted:
            return rerouted, False, 'rerouted'

        if preferred in online_candidates:
            return preferred, True, 'busy'

        if online_candidates:
            return online_candidates[0], True, 'busy'
        return None, True, 'no_online'

    selected = _first_available(online_candidates)
    if selected:
        return selected, False, 'available'

    return online_candidates[0], True, 'busy'


def _notify_reception_of_call(call):
    if not call.reception_user_id:
        return

    room_name = _get_call_room(call)
    payload = {
        'call_id': call.call_id,
        'call_type': call.call_type,
        'status': call.status,
        'patient_name': call.patient_name,
        'patient_phone': call.patient_phone,
        'room_name': room_name,
        'ice_servers': _build_ice_servers(),
        'created_at': call.created_at.isoformat() if call.created_at else None
    }
    _emit_to_reception(call.reception_user_id, 'incoming_call', payload)


def _emit_call_event(call, event_name, message=None, extra_payload=None):
    payload = {
        'call_id': call.call_id,
        'call_type': call.call_type,
        'status': call.status,
        'message': message or '',
        'room_name': _get_call_room(call)
    }
    if extra_payload:
        payload.update(extra_payload)
    socketio.emit(event_name, payload, room=payload['room_name'])


@socketio.on('connect')
def socket_connect():
    """Register socket connections for reception and website callers."""
    user_type = session.get('user_type')
    user_id = session.get('user_id')
    if user_type == 'reception' and user_id:
        _register_reception_socket(user_id, request.sid)
        emit('socket_ready', {'role': 'reception', 'user_id': user_id})
        return
    emit('socket_ready', {'role': 'patient'})


@socketio.on('disconnect')
def socket_disconnect():
    """Remove disconnected sockets from runtime registries."""
    user_type = session.get('user_type')
    user_id = session.get('user_id')
    if user_type == 'reception' and user_id:
        _unregister_reception_socket(user_id, request.sid)
    _cleanup_call_registry_sid(request.sid)


@socketio.on('join_call_room')
def join_call_room_event(data):
    """Join patient/reception to a call signaling room."""
    payload = data or {}
    call_id = (payload.get('call_id') or '').strip()
    role = (payload.get('role') or 'patient').strip().lower()

    if not call_id:
        emit('call_error', {'message': 'call_id is required'})
        return
    if role not in ['patient', 'reception']:
        emit('call_error', {'message': 'Invalid role'})
        return

    call = Call.query.filter_by(call_id=call_id).first()
    if not call:
        emit('call_error', {'message': 'Call not found'})
        return
    if call.status in ['ended', 'rejected', 'message_left', 'failed']:
        emit('call_error', {'message': 'This call is no longer active'})
        return

    if role == 'reception':
        if session.get('user_type') != 'reception':
            emit('call_error', {'message': 'Reception authentication required'})
            return
        if call.reception_user_id and call.reception_user_id != session.get('user_id'):
            emit('call_error', {'message': 'Unauthorized for this call'})
            return

    room_name = _get_call_room(call)
    join_room(room_name)
    registry = _ensure_call_registry(call_id)
    registry[role].add(request.sid)

    emit('call_room_joined', {
        'call_id': call.call_id,
        'call_type': call.call_type,
        'role': role,
        'status': call.status,
        'room_name': room_name,
        'ice_servers': _build_ice_servers()
    })

    emit('call_participant_joined', {
        'call_id': call.call_id,
        'role': role
    }, room=room_name, include_self=False)


@socketio.on('leave_call_room')
def leave_call_room_event(data):
    payload = data or {}
    call_id = (payload.get('call_id') or '').strip()
    role = (payload.get('role') or 'patient').strip().lower()
    if not call_id:
        return

    call = Call.query.filter_by(call_id=call_id).first()
    if call:
        leave_room(_get_call_room(call))

    registry = _ensure_call_registry(call_id)
    if role in ['patient', 'reception']:
        registry[role].discard(request.sid)


@socketio.on('webrtc_offer')
def webrtc_offer_event(data):
    payload = data or {}
    call_id = (payload.get('call_id') or '').strip()
    offer = payload.get('offer')
    if not call_id or not offer:
        return
    call = Call.query.filter_by(call_id=call_id).first()
    if not call:
        return
    emit('webrtc_offer', {'call_id': call_id, 'offer': offer}, room=_get_call_room(call), include_self=False)


@socketio.on('webrtc_answer')
def webrtc_answer_event(data):
    payload = data or {}
    call_id = (payload.get('call_id') or '').strip()
    answer = payload.get('answer')
    if not call_id or not answer:
        return
    call = Call.query.filter_by(call_id=call_id).first()
    if not call:
        return
    emit('webrtc_answer', {'call_id': call_id, 'answer': answer}, room=_get_call_room(call), include_self=False)


@socketio.on('webrtc_ice_candidate')
def webrtc_ice_candidate_event(data):
    payload = data or {}
    call_id = (payload.get('call_id') or '').strip()
    candidate = payload.get('candidate')
    if not call_id or not candidate:
        return
    call = Call.query.filter_by(call_id=call_id).first()
    if not call:
        return
    emit('webrtc_ice_candidate', {'call_id': call_id, 'candidate': candidate}, room=_get_call_room(call), include_self=False)


@app.route('/api/webrtc-config', methods=['GET'])
def get_webrtc_config():
    """Expose ICE server config for browser WebRTC."""
    if not _webrtc_configured():
        return jsonify({'success': False, 'message': _webrtc_configuration_error()}), 503
    return jsonify({'success': True, 'ice_servers': _build_ice_servers()}), 200


@app.route('/api/receptionists-availability', methods=['GET'])
def receptionists_availability():
    """Expose live receptionist call availability for caller-side routing."""
    try:
        receptionists = Reception.query.filter_by(is_active=True).order_by(Reception.full_name.asc()).all()
        payload = []

        for receptionist in receptionists:
            if not _is_reception_call_capable(receptionist):
                continue

            snapshot = _build_reception_presence_snapshot(receptionist)
            payload.append({
                'id': receptionist.id,
                'full_name': receptionist.full_name,
                'department': receptionist.department or 'general',
                'shift': receptionist.shift or 'general',
                'is_online': snapshot['is_online'],
                'is_busy': snapshot['is_busy'],
                'is_schedulable': snapshot['is_schedulable'],
                'can_receive_calls': snapshot['can_receive_calls'],
                'availability': snapshot['availability']
            })

        summary = {
            'total': len(payload),
            'available': sum(1 for item in payload if item['availability'] == 'available'),
            'busy': sum(1 for item in payload if item['availability'] == 'busy'),
            'offline': sum(1 for item in payload if item['availability'] == 'offline'),
            'away': sum(1 for item in payload if item['availability'] == 'away')
        }

        return jsonify({
            'success': True,
            'receptionists': payload,
            'summary': summary,
            'generated_at': datetime.now(timezone.utc).isoformat()
        }), 200
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 400


@app.route('/api/initiate-call', methods=['POST'])
def initiate_call():
    """Initiate customer-care call using TURN + Socket.IO signaling."""
    if not _webrtc_configured():
        return jsonify({'success': False, 'message': _webrtc_configuration_error()}), 503

    try:
        data = request.get_json(silent=True) or {}
        patient_name = (data.get('patient_name') or '').strip() or 'Website Caller'
        patient_phone = _normalize_phone_number(data.get('patient_phone'))
        preferred_reception_id_raw = data.get('preferred_reception_id')
        preferred_reception_id = None
        if preferred_reception_id_raw not in [None, '']:
            try:
                preferred_reception_id = int(preferred_reception_id_raw)
            except (TypeError, ValueError):
                preferred_reception_id = None

        assigned_reception, is_busy, selection_reason = _select_customer_care_for_call(
            preferred_reception_id=preferred_reception_id
        )
        if not assigned_reception:
            if selection_reason == 'no_online':
                return jsonify({
                    'success': False,
                    'message': 'No online customer care receptionist is available right now. Please try again later or send a message.'
                }), 503
            return jsonify({
                'success': False,
                'message': 'No active customer care user is available.'
            }), 503

        call_id = str(uuid4())[:8]
        call = Call(
            call_id=call_id,
            patient_name=patient_name,
            patient_phone=patient_phone,
            call_type='customer_care',
            status='busy' if is_busy else 'ringing',
            reception_user_id=assigned_reception.id
        )
        _get_call_room(call)
        db.session.add(call)
        db.session.flush()

        if call.reception_user_id and not is_busy:
            db.session.add(Notification(
                reception_id=call.reception_user_id,
                notification_type='call',
                title=f'Incoming Call from {call.patient_name}',
                message=f'{call.patient_name} is waiting for customer care.'
            ))

        db.session.commit()

        if not is_busy:
            _notify_reception_of_call(call)
            routed_message = 'Real-time call initiated. Waiting for customer care to answer.'
            if selection_reason == 'rerouted':
                routed_message = (
                    f'Preferred receptionist is unavailable. '
                    f'Your call has been routed to {assigned_reception.full_name}.'
                )
            return jsonify({
                'success': True,
                'message': routed_message,
                'call_id': call.call_id,
                'status': call.status,
                'room_name': call.conference_name,
                'ice_servers': _build_ice_servers(),
                'assigned_reception': {
                    'id': assigned_reception.id,
                    'full_name': assigned_reception.full_name
                }
            }), 200

        busy_message = 'The other user is on another call.'
        if assigned_reception:
            busy_message = (
                f'{assigned_reception.full_name} is currently on another call. '
                'You can hold, try again later, or send a message.'
            )
        return jsonify({
            'success': True,
            'message': busy_message,
            'voice_prompt': _busy_voice_prompt(),
            'call_id': call.call_id,
            'status': 'busy',
            'room_name': call.conference_name,
            'ice_servers': _build_ice_servers(),
            'next_actions': ['hold', 'send_message', 'end_call'],
            'assigned_reception': {
                'id': assigned_reception.id,
                'full_name': assigned_reception.full_name
            } if assigned_reception else None
        }), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 400


@app.route('/api/initiate-emergency-call', methods=['POST'])
def initiate_emergency_call():
    """System-generated emergency call over WebRTC (no phone required)."""
    if not _webrtc_configured():
        return jsonify({'success': False, 'message': _webrtc_configuration_error()}), 503

    try:
        data = request.get_json(silent=True) or {}
        patient_name = (data.get('patient_name') or '').strip() or 'Emergency Alert'
        preferred_reception_id_raw = data.get('preferred_reception_id')
        preferred_reception_id = None
        if preferred_reception_id_raw not in [None, '']:
            try:
                preferred_reception_id = int(preferred_reception_id_raw)
            except (TypeError, ValueError):
                preferred_reception_id = None

        assigned_reception, is_busy, selection_reason = _select_customer_care_for_call(
            preferred_reception_id=preferred_reception_id
        )
        if not assigned_reception:
            if selection_reason == 'no_online':
                return jsonify({
                    'success': False,
                    'message': 'No online emergency receptionist is available right now. Please try again in a moment.'
                }), 503
            return jsonify({
                'success': False,
                'message': 'No active customer care user is available for emergency call.'
            }), 503

        call_id = str(uuid4())[:8]
        call = Call(
            call_id=call_id,
            patient_name=patient_name,
            patient_phone='SYSTEM',
            call_type='emergency',
            status='busy' if is_busy else 'ringing',
            reception_user_id=assigned_reception.id
        )
        _get_call_room(call)
        db.session.add(call)
        db.session.flush()

        if call.reception_user_id and not is_busy:
            db.session.add(Notification(
                reception_id=call.reception_user_id,
                notification_type='call',
                title='Emergency System Call',
                message=f'Emergency call requested by {call.patient_name}.'
            ))

        db.session.commit()

        if not is_busy:
            _notify_reception_of_call(call)
            routed_message = 'Emergency system call initiated.'
            if selection_reason == 'rerouted':
                routed_message = (
                    f'Preferred receptionist is unavailable. '
                    f'Emergency call routed to {assigned_reception.full_name}.'
                )
            return jsonify({
                'success': True,
                'message': routed_message,
                'call_id': call.call_id,
                'status': call.status,
                'room_name': call.conference_name,
                'ice_servers': _build_ice_servers(),
                'assigned_reception': {
                    'id': assigned_reception.id,
                    'full_name': assigned_reception.full_name
                }
            }), 200

        busy_message = 'Customer care is currently on another call.'
        if assigned_reception:
            busy_message = (
                f'{assigned_reception.full_name} is currently on another call. '
                'Please hold, try again later, or send a message.'
            )
        return jsonify({
            'success': True,
            'message': busy_message,
            'voice_prompt': _busy_voice_prompt(),
            'call_id': call.call_id,
            'status': 'busy',
            'room_name': call.conference_name,
            'ice_servers': _build_ice_servers(),
            'next_actions': ['hold', 'send_message', 'end_call'],
            'assigned_reception': {
                'id': assigned_reception.id,
                'full_name': assigned_reception.full_name
            } if assigned_reception else None
        }), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 400


@app.route('/api/answer-call', methods=['POST'])
@reception_required
def answer_call():
    """Reception answers a waiting call and triggers WebRTC connection."""
    try:
        data = request.get_json(silent=True) or {}
        call_id = data.get('call_id')
        if not call_id:
            return jsonify({'success': False, 'message': 'call_id is required'}), 400

        call = Call.query.filter_by(call_id=call_id).first_or_404()
        receptionist = Reception.query.get(session.get('user_id'))
        if not receptionist:
            return jsonify({'success': False, 'message': 'Reception account not found'}), 404

        if call.status in ['ended', 'rejected', 'message_left', 'failed']:
            return jsonify({'success': False, 'message': 'This call is no longer active'}), 400
        if call.reception_user_id and call.reception_user_id != receptionist.id:
            return jsonify({'success': False, 'message': 'Unauthorized'}), 403
        if _has_active_customer_care_call(receptionist.id, exclude_call_id=call.call_id):
            return jsonify({'success': False, 'message': 'You are already on another active call.'}), 409

        call.reception_user_id = receptionist.id
        call.status = 'connected'
        if not call.answered_at:
            call.answered_at = datetime.now(timezone.utc)
        room_name = _get_call_room(call)
        db.session.commit()

        _emit_call_event(
            call,
            'call_connected',
            'Customer care connected.',
            extra_payload={'ice_servers': _build_ice_servers()}
        )

        return jsonify({
            'success': True,
            'message': 'Call answered',
            'call_id': call.call_id,
            'status': call.status,
            'room_name': room_name,
            'ice_servers': _build_ice_servers()
        }), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 400


@app.route('/api/reject-call', methods=['POST'])
@reception_required
def reject_call():
    """API endpoint for reception to reject a call"""
    try:
        data = request.get_json(silent=True) or {}
        call_id = data.get('call_id')
        reason = data.get('reason', 'Not available')
        if not call_id:
            return jsonify({'success': False, 'message': 'call_id is required'}), 400
        
        call = Call.query.filter_by(call_id=call_id).first_or_404()
        
        # Verify this reception was assigned the call
        if call.reception_user_id and call.reception_user_id != session.get('user_id'):
            return jsonify({'success': False, 'message': 'Unauthorized'}), 403
        
        call.status = 'rejected'
        call.ended_at = datetime.now(timezone.utc)
        if call.answered_at and call.duration <= 0:
            call.duration = _calculate_call_duration_seconds(call.answered_at, call.ended_at)
        
        db.session.commit()
        _emit_call_event(call, 'call_rejected', reason)
        
        return jsonify({
            'success': True,
            'message': 'Call rejected',
            'call_id': call.call_id,
            'status': call.status,
            'reason': reason
        }), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 400


@app.route('/api/end-call', methods=['POST'])
def end_call():
    """API endpoint to end a call"""
    try:
        data = request.get_json(silent=True) or {}
        call_id = (
            data.get('call_id')
            or data.get('callId')
            or request.form.get('call_id')
            or request.args.get('call_id')
        )
        call_id = str(call_id).strip() if call_id is not None else ''
        if not call_id:
            return jsonify({'success': False, 'message': 'call_id is required'}), 400

        call = Call.query.filter_by(call_id=call_id).first()
        if not call:
            return jsonify({
                'success': True,
                'message': 'Call already closed.',
                'call_id': call_id,
                'status': 'ended',
                'duration': '0:00'
            }), 200

        if call.status in ['ended', 'rejected', 'message_left', 'failed']:
            return jsonify({
                'success': True,
                'message': 'Call already closed.',
                'call_id': call.call_id,
                'status': call.status,
                'duration': call.get_duration_formatted()
            }), 200

        call.status = 'ended'
        call.ended_at = datetime.now(timezone.utc)
        if call.answered_at:
            call.duration = _calculate_call_duration_seconds(call.answered_at, call.ended_at)
        
        db.session.commit()
        _emit_call_event(call, 'call_ended', 'Call ended.')
        
        return jsonify({
            'success': True,
            'message': 'Call ended successfully',
            'call_id': call.call_id,
            'status': call.status,
            'duration': call.get_duration_formatted()
        }), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 400


@app.route('/api/hold-call', methods=['POST'])
def hold_call():
    """Put a busy call on hold until customer care becomes free."""
    try:
        data = request.get_json(silent=True) or {}
        call_id = data.get('call_id')
        if not call_id:
            return jsonify({'success': False, 'message': 'call_id is required'}), 400

        call = Call.query.filter_by(call_id=call_id).first_or_404()
        if call.status in ['ended', 'rejected', 'message_left', 'failed']:
            return jsonify({'success': False, 'message': 'This call is no longer active'}), 400

        call.status = 'on_hold'
        call.hold_requested_at = datetime.now(timezone.utc)

        if call.reception_user_id:
            notification = Notification(
                reception_id=call.reception_user_id,
                notification_type='call',
                title=f'Caller on hold: {call.patient_name}',
                message=f'{call.patient_name} is waiting on hold.'
            )
            db.session.add(notification)

        db.session.commit()
        _emit_call_event(call, 'call_on_hold', 'Caller is on hold.')

        return jsonify({
            'success': True,
            'message': 'You are now on hold. Customer care will reconnect once free.',
            'call_id': call.call_id,
            'status': call.status
        }), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 400


@app.route('/api/send-call-message', methods=['POST'])
def send_call_message():
    """Allow caller to leave a message during a busy/held call."""
    try:
        _ensure_communication_schema()
        _ensure_communication_thread_table()
        data = request.get_json(silent=True) or {}
        call_id = data.get('call_id')
        message = (data.get('message') or '').strip()
        patient_email = (data.get('patient_email') or '').strip()

        if not call_id:
            return jsonify({'success': False, 'message': 'call_id is required'}), 400
        if not message:
            return jsonify({'success': False, 'message': 'Message is required'}), 400

        call = Call.query.filter_by(call_id=call_id).first_or_404()

        communication = Communication(
            patient_name=(data.get('patient_name') or call.patient_name or 'Website Caller'),
            patient_email=patient_email if patient_email else 'no-reply@makokhamedical.com',
            patient_phone=(data.get('patient_phone') or call.patient_phone or ''),
            reception_id=call.reception_user_id,
            message_type='call',
            message_content=f'Call {call.call_id} message: {message}',
            priority='high',
            public_token=uuid4().hex
        )
        db.session.add(communication)
        db.session.flush()

        db.session.add(
            CommunicationMessage(
                communication_id=communication.id,
                sender_type='patient',
                sender_name=communication.patient_name,
                sender_email=communication.patient_email,
                message_content=communication.message_content,
                created_at=communication.created_at or datetime.now(timezone.utc)
            )
        )

        if call.reception_user_id:
            notification = Notification(
                reception_id=call.reception_user_id,
                communication_id=communication.id,
                notification_type='message',
                title=f'Call message from {communication.patient_name}',
                message=message[:100] + '...' if len(message) > 100 else message
            )
            db.session.add(notification)

        call.status = 'message_left'
        call.ended_at = datetime.now(timezone.utc)
        if call.answered_at and call.duration <= 0:
            call.duration = _calculate_call_duration_seconds(call.answered_at, call.ended_at)

        db.session.commit()
        _emit_call_event(call, 'call_message_left', 'Caller left a message.')

        return jsonify({
            'success': True,
            'message': 'Message sent to customer care successfully.',
            'call_id': call.call_id,
            'status': call.status,
            'message_id': communication.id
        }), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 400


@app.route('/api/call-status/<string:call_id>', methods=['GET'])
def get_call_status(call_id):
    """Get latest call status for caller-side polling."""
    try:
        call = Call.query.filter_by(call_id=call_id).first_or_404()

        if call.status == 'on_hold':
            # Try reconnecting hold calls when a reception user is free and online.
            assigned_reception = Reception.query.get(call.reception_user_id) if call.reception_user_id else None
            can_reconnect = bool(
                assigned_reception
                and _is_reception_online(assigned_reception.id)
                and not _has_active_customer_care_call(assigned_reception.id, exclude_call_id=call.call_id)
            )

            if not can_reconnect:
                alternative, alt_busy, _selection_reason = _select_customer_care_for_call(
                    exclude_call_id=call.call_id
                )
                if alternative and not alt_busy:
                    call.reception_user_id = alternative.id
                    can_reconnect = True

            if can_reconnect:
                call.status = 'ringing'
                db.session.commit()
                _notify_reception_of_call(call)

        status_messages = {
            'busy': 'The other user is on another call. Please hold, try again later, or send a message.',
            'on_hold': 'You are on hold. We will reconnect you automatically once customer care is free.',
            'initiated': 'Preparing call setup.',
            'dialing': 'Initializing real-time call.',
            'ringing': 'Calling customer care...',
            'connected': 'Connected.',
            'ended': 'Call has ended.',
            'rejected': 'Call was rejected.',
            'failed': 'Call could not be completed. Please try again.',
            'message_left': 'Message sent. Customer care will get back to you.'
        }

        return jsonify({
            'success': True,
            'call_id': call.call_id,
            'call_type': call.call_type,
            'status': call.status,
            'message': status_messages.get(call.status, 'Call status updated.'),
            'voice_prompt': _busy_voice_prompt() if call.status == 'busy' else '',
            'duration': call.get_duration_formatted(),
            'room_name': _get_call_room(call),
            'ice_servers': _build_ice_servers(),
            'answered_at': call.answered_at.isoformat() if call.answered_at else None,
            'ended_at': call.ended_at.isoformat() if call.ended_at else None,
            'last_error': call.last_error
        }), 200
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 400


@app.route('/api/get-call-history', methods=['GET'])
def get_call_history():
    """API endpoint to get recent call history."""
    try:
        limit = request.args.get('limit', 10, type=int)
        limit = max(1, min(limit, 50))
        calls = Call.query.order_by(desc(Call.created_at)).limit(limit).all()
        
        call_list = [{
            'call_id': c.call_id,
            'patient_name': c.patient_name,
            'patient_phone': c.patient_phone,
            'call_type': c.call_type,
            'status': c.status,
            'created_at': c.created_at.isoformat() if c.created_at else None,
            'duration': c.get_duration_formatted(),
            'answered_at': c.answered_at.isoformat() if c.answered_at else None
        } for c in calls]
        
        return jsonify({
            'success': True,
            'calls': call_list
        }), 200
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 400


# ==================== ERROR HANDLERS ====================

@app.errorhandler(404)
def not_found_error(error):
    """Handle 404 errors"""
    return render_template('errors/404.html'), 404


@app.errorhandler(500)
def internal_error(error):
    """Handle 500 errors"""
    db.session.rollback()
    return render_template('errors/500.html'), 500


@app.errorhandler(CSRFError)
def handle_csrf_error(error):
    """Return JSON errors for API requests and flash+redirect for form pages."""
    message = (getattr(error, 'description', '') or 'CSRF validation failed.').strip()
    if (request.path or '').startswith('/api/'):
        return jsonify({'success': False, 'message': message}), 400
    flash('Security validation failed. Please refresh the page and try again.', 'error')
    return redirect(request.referrer or url_for('index'))


# ==================== INITIALIZATION ====================

def create_default_admin():
    """Create or sync the admin account from environment variables."""
    admin_username = (os.getenv('ADMIN_USERNAME') or '').strip()
    admin_password = (os.getenv('ADMIN_PASSWORD') or '').strip()
    admin_email = (os.getenv('ADMIN_EMAIL') or '').strip()
    admin_name = (os.getenv('ADMIN_NAME') or '').strip()

    missing = []
    if not admin_username:
        missing.append('ADMIN_USERNAME')
    if not admin_password:
        missing.append('ADMIN_PASSWORD')
    if not admin_email:
        missing.append('ADMIN_EMAIL')
    if not admin_name:
        missing.append('ADMIN_NAME')
    if missing:
        raise RuntimeError(
            f"Missing required admin env vars: {', '.join(missing)}. "
            "Set them in .env before starting the app."
        )

    admin = Admin.query.filter_by(username=admin_username).first()
    if not admin:
        admin = Admin.query.filter_by(email=admin_email).first()
    if not admin:
        admin = Admin(
            username=admin_username,
            email=admin_email,
            full_name=admin_name
        )
        admin.set_password(admin_password)
        db.session.add(admin)
        db.session.commit()
        print(f"Admin created from environment: '{admin_username}'")
        return

    changed = False
    if admin.username != admin_username:
        admin.username = admin_username
        changed = True
    if admin.email != admin_email:
        admin.email = admin_email
        changed = True
    if admin.full_name != admin_name:
        admin.full_name = admin_name
        changed = True
    if not admin.check_password(admin_password):
        admin.set_password(admin_password)
        changed = True

    if changed:
        db.session.commit()
        print(f"Admin credentials synchronized from environment for '{admin_username}'")


def ensure_runtime_schema():
    """Apply lightweight schema updates for call-related real-time fields."""
    inspector = inspect(db.engine)
    table_names = inspector.get_table_names()
    if 'call' not in table_names:
        return

    existing_columns = {column['name'] for column in inspector.get_columns('call')}
    required_columns = {
        'call_type': "VARCHAR(20) DEFAULT 'customer_care'",
        'twilio_patient_call_sid': 'VARCHAR(64)',
        'twilio_staff_call_sid': 'VARCHAR(64)',
        'patient_leg_status': 'VARCHAR(30)',
        'staff_leg_status': 'VARCHAR(30)',
        'conference_name': 'VARCHAR(120)',
        'hold_requested_at': 'TIMESTAMP',
        'last_error': 'TEXT'
    }

    schema_changed = False
    for column_name, definition in required_columns.items():
        if column_name not in existing_columns:
            db.session.execute(text(f"ALTER TABLE call ADD COLUMN {column_name} {definition}"))
            schema_changed = True

    if schema_changed:
        db.session.commit()

    db.session.execute(text("UPDATE call SET call_type = 'customer_care' WHERE call_type IS NULL OR call_type = ''"))
    db.session.commit()


def ensure_event_schema():
    """Apply lightweight schema updates for event-management fields."""
    inspector = inspect(db.engine)
    table_names = inspector.get_table_names()

    if 'event_photo' not in table_names:
        EventPhoto.__table__.create(db.engine, checkfirst=True)
        inspector = inspect(db.engine)
        table_names = inspector.get_table_names()

    if 'event' not in table_names:
        return

    existing_columns = {column['name'] for column in inspector.get_columns('event')}
    event_schema_changed = False
    if 'status' not in existing_columns:
        db.session.execute(text("ALTER TABLE event ADD COLUMN status VARCHAR(50) DEFAULT 'upcoming'"))
        event_schema_changed = True
    if 'image_focus_x' not in existing_columns:
        db.session.execute(text("ALTER TABLE event ADD COLUMN image_focus_x FLOAT DEFAULT 50.0"))
        event_schema_changed = True
    if 'image_focus_y' not in existing_columns:
        db.session.execute(text("ALTER TABLE event ADD COLUMN image_focus_y FLOAT DEFAULT 50.0"))
        event_schema_changed = True
    if event_schema_changed:
        db.session.commit()

    if 'event_photo' in table_names:
        event_photo_columns = {column['name'] for column in inspector.get_columns('event_photo')}
        event_photo_schema_changed = False
        if 'focus_x' not in event_photo_columns:
            db.session.execute(text("ALTER TABLE event_photo ADD COLUMN focus_x FLOAT DEFAULT 50.0"))
            event_photo_schema_changed = True
        if 'focus_y' not in event_photo_columns:
            db.session.execute(text("ALTER TABLE event_photo ADD COLUMN focus_y FLOAT DEFAULT 50.0"))
            event_photo_schema_changed = True
        if event_photo_schema_changed:
            db.session.commit()

        db.session.execute(text("UPDATE event_photo SET focus_x = 50.0 WHERE focus_x IS NULL"))
        db.session.execute(text("UPDATE event_photo SET focus_y = 50.0 WHERE focus_y IS NULL"))
        db.session.commit()

    db.session.execute(text("UPDATE event SET image_focus_x = 50.0 WHERE image_focus_x IS NULL"))
    db.session.execute(text("UPDATE event SET image_focus_y = 50.0 WHERE image_focus_y IS NULL"))
    db.session.commit()

    events_without_status = Event.query.filter(
        or_(Event.status.is_(None), Event.status == '')
    ).all()
    if not events_without_status:
        return

    for event in events_without_status:
        event.status = _normalize_event_status(None, event.event_date)
    db.session.commit()


def ensure_site_settings():
    """Ensure default editable site-content keys exist."""
    global SITE_SETTINGS_TABLE_READY
    if not SITE_SETTINGS_TABLE_READY:
        inspector = inspect(db.engine)
        if 'site_setting' not in inspector.get_table_names():
            SiteSetting.__table__.create(db.engine, checkfirst=True)
        SITE_SETTINGS_TABLE_READY = True

    existing_keys = {
        row.setting_key for row in SiteSetting.query.with_entities(SiteSetting.setting_key).all()
    }
    missing_keys = [key for key in DEFAULT_SITE_SETTINGS.keys() if key not in existing_keys]
    if not missing_keys:
        return

    for key in missing_keys:
        db.session.add(SiteSetting(setting_key=key, setting_value=DEFAULT_SITE_SETTINGS[key]))
    db.session.commit()


def migrate_legacy_hospital_ratings():
    """
    One-time migration from legacy `hospital_rating` table into `review`.
    After migration, drop `hospital_rating` so Review remains the single source of truth.
    """
    inspector = inspect(db.engine)
    if 'hospital_rating' not in inspector.get_table_names():
        return

    migrated_count = 0
    rows = db.session.execute(text("""
        SELECT patient_name, patient_email, rating, feedback, created_at
        FROM hospital_rating
        ORDER BY id ASC
    """)).mappings().all()

    for row in rows:
        patient_name = (row.get('patient_name') or '').strip() or 'Anonymous Patient'
        patient_email = (row.get('patient_email') or '').strip() or 'no-reply@makokhamedical.com'

        try:
            rating_value = _normalize_rating_value(row.get('rating'))
        except ValueError:
            continue

        review_text = (row.get('feedback') or '').strip() or 'Hospital rating submitted without written review.'

        existing = Review.query.filter_by(
            patient_name=patient_name,
            patient_email=patient_email,
            rating=rating_value,
            review_text=review_text
        ).first()
        if existing:
            continue

        created_at = row.get('created_at')
        if isinstance(created_at, str):
            try:
                created_at = datetime.fromisoformat(created_at)
            except ValueError:
                created_at = None

        review = Review(
            patient_name=patient_name,
            patient_email=patient_email,
            review_text=review_text,
            rating=rating_value,
            doctor_id=None,
            created_at=_coerce_utc(created_at) if created_at else datetime.now(timezone.utc)
        )
        db.session.add(review)
        migrated_count += 1

    db.session.commit()

    try:
        db.session.execute(text("DROP TABLE hospital_rating"))
        db.session.commit()
    except Exception:
        db.session.rollback()

    if migrated_count > 0:
        print(f"Migrated {migrated_count} legacy hospital ratings into review records.")





def init_db():
    """Initialize database"""
    with app.app_context():
        db.create_all()
        _ensure_founder_table()
        _ensure_partner_table()
        _ensure_doctor_schema()
        _ensure_communication_schema()
        _ensure_communication_thread_table()
        ensure_runtime_schema()
        ensure_event_schema()
        ensure_site_settings()
        migrate_legacy_hospital_ratings()
        create_default_admin()
        print("Database initialized successfully!")


_DB_BOOTSTRAPPED = False


def _init_db_if_enabled_once():
    """Initialize database once per process when AUTO_INIT_DB is enabled."""
    global _DB_BOOTSTRAPPED
    if _DB_BOOTSTRAPPED:
        return
    if not _env_flag('AUTO_INIT_DB', True):
        _DB_BOOTSTRAPPED = True
        return
    try:
        init_db()
    except Exception:
        app.logger.exception(
            'Startup database initialization failed; requests will retry schema bootstrap lazily.'
        )
    finally:
        _DB_BOOTSTRAPPED = True


# Ensure schemas are created under WSGI servers (e.g., Gunicorn), where
# the __main__ block is not executed.
_init_db_if_enabled_once()


# ==================== MAIN ====================

if __name__ == '__main__':
    # Initialize database and create tables
    _init_db_if_enabled_once()

    # Run the application
    debug_mode = _env_flag('FLASK_DEBUG', default=not IS_PRODUCTION) and not IS_PRODUCTION
    runtime_host = (os.getenv('FLASK_RUN_HOST') or '0.0.0.0').strip()
    runtime_port = _env_int('PORT', _env_int('FLASK_RUN_PORT', 5000))
    socketio.run(app, debug=debug_mode, host=runtime_host, port=runtime_port)
