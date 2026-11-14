from calendar import Calendar
from datetime import date
from urllib.parse import urlencode
import re


def month_dates(value, today):
    if not value:
        return today.replace(day=1)
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}", value):
        raise ValueError("Choose a valid calendar month.")
    year, month = map(int, value.split("-"))
    return date(year, month, 1)


def calendar_context(month, tasks, path, filters, today):
    by_day = {}
    for task in tasks:
        by_day.setdefault(task.due, []).append(task)
    weeks = []
    for week in Calendar(firstweekday=0).monthdayscalendar(month.year, month.month):
        weeks.append([{"date": date(month.year, month.month, day) if day else None,
                       "tasks": by_day.get(date(month.year, month.month, day), []) if day else []}
                      for day in week])

    def url(year, number):
        return path + "?" + urlencode({**filters, "month": f"{year:04d}-{number:02d}"})

    previous = None if month.year == 1 and month.month == 1 else url(month.year - (month.month == 1), month.month - 1 if month.month > 1 else 12)
    following = None if month.year == 9999 and month.month == 12 else url(month.year + (month.month == 12), month.month + 1 if month.month < 12 else 1)
    return {"calendar_weeks": weeks, "calendar_month": month, "month_title": month.strftime("%B %Y"),
            "previous_month_url": previous, "next_month_url": following,
            "current_month_url": url(today.year, today.month), "deadline_count": len(tasks)}
