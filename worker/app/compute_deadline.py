"""
worker/app/compute_deadline.py

Computes actual deadline dates from extracted timing rules.

Handles:
  - calendar_days: simple date addition
  - working_days: Mon-Fri, skipping only dates in the provided holiday_schedule
  - business_days: alias for working_days
  - weeks: 7-day multiples
  - months: calendar month addition
  - hours: date-level precision only

IMPORTANT: This module does not assume any holiday schedule by default.
Whether holidays count as non-working days depends entirely on what the
contract says. The agent is responsible for passing in the correct
holiday_schedule based on what it finds in the contract's definitions
or recognition articles. Private sector contracts often define no
holidays at all. Some define a custom list. Federal contracts may
reference the OPM schedule. Never assume.
"""

from __future__ import annotations

from datetime import date, timedelta
from dateutil.relativedelta import relativedelta


def compute_deadline(
    trigger_date: str,
    offset_days: int,
    unit: str,
    holiday_schedule: set[str] | None = None,
    work_schedule: list[int] | None = None,
) -> str:
    """
    Compute a deadline date from a trigger date, offset_days, and unit.

    Args:
        trigger_date:      ISO date string of the triggering event, e.g. "2026-05-01"
        offset_days:            Number of units, e.g. 10
        unit:              One of: calendar_days, working_days, business_days, weeks, months, hours
        holiday_schedule:  Set of ISO date strings that are non-working days under this
                           contract, e.g. {"2026-11-26", "2026-12-25"}. If None or empty,
                           working_days skips weekends only — no holidays assumed.

    Returns:
        ISO date string of the deadline, e.g. "2026-05-15"

    Raises:
        ValueError: if unit is unrecognized or trigger_date is malformed
    """

    start = date.fromisoformat(trigger_date)
    unit = unit.lower().strip()
    holidays: set[date] = (
        {date.fromisoformat(d) for d in holiday_schedule} if holiday_schedule else set()
    )

    if unit == "calendar_days":
        return (start + timedelta(days=offset_days)).isoformat()

    if unit in ("working_days", "business_days"):
        if work_schedule is None:
            raise ValueError(
                    "work_schedule is required for working_days and business_days. "
                    "Pass a list of integers (0=Monday, 6=Sunday) representing which "
                    "days of the week this bargaining unit works. Cannot assume Mon-Fri "
                    "without contract confirmation."
                )
        return _add_working_days(start, offset_days, holidays, work_schedule).isoformat()

    if unit == "weeks":
        return (start + timedelta(weeks=offset_days)).isoformat()

    if unit == "months":
        return (start + relativedelta(months=offset_days)).isoformat()

    if unit == "hours":
        # Return date only — hour-level precision is a frontend concern
        return (start + timedelta(hours=offset_days)).date().isoformat()

    raise ValueError(
        f"Unrecognized unit '{unit}'. "
        "Expected one of: calendar_days, working_days, business_days, weeks, months, hours"
    )


def _add_working_days(
    start: date,
    days: int,
    holidays: set[date],
    work_schedule: list[int],
) -> date:
    """
    Add `days` working days to `start`.

    Working days are defined by work_schedule — a list of weekday integers
    (0=Monday, 6=Sunday) representing which days this bargaining unit works.
    Never assumes Mon-Fri. A casino might pass [0,1,2,3,4,5,6]; a school
    cafeteria might pass [0,1,2,3,4].

    Skips dates in holidays — non-working days explicitly defined by the
    contract. Holidays with premium pay are NOT in holidays because the
    employee still works that day.
    """
    working_days_set = set(work_schedule)
    current = start
    remaining = days

    while remaining > 0:
        current += timedelta(days=1)
        if current.weekday() in working_days_set and current not in holidays:
            remaining -= 1

    return current


def describe_deadline(
    trigger_date: str,
    offset_days: int,
    unit: str,
    action: str,
    party: str,
    holiday_schedule: set[str] | None = None,
    work_schedule: list[int] | None = None,
) -> dict:
    """
    Compute a deadline and return a human-readable description.

    Args:
        trigger_date:      ISO date string of the triggering event
        offset_days:            Number of units
        unit:              calendar_days, working_days, etc.
        action:            What must happen, e.g. "file grievance at Step 1"
        party:             Who must act, e.g. "employee"
        holiday_schedule:  Set of ISO date strings that are non-working days
                           under this contract. If None, no holidays assumed.

    Returns:
        Dict with deadline date, day of week, plain-English description,
        and a warning if the deadline falls on a weekend or a contract holiday.
    """
    deadline_str = compute_deadline(
            trigger_date, offset_days, unit, holiday_schedule, work_schedule
        )
    deadline = date.fromisoformat(deadline_str)
    day_of_week = deadline.strftime("%A")

    holidays: set[date] = (
        {date.fromisoformat(d) for d in holiday_schedule} if holiday_schedule else set()
    )

    warning = ""
    if deadline in holidays:
        warning = (
            f" Note: {deadline_str} is a contract-defined holiday — "
            "verify whether this extends the deadline under your contract."
        )
    elif deadline.weekday() >= 5:
        warning = (
            f" Note: {deadline_str} falls on a {day_of_week} — "
            "verify whether weekends extend this deadline under your contract."
        )

    holiday_schedule_note = (
        f" Holiday schedule applied: {sorted(holiday_schedule)}."
        if holiday_schedule
        else " No holiday schedule applied — contract did not define non-working holidays."
    )

    description = (
        f"{party.capitalize()} must {action} by {deadline_str} ({day_of_week}), "
        f"which is {offset_days} {unit.replace('_', ' ')} after {trigger_date}."
        f"{warning}{holiday_schedule_note}"
    )

    return {
        "deadline": deadline_str,
        "day_of_week": day_of_week,
        "offset_days": offset_days,
        "unit": unit,
        "action": action,
        "party": party,
        "trigger_date": trigger_date,
        "holiday_schedule": sorted(holiday_schedule) if holiday_schedule else [],
        "description": description,
        "warning": warning.strip(),
    }
