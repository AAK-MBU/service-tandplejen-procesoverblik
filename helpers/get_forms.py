"""Module to get forms from the SQL database."""

import os

import logging

from mbu_process_dashboard_shared_components.process_dashboard_client import ProcessDashboardClient

from mbu_process_dashboard_shared_components import process

from helpers import helper_functions


TILFLYTTER_PROCESS_NAME = "Tilflytter til Aarhus Kommune"

# Steps to update on an active tilflytter process run when the citizen instead chooses fritvalg
TILFLYTTER_FRITVALG_STEP_STATUSES = {
    "Borger har valgt privat tandklinik": "success",
    "Formular indsendt": "cancelled",
    "Formular journaliseret": "cancelled",
}

# The mirror case: a tilflytter formular means the citizen stayed with the municipal
# clinic, so the private-clinic step will never happen on that run. "optional" marks it
# as not applicable rather than done or cancelled, so it does not hold the run open.
TILFLYTTER_OWN_FORM_STEP_STATUSES = {
    "Borger har valgt privat tandklinik": "optional",
}

# The three workqueues this step feeds. A submission's status in the database is no
# longer updated once it has been handled, so what tells us whether a submission has
# already been queued is the target workqueue's own references - fetched once per pass
# into a set per queue, rather than re-read for every submission.
WORKQUEUE_UDSKRIVNING_22 = "jou.solteqtand.udskrivning_22"
WORKQUEUE_TILFLYTTER = "jou.solteqtand.tilflytter"
WORKQUEUE_FRITVALG = "tan.fritvalg.fritvalg_registreret"

TARGET_WORKQUEUE_NAMES = (WORKQUEUE_UDSKRIVNING_22, WORKQUEUE_TILFLYTTER, WORKQUEUE_FRITVALG)


