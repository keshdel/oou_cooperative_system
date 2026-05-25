# extensions.py
# Flask-Mail has been replaced by Resend (see email_service.py).
# This stub keeps any legacy `from extensions import mail` imports from crashing.

class _MailStub:
    """No-op placeholder so old imports don't raise AttributeError."""
    def send(self, *a, **kw):
        pass
    def init_app(self, *a, **kw):
        pass

mail = _MailStub()
