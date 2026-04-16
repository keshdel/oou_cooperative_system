"""
Enterprise Configuration
"""

import os
from datetime import timedelta

class Config:
    # Base Config
    SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-key-change-in-production')
    DEBUG = False
    TESTING = False
    
    # Database
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL', 'sqlite:///cooperative.db')
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        'pool_size': 10,
        'pool_recycle': 3600,
        'pool_pre_ping': True,
    }
    
    # Redis
    REDIS_URL = os.environ.get('REDIS_URL', 'redis://localhost:6379')
    
    # Session
    SESSION_TYPE = 'redis'
    SESSION_PERMANENT = False
    SESSION_USE_SIGNER = True
    SESSION_KEY_PREFIX = 'coop:'
    SESSION_REDIS = REDIS_URL
    PERMANENT_SESSION_LIFETIME = timedelta(hours=8)
    
    # Security
    REMEMBER_COOKIE_DURATION = timedelta(days=14)
    REMEMBER_COOKIE_SECURE = True
    REMEMBER_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SECURE = True
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    
    # Rate Limiting
    RATELIMIT_ENABLED = True
    RATELIMIT_DEFAULT = "100/hour"
    RATELIMIT_STORAGE_URL = REDIS_URL
    
    # File Upload
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16MB
    UPLOAD_FOLDER = 'static/uploads'
    ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'pdf'}
    
    # Mail
    MAIL_SERVER = os.environ.get('MAIL_SERVER', 'smtp.gmail.com')
    MAIL_PORT = int(os.environ.get('MAIL_PORT', 587))
    MAIL_USE_TLS = os.environ.get('MAIL_USE_TLS', 'True') == 'True'
    MAIL_USERNAME = os.environ.get('MAIL_USERNAME')
    MAIL_PASSWORD = os.environ.get('MAIL_PASSWORD')
    MAIL_DEFAULT_SENDER = os.environ.get('MAIL_DEFAULT_SENDER', 'noreply@ooucoop.org')
    
    # Pagination
    ITEMS_PER_PAGE = 20
    
    # Cache
    CACHE_TYPE = 'redis'
    CACHE_REDIS_URL = REDIS_URL
    CACHE_DEFAULT_TIMEOUT = 300
    
    # Celery
    CELERY_BROKER_URL = REDIS_URL
    CELERY_RESULT_BACKEND = REDIS_URL
    CELERY_ACCEPT_CONTENT = ['json']
    CELERY_TASK_SERIALIZER = 'json'
    CELERY_RESULT_SERIALIZER = 'json'
    CELERY_TIMEZONE = 'Africa/Lagos'
    
    # 2FA
    TWO_FACTOR_AUTHENTICATION_ENABLED = True
    TWO_FACTOR_ISSUER = 'OOU Cooperative'
    
    # Payment Gateway
    PAYSTACK_SECRET_KEY = os.environ.get('PAYSTACK_SECRET')
    PAYSTACK_PUBLIC_KEY = os.environ.get('PAYSTACK_PUBLIC')
    
    # Logging
    LOG_LEVEL = 'INFO'
    LOG_FILE = 'logs/app.log'
    
    # API
    API_RATE_LIMIT = "1000/hour"
    API_KEY_EXPIRY_DAYS = 30
    
    # Cooperative Settings (from DB with fallback)
    MIN_SAVINGS = 5000
    LATE_FEE_PERCENT = 10
    LOAN_INTEREST_RATE = 11
    LOAN_MULTIPLIER = 2
    MIN_MEMBERSHIP_MONTHS = 6
    MIN_SAVINGS_FOR_LOAN = 50000

class DevelopmentConfig(Config):
    DEBUG = True
    SESSION_COOKIE_SECURE = False
    REMEMBER_COOKIE_SECURE = False

class ProductionConfig(Config):
    DEBUG = False
    SESSION_COOKIE_SECURE = True
    REMEMBER_COOKIE_SECURE = True

class TestingConfig(Config):
    TESTING = True
    DEBUG = True
    SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'
    WTF_CSRF_ENABLED = False

# Select config based on environment
config = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'testing': TestingConfig,
    'default': DevelopmentConfig
}