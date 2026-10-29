"""
Seed script to populate clean, real-world data (Profile, Clients, Invoices, Activities).
Uses realistic UUIDs and real test emails.
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import uuid

from sqlalchemy import delete
from app.core.database import engine, AsyncSessionLocal, Base
from app.core.security import DEMO_USER_ID
from app.models import (
    BusinessProfile, Client, Invoice, ActivityLog,
    ReminderAttempt, PaymentPromise, PaymentReview, IncomingMessage, ReplyClassification
)


async def seed():
    print("Resetting and seeding clean real-world data in Supabase...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSessionLocal() as session:
        # Clear existing tables cleanly
        await session.execute(delete(ActivityLog))
        await session.execute(delete(ReminderAttempt))
        await session.execute(delete(PaymentPromise))
        await session.execute(delete(PaymentReview))
        await session.execute(delete(ReplyClassification))
        await session.execute(delete(IncomingMessage))
        await session.execute(delete(Invoice))
        await session.execute(delete(Client))
        await session.execute(delete(BusinessProfile))
        await session.commit()

        user_uuid = uuid.UUID(DEMO_USER_ID)

        # 1. Business Profile
        profile = BusinessProfile(
            id=user_uuid,
            name="Harsh Malviya",
            email="harsh@nexusdigital.studio",
            company="Nexus Digital Studio",
            address="742 Evergreen Terrace, Sector 4, Tech Park",
        )
        session.add(profile)
        print("Created Business Profile.")

        # 2. Clients with Real Emails & Details
        clients_data = [
            {
                "id": uuid.uuid4(),
                "name": "Riley Johnson",
                "email": "harshmal82260@gmail.com",
                "company": "Johnson Media Group",
                "phone": "+1 (555) 234-5678",
                "notes": "Key digital advertising account. Monthly retainer.",
            },
            {
                "id": uuid.uuid4(),
                "name": "Jamie Brown",
                "email": "harsh.malviya@vkaps.com",
                "company": "Brown Logistics Ltd",
                "phone": "+1 (555) 876-5432",
                "notes": "Supply chain dashboard redesign client.",
            },
            {
                "id": uuid.uuid4(),
                "name": "Avery Taylor",
                "email": "malviyaharsh500@gmail.com",
                "company": "Vanguard Web Studio",
                "phone": "+1 (555) 345-6789",
                "notes": "Brand identity and UI kit project.",
            },
            {
                "id": uuid.uuid4(),
                "name": "Casey Wilson",
                "email": "futuretechdotcodotin@gmail.com",
                "company": "FutureTech Enterprise",
                "phone": "+1 (555) 987-6543",
                "notes": "Custom AI bot integration project.",
            },
        ]

        created_clients = []
        for c in clients_data:
            client_obj = Client(
                id=c["id"],
                owner_id=user_uuid,
                name=c["name"],
                email=c["email"],
                company=c["company"],
                phone=c["phone"],
                notes=c["notes"],
            )
            session.add(client_obj)
            created_clients.append(client_obj)

        await session.flush()
        print(f"Ensured {len(created_clients)} realistic clients.")

        # 3. Invoices with Real Client IDs
        today = date.today()
        invoices_data = [
            {
                "id": uuid.uuid4(),
                "invoice_number": "INV-103",
                "client": created_clients[0],  # Riley Johnson
                "amount": Decimal("2900.00"),
                "currency": "USD",
                "invoice_date": today - timedelta(days=28),
                "due_date": today - timedelta(days=6),
                "status": "OVERDUE",
                "reminder_count": 1,
            },
            {
                "id": uuid.uuid4(),
                "invoice_number": "INV-105",
                "client": created_clients[1],  # Jamie Brown
                "amount": Decimal("4400.00"),
                "currency": "USD",
                "invoice_date": today - timedelta(days=45),
                "due_date": today - timedelta(days=13),
                "status": "OVERDUE",
                "reminder_count": 2,
            },
            {
                "id": uuid.uuid4(),
                "invoice_number": "INV-108",
                "client": created_clients[2],  # Avery Taylor
                "amount": Decimal("2500.00"),
                "currency": "USD",
                "invoice_date": today - timedelta(days=20),
                "due_date": today - timedelta(days=8),
                "status": "OVERDUE",
                "reminder_count": 1,
            },
            {
                "id": uuid.uuid4(),
                "invoice_number": "INV-102",
                "client": created_clients[3],  # Casey Wilson
                "amount": Decimal("7000.00"),
                "currency": "USD",
                "invoice_date": today - timedelta(days=10),
                "due_date": today + timedelta(days=5),
                "status": "PENDING",
                "reminder_count": 0,
            },
        ]

        created_invoices = []
        for inv in invoices_data:
            inv_obj = Invoice(
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
                last_reminder_at=datetime.now(timezone.utc) - timedelta(days=2) if inv["reminder_count"] > 0 else None,
            )
            session.add(inv_obj)
            created_invoices.append(inv_obj)

        await session.flush()
        print(f"Ensured {len(created_invoices)} invoices.")

        # 4. Activity Logs with Clean foreign keys
        now = datetime.now(timezone.utc)
        activities_data = [
            {
                "event_type": "INVOICE_CREATED",
                "description": "Invoice INV-103 ($2,900) created for Riley Johnson (harshmal82260@gmail.com)",
                "inv_id": created_invoices[0].id,
                "client_id": created_clients[0].id,
            },
            {
                "event_type": "REMINDER_SENT",
                "description": "Reminder 1 dispatched to Riley Johnson (harshmal82260@gmail.com) · INV-103",
                "inv_id": created_invoices[0].id,
                "client_id": created_clients[0].id,
            },
            {
                "event_type": "REMINDER_SENT",
                "description": "Reminder 2 dispatched to Jamie Brown (harsh.malviya@vkaps.com) · INV-105",
                "inv_id": created_invoices[1].id,
                "client_id": created_clients[1].id,
            },
            {
                "event_type": "REMINDER_SENT",
                "description": "Reminder 1 dispatched to Avery Taylor (malviyaharsh500@gmail.com) · INV-108",
                "inv_id": created_invoices[2].id,
                "client_id": created_clients[2].id,
            },
        ]

        for act in activities_data:
            session.add(ActivityLog(
                owner_id=user_uuid,
                invoice_id=act["inv_id"],
                client_id=act["client_id"],
                event_type=act["event_type"],
                description=act["description"],
                created_at=now - timedelta(hours=3),
            ))

        await session.commit()
        print("Database cleanly re-seeded with realistic data!")


if __name__ == "__main__":
    asyncio.run(seed())
