"""Mock clinic data, kept in memory. Restarting the server resets everything."""
import itertools
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
WINDOW_DAYS = 5

DOCTORS = [
    {"id": "d1", "name": "Dr. Ananya Sharma", "speciality": "Cardiology", "experience_years": 14,
     "times": ["09:00", "09:30", "10:00", "11:30", "15:00", "16:30"]},
    {"id": "d2", "name": "Dr. Rohan Mehta", "speciality": "Dermatology", "experience_years": 9,
     "times": ["10:00", "10:30", "12:00", "14:00", "17:00"]},
    {"id": "d3", "name": "Dr. Priya Nair", "speciality": "Pediatrics", "experience_years": 11,
     "times": ["09:00", "10:30", "11:00", "15:30", "16:00"]},
    {"id": "d4", "name": "Dr. Imran Qureshi", "speciality": "Orthopedics", "experience_years": 17,
     "times": ["11:00", "11:30", "13:00", "14:30", "18:00"]},
    {"id": "d5", "name": "Dr. Kavita Rao", "speciality": "General Medicine", "experience_years": 8,
     "times": ["09:00", "09:30", "10:00", "10:30", "16:00", "16:30", "17:00"]},
    {"id": "d6", "name": "Dr. Arjun Iyer", "speciality": "ENT", "experience_years": 12,
     "times": ["10:00", "12:30", "15:00", "17:30"]},
]
DOCTOR_BY_ID = {d["id"]: d for d in DOCTORS}


def normalize_phone(raw: str) -> str | None:
    """Return a clean 10-digit Indian mobile number, or None if it doesn't look valid."""
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    return digits if len(digits) == 10 else None


def _time12(hhmm: str) -> str:
    return datetime.strptime(hhmm, "%H:%M").strftime("%I:%M %p").lstrip("0")


def _label(d: datetime, hhmm: str) -> str:
    return f"{d.strftime('%a %d %b')}, {_time12(hhmm)}"


class Store:
    def __init__(self):
        self._ids = itertools.count(1001)
        self.appointments: list[dict] = []
        # One demo booking so the cancel flow can be tried straight away (phone 9876543210).
        tomorrow = self._days()[1]
        self._create("9876543210", "Demo Patient", self._slot_id("d2", tomorrow, "10:00"))

    # ---------- slots ----------
    def _days(self) -> list[datetime]:
        today = datetime.now(IST).replace(hour=0, minute=0, second=0, microsecond=0)
        out, i = [], 0
        while len(out) < WINDOW_DAYS:
            d = today + timedelta(days=i)
            if d.weekday() != 6:  # clinic closed on Sundays
                out.append(d)
            i += 1
        return out

    @staticmethod
    def _slot_id(doctor_id: str, day: datetime, hhmm: str) -> str:
        return f"{doctor_id}-{day.strftime('%Y%m%d')}-{hhmm.replace(':', '')}"

    def _booked_slot_ids(self) -> set[str]:
        return {a["slot_id"] for a in self.appointments if a["status"] == "booked"}

    def _slots_for(self, doctor: dict, only_date: str | None = None) -> list[dict]:
        now = datetime.now(IST)
        taken = self._booked_slot_ids()
        slots = []
        for day in self._days():
            if only_date and day.strftime("%Y-%m-%d") != only_date:
                continue
            for t in doctor["times"]:
                start = day.replace(hour=int(t[:2]), minute=int(t[3:]))
                sid = self._slot_id(doctor["id"], day, t)
                if start <= now or sid in taken:
                    continue
                slots.append({"slot_id": sid, "date": day.strftime("%Y-%m-%d"), "time": t,
                              "label": _label(day, t)})
        return slots

    def _find_slot(self, slot_id: str) -> tuple[dict, dict] | None:
        doctor = DOCTOR_BY_ID.get(slot_id.split("-")[0])
        if not doctor:
            return None
        for s in self._slots_for(doctor):
            if s["slot_id"] == slot_id:
                return doctor, s
        return None

    # ---------- queries ----------
    def doctors_with_slots(self) -> list[dict]:
        return [{k: v for k, v in d.items() if k != "times"} | {"slots": self._slots_for(d)}
                for d in DOCTORS]

    def availability(self, doctor_name=None, speciality=None, date=None, limit=8) -> dict:
        if date:
            low = date.strip().lower()
            today = datetime.now(IST)
            if low == "today":
                date = today.strftime("%Y-%m-%d")
            elif low == "tomorrow":
                date = (today + timedelta(days=1)).strftime("%Y-%m-%d")
        matches = DOCTORS
        if doctor_name:
            q = re.sub(r"^dr\.?\s*", "", doctor_name.strip().lower())
            matches = [d for d in matches if q and q in d["name"].lower()]
        if speciality:
            q = speciality.strip().lower()
            matches = [d for d in matches if q in d["speciality"].lower() or d["speciality"].lower() in q]
        if not matches:
            return {"ok": False, "error": "No matching doctor.",
                    "doctors": [{"name": d["name"], "speciality": d["speciality"]} for d in DOCTORS]}
        result = []
        for d in matches:
            slots = self._slots_for(d, date)[:limit]
            result.append({"doctor_name": d["name"], "speciality": d["speciality"], "slots": slots})
        return {"ok": True, "date_filter": date, "doctors": result}

    def appointments_view(self) -> list[dict]:
        return sorted(self.appointments, key=lambda a: a["id"], reverse=True)

    # ---------- actions ----------
    def _create(self, phone: str, patient_name: str, slot_id: str) -> dict:
        doctor, slot = self._find_slot(slot_id)
        appt = {"id": f"A{next(self._ids)}", "patient_name": patient_name, "phone": phone,
                "doctor_id": doctor["id"], "doctor_name": doctor["name"],
                "speciality": doctor["speciality"], "slot_id": slot_id, "date": slot["date"],
                "time": slot["time"], "label": slot["label"], "status": "booked"}
        self.appointments.append(appt)
        return appt

    def book(self, phone_raw: str, patient_name: str, slot_id: str) -> dict:
        phone = normalize_phone(phone_raw)
        if not phone:
            return {"ok": False, "error": "Phone number must be 10 digits. Ask the caller to repeat it."}
        if not self._find_slot(slot_id):
            return {"ok": False, "error": "That slot is not available. Check availability again."}
        return {"ok": True, "appointment": self._create(phone, patient_name.strip() or "Caller", slot_id)}

    def cancel(self, phone_raw: str, appointment_id: str | None = None) -> dict:
        phone = normalize_phone(phone_raw)
        if not phone:
            return {"ok": False, "error": "Phone number must be 10 digits. Ask the caller to repeat it."}
        active = [a for a in self.appointments if a["phone"] == phone and a["status"] == "booked"]
        if not active:
            return {"ok": False, "error": "No active appointment found for this number."}
        if appointment_id:
            active = [a for a in active if a["id"] == appointment_id]
            if not active:
                return {"ok": False, "error": "That appointment id does not belong to this number."}
        elif len(active) > 1:
            return {"ok": False, "needs_choice": True,
                    "message": "Several appointments found. Ask which one to cancel.",
                    "appointments": [{"id": a["id"], "doctor_name": a["doctor_name"], "label": a["label"]}
                                     for a in active]}
        active[0]["status"] = "cancelled"
        return {"ok": True, "appointment": active[0]}


store = Store()
