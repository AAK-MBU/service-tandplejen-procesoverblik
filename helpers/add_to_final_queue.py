"""Module for fetching patients that have turned 22 as of today's date"""

import os

import logging

import requests

from helpers import helper_functions

API_ADMIN_TOKEN = os.getenv("API_ADMIN_TOKEN")


def main():
    """Main function to execute the script."""

    faglig_vurdering_step_id = None
    samtykke_step_id = None
    journal_og_roentgen_afleveret_og_journaliseret_step_id = None

    base_url = "https://mbu-dashboard-api.adm.aarhuskommune.dk/api/v1"

    udskrivning_process = _find_process_by_name(base_url=base_url, process_name="Udskrivning 22 år")
    udskrivning_process_id = int(udskrivning_process.get("id"))

    for step in udskrivning_process.get("steps", []):
        name = step.get("name")

        if name == "Faglig vurdering":
            faglig_vurdering_step_id = int(step.get("id"))

        elif name == "Samtykke":
            samtykke_step_id = int(step.get("id"))

        elif name == "Journalmateriale sendt og journaliseret":
            journal_og_roentgen_afleveret_og_journaliseret_step_id = int(step.get("id"))

        if faglig_vurdering_step_id and samtykke_step_id and journal_og_roentgen_afleveret_og_journaliseret_step_id:
            break

    ready_process_runs = _find_ready_process_runs(
        base_url=base_url,
        process_id=udskrivning_process_id,
        faglig_vurdering_step_id=faglig_vurdering_step_id,
        samtykke_step_id=samtykke_step_id,
        journal_og_roentgen_afleveret_og_journaliseret_step_id=journal_og_roentgen_afleveret_og_journaliseret_step_id
    )

    logging.info(f"found {len(ready_process_runs)} ready process runs.")

    workqueue_name = "tan.udskrivning22.journal_og_roentgen_afleveret"
    workqueue = helper_functions.fetch_workqueue(workqueue_name=workqueue_name, dev=False)
    existing_refs = {str(r) for r in helper_functions.get_workqueue_item_references(workqueue=workqueue, dev=False)}

    for process_run in ready_process_runs:
        meta = process_run.get("meta", {})
        cpr = meta.get("cpr")

        if cpr in existing_refs:
            logging.info(f"Workitem for CPR {cpr} already exists in final queue — skipping creation.")

            continue

        workqueue.add_item(data={"item": {"reference": cpr, "data": meta}}, reference=cpr)

        logging.info(f"Created new workitem for CPR {cpr} in final queue.")


def _find_process_by_name(base_url: str, process_name: str):
    """
    Internal helper to find a process by name via paginated API requests.
    """

    headers = {
        "X-API-Key": os.getenv("API_ADMIN_TOKEN"),
        "Content-Type": "application/json"
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
        results = data.get("items", [])

        for process in results:
            if process.get("name") == process_name:
                return process

        # stop if there’s no next page
        if not data.get("next"):
            logging.info("Reached the last page — process not found.")

            break

        page += 1

    return None


def _find_ready_process_runs(
    base_url: str,
    process_id: int,
    faglig_vurdering_step_id: int,
    samtykke_step_id: int,
    journal_og_roentgen_afleveret_og_journaliseret_step_id: int
):
    """
    Internal helper to find a process by name via paginated API requests.
    """

    ready_process_runs = []

    headers = {
        "X-API-Key": os.getenv("API_ADMIN_TOKEN"),
        "Content-Type": "application/json"
    }

    page = 1
    size = 100

    while True:
        url = f"{base_url}/runs/?process_id={process_id}&run_status=running&order_by=created_at&sort_direction=desc&page={page}&size={size}"
        response = requests.get(url, headers=headers, timeout=10)

        if response.status_code != 200:
            logging.info(f"Request failed with status {response.status_code}")

            break

        data = response.json()
        results = data.get("items", [])
        total_pages = data.get("pages", 1)

        for process_run in results:
            faglig_vurdering_step_status = None
            samtykke_step_status = None
            journal_og_roentgen_afleveret_og_journaliseret_step_status = None

            for process_step in process_run.get("steps", []):
                process_step_status = process_step.get("status")

                if process_step.get("step_id") == faglig_vurdering_step_id:
                    faglig_vurdering_step_status = process_step_status

                elif process_step.get("step_id") == samtykke_step_id:
                    samtykke_step_status = process_step_status

                elif process_step.get("step_id") == journal_og_roentgen_afleveret_og_journaliseret_step_id:
                    journal_og_roentgen_afleveret_og_journaliseret_step_status = process_step_status

                if faglig_vurdering_step_status and samtykke_step_status and journal_og_roentgen_afleveret_og_journaliseret_step_status:
                    if faglig_vurdering_step_status == "success" and samtykke_step_status == "success" and journal_og_roentgen_afleveret_og_journaliseret_step_status == "pending":
                        ready_process_runs.append(process_run)

                    break

        if page >= total_pages:
            logging.info("Finished scanning all pages for process runs.")

            break

        page += 1

    return ready_process_runs
