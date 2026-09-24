"""Non-interactive seed: company, branch, developer login, fuel types."""
import sys
from sqlalchemy import text
from api.database import SessionLocal
from api import models, auth


def main() -> None:
    db = SessionLocal()
    try:
        slug = "hybrid-fuels"
        existing = db.query(models.Company).filter(models.Company.slug == slug).first()
        if existing:
            print(f"Company already exists (id={existing.id}).")
            company_id = existing.id
        else:
            row = db.execute(
                text(
                    "SELECT fn_create_company(:name, :slug, NULL, NULL, NULL, :phone, :email, NULL) AS id"
                ),
                {
                    "name": "Hybrid Fuels",
                    "slug": slug,
                    "phone": "+263719305449",
                    "email": "admin@hybridfuel.local",
                },
            ).mappings().first()
            db.commit()
            company_id = row["id"]
            print(f"Created company Hybrid Fuels (id={company_id}).")

        branch = (
            db.query(models.CompanyBranch)
            .filter(
                models.CompanyBranch.company_id == company_id,
                models.CompanyBranch.branch_name == "Main Branch",
            )
            .first()
        )
        if not branch:
            branch = models.CompanyBranch(company_id=company_id, branch_name="Main Branch")
            db.add(branch)
            db.commit()
            print(f"Created branch Main Branch (id={branch.id}).")
        else:
            print(f"Branch already exists (id={branch.id}).")

        username = "admin"
        user = db.query(models.User).filter(models.User.username == username).first()
        if not user:
            user = models.User(
                company_id=company_id,
                branch_id=None,
                username=username,
                password_hash=auth.hash_password("Jesus1@one"),
                full_name="System Administrator",
                role="developer",
            )
            db.add(user)
            db.commit()
            print(f"Created user '{username}' / 'Jesus1@one' (role=developer).")
        else:
            print(f"User '{username}' already exists.")

        for name, cost, sell in (
            ("Petrol 93", 1.35, 1.55),
            ("Petrol 95", 1.40, 1.62),
            ("Diesel", 1.30, 1.48),
        ):
            if not (
                db.query(models.FuelType)
                .filter(models.FuelType.company_id == company_id, models.FuelType.name == name)
                .first()
            ):
                db.add(
                    models.FuelType(
                        company_id=company_id,
                        name=name,
                        default_cost=cost,
                        default_sell_price=sell,
                        updated_by=user.id,
                    )
                )
        db.commit()
        print("Fuel types seeded: Petrol 93, Petrol 95, Diesel.")
        print("Login: admin / Jesus1@one")
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
