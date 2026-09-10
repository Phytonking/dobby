from urllib.parse import quote
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import AuthorizedSession
from .models import UserError

SCOPES = ["https://www.googleapis.com/auth/calendar.events"]


class Calendar:
    def __init__(self, config):
        credentials = Credentials.from_authorized_user_file(config.token_file, SCOPES)
        self.session = AuthorizedSession(credentials)
        self.base = (
            "https://www.googleapis.com/calendar/v3/calendars/" + quote(config.calendar, safe="") + "/events"
        )

    def call(self, method, event_id="", **kwargs):
        url = self.base + ("/" + quote(event_id, safe="") if event_id else "")
        response = self.session.request(method, url, timeout=30, **kwargs)
        if response.status_code == 412:
            raise UserError("This event changed since the preview. Run /schedule again.")
        if response.status_code in (404, 410):
            raise UserError("Event not found or already deleted. Refresh /events.")
        if response.status_code == 409:
            raise UserError("This request may already have completed. Check /events before retrying.")
        if not response.ok:
            raise UserError(
                f"Google Calendar request failed (HTTP {response.status_code}). "
                "Check account access, quota and OAuth authorization."
            )
        return response.json() if response.content else {}

    def get(self, event_id):
        return self.call("GET", event_id)

    def list(self, start, end):
        result, token = [], None
        while True:
            page = self.call(
                "GET",
                params={
                    "timeMin": start,
                    "timeMax": end,
                    "singleEvents": "true",
                    "orderBy": "startTime",
                    "maxResults": 250,
                    **({"pageToken": token} if token else {}),
                },
            )
            result.extend(page.get("items", []))
            token = page.get("nextPageToken")
            if not token:
                return result
            if len(result) > 5000:
                raise UserError("Too many events in this time range; use a shorter range.")

    def check_conflicts(self, body, event_id=None):
        if "start" not in body:
            return
        events = self.list(body["start"]["dateTime"], body["end"]["dateTime"])
        busy = [
            e
            for e in events
            if e["id"] != event_id
            and e.get("status") != "cancelled"
            and e.get("transparency") != "transparent"
            and not any(
                a.get("self") and a.get("responseStatus") == "declined" for a in e.get("attendees", [])
            )
        ]
        if busy:
            raise UserError("The linked calendar is busy during this time. Choose another slot.")

    def apply(self, proposal):
        if proposal.action != "delete":
            self.check_conflicts(proposal.body, proposal.event_id)
        params = {"sendUpdates": "all"}
        if proposal.action == "create":
            return self.call("POST", params=params, json={**proposal.body, "id": proposal.operation_id})
        headers = {"If-Match": proposal.etag}
        if proposal.action == "delete":
            return self.call("DELETE", proposal.event_id, params=params, headers=headers)
        return self.call(
            "PATCH",
            proposal.event_id,
            params={**params, "conferenceDataVersion": 1},
            headers=headers,
            json=proposal.body,
        )
