from fastapi import APIRouter
from app.presentation.api.v1 import (
    auth,
    hotels,
    branches,
    floors,
    room_types,
    rooms,
    guests,
    reservations,
    users,
    permissions,
    services,
    hotel_services,
    housekeeping,
    finance,
    reports,
    audit_logs,
    files,
    notifications,
    amenities,
    tasks,
    problems,
    maintenance,
    expenses,
    shop,
    face,
    shifts,
    messages,
    vision,
)

api_router = APIRouter(prefix="/api/v1")

api_router.include_router(auth.router, prefix="/auth", tags=["Auth"])
api_router.include_router(hotels.router, prefix="/hotels", tags=["Hotels"])
api_router.include_router(branches.router, prefix="/branches", tags=["Branches"])
api_router.include_router(floors.router, prefix="/floors", tags=["Floors"])
api_router.include_router(room_types.router, prefix="/room-types", tags=["Room Types"])
api_router.include_router(rooms.router, prefix="/rooms", tags=["Rooms"])
api_router.include_router(guests.router, prefix="/guests", tags=["Guests"])
api_router.include_router(reservations.router, prefix="/reservations", tags=["Reservations"])
api_router.include_router(users.router, prefix="/employees", tags=["Employees"])
api_router.include_router(permissions.router, prefix="/permissions", tags=["Permissions"])
api_router.include_router(services.router, prefix="/services", tags=["Services"])
api_router.include_router(hotel_services.router, prefix="/hotel-services", tags=["Hotel Services"])
api_router.include_router(housekeeping.router, prefix="/housekeeping", tags=["Housekeeping"])
api_router.include_router(finance.router, prefix="/finance", tags=["Finance"])
api_router.include_router(reports.router, prefix="/reports", tags=["Reports"])
api_router.include_router(audit_logs.router, prefix="/audit-logs", tags=["Audit Logs"])
api_router.include_router(files.router, prefix="/files", tags=["Files"])
api_router.include_router(notifications.router, prefix="/notifications", tags=["Notifications"])
api_router.include_router(amenities.router, prefix="/amenities", tags=["Amenities"])
api_router.include_router(tasks.router, prefix="/tasks", tags=["Mobile Tasks"])
api_router.include_router(problems.router, prefix="/problems", tags=["Mobile Problems"])
api_router.include_router(maintenance.router, prefix="/maintenance", tags=["Maintenance"])
api_router.include_router(expenses.router, prefix="/expenses", tags=["Expenses"])
api_router.include_router(shop.router, prefix="/shop", tags=["Shop"])
api_router.include_router(face.router, prefix="/auth/face", tags=["Face Login"])
api_router.include_router(shifts.router, prefix="/shifts", tags=["Shifts"])
api_router.include_router(messages.router, prefix="/messages", tags=["Staff Messages"])
api_router.include_router(vision.router, prefix="/vision", tags=["Vision / Guest Face"])
