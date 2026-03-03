#!/usr/bin/env python
"""Check admin count in the database."""

import os
from dotenv import load_dotenv
import psycopg2
from urllib.parse import urlparse

# Load environment variables
load_dotenv()

def check_admin_count():
    """Check how many admins exist in the database."""
    database_url = os.getenv('DATABASE_URL')
    if not database_url:
        print("DATABASE_URL not found in environment")
        return
    
    try:
        # Parse the URL properly
        parsed_url = urlparse(database_url)
        
        # Build connection parameters
        conn_params = {
            'host': parsed_url.hostname,
            'port': parsed_url.port or 5432,
            'user': parsed_url.username,
            'password': parsed_url.password,
            'database': parsed_url.path.lstrip('/'),
        }
        
        # Add sslmode if specified in the query params
        if 'sslmode' in parsed_url.query:
            conn_params['sslmode'] = 'require'
        
        conn = psycopg2.connect(**conn_params)
        cursor = conn.cursor()
        
        # Get admin count
        cursor.execute("SELECT COUNT(*) FROM admin")
        count = cursor.fetchone()[0]
        print(f"Admin count: {count}")
        
        # Get admin details
        cursor.execute("SELECT id, username, email, full_name FROM admin")
        admins = cursor.fetchall()
        if admins:
            print("\nAdmin accounts:")
            for admin in admins:
                print(f"  ID: {admin[0]}, Username: {admin[1]}, Email: {admin[2]}, Name: {admin[3]}")
        
        cursor.close()
        conn.close()
    except Exception as e:
        print(f"Error: {e}")

if __name__ == '__main__':
    check_admin_count()
