#!/usr/bin/env python3
import sys
import getpass
from sqlalchemy.orm import Session
from app.database import engine, User, init_db, SessionLocal
from app.auth import hash_password

def main():
    print("=== BEES Genomic Pipeline - Admin User Creation ===")
    
    # Initialize the database if not already done
    try:
        init_db()
        print("Database tables verified.")
    except Exception as e:
        print(f"Error initializing database: {e}", file=sys.stderr)
        sys.exit(1)
        
    db: Session = SessionLocal()
    
    try:
        username = input("Enter new admin username: ").strip()
        if not username:
            print("Username cannot be empty.")
            sys.exit(1)
            
        # Check if user already exists
        existing_user = db.query(User).filter(User.username == username).first()
        if existing_user:
            print(f"Error: User '{username}' already exists.")
            sys.exit(1)
            
        password = getpass.getpass("Enter password: ")
        if len(password) < 8:
            print("Error: Password must be at least 8 characters long.")
            sys.exit(1)
            
        confirm_password = getpass.getpass("Confirm password: ")
        if password != confirm_password:
            print("Error: Passwords do not match.")
            sys.exit(1)
            
        hashed = hash_password(password)
        new_user = User(
            username=username,
            hashed_password=hashed,
            role="admin"
        )
        
        db.add(new_user)
        db.commit()
        print(f"Admin user '{username}' successfully created.")
        
    except KeyboardInterrupt:
        print("\nOperation cancelled.")
    except Exception as e:
        print(f"An unexpected error occurred: {e}", file=sys.stderr)
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    main()
