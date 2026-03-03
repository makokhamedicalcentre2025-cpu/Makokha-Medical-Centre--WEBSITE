"""
Database migration script to encrypt existing data.
Run this after updating the encryption fields in your models.

Usage:
    python migrate_to_encryption.py
"""

import os
import sys
from dotenv import load_dotenv
from crypto_utils import EncryptionManager, AuditLog

# Add parent directory to path
sys.path.insert(0, os.path.dirname(__file__))

load_dotenv()

def migrate_doctor_data():
    """Migrate doctor phone, email, and bio to encrypted fields."""
    from app import app, db, Doctor
    
    with app.app_context():
        docs = Doctor.query.all()
        count = 0
        
        for doc in docs:
            # Encrypt phone if not already encrypted
            if doc.phone and not doc.phone_encrypted:
                doc.phone_encrypted = doc.phone
                count += 1
            
            # Encrypt email if not already encrypted
            if doc.email and not doc.email_encrypted:
                doc.email_encrypted = doc.email
                count += 1
            
            # Encrypt bio if not already encrypted
            if doc.bio and not doc.bio_encrypted:
                doc.bio_encrypted = doc.bio
                count += 1
        
        if count > 0:
            db.session.commit()
            print(f"✓ Migrated {len(docs)} doctor records with {count} encrypted fields")
            AuditLog.log_event('MIGRATION', action='Encrypted doctor records', changes={'count': count})


def migrate_review_data():
    """Migrate review patient info to encrypted fields."""
    from app import app, db, Review
    
    with app.app_context():
        reviews = Review.query.all()
        count = 0
        
        for review in reviews:
            if review.patient_name and not review.patient_name_encrypted:
                review.patient_name_encrypted = review.patient_name
                count += 1
            
            if review.patient_email and not review.patient_email_encrypted:
                review.patient_email_encrypted = review.patient_email
                count += 1
            
            if review.review_text and not review.review_text_encrypted:
                review.review_text_encrypted = review.review_text
                count += 1
        
        if count > 0:
            db.session.commit()
            print(f"✓ Migrated {len(reviews)} review records with {count} encrypted fields")
            AuditLog.log_event('MIGRATION', action='Encrypted review records', changes={'count': count})


def migrate_admin_data():
    """Migrate admin email to encrypted field."""
    from app import app, db, Admin
    
    with app.app_context():
        admins = Admin.query.all()
        count = 0
        
        for admin in admins:
            if admin.email and not admin.email_encrypted:
                admin.email_encrypted = admin.email
                count += 1
        
        if count > 0:
            db.session.commit()
            print(f"✓ Migrated {len(admins)} admin records with {count} encrypted fields")
            AuditLog.log_event('MIGRATION', action='Encrypted admin records', changes={'count': count})


def migrate_reception_data():
    """Migrate reception staff data to encrypted fields."""
    from app import app, db, Reception
    
    with app.app_context():
        staff = Reception.query.all()
        count = 0
        
        for person in staff:
            if person.email and not person.email_encrypted:
                person.email_encrypted = person.email
                count += 1
            
            if person.phone and not person.phone_encrypted:
                person.phone_encrypted = person.phone
                count += 1
        
        if count > 0:
            db.session.commit()
            print(f"✓ Migrated {len(staff)} reception records with {count} encrypted fields")
            AuditLog.log_event('MIGRATION', action='Encrypted reception records', changes={'count': count})


def migrate_communication_data():
    """Migrate communication messages to encrypted fields."""
    from app import app, db, Communication
    
    with app.app_context():
        comms = Communication.query.all()
        count = 0
        
        for comm in comms:
            if comm.patient_name and not comm.patient_name_encrypted:
                comm.patient_name_encrypted = comm.patient_name
                count += 1
            
            if comm.patient_email and not comm.patient_email_encrypted:
                comm.patient_email_encrypted = comm.patient_email
                count += 1
            
            if comm.patient_phone and not comm.patient_phone_encrypted:
                comm.patient_phone_encrypted = comm.patient_phone
                count += 1
            
            if comm.message_content and not comm.message_content_encrypted:
                comm.message_content_encrypted = comm.message_content
                count += 1
            
            if comm.reply_content and not comm.reply_content_encrypted:
                comm.reply_content_encrypted = comm.reply_content
                count += 1
        
        if count > 0:
            db.session.commit()
            print(f"✓ Migrated {len(comms)} communication records with {count} encrypted fields")
            AuditLog.log_event('MIGRATION', action='Encrypted communication records', changes={'count': count})


