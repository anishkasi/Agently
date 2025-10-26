import asyncio
from adapter.db.session import engine, Base
from adapter.db import models

async def test_connection():
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        print("✅ Connected to Supabase and created tables successfully!")
    except Exception as e:
        print(f"❌ Connection failed: {e}")

if __name__ == "__main__":
    asyncio.run(test_connection())