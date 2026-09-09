"""Module for cancelling tilflytter process runs that Tandplejen has called off.

A tilflytter citizen has a "Velkomstbrev" booking reminder that carries the send state as
a numeric aftalestatus (636 "Tilflytter - Afventer godkendelse", 638 "Tilflytter -
Afsendelse godkendt", 640 "Tilflytter - Velkomstbrev udsendt"). Setting it to 642 is how
Tandplejen stops a tilflytter process run: it is a deliberate kill switch, not a send
state, so it applies at any point in the run - including after the welcome letter has
already gone out and "Velkomstbrev sendt" is marked success. A run is therefore never
skipped because of how far it has progressed.

Cancelling the "Velkomstbrev sendt" step run cancels the whole process run, which is the
wanted outcome - so this module only has to cancel that one step.
"""

import os

import logging

from datetime import datetime

from mbu_process_dashboard_shared_components.process_dashboard_client import ProcessDashboardClient

from mbu_process_dashboard_shared_components import process, process_run

from mbu_solteqtand_shared_components.database.db_handler import SolteqTandDatabase

from helpers import helper_functions

SOLTEQ_TAND_DB_CONN_STRING = os.getenv("DBCONNECTIONSTRINGSOLTEQTAND")

PROCESS_NAME = "Tilflytter til Aarhus Kommune"

# The booking reminder carrying the send state, and the aftalestatus id meaning the send
# has been called off. Solteq stores the aftalestatus as a numeric id, so DB filters use the id.
WELCOME_BOOKING_TEXT = "Velkomstbrev"
CANCELLED_BOOKING_STATUS_ID = 642

# Cancelling this step run cancels the whole process run.
WELCOME_SENT_STEP_NAME = "Velkomstbrev sendt"
CANCELLED_STEP_STATUSES = {WELCOME_SENT_STEP_NAME: "cancelled"}


def main():
    """Cancel running tilflytter process runs whose "Velkomstbrev" reminder has aftalestatus 642."""

    client = ProcessDashboardClient(api_admin_token=os.getenv("API_ADMIN_TOKEN"))

    process_id, process_steps = process.find_process_id_and_steps(client=client, process_name=PROCESS_NAME)

    if not process_id:
        logging.info(f"Process '{PROCESS_NAME}' not found - skipping.")

        return

    welcome_sent_step_id = next(
        (
            step.get("id")
            for step in process_steps
            if step.get("name") == WELCOME_SENT_STEP_NAME
        ),
        None
    )

    if welcome_sent_step_id is None:
        logging.warning(f"Step '{WELCOME_SENT_STEP_NAME}' not found in process '{PROCESS_NAME}' - skipping.")

        return

    running_process_runs = process_run.get_all_process_runs(client=client, process_id=process_id, run_status="running")

    logging.info(f"Found {len(running_process_runs)} running '{PROCESS_NAME}' process runs.")

    db_handler = SolteqTandDatabase(conn_str=SOLTEQ_TAND_DB_CONN_STRING)

    for run in running_process_runs:
        # Guard per run: one malformed run / transient error must not abort the whole pass
        # (which would leave every other citizen's run open as well).
        try:
            citizen_cpr = (run.get("meta") or {}).get("cpr") or run.get("entity_id")

            if not citizen_cpr:
                logging.warning(f"No CPR on process run {run.get('id')} - skipping.")

                continue

            # Purely idempotency: the step is already cancelled, so there is nothing left
            # to cancel. Any other status - success included - is still fair game, since
            # 642 is a kill switch that applies however far the run has got.
            if _welcome_sent_step_is_cancelled(process_run_data=run, welcome_sent_step_id=welcome_sent_step_id):
                continue

            run_started_at = _run_started_at(process_run_data=run)

            if run_started_at is None:
                logging.warning(f"No usable start timestamp on process run {run.get('id')} - skipping.")

                continue

            if not _welcome_booking_is_cancelled(db_handler=db_handler, cpr=citizen_cpr, run_started_at=run_started_at):
                continue

            logging.info(f"Tilflytter welcome letter called off for citizen {citizen_cpr} - cancelling process run {run.get('id')}...")

            helper_functions.update_process_run_steps(
                client=client,
                process_steps=process_steps,
                process_run=run,
                step_statuses=CANCELLED_STEP_STATUSES,
            )

        except Exception:
            logging.exception("Failed to evaluate tilflytter process run %s - skipping", run.get("id"))

            continue


def _welcome_sent_step_is_cancelled(process_run_data: dict, welcome_sent_step_id: int) -> bool:
    """True if the "Velkomstbrev sendt" step run on this process run is already cancelled."""

    return any(
        step.get("step_id") == welcome_sent_step_id and step.get("status") == "cancelled"
        for step in process_run_data.get("steps") or []
    )


def _run_started_at(process_run_data: dict) -> datetime | None:
    """
    The process run's start time, as a naive local datetime.

    The dashboard returns UTC ISO timestamps ("...Z"), while the Solteq DB stores naive
    local time - the same assumption the sibling module's `datetime.now()` filter makes -
    so the value is converted to local time to keep the comparison honest. Returns None if
    neither timestamp is present or parseable, which leaves the caller to skip the run
    rather than fall back to an unbounded lookup.
    """

    raw = process_run_data.get("started_at") or process_run_data.get("created_at")

    if not raw:
        return None

    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))

    except ValueError:
        logging.warning(f"Could not parse start timestamp '{raw}' on process run {process_run_data.get('id')}.")

        return None

    if parsed.tzinfo is None:
        return parsed

    return parsed.astimezone().replace(tzinfo=None)


def _welcome_booking_is_cancelled(db_handler: SolteqTandDatabase, cpr: str, run_started_at: datetime) -> bool:
    """
    True if the citizen has a "Velkomstbrev" booking reminder with aftalestatus 642,
    i.e. Tandplejen has called off this process run.

    Only reminders last modified since the run started count. 642 is set by hand during
    the run, so that bound is what separates a cancellation of *this* run from an
    abandoned reminder left at 642 by an earlier tilflytter cycle (a citizen who moved
    away and back). Bounding on b.StartTime instead would miss a late cancellation, once
    the reminder date has passed - and by then the letter has often already been sent,
    which is exactly when Tandplejen still wants to be able to stop the run.

    b.Status is not in the SELECT of get_list_of_bookings, but it can still be filtered
    on - a match means the run was called off.
    """

    cancelled_bookings = db_handler.get_list_of_bookings(
        filters={
            "p.cpr": cpr,
            "b.BookingText": WELCOME_BOOKING_TEXT,
            "b.Status": CANCELLED_BOOKING_STATUS_ID,
            "b.LastModifiedDateTime": (">=", run_started_at),
        }
    )

    return bool(cancelled_bookings)