def migrate_communication_message_data():
    """Migrate communication message threads to encrypted fields."""
    from app import app, db, CommunicationMessage
    
    with app.app_context():
        messages = CommunicationMessage.query.all()
        count = 0
        
        for msg in messages:
            if msg.sender_name and not msg.sender_name_encrypted:
                msg.sender_name_encrypted = msg.sender_name
                count += 1
            
            if msg.sender_email and not msg.sender_email_encrypted:
                msg.sender_email_encrypted = msg.sender_email
                count += 1
            
            if msg.message_content and not msg.message_content_encrypted:
                msg.message_content_encrypted = msg.message_content
                count += 1
        
        if count > 0:
            db.session.commit()
            print(f"✓ Migrated {len(messages)} communication message records with {count} encrypted fields")
            AuditLog.log_event('MIGRATION', action='Encrypted communication messages', changes={'count': count})


def migrate_appointment_data():
    """Migrate appointment data to encrypted fields."""
    from app import app, db, Appointment
    
    with app.app_context():
        appointments = Appointment.query.all()
        count = 0
        
        for appt in appointments:
            if appt.patient_name and not appt.patient_name_encrypted:
                appt.patient_name_encrypted = appt.patient_name
                count += 1
            
            if appt.patient_email and not appt.patient_email_encrypted:
                appt.patient_email_encrypted = appt.patient_email
                count += 1
            
            if appt.patient_phone and not appt.patient_phone_encrypted:
                appt.patient_phone_encrypted = appt.patient_phone
                count += 1
            
            if appt.reason and not appt.reason_encrypted:
                appt.reason_encrypted = appt.reason
                count += 1
            
            if appt.notes and not appt.notes_encrypted:
                appt.notes_encrypted = appt.notes
                count += 1
        
        if count > 0:
            db.session.commit()
            print(f"✓ Migrated {len(appointments)} appointment records with {count} encrypted fields")
            AuditLog.log_event('MIGRATION', action='Encrypted appointment records', changes={'count': count})


def migrate_call_data():
    """Migrate call data to encrypted fields."""
    from app import app, db, Call
    
    with app.app_context():
        calls = Call.query.all()
        count = 0
        
        for call in calls:
            if call.patient_name and not call.patient_name_encrypted:
                call.patient_name_encrypted = call.patient_name
                count += 1
            
            if call.patient_phone and not call.patient_phone_encrypted:
                call.patient_phone_encrypted = call.patient_phone
                count += 1
        
        if count > 0:
            db.session.commit()
            print(f"✓ Migrated {len(calls)} call records with {count} encrypted fields")
            AuditLog.log_event('MIGRATION', action='Encrypted call records', changes={'count': count})


def main():
    """Run all migrations."""
    print("=" * 60)
    print("MEDICAL CENTRE DATABASE ENCRYPTION MIGRATION")
    print("=" * 60)
    print()
    
    # Check that encryption key is set
    if not os.getenv('ENCRYPTION_KEY'):
        print("⚠ WARNING: ENCRYPTION_KEY is not set in environment variables!")
        print("Generate a key using:")
        print("  python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\"")
        print()
        print("Set it in your .env file:")
        print("  ENCRYPTION_KEY=<generated_key>")
        print()
        response = input("Continue with temporary key? (y/n): ")
        if response.lower() != 'y':
            sys.exit(1)
    
    print("Starting encrypted field migration...\n")
    
    try:
        migrate_admin_data()
        migrate_doctor_data()
        migrate_review_data()
        migrate_reception_data()
        migrate_communication_data()
        migrate_communication_message_data()
        migrate_appointment_data()
        migrate_call_data()
        
        print()
        print("=" * 60)
        print("✓ MIGRATION COMPLETED SUCCESSFULLY")
        print("=" * 60)
        print()
        print("All sensitive data has been encrypted.")
        print("Both plain and encrypted versions are stored for backward compatibility.")
        print()
        
    except Exception as e:
        print()
        print("=" * 60)
        print(f"✗ MIGRATION FAILED: {e}")
        print("=" * 60)
        sys.exit(1)


if __name__ == '__main__':
    main()
