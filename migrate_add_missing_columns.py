"""
Database migration script to add missing columns for encryption and new features.
This script adds columns that exist in models but are missing from the database.
"""

import os
import sys
from datetime import datetime, timezone
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Import app and db
from app import app, db

def migrate_database():
    """Add missing columns to existing tables."""
    
    with app.app_context():
        from sqlalchemy import inspect, text
        
        print("=" * 70)
        print("DATABASE MIGRATION: Adding Missing Columns")
        print("=" * 70)
        
        # Get database connection
        with db.engine.connect() as conn:
            inspector = inspect(db.engine)
            
            # Define tables and their missing columns
            migrations = {
                'admin': [
                    ('last_password_change', 'TIMESTAMP'),
                ],
                'reception': [
                    ('last_password_change', 'TIMESTAMP'),
                ],
            }
            
            for table_name, columns_to_add in migrations.items():
                print(f"\nChecking table: {table_name}")
                
                try:
                    # Get existing columns
                    existing_columns = {col['name'] for col in inspector.get_columns(table_name)}
                    print(f"  Existing columns: {len(existing_columns)}")
                    
                    # Check and add missing columns
                    for column_name, column_type in columns_to_add:
                        if column_name in existing_columns:
                            print(f"  ✓ Column '{column_name}' already exists")
                        else:
                            print(f"  ⚠ Adding missing column '{column_name}'...")
                            try:
                                alter_query = f"""
                                    ALTER TABLE {table_name}
                                    ADD COLUMN {column_name} {column_type} DEFAULT NULL;
                                """
                                conn.execute(text(alter_query))
                                conn.commit()
                                print(f"    ✓ Successfully added '{column_name}' to '{table_name}'")
                            except Exception as e:
                                print(f"    ✗ Error adding column: {str(e)}")
                                conn.rollback()
                
                except Exception as e:
                    print(f"  ✗ Error checking table '{table_name}': {str(e)}")
        
        print("\n" + "=" * 70)
        print("Migration completed!")
        print("=" * 70)


if __name__ == '__main__':
    try:
        migrate_database()
        print("\n✓ Database migration successful!")
        sys.exit(0)
    except Exception as e:
        print(f"\n✗ Migration failed: {str(e)}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
