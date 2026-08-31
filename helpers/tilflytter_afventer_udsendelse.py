"""Module for resuming paused tilflytter items once the welcome letter is ready to send.

An item pauses (pending user action) in the tilflytter-registreret queue when the RPA is
waiting for either:
  - an under-18 welcome letter to be approved - Tandplejen approves by setting the
    "Velkomstbrev" booking reminder's aftalestatus to 638 ("Tilflytter - Afsendelse
    godkendt"), or
  - a manual send by Tandplejen ("Tilflytter - Ikke tilmeldt digital post - udsend brev
    manuelt") plus a journalised "Velkomstbrev" document.

When the relevant condition is met, the item is set back to 'new' so the RPA runs it again.
Checking both conditions is safe: the RPA re-validates on re-run and re-pauses if it turns
out not to be ready, so a coarse trigger never causes a premature send.
"""

import os

import logging

from datetime import datetime

from dateutil.relativedelta import relativedelta

from automation_server_client._models import WorkItem

from mbu_solteqtand_shared_components.database.db_handler import SolteqTandDatabase

from helpers import helper_functions

SOLTEQ_TAND_DB_CONN_STRING = os.getenv("DBCONNECTIONSTRINGSOLTEQTAND")

# The queue the RPA processes with --tilflytter_registreret; paused items land here.
WORKQUEUE_NAME = "tan.tilflytter.tilflytter_registreret"

MANUAL_SEND_EVENT = "Tilflytter - Ikke tilmeldt digital post - udsend brev manuelt"
WELCOME_DOCUMENT_NAME = "Velkomstbrev"

# The booking reminder carrying the approval state. Solteq stores the aftalestatus as a
# numeric id in the database (636 "Tilflytter - Afventer godkendelse", 638 "Tilflytter -
# Afsendelse godkendt", 640 "Tilflytter - Velkomstbrev udsendt"), so DB filters use the id.
WELCOME_BOOKING_TEXT = "Velkomstbrev"
APPROVED_BOOKING_STATUS_ID = 638


def main():
    """Re-queue paused tilflytter items whose welcome letter is ready to send / has been sent."""

    workqueue = helper_functions.fetch_workqueue(WORKQUEUE_NAME)
    workitems = helper_functions.fetch_workqueue_workitems(workqueue)

    db_handler = SolteqTandDatabase(conn_str=SOLTEQ_TAND_DB_CONN_STRING)

    for item_dict in workitems:
        # Guard per item: one malformed item / transient error must not abort the whole
        # pass (which would block every other citizen's resume and degrade the service).
        try:
            if item_dict.get("status") not in ("pending user action", "failed"):
                continue

            item = WorkItem(**item_dict)

            citizen_cpr = item.data["item"]["data"]["cpr"]

            if _is_ready_to_resume(db_handler=db_handler, cpr=citizen_cpr):
                logging.info(f"Tilflytter welcome letter ready for citizen {citizen_cpr} - updating workitem status...")

                item.update_status(status="new", message="Status opdateret af service")

        except Exception:
            logging.exception("Failed to evaluate tilflytter workitem %s - skipping", item_dict.get("id"))
            continue


def _is_ready_to_resume(db_handler: SolteqTandDatabase, cpr: str) -> bool:
    """
    Phase-aware resume condition, so a consumed earlier signal never keeps re-queuing.

    The manual-send event only exists once the RPA has reached the not-registered
    branch, so its presence tells us which pause the item is currently in:
      - manual-send event exists -> manual-send phase: ready when it has been handled
        AND the welcome document exists.
      - otherwise -> approval phase: ready when the "Velkomstbrev" booking reminder has
        been set to the approved aftalestatus (638).

    (A plain "approval OR manual-send" check would keep firing on the already-given
    approval after an under-18 + not-registered citizen moves on to the manual-send pause.)
    """

    manual_send_events = db_handler.get_list_of_events(filters={"e.currentStateText": [MANUAL_SEND_EVENT], "p.cpr": cpr})

    if manual_send_events:
        one_month_ago = datetime.now() - relativedelta(months=1)

        handled = any(event["archived"] for event in manual_send_events)
        document_exists = bool(db_handler.get_documents(cpr, WELCOME_DOCUMENT_NAME, created_after=one_month_ago))

        return handled and document_exists

    return _welcome_booking_is_approved(db_handler=db_handler, cpr=cpr)


def _welcome_booking_is_approved(db_handler: SolteqTandDatabase, cpr: str) -> bool:
    """
    True if the citizen has a "Velkomstbrev" booking reminder with aftalestatus 638
    ("Tilflytter - Afsendelse godkendt"), i.e. Tandplejen has approved the send.

    Only future bookings count: the reminder is created 3 months out, so limiting the
    lookup to future bookings keeps an abandoned reminder from an earlier tilflytter run
    from being read as an approval of the current one. b.Status is not in the SELECT of
    get_list_of_bookings, but it can still be filtered on - a match is the approval.
    """

    approved_bookings = db_handler.get_list_of_bookings(
        filters={
            "p.cpr": cpr,
            "b.BookingText": WELCOME_BOOKING_TEXT,
            "b.Status": APPROVED_BOOKING_STATUS_ID,
            "b.StartTime": (">=", datetime.now()),
        }
    )

    return bool(approved_bookings)
