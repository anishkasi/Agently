"""
Reset Database Script - Drops all tables, recreates schema, and clears Redis cache.

⚠️  WARNING: This will DELETE ALL DATA in your database and Redis cache!
Use with caution, especially in production environments.

Usage:
    python reset_db.py
"""

import asyncio
from sqlalchemy.schema import DropTable
from sqlalchemy.ext.compiler import compiles
from sqlalchemy import text

from adapter.db.session import engine, Base
from adapter.db import models  # Import models to register them with Base
from adapter.cache.redis_cache import get_redis
from core import settings


# Add CASCADE to DROP TABLE on PostgreSQL (register BEFORE calling drop_all)
@compiles(DropTable, "postgresql")
def _compile_drop_table(element, compiler, **kw):
    """Compile DROP TABLE with CASCADE for PostgreSQL to handle foreign key constraints."""
    return compiler.visit_drop_table(element) + " CASCADE"


async def reset_database():
    """
    Reset the entire database:
    1. Drop all existing tables (with CASCADE)
    2. Create pgvector extension if needed
    3. Recreate all tables from models
    4. Clear Redis cache to avoid stale data
    """
    print("=" * 60)
    print("⚠️  DATABASE RESET - ALL DATA WILL BE DELETED")
    print("=" * 60)
    
    # Database reset
    async with engine.begin() as conn:
        print("\n🗑️  Dropping all tables...")
        try:
            # Use CASCADE on PostgreSQL via compiler hook
            await conn.run_sync(Base.metadata.drop_all)
            print("✅ All tables dropped successfully")
        except Exception as e:
            print(f"⚠️  Error dropping tables: {e}")
            print("Continuing with schema creation...")
        
        print("\n🔧 Creating pgvector extension...")
        # Ensure pgvector extension exists before creating tables that use vector type
        try:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            print("✅ pgvector extension ready")
        except Exception as e:
            print(f"⚠️  Could not create pgvector extension: {e}")
            print("If using Supabase, pgvector should already be enabled.")
        
        print("\n📋 Recreating all tables from models...")
        try:
            await conn.run_sync(Base.metadata.create_all)
            print("✅ All tables created successfully")
        except Exception as e:
            print(f"❌ Error creating tables: {e}")
            raise
    
    print("\n🎉 Database schema reset complete!")
    
    # Redis cache reset
    print("\n🧹 Clearing Redis cache...")
    try:
        redis = await get_redis(settings.REDIS_URL)
        await redis.flushdb()
        print("✅ Redis cache cleared successfully")
        await redis.close()
    except Exception as e:
        print(f"⚠️  Failed to clear Redis cache: {e}")
        print("You may need to manually clear Redis with: redis-cli FLUSHDB")
    
    print("\n" + "=" * 60)
    print("✅ RESET COMPLETE - Database and cache are now empty")
    print("=" * 60)
    print("\nNext steps:")
    print("  1. Run: python init_db.py (to verify schema)")
    print("  2. Start your bot: python dev_run.py")
    print("  3. Initialize groups with: /init_group")
    print("  4. Configure groups with: /config")


async def main():
    """Main entry point with confirmation prompt."""
    # Safety confirmation
    print("\n⚠️  WARNING: This will permanently delete ALL data!")
    print(f"Database: {settings.DATABASE_URL.split('@')[1] if '@' in settings.DATABASE_URL else settings.DATABASE_URL}")
    print(f"Redis: {settings.REDIS_URL}")
    
    try:
        response = input("\nType 'yes' to confirm reset: ").strip().lower()
        if response != "yes":
            print("❌ Reset cancelled")
            return
    except (KeyboardInterrupt, EOFError):
        print("\n❌ Reset cancelled")
        return
    
    await reset_database()


if __name__ == "__main__":
    asyncio.run(main())