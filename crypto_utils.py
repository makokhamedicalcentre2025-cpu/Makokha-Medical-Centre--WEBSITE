"""
Encryption and security utilities for the Makokha Medical Centre application.
Provides field-level encryption, file encryption, and secure hashing.
"""

import os
import json
import hmac
import hashlib
from datetime import datetime, timezone
from base64 import b64encode, b64decode
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend
from dotenv import load_dotenv

# Load environment variables
load_dotenv()


class EncryptionManager:
    """Manages field-level encryption for sensitive database data."""
    
    _instance = None
    _cipher = None
    
    def __new__(cls):
        """Singleton pattern to ensure only one instance."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        """Initialize the encryption manager."""
        if self._initialized:
            return
        
        self._initialized = True
        # Get or generate encryption key
        key = self._get_or_create_encryption_key()
        self._cipher = Fernet(key)
    
    @staticmethod
    def _get_or_create_encryption_key():
        """Get encryption key from environment or generate new one."""
        key_env = os.getenv('ENCRYPTION_KEY')
        
        if key_env:
            try:
                # Validate that it's a proper Fernet key
                key_bytes = key_env.encode() if isinstance(key_env, str) else key_env
                Fernet(key_bytes)  # Test if valid
                return key_bytes
            except Exception:
                raise ValueError(
                    'Invalid ENCRYPTION_KEY format. Generate a new key using: '
                    'python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
                )
        
        # Generate a new key if not provided
        import warnings
        warnings.warn(
            'ENCRYPTION_KEY environment variable not set. Generating a temporary key. '
            'Set ENCRYPTION_KEY environment variable with a persistent key for production.',
            RuntimeWarning
        )
        return Fernet.generate_key()
    
    def encrypt(self, plaintext):
        """
        Encrypt plaintext string.
        
        Args:
            plaintext: String to encrypt
            
        Returns:
            Encrypted string (base64 encoded for database storage)
        """
        if plaintext is None:
            return None
        
        if not isinstance(plaintext, (str, bytes)):
            plaintext = str(plaintext)
        
        if isinstance(plaintext, str):
            plaintext = plaintext.encode('utf-8')
        
        encrypted = self._cipher.encrypt(plaintext)
        return b64encode(encrypted).decode('utf-8')
    
    def decrypt(self, ciphertext):
        """
        Decrypt ciphertext string.
        
        Args:
            ciphertext: Encrypted string (base64 encoded)
            
        Returns:
            Decrypted string
        """
        if ciphertext is None:
            return None
        
        if isinstance(ciphertext, str):
            ciphertext = ciphertext.encode('utf-8')
        
        try:
            encrypted = b64decode(ciphertext)
            decrypted = self._cipher.decrypt(encrypted)
            return decrypted.decode('utf-8')
        except Exception as e:
            # Log the error but don't expose encryption details
            print(f'Decryption error: {e}')
            return None


class FileEncryption:
    """Handles encryption and decryption of uploaded files."""
    
    @staticmethod
    def _derive_key_from_master(context='file', length=32):
        """Derive a 32-byte key from master encryption key for file operations."""
        master_key = os.getenv('ENCRYPTION_KEY', '').encode()
        if not master_key:
            raise ValueError('ENCRYPTION_KEY must be set for file encryption')
        
        # Use hashlib for key derivation (simpler, no import issues)
        salt = context.encode().ljust(16, b'\x00')[:16]
        key_material = hmac.new(salt, master_key, hashlib.sha256).digest()
        # Additional rounds for strength
        for _ in range(99999):
            key_material = hmac.new(key_material, salt, hashlib.sha256).digest()
        return key_material[:length]
    
    @staticmethod
    def encrypt_file(file_path):
        """
        Encrypt a file in place using AES-256-GCM.
        
        Args:
            file_path: Path to file to encrypt
            
        Returns:
            Dictionary with success status and metadata
        """
        if not os.path.exists(file_path):
            return {'success': False, 'error': 'File not found'}
        
        try:
            # Read the original file
            with open(file_path, 'rb') as f:
                plaintext = f.read()
            
            # Generate random nonce and derive key
            nonce = os.urandom(12)
            key = FileEncryption._derive_key_from_master()
            
            # Encrypt using AES-256-GCM
            cipher = Cipher(
                algorithms.AES(key),
                modes.GCM(nonce),
                backend=default_backend()
            )
            encryptor = cipher.encryptor()
            ciphertext = encryptor.update(plaintext) + encryptor.finalize()
            
            # Write encrypted file with nonce and tag
            encrypted_data = nonce + encryptor.tag + ciphertext
            with open(file_path, 'wb') as f:
                f.write(encrypted_data)
            
            return {
                'success': True,
                'message': 'File encrypted successfully',
                'file_size': len(plaintext)
            }
        except Exception as e:
            return {'success': False, 'error': str(e)}
    
    @staticmethod
    def decrypt_file(file_path, output_path=None):
        """
        Decrypt a file.
        
        Args:
            file_path: Path to encrypted file
            output_path: Where to save decrypted file (optional, else decrypts in place)
            
        Returns:
            Decrypted file content or None on failure
        """
        if not os.path.exists(file_path):
            return None
        
        try:
            with open(file_path, 'rb') as f:
                encrypted_data = f.read()
            
            # Extract nonce, tag, and ciphertext
            nonce = encrypted_data[:12]
            tag = encrypted_data[12:28]
            ciphertext = encrypted_data[28:]
            
            # Derive key and decrypt
            key = FileEncryption._derive_key_from_master()
            cipher = Cipher(
                algorithms.AES(key),
                modes.GCM(nonce, tag),
                backend=default_backend()
            )
            decryptor = cipher.decryptor()
            plaintext = decryptor.update(ciphertext) + decryptor.finalize()
            
            # Write to output or return
            if output_path:
                with open(output_path, 'wb') as f:
                    f.write(plaintext)
                return True
            
            return plaintext
        except Exception:
            return None


class SecureFieldType:
    """
    SQLAlchemy custom column type for encrypted fields.
    Transparently encrypts/decrypts data at the ORM level.
    """
    
    def __init__(self):
        self.encryption_manager = EncryptionManager()
    
    def process_bind_param(self, value, dialect):
        """Encrypt data before storing in database."""
        if value is None:
            return None
        return self.encryption_manager.encrypt(value)
    
    def process_result_value(self, value, dialect):
        """Decrypt data when retrieving from database."""
        if value is None:
            return None
        return self.encryption_manager.decrypt(value)


class AuditLog:
    """Audit logging for security events and data access."""
    
    @staticmethod
    def log_event(event_type, user_id=None, resource_type=None, 
                  resource_id=None, action=None, changes=None, 
                  ip_address=None, user_agent=None):
        """
        Log a security event.
        
        Args:
            event_type: Type of event (login, access, modify, delete, etc.)
            user_id: ID of user performing action
            resource_type: Type of resource affected
            resource_id: ID of resource affected
            action: Specific action taken
            changes: Dictionary of changes made
            ip_address: IP address of request
            user_agent: HTTP user agent
        """
        log_entry = {
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'event_type': event_type,
            'user_id': user_id,
            'resource_type': resource_type,
            'resource_id': resource_id,
            'action': action,
            'changes': changes,
            'ip_address': ip_address,
            'user_agent': user_agent
        }
        
        # In production, this should write to a secure log file or audit database
        # For now, we'll just log to file
        try:
            log_dir = os.path.join(os.getcwd(), 'logs')
            os.makedirs(log_dir, exist_ok=True)
            
            log_file = os.path.join(log_dir, 'audit.log')
            with open(log_file, 'a') as f:
                f.write(json.dumps(log_entry) + '\n')
        except Exception as e:
            print(f'Error writing audit log: {e}')


class PasswordValidator:
    """Validates password strength and requirements."""
    
    MIN_LENGTH = 12
    REQUIRE_UPPERCASE = True
    REQUIRE_LOWERCASE = True
    REQUIRE_NUMBERS = True
    REQUIRE_SPECIAL = True
    
    SPECIAL_CHARS = set('!@#$%^&*()_+-=[]{}|;:,.<>?')
    
    @classmethod
    def validate(cls, password):
        """
        Validate password strength.
        
        Args:
            password: Password string to validate
            
        Returns:
            Tuple of (is_valid, error_message)
        """
        if not password:
            return False, 'Password cannot be empty'
        
        if len(password) < cls.MIN_LENGTH:
            return False, f'Password must be at least {cls.MIN_LENGTH} characters long'
        
        has_upper = any(c.isupper() for c in password)
        has_lower = any(c.islower() for c in password)
        has_digit = any(c.isdigit() for c in password)
        has_special = any(c in cls.SPECIAL_CHARS for c in password)
        
        if cls.REQUIRE_UPPERCASE and not has_upper:
            return False, 'Password must contain at least one uppercase letter'
        
        if cls.REQUIRE_LOWERCASE and not has_lower:
            return False, 'Password must contain at least one lowercase letter'
        
        if cls.REQUIRE_NUMBERS and not has_digit:
            return False, 'Password must contain at least one number'
        
        if cls.REQUIRE_SPECIAL and not has_special:
            return False, 'Password must contain at least one special character: !@#$%^&*()_+-=[]{}|;:,.<>?'
        
        return True, 'Password is strong'


class InputValidator:
    """Validates and sanitizes user input."""
    
    @staticmethod
    def sanitize_email(email):
        """Validate and return sanitized email."""
        if not email:
            return None
        
        email = str(email).strip().lower()
        
        # Basic email validation
        if '@' not in email or '.' not in email.split('@')[1]:
            return None
        
        if len(email) > 254:  # RFC 5321
            return None
        
        return email
    
    @staticmethod
    def sanitize_phone(phone):
        """Validate and return sanitized phone number."""
        if not phone:
            return None
        
        phone = str(phone).strip()
        # Remove common separators
        sanitized = ''.join(c for c in phone if c.isdigit() or c in '+-() ')
        
        # Phone should have at least 7 digits
        digits_only = ''.join(c for c in sanitized if c.isdigit())
        if len(digits_only) < 7:
            return None
        
        return sanitized
    
    @staticmethod
    def sanitize_text(text, max_length=None, allow_html=False):
        """Sanitize text input."""
        if not text:
            return None
        
        text = str(text).strip()
        
        if not allow_html:
            # Remove any HTML/script tags using bleach
            try:
                from bleach import clean
                text = clean(text, tags=[], strip=True)
            except ImportError:
                # Basic fallback if bleach not available
                text = text.replace('<', '&lt;').replace('>', '&gt;')
        
        if max_length and len(text) > max_length:
            text = text[:max_length]
        
        return text
    
    @staticmethod
    def is_valid_filename(filename, allowed_extensions=None):
        """Validate uploaded filename."""
        if not filename:
            return False
        
        from werkzeug.utils import secure_filename
        
        # Use werkzeug's secure_filename
        safe_name = secure_filename(filename)
        
        if not safe_name or safe_name == '' or len(safe_name) > 255:
            return False
        
        if allowed_extensions:
            ext = os.path.splitext(safe_name)[1].lstrip('.').lower()
            if ext not in allowed_extensions:
                return False
        
        return True


def get_encryption_manager():
    """Get singleton instance of encryption manager."""
    return EncryptionManager()
