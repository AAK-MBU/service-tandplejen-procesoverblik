import os
import signal
import time
import logging

from config import PATH_TO_REQUESTS_CA_BUNDLE

from helpers import helper_functions, faglig_vurdering_udfoert, get_forms, add_to_final_queue, reevaluate_faglig_vurdering

os.environ["REQUESTS_CA_BUNDLE"] = PATH_TO_REQUESTS_CA_BUNDLE

logging.basicConfig(
    filename="service.log",
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

running = True


def stop_handler(signum, frame):
    """
    Docstring for stop_handler

    :param signum: Description
    :param frame: Description
    """

    global running
    logging.info("Windows shutdown signal received. Stopping service...")
    running = False


signal.signal(signal.SIGTERM, stop_handler)
signal.signal(signal.SIGINT, stop_handler)


def main_loop():
    """
    Docstring for main_loop
    """

    logging.info("Workqueue service started successfully.")

    while running:
        try:
            # Step 1 - Checking 'faglig_vurdering_udfoert' workqueue...
            logging.info("Step 1 - Checking 'faglig_vurdering_udfoert' workqueue...")
            workqueue_name = "tan.udskrivning22.faglig_vurdering_udfoert"

            workqueue = helper_functions.fetch_workqueue(workqueue_name)
            workitems = helper_functions.fetch_workqueue_workitems(workqueue)

            logging.info("before faglig main()")
            faglig_vurdering_udfoert.main(workitems)
            logging.info("Step 1 DONE.")

            # Step 2 - Checking formular submissions...
            logging.info("Step 2 - Checking formular submissions...")
            form_results = get_forms.get_forms()
            logging.info(f"Found {len(form_results)} forms.")

            journalising_workqueue_name = "jou.solteqtand.main"
            journalising_workqueue = helper_functions.fetch_workqueue(journalising_workqueue_name)
            existing_refs = {str(r) for r in helper_functions.get_workqueue_item_references(journalising_workqueue)}

            for res in form_results:
                form_id = res.get("form_id")

                if form_id in existing_refs:
                    logging.info(f"Form {form_id} already exists → skipping.")

                else:
                    journalising_workqueue.add_item(data={"item": {"reference": form_id, "data": res}}, reference=form_id)
                    logging.info(f"Created new workitem for form_id {form_id}.")

            logging.info("Step 2 DONE.")

            # Step 3 - Processing final queue...
            logging.info("Step 3 - Processing final queue...")
            add_to_final_queue.main()
            logging.info("Step 3 DONE.")

            # Step 4 - Checking for incomplete incidents of faglig vurdering...
            logging.info("Step 4 - Checking for incomplete incidents of faglig vurdering...")
            reevaluate_faglig_vurdering.main()
            logging.info("Step 4 DONE.")

            # Sleep 5 minutes
            logging.info("Sleeping for 5 minutes...")
            for _ in range(300):
                if not running:
                    break
                time.sleep(1)

        except Exception as e:
            logging.error(f"Error in worker loop: {e}")
            logging.info("Retrying in 60 seconds...")
            for _ in range(60):
                if not running:
                    break
                time.sleep(1)

    logging.info("Workqueue service stopped cleanly.")


if __name__ == "__main__":
    main_loop()
