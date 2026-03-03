#!/usr/bin/env python
"""Delete all admin accounts from the database using raw SQL."""

import os
from dotenv import load_dotenv
import psycopg2
from psycopg2 import sql

# Load environment variables
load_dotenv()

def delete_all_admins():
    """Delete all admin accounts using raw SQL."""
    database_url = os.getenv('DATABASE_URL')
    if not database_url:
        print("DATABASE_URL not found in environment")
        return
    
    try:
        # Parse database URL
        # Format: postgresql://user:password@host:port/dbname
        conn = psycopg2.connect(database_url)
        cursor = conn.cursor()
        
        # Delete all admins
        cursor.execute("DELETE FROM admin")
        deleted_count = cursor.rowcount
        conn.commit()
        
        if deleted_count == 0:
            print("No admins found in database.")
        else:
            print(f"Deleted {deleted_count} admin account(s) from database.")
        
        cursor.close()
        conn.close()
    except Exception as e:
        print(f"Error deleting admins: {e}")

if __name__ == '__main__':
    delete_all_admins()
