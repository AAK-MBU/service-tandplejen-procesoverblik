"""Module for fetching patients that have turned 22 as of today's date"""

import os

import logging

from automation_server_client._models import WorkItem

from mbu_solteqtand_shared_components.database.db_handler import SolteqTandDatabase

from helpers import helper_functions

SOLTEQ_TAND_DB_CONN_STRING = os.getenv("DBCONNECTIONSTRINGSOLTEQTAND")


def main():
    """Main function to execute the script."""

    workqueue_name = "tan.udskrivning22.faglig_vurdering_udfoert"

    workqueue = helper_functions.fetch_workqueue(workqueue_name)
    workitems = helper_functions.fetch_workqueue_workitems(workqueue)

    for item_dict in workitems:
        if item_dict.get("status") not in ("pending user action", "failed"):
            continue

        item = WorkItem(**item_dict)

        citizen_cpr = item.reference

        db_handler = SolteqTandDatabase(conn_str=SOLTEQ_TAND_DB_CONN_STRING)

        citizen_bookings = _check_if_faglig_vurdering_udfoert(db_handler=db_handler, cpr=citizen_cpr)

        if citizen_bookings:
            if len(citizen_bookings) > 1:
                logging.info(f"Citizen {citizen_cpr} has more than 1 booking with aftaletype 'Z - 22 år - Borger fyldt 22 år'!")

                item.fail(message="Borgeren har mere end 1 aftale med aftaletype 'Z - 22 år - Borger fyldt 22 år'!")

            else:
                if int(citizen_bookings[0].get("Status")) in (632, 634):
                    logging.info(f"Faglig vurdering has been completed for citizen {citizen_cpr} - Updating workitem status...")

                    item.update_status(status="new", message="Status opdateret af service")


def _check_if_faglig_vurdering_udfoert(db_handler: SolteqTandDatabase, cpr: str):
    """
    Check if a citizen has a booking with the specified aftaletype and -status
    """

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
    return db_handler._execute_query(query, params=(cpr,))