def main():
    """
    Fetch forms for 4 tandpleje formulars
    """

    connection_string = os.environ.get("DBCONNECTIONSTRINGPROD")

    sql = """
        SELECT
            -- Fields for all formulars
            form_id,
            form_type,
            form_data,
            status,
            COALESCE(JSON_VALUE([view_Journalizing].form_data, '$.data.cpr_nummer_borger'), JSON_VALUE([view_Journalizing].form_data, '$.data.borger_cpr_nummer_manuelt')) AS citizen_cpr,
            JSON_VALUE([view_Journalizing].form_data, '$.data.vaelg_tandlaege_api') AS vaelg_tandlaege_api,
            JSON_VALUE([view_Journalizing].form_data, '$.data.tandlaege_navn_manuelt') AS tandlaege_navn_manuelt,
            JSON_VALUE([view_Journalizing].form_data, '$.data.tandlaege_adresse__dawa') AS tandlaege_adresse_manuelt,
            JSON_VALUE([view_Journalizing].form_data, '$.data.tandlaege_ydernummer_manuelt') AS tandlaege_ydernummer_manuelt,
            (SELECT TOP 1 JSON_VALUE(a.value, '$.url') FROM OPENJSON(JSON_QUERY([view_Journalizing].form_data, '$.data.attachments')) a) AS url,

            JSON_VALUE([view_Journalizing].form_data, '$.data.tandlaege_telefonnummer_manuelt') AS tandlaege_telefonnummer_manuelt,

            -- Fields for udskrivning 22 år
            JSON_VALUE([view_Journalizing].form_data, '$.data.samtykke_valg') AS samtykke_valg,

            -- Fields for tilflytter
            JSON_VALUE([view_Journalizing].form_data, '$.data.borger_telefonnummer') AS borger_telefonnummer,
            JSON_VALUE([view_Journalizing].form_data, '$.data.behandling_samtykke_svarer_paa_vegne_af_barn_valg') AS behandling_samtykke,
            JSON_VALUE([view_Journalizing].form_data, '$.data.cpr_nummer_anden_foraeldremyndighed') AS cpr_nummer_anden_foraeldremyndighed,
            JSON_VALUE([view_Journalizing].form_data, '$.data.anden_foraeldermyndighed_telefonnummer_manuelt') AS anden_foraeldermyndighed_telefonnummer_manuelt,
            JSON_VALUE([view_Journalizing].form_data, '$.data.kommunevaelger') AS kommunevaelger,
            JSON_VALUE([view_Journalizing].form_data, '$.data.kommunal_tandklinik_navn_manuelt') AS kommunal_tandklinik_navn_manuelt,

            -- Fields for both tilflytter and fritvalg
            JSON_VALUE([view_Journalizing].form_data, '$.data.borger_navn') AS borger_navn,
            JSON_VALUE([view_Journalizing].form_data, '$.data.barnets_navn') AS barnets_navn,
            JSON_VALUE([view_Journalizing].form_data, '$.data.cpr_nummer_barnet') AS cpr_nummer_barnet,
            COALESCE(NULLIF(TRIM(JSON_VALUE([view_Journalizing].form_data, '$.data.journal_samtykke_borger_valg')), ''), NULLIF(TRIM(JSON_VALUE([view_Journalizing].form_data, '$.data.journal_samtykke_svarer_paa_vegne_af_barn_valg')), '')) AS journal_samtykke_valg

        FROM [RPA].[journalizing].[view_Journalizing]
        WHERE
            form_type in (
                'udskrivning_22_aar_privat_tandkl',
                'udskrivning_22_aar_tandpleje_for',
                'tilflytter_til_aarhus_kommune_sa',
                'fritvalgsordning_samlet_formular'
            )
            AND status = 'New'
        ORDER BY
            form_submitted_date DESC
    """

    items = helper_functions.get_items_from_query_with_params(connection_string=connection_string, query=sql, params=[])

    dev = False

    workqueues = {}
    existing_refs = {}

    for target_workqueue_name in TARGET_WORKQUEUE_NAMES:
        workqueue = helper_functions.fetch_workqueue(workqueue_name=target_workqueue_name, dev=dev)

        workqueues[target_workqueue_name] = workqueue
        existing_refs[target_workqueue_name] = {str(r) for r in helper_functions.get_workqueue_item_references(workqueue, dev=dev)}

        logging.info(f"Fetched {len(existing_refs[target_workqueue_name])} existing references from '{target_workqueue_name}'.")

    for sub in items:
        form_data = sub.get("form_data")
        if "purged" in form_data:
            continue

        workqueue_name = ""

        form_id = sub.get("form_id")

        form_type = sub.get("form_type")

        udfylder_cpr = sub.get("citizen_cpr")

        if sub.get("vaelg_tandlaege_api"):
            parts = [p.strip() for p in sub["vaelg_tandlaege_api"].split("||")]
            klinik_navn, klinik_adresse, klinik_ydernummer = parts

        else:
            klinik_navn = sub.get("tandlaege_navn_manuelt")
            klinik_adresse = sub.get("tandlaege_adresse_manuelt")
            klinik_ydernummer = sub.get("tandlaege_ydernummer_manuelt", None)

        url = sub.get("url")

        patient_data_dict = {
            "cpr": "",
            "form_id": form_id,
            "form_type": form_type,
            "form_data": form_data,
            "klinik_navn": klinik_navn,
            "klinik_adresse": klinik_adresse,
            "klinik_ydernummer": klinik_ydernummer,
            "url": url
        }

        if form_type in ("udskrivning_22_aar_privat_tandkl", "udskrivning_22_aar_tandpleje_for"):
            workqueue_name = "jou.solteqtand.udskrivning_22"

            patient_cpr = udfylder_cpr
            samtykke_valg = sub.get("samtykke_valg") == "ja"

            patient_data_dict["klinik_telefonnummer"] = sub.get("tandlaege_telefonnummer_manuelt")

            patient_data_dict["samtykke_valg"] = samtykke_valg

        else:
            child_cpr = sub.get("cpr_nummer_barnet")

            if child_cpr:
                patient_cpr = child_cpr
                patient_name = sub.get("barnets_navn")

            else:
                patient_cpr = udfylder_cpr
                patient_name = sub.get("borger_navn")

            journal_samtykke_valg = sub.get("journal_samtykke_valg") == "ja"

            patient_data_dict["journal_samtykke"] = journal_samtykke_valg

            if form_type == "tilflytter_til_aarhus_kommune_sa":
                workqueue_name = "jou.solteqtand.tilflytter"

                behandling_samtykke = sub.get("behandling_samtykke")

                if behandling_samtykke == "ja":
                    behandling_samtykke_svarer_paa_vegne_af_barn_valg = True

                elif behandling_samtykke == "nej":
                    behandling_samtykke_svarer_paa_vegne_af_barn_valg = False

                else:
                    behandling_samtykke_svarer_paa_vegne_af_barn_valg = None

                borger_telefonnummer = sub.get("borger_telefonnummer")
                cpr_nummer_anden_foraeldremyndighed = sub.get("cpr_nummer_anden_foraeldremyndighed")
                anden_foraeldermyndighed_telefonnummer_manuelt = sub.get("anden_foraeldermyndighed_telefonnummer_manuelt")
                kommunevaelger = sub.get("kommunevaelger")
                kommunal_tandklinik_navn_manuelt = sub.get("kommunal_tandklinik_navn_manuelt")

                patient_data_dict["borger_telefonnummer"] = borger_telefonnummer
                patient_data_dict["behandling_samtykke"] = behandling_samtykke_svarer_paa_vegne_af_barn_valg
                patient_data_dict["cpr_nummer_anden_foraeldremyndighed"] = cpr_nummer_anden_foraeldremyndighed
                patient_data_dict["anden_foraeldermyndighed_telefonnummer_manuelt"] = anden_foraeldermyndighed_telefonnummer_manuelt
                patient_data_dict["kommunevaelger"] = kommunevaelger
                patient_data_dict["kommunal_tandklinik_navn_manuelt"] = kommunal_tandklinik_navn_manuelt

                _update_latest_tilflytter_run(patient_cpr=patient_cpr, step_statuses=TILFLYTTER_OWN_FORM_STEP_STATUSES)

            elif form_type == "fritvalgsordning_samlet_formular":
                workqueue_name = "tan.fritvalg.fritvalg_registreret"

                _update_latest_tilflytter_run(patient_cpr=patient_cpr, step_statuses=TILFLYTTER_FRITVALG_STEP_STATUSES)

                patient_data_dict["cpr"] = patient_cpr
                patient_data_dict["name"] = patient_name

        if patient_data_dict["cpr"] == "":
            patient_data_dict["cpr"] = patient_cpr

        workqueue = workqueues[workqueue_name]

        queue_refs = existing_refs[workqueue_name]

        if form_type == "fritvalgsordning_samlet_formular":
            ref = patient_cpr

        else:
            ref = form_id

        # The queue's references come back as strings, so compare like for like.
        ref = str(ref)

        if ref in queue_refs:
            logging.info(f"Reference {ref} already exists → skipping.")

        else:
            workqueue.add_item(data={"item": {"reference": ref, "data": patient_data_dict}}, reference=ref)

            # Keep the set current so a second submission with the same reference later
            # in this same pass is skipped rather than queued twice.
            queue_refs.add(ref)

            logging.info(f"Created new workitem for form_id {ref}.")


def _update_latest_tilflytter_run(patient_cpr: str, step_statuses: dict[str, str]):
    """
    Apply step statuses to the citizen's most recent tilflytter process run.

    Both directions of the tilflytter/fritvalg split need this: a fritvalg formular
    cancels the tilflytter run's form steps, and a tilflytter formular marks the
    private-clinic step optional. No-op when the citizen has no tilflytter run.
    """

    api_admin_token = os.getenv("API_ADMIN_TOKEN")

    client = ProcessDashboardClient(api_admin_token=api_admin_token)

    process_id, tilflytter_process_steps = process.find_process_id_and_steps(client=client, process_name=TILFLYTTER_PROCESS_NAME)

    response = client.get(endpoint=f"/runs/?process_id={process_id}&meta_filter=cpr%3A{patient_cpr}&order_by=created_at&sort_direction=desc&page=1&size=50")

    data = response.json()
    results = data.get("items", [])

    if not results:
        logging.info(f"No tilflytter process run found for CPR {patient_cpr} - skipping step update.")

        return

    helper_functions.update_process_run_steps(
        client=client,
        process_steps=tilflytter_process_steps,
        process_run=results[0],
        step_statuses=step_statuses,
    )
