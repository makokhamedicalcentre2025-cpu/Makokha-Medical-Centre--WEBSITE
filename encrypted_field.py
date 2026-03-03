"""
Custom SQLAlchemy column types for encrypted storage.
"""

from sqlalchemy import String, TypeDecorator
from sqlalchemy.ext.hybrid import hybrid_property
from crypto_utils import EncryptionManager


class EncryptedString(TypeDecorator):
    """
    SQLAlchemy type that transparently encrypts/decrypts string data.
    Encrypted data is stored as base64 in the database.
    """
    
    impl = String
    cache_ok = True
    
    def __init__(self, length=None):
        """Initialize with optional length parameter."""
        super().__init__(length)
        self.encryption_manager = EncryptionManager()
    
    def process_bind_param(self, value, dialect):
        """Encrypt before writing to database."""
        if value is None:
            return None
        
        return self.encryption_manager.encrypt(value)
    
    def process_result_value(self, value, dialect):
        """Decrypt after reading from database."""
        if value is None:
            return None
        
        return self.encryption_manager.decrypt(value)
