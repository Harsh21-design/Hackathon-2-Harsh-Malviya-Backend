"""
Seed script to populate initial demo data (Profile, Clients, Invoices, Activities).
Matches the frontend mock dataset.
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
from decimal import Decimal
import uuid

from app.core.database import engine, AsyncSessionLocal, Base
from app.core.security import DEMO_USER_ID
from app.models import BusinessProfile, Client, Invoice, ActivityLog, ReminderAttempt


async def seed():
    print("Preparing database tables...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSessionLocal() as session:
        user_uuid = uuid.UUID(DEMO_USER_ID)

        # 1. Check or create Business Profile
        profile = await session.get(BusinessProfile, user_uuid)
        if not profile:
            profile = BusinessProfile(
                id=user_uuid,
                name="Harsh Malviya",
                email="harsh@example.com",
                company="Nexus Digital Studio",
                address="742 Evergreen Terrace, Sector 4, Tech Park",
            )
            session.add(profile)
            print("Created Business Profile.")

        # 2. Clients
        clients_data = [
            {"id": uuid.UUID("11111111-1111-1111-1111-111111111111"), "name": "Riley Johnson", "email": "riley.johnson@example.com", "company": "Johnson Media"},
            {"id": uuid.UUID("22222222-2222-2222-2222-222222222222"), "name": "David Chen", "email": "david.chen@example.com", "company": "Chen Logistics"},
            {"id": uuid.UUID("33333333-3333-3333-3333-333333333333"), "name": "Elena Rostova", "email": "elena.r@example.com", "company": "Vanguard Design"},
            {"id": uuid.UUID("44444444-4444-4444-4444-444444444444"), "name": "Marcus Aurelius", "email": "marcus@stoicbrands.com", "company": "Stoic Brands"},
        ]

        client_map = {}
        for c in clients_data:
            existing_c = await session.get(Client, c["id"])
            if not existing_c:
                existing_c = Client(
                    id=c["id"],
                    owner_id=user_uuid,
                    name=c["name"],
                    email=c["email"],
                    company=c["company"],
                )
                session.add(existing_c)
            client_map[c["name"]] = existing_c

        await session.flush()
        print(f"Ensured {len(clients_data)} clients.")

        # 3. Invoices
        today = date.today()
        invoices_data = [
            {
                "id": uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
                "invoice_number": "INV-103",
                "client": client_map["Riley Johnson"],
                "amount": Decimal("2900.00"),
                "currency": "USD",
                "invoice_date": today - timedelta(days=28),
                "due_date": today - timedelta(days=6),
                "status": "OVERDUE",
                "reminder_count": 1,
            },
            {
                "id": uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
                "invoice_number": "INV-102",
                "client": client_map["David Chen"],
                "amount": Decimal("4800.00"),
                "currency": "USD",
                "invoice_date": today - timedelta(days=45),
                "due_date": today - timedelta(days=14),
                "status": "OVERDUE",
                "reminder_count": 2,
            },
            {
                "id": uuid.UUID("cccccccc-cccc-cccc-cccc-cccccccccccc"),
                "invoice_number": "INV-101",
                "client": client_map["Elena Rostova"],
                "amount": Decimal("1250.00"),
                "currency": "USD",
                "invoice_date": today - timedelta(days=20),
                "due_date": today + timedelta(days=10),
                "status": "PENDING",
                "reminder_count": 0,
            },
            {
                "id": uuid.UUID("dddddddd-dddd-dddd-dddd-dddddddddddd"),
                "invoice_number": "INV-100",
                "client": client_map["Marcus Aurelius"],
                "amount": Decimal("3500.00"),
                "currency": "USD",
                "invoice_date": today - timedelta(days=60),
                "due_date": today - timedelta(days=30),
                "status": "PAID",
                "reminder_count": 2,
            },
        ]

        for inv in invoices_data:
            existing_inv = await session.get(Invoice, inv["id"])
            if not existing_inv:
                existing_inv = Invoice(
                    id=inv["id"],
                    owner_id=user_uuid,
                    client_id=inv["client"].id,
                    invoice_number=inv["invoice_number"],
                    amount=inv["amount"],
                    currency=inv["currency"],
                    invoice_date=inv["invoice_date"],
                    due_date=inv["due_date"],
                    status=inv["status"],
                    reminder_count=inv["reminder_count"],
                    last_reminder_at=datetime.utcnow() - timedelta(days=2) if inv["reminder_count"] > 0 else None,
                )
                session.add(existing_inv)

        await session.flush()
        print(f"Ensured {len(invoices_data)} invoices.")

        # 4. Activity Logs
        activities_data = [
            {"event_type": "INVOICE_CREATED", "description": "Invoice INV-103 ($2,900) created for Riley Johnson", "inv_id": uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")},
            {"event_type": "REMINDER_SENT", "description": "Reminder 1 sent to Riley Johnson (INV-103)", "inv_id": uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")},
            {"event_type": "REMINDER_SENT", "description": "Reminder 2 sent to David Chen (INV-102)", "inv_id": uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")},
            {"event_type": "PAYMENT_VERIFIED", "description": "Payment of $3,500 verified for Marcus Aurelius (INV-100)", "inv_id": uuid.UUID("dddddddd-dddd-dddd-dddd-dddddddddddd")},
        ]

        for act in activities_data:
            session.add(ActivityLog(
                owner_id=user_uuid,
                invoice_id=act["inv_id"],
                event_type=act["event_type"],
                description=act["description"],
                created_at=datetime.utcnow() - timedelta(hours=2),
            ))

        await session.commit()
        print("Database successfully seeded with demo data!")


if __name__ == "__main__":
    asyncio.run(seed())
