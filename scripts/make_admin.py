# Allow `python scripts/<name>.py` from the project root (adds the root to the import path).
import os as _os
import sys as _sys

_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import sys
import asyncio
from app.db.session import AsyncSessionLocal
from app.models.identity import User, Shop
from sqlalchemy import select

async def make_admin(email: str):
    target_email = email.strip().lower()
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.email == target_email))
        user = result.scalar_one_or_none()
        if not user:
            print(f"❌ No user found with email: {target_email}")
            return
        
        user.is_super_admin = True
        user.is_lifetime_pro = True
        user.plan = "PRO"
        
        # Elevate all shops owned by user to LIFETIME_FREE & PRO
        shop_res = await session.execute(select(Shop).where(Shop.owner_id == user.id))
        shops = shop_res.scalars().all()
        for shop in shops:
            shop.is_lifetime_pro = True
            shop.subscription_status = "LIFETIME_FREE"
            shop.plan = "PRO"
            
        await session.commit()
        print(f"👑 SUCCESS: User {target_email} has been elevated to Super Admin & Lifetime PRO.")
        print(f"Updated {len(shops)} owned workspace(s).")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/make_admin.py <email@domain.com>")
        sys.exit(1)
    asyncio.run(make_admin(sys.argv[1]))
