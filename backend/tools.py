"""LLM tools (function calling). Schemas + handlers registered inside the Pipecat pipeline."""
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.services.llm_service import FunctionCallParams

from events import bus
from store import store

check_schema = FunctionSchema(
    name="check_doctor_availability",
    description="List open appointment slots. Filter by doctor name, speciality and/or date. "
                "Call with no filters to see every doctor.",
    properties={
        "doctor_name": {"type": "string", "description": "Doctor name, e.g. 'Sharma'."},
        "speciality": {"type": "string", "description": "e.g. 'Cardiology', 'Pediatrics'."},
        "date": {"type": "string", "description": "YYYY-MM-DD, or 'today' / 'tomorrow'."},
    },
    required=[],
)

book_schema = FunctionSchema(
    name="book_appointment",
    description="Book one slot for the caller. Only call after the caller picked a slot and "
                "confirmed their phone number after you read it back.",
    properties={
        "phone_number": {"type": "string", "description": "Caller's 10-digit phone number, digits only."},
        "phone_confirmed": {"type": "boolean",
                            "description": "True only if you read the number back and the caller said yes."},
        "patient_name": {"type": "string", "description": "Caller's name."},
        "slot_id": {"type": "string", "description": "slot_id exactly as returned by check_doctor_availability."},
    },
    required=["phone_number", "phone_confirmed", "patient_name", "slot_id"],
)

cancel_schema = FunctionSchema(
    name="cancel_appointment",
    description="Cancel the caller's existing appointment, found by phone number. Only call after "
                "the caller confirmed the number and confirmed they want to cancel.",
    properties={
        "phone_number": {"type": "string", "description": "Caller's 10-digit phone number, digits only."},
        "phone_confirmed": {"type": "boolean",
                            "description": "True only if you read the number back and the caller said yes."},
        "appointment_id": {"type": "string",
                           "description": "Only needed when the caller has several appointments."},
    },
    required=["phone_number", "phone_confirmed"],
)

tools = ToolsSchema(standard_tools=[check_schema, book_schema, cancel_schema])

NOT_CONFIRMED = {"ok": False, "error": "Read the phone number back to the caller and get a yes first."}


async def check_doctor_availability(params: FunctionCallParams):
    a = params.arguments
    result = store.availability(a.get("doctor_name"), a.get("speciality"), a.get("date"))
    await params.result_callback(result)


async def book_appointment(params: FunctionCallParams):
    a = params.arguments
    if not a.get("phone_confirmed"):
        return await params.result_callback(NOT_CONFIRMED)
    result = store.book(a.get("phone_number", ""), a.get("patient_name", ""), a.get("slot_id", ""))
    if result["ok"]:
        bus.publish("appointment", {"action": "booked", "appointment": result["appointment"]})
    await params.result_callback(result)


async def cancel_appointment(params: FunctionCallParams):
    a = params.arguments
    if not a.get("phone_confirmed"):
        return await params.result_callback(NOT_CONFIRMED)
    result = store.cancel(a.get("phone_number", ""), a.get("appointment_id"))
    if result["ok"]:
        bus.publish("appointment", {"action": "cancelled", "appointment": result["appointment"]})
    await params.result_callback(result)


HANDLERS = {
    "check_doctor_availability": check_doctor_availability,
    "book_appointment": book_appointment,
    "cancel_appointment": cancel_appointment,
}
