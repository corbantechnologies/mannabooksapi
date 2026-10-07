import asyncio
from sqlalchemy import select
from app.db.session import async_session_factory
from app.models.identity import Shop

async def check_shops():
    async with async_session_factory() as session:
        res = await session.execute(select(Shop).limit(5))
        shops = res.scalars().all()
        print(f"Found {len(shops)} shops:")
        for s in shops:
            print(f"- Shop ID: {s.id}, Name: {s.name}, Slug: {s.slug}")

if __name__ == "__main__":
    asyncio.run(check_shops())
