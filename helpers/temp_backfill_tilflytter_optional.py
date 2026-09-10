"""TEMPORARY backfill - delete this module and its call in service.py once it is done.

`get_forms.py` marks "Borger har valgt privat tandklinik" as optional when a tilflytter
formular is submitted, but that logic did not exist when the ~300 earlier submissions were
fetched and processed. Their runs still carry the step unsettled, so it holds them open.

This pass applies the same status to those older runs: a running tilflytter run that got
through the formular steps, but whose private-clinic step was never settled, is one the
new logic would have marked optional.

The backfill is finished once the summary line reports 0 updated runs.
"""

import os

import logging

from mbu_process_dashboard_shared_components.process_dashboard_client import ProcessDashboardClient

from mbu_process_dashboard_shared_components import process, process_run

from helpers import helper_functions

PROCESS_NAME = "Tilflytter til Aarhus Kommune"

# Both must be success: that is what tells us the citizen went through the tilflytter
# formular, which is the case the new get_forms logic covers.
REQUIRED_SUCCESS_STEP_NAMES = ("Formular indsendt", "Formular journaliseret")

TARGET_STEP_NAME = "Borger har valgt privat tandklinik"
TARGET_STEP_STATUSES = {TARGET_STEP_NAME: "optional"}

# Statuses meaning the step is already settled and must be left alone. "success" is the
# genuine fritvalg case - the citizen really did choose a private tandklinik - and
# "optional" is a run this backfill has already handled, which is what stops it from
# re-patching the same runs on every 5 minute pass.
SETTLED_STATUSES = ("success", "optional", "cancelled")


def main():
    """Mark the private-clinic step optional on tilflytter runs that predate the get_forms change."""

    client = ProcessDashboardClient(api_admin_token=os.getenv("API_ADMIN_TOKEN"))

    process_id, process_steps = process.find_process_id_and_steps(client=client, process_name=PROCESS_NAME)

    if not process_id:
        logging.info(f"Process '{PROCESS_NAME}' not found - skipping backfill.")

        return

    step_id_map = {
        step.get("name"): step.get("id")
        for step in process_steps
    }

    required_step_ids = [step_id_map.get(step_name) for step_name in REQUIRED_SUCCESS_STEP_NAMES]

    target_step_id = step_id_map.get(TARGET_STEP_NAME)

    if target_step_id is None or any(step_id is None for step_id in required_step_ids):
        logging.warning(f"Could not resolve all step ids for '{PROCESS_NAME}' - skipping backfill.")

        return

    running_process_runs = process_run.get_all_process_runs(client=client, process_id=process_id, run_status="running")

    updated_runs = 0

    for run in running_process_runs:
        # Guard per run: one malformed run / transient error must not abort the backfill.
        try:
            step_statuses = {
                step.get("step_id"): step.get("status")
                for step in run.get("steps") or []
            }

            if not all(step_statuses.get(step_id) == "success" for step_id in required_step_ids):
                continue

            if step_statuses.get(target_step_id) in SETTLED_STATUSES:
                continue

            logging.info(f"Backfilling '{TARGET_STEP_NAME}' as optional on process run {run.get('id')}...")

            helper_functions.update_process_run_steps(
                client=client,
                process_steps=process_steps,
                process_run=run,
                step_statuses=TARGET_STEP_STATUSES,
            )

            updated_runs += 1

        except Exception:
            logging.exception("Failed to backfill tilflytter process run %s - skipping", run.get("id"))

            continue

    logging.info(
        f"Backfill: updated {updated_runs} of {len(running_process_runs)} running '{PROCESS_NAME}' runs "
        "- when this reaches 0 the backfill is done and this step can be deleted."
    )
