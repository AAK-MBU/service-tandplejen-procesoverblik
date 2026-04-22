"""Module for fetching patients that have turned 22 as of today's date"""

import os

import logging

from automation_server_client._models import WorkItem

from mbu_solteqtand_shared_components.database.db_handler import SolteqTandDatabase

from helpers import helper_functions

SOLTEQ_TAND_DB_CONN_STRING = os.getenv("DBCONNECTIONSTRINGSOLTEQTAND")


def main():
    """Main function to execute the script."""

    for process in ["udskrivning", "fritvalg"]:
        if process == "fritvalg":
            workqueue_name = "tan.fritvalg.faglig_vurdering_udfoert"

            dev = True

        else:
            workqueue_name = "tan.udskrivning22.faglig_vurdering_udfoert"

            dev = False

        workqueue = helper_functions.fetch_workqueue(workqueue_name, dev=dev)
        workitems = helper_functions.fetch_workqueue_workitems(workqueue)

        for item_dict in workitems:
            change_status = False

            if item_dict.get("status") not in ("pending user action", "failed"):
                continue

            item = WorkItem(**item_dict)

            item_data = item.data

            citizen_cpr = item_data.get("cpr")

            db_handler = SolteqTandDatabase(conn_str=SOLTEQ_TAND_DB_CONN_STRING)

            results = _check_if_faglig_vurdering_udfoert(db_handler=db_handler, cpr=citizen_cpr, process=process)

            if results:
                if len(results) > 1:
                    if process == "fritvalg":
                        logging.info(f"Citizen {citizen_cpr} has more than 1 'Fritvalgsordning godkendt'-event!")

                        item.fail(message="Borgeren har mere end 1 'Fritvalgsordning godkendt'-hændelse!")

                    else:
                        logging.info(f"Citizen {citizen_cpr} has more than 1 booking with aftaletype 'Z - 22 år - Borger fyldt 22 år'!")

                        item.fail(message="Borgeren har mere end 1 aftale med aftaletype 'Z - 22 år - Borger fyldt 22 år'!")

                else:
                    if process == "fritvalg":
                        change_status = True

                    else:
                        if int(results[0].get("Status")) in (632, 634):
                            change_status = True

                if change_status:
                    logging.info(f"Faglig vurdering has been completed for citizen {citizen_cpr} - Updating workitem status...")

                    item.update_status(status="new", message="Status opdateret af service")


def _check_if_faglig_vurdering_udfoert(db_handler: SolteqTandDatabase, cpr: str, process: str):
    """
    Check if a citizen has a booking with the specified aftaletype and -status
    """

    if process == "fritvalg":
        filters = {
            "e.currentStateText": "Fritvalgsordning godkendt",
            "p.cpr": cpr,
            "e.archived": 0,
        }

        result = db_handler.get_list_of_events(filters=filters)

    else:
        query = """
            SELECT
                b.BookingID,
                b.CreatedDateTime,
                bt.Description,
                b.Status
            FROM
                [tmtdata_prod].[dbo].[BOOKING] b
            JOIN
                [tmtdata_prod].[dbo].[PATIENT] p ON p.patientId = b.patientId
            JOIN
                [tmtdata_prod].[dbo].[BOOKINGTYPE] bt ON bt.BookingTypeID = b.BookingTypeID
            WHERE
                cpr = ?
                AND Description = 'Z - 22 år - Borger fyldt 22 år'
            ORDER BY
                CreatedDateTime DESC
        """

        # pylint: disable=protected-access
        result = db_handler._execute_query(query, params=(cpr,))

    return result
