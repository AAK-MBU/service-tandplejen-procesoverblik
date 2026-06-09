"""Module that adds process runs to the final ATS queue when all prerequisites are complete."""

import os
import logging
import requests

from dataclasses import dataclass

from helpers import helper_functions


@dataclass
class ProcessConfig:
    process_name: str
    base_url: str
    success_step_names: list[str]
    pending_step_name: str
    workqueue_name: str
    dev: bool


PREREQUISITE_STEPS = ["Faglig vurdering", "Samtykke"]

PROCESS_CONFIGS = [
    ProcessConfig(
        process_name="Udskrivning 22 år",
        base_url="https://mbu-dashboard-api.adm.aarhuskommune.dk/api/v1",
        success_step_names=PREREQUISITE_STEPS,
        pending_step_name="Journalmateriale sendt og journaliseret",
        workqueue_name="tan.udskrivning22.journal_og_roentgen_afleveret",
        dev=False,
    ),
    ProcessConfig(
        process_name="Frit valg",
        base_url="https://dev-mbu-dashboard-api.adm.aarhuskommune.dk/api/v1",
        success_step_names=PREREQUISITE_STEPS,
        pending_step_name="Besked til privat tandklinik afsendt og journaliseret",
        workqueue_name="tan.fritvalg.journal_og_roentgen_afleveret",
        dev=False,
    ),
]


def main():
    """Main function to execute the script."""

    for config in PROCESS_CONFIGS:
        _process_queue(config)


def _process_queue(config: ProcessConfig):
    """Find ready process runs for one process and enqueue them."""

    process = _find_process_by_name(base_url=config.base_url, process_name=config.process_name)

    if not process:
        logging.info(f"Process '{config.process_name}' not found.")
        return

    process_id = int(process.get("id"))

    step_id_map = {}
    all_step_names = config.success_step_names + [config.pending_step_name]

    for step in process.get("steps", []):
        name = step.get("name")
        if name in all_step_names:
            step_id_map[name] = int(step.get("id"))

    success_step_ids = [step_id_map[name] for name in config.success_step_names if name in step_id_map]
    pending_step_id = step_id_map.get(config.pending_step_name)

    if len(success_step_ids) != len(config.success_step_names) or not pending_step_id:
        logging.info(f"Could not resolve all required step IDs for process '{config.process_name}'.")
        return

    ready_runs = _find_ready_process_runs(
        base_url=config.base_url,
        process_id=process_id,
        success_step_ids=success_step_ids,
        pending_step_id=pending_step_id,
    )

    logging.info(f"Found {len(ready_runs)} ready process runs for '{config.process_name}'.")

    workqueue = helper_functions.fetch_workqueue(workqueue_name=config.workqueue_name, dev=config.dev)
    existing_refs = {str(r) for r in helper_functions.get_workqueue_item_references(workqueue=workqueue, dev=config.dev)}

    for process_run in ready_runs:
        meta = process_run.get("meta", {})
        cpr = meta.get("cpr")

        if cpr in existing_refs:
            logging.info(f"Workitem for CPR {cpr} already exists in final queue — skipping creation.")
            continue

        workqueue.add_item(data={"item": {"reference": cpr, "data": meta}}, reference=cpr)
        logging.info(f"Created new workitem for CPR {cpr} in final queue.")


def _find_process_by_name(base_url: str, process_name: str):
    """Find a process by name via paginated API requests."""

    headers = {
        "X-API-Key": os.getenv("API_ADMIN_TOKEN"),
        "Content-Type": "application/json",
    }

    page = 1
    size = 100

    while True:
        url = f"{base_url}/processes/?page={page}&size={size}"
        response = requests.get(url, headers=headers, timeout=10)

        if response.status_code != 200:
            logging.info(f"Request failed with status {response.status_code}")
            break

        data = response.json()

        for process in data.get("items", []):
            if process.get("name") == process_name:
                return process

        if not data.get("next"):
            logging.info("Reached the last page — process not found.")
            break

        page += 1

    return None


def _find_ready_process_runs(
    base_url: str,
    process_id: int,
    success_step_ids: list[int],
    pending_step_id: int,
):
    """Return running process runs where all success steps are done and the target step is still pending."""

    ready_process_runs = []

    headers = {
        "X-API-Key": os.getenv("API_ADMIN_TOKEN"),
        "Content-Type": "application/json",
    }

    watched_step_ids = set(success_step_ids) | {pending_step_id}

    page = 1
    size = 100

    while True:
        url = f"{base_url}/runs/?process_id={process_id}&run_status=running&order_by=created_at&sort_direction=desc&page={page}&size={size}"
        response = requests.get(url, headers=headers, timeout=10)

        if response.status_code != 200:
            logging.info(f"Request failed with status {response.status_code}")
            break

        data = response.json()
        total_pages = data.get("pages", 1)

        for process_run in data.get("items", []):
            step_statuses = {
                step.get("step_id"): step.get("status")
                for step in process_run.get("steps", [])
                if step.get("step_id") in watched_step_ids
            }

            all_success = all(step_statuses.get(sid) == "success" for sid in success_step_ids)
            final_pending = step_statuses.get(pending_step_id) == "pending"

            if all_success and final_pending:
                ready_process_runs.append(process_run)

        if page >= total_pages:
            logging.info("Finished scanning all pages for process runs.")
            break

        page += 1

    return ready_process_runs
