"""
Run once after schema.sql has been applied, to create your first company
(tenant) and its first login (role 'developer', which can create everyone
else via the API).

    python create_first_company.py

This talks to the database directly — it's the same thing
POST /companies + POST /companies/{id}/bootstrap-admin do over HTTP, so
you can just use those endpoints instead once the API is running.
"""
from sqlalchemy import text
from api.database import SessionLocal
from api import models, auth


def main():
    db = SessionLocal()
    try:
        print("--- Company ---")
        name = input("Company name: ").strip()
        slug = input("Company slug (used in URLs, e.g. 'acme-fuels'): ").strip()
        phone = input("Company phone (optional): ").strip() or None
        email = input("Company email (optional): ").strip() or None

        if db.query(models.Company).filter(models.Company.slug == slug).first():
            print("That slug is already taken.")
            return

        row = db.execute(
            text("SELECT fn_create_company(:name, :slug, NULL, NULL, NULL, :phone, :email, NULL) AS id"),
            {"name": name, "slug": slug, "phone": phone, "email": email},
        ).mappings().first()
        db.commit()
        company_id = row["id"]
        print(f"Created company '{name}' (id={company_id}).")

        print("\n--- First branch ---")
        branch_name = input("Branch name (e.g. 'Main Branch'): ").strip()
        branch = models.CompanyBranch(company_id=company_id, branch_name=branch_name)
        db.add(branch)
        db.commit()
        print(f"Created branch '{branch_name}' (id={branch.id}).")

        print("\n--- First login (role: developer) ---")
        username = input("Username: ").strip()
        full_name = input("Full name: ").strip()
        password = input("Password: ").strip()

        if db.query(models.User).filter(models.User.username == username).first():
            print("That username already exists.")
            return

        user = models.User(
            company_id=company_id,
            branch_id=None,  # company-wide access
            username=username,
            password_hash=auth.hash_password(password),
            full_name=full_name,
            role="developer",
        )
        db.add(user)
        db.commit()
        print(f"\nAll set. Log in as '{username}' — company_id={company_id}, branch_id={branch.id}.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
