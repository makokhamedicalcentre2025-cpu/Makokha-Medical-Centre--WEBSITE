"""
Database migration script to increase column sizes for encrypted fields.
This accommodates the larger size of encrypted data (base64-encoded symmetrical encryption).
"""

import os
import sys
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Import app and db
from app import app, db

def migrate_increase_column_sizes():
    """Increase column sizes for encrypted fields."""
    
    with app.app_context():
        from sqlalchemy import text
        
        print("=" * 70)
        print("DATABASE MIGRATION: Increasing Column Sizes for Encrypted Data")
        print("=" * 70)
        print("\nNote: Encrypted data is base64-encoded and significantly larger")
        print("than the original plaintext. Columns must be larger.\n")
        
        # Get database connection
        with db.engine.connect() as conn:
            
            # Define tables and columns to resize
            # Format: (table_name, column_name, new_size)
            migrations = [
                # Admin table
                ('admin', 'username', 'VARCHAR(500)'),      # From 80, "MMN" encrypted = ~88 chars
                ('admin', 'email', 'VARCHAR(500)'),         # From 120, email encrypted = ~160 chars
                ('admin', 'full_name', 'VARCHAR(500)'),     # From 150, name encrypted = ~200 chars
                
                # Reception table
                ('reception', 'username', 'VARCHAR(500)'),
                ('reception', 'email', 'VARCHAR(500)'),
                ('reception', 'full_name', 'VARCHAR(500)'),
                ('reception', 'phone', 'VARCHAR(300)'),
                ('reception', 'department', 'VARCHAR(300)'),
                ('reception', 'shift', 'VARCHAR(300)'),
            ]
            
            success_count = 0
            skip_count = 0
            error_count = 0
            
            for table_name, column_name, new_type in migrations:
                print(f"Altering {table_name}.{column_name} to {new_type}...")
                try:
                    alter_query = f"""
                        ALTER TABLE {table_name}
                        ALTER COLUMN {column_name} TYPE {new_type};
                    """
                    conn.execute(text(alter_query))
                    conn.commit()
                    print(f"  ✓ Successfully resized {table_name}.{column_name}")
                    success_count += 1
                except Exception as e:
                    error_msg = str(e)
                    # Check if column already has correct type
                    if 'attribute' in error_msg.lower() or 'does not exist' in error_msg.lower():
                        print(f"  ⚠ Column doesn't exist or already correct: {table_name}.{column_name}")
                        skip_count += 1
                    else:
                        print(f"  ✗ Error: {error_msg}")
                        error_count += 1
                    conn.rollback()
        
        print("\n" + "=" * 70)
        print(f"Migration Results:")
        print(f"  ✓ Successful: {success_count}")
        print(f"  ⚠ Skipped: {skip_count}")
        print(f"  ✗ Errors: {error_count}")
        print("=" * 70)
        
        return error_count == 0


if __name__ == '__main__':
    try:
        success = migrate_increase_column_sizes()
        if success:
            print("\n✓ Database migration completed successfully!")
            sys.exit(0)
        else:
            print("\n⚠ Migration completed with some errors (see above)")
            sys.exit(1)
    except Exception as e:
        print(f"\n✗ Migration failed: {str(e)}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
