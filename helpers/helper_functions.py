"""Helper functions"""

import os
import logging
import requests

import pyodbc

from automation_server_client import AutomationServer
from automation_server_client._models import Workqueue

from mbu_process_dashboard_shared_components.process_dashboard_client import ProcessDashboardClient

from mbu_process_dashboard_shared_components import process_step_run

logger = logging.getLogger(__name__)

DBCONNECTIONSTRINGPROD = os.getenv("DBCONNECTIONSTRINGPROD")
DBCONNECTIONSTRINGDEV = os.getenv("DBCONNECTIONSTRINGDEV")

ATS_TOKEN = os.getenv("ATS_TOKEN")
ATS_URL = os.getenv("ATS_URL")

ATS_TOKEN_DEV = os.getenv("ATS_TOKEN_DEV")
ATS_URL_DEV = os.getenv("ATS_URL_DEV")


def handle_process_dashboard(client: ProcessDashboardClient, status: str, step_run_id: str, failure: Exception | None = None):
    """
    Method for handling updating the process dashboard
    """

    status_update_data = {
        "status": status
    }

    if failure:
        step_run_update_data = process_step_run.build_step_run_update(status=status, failure=failure)

        status_update_data["failure"] = failure

    else:
        step_run_update_data = process_step_run.build_step_run_update(status=status)

    logger.info("before update_dashboard_step_run_by_id() ...")

    updated_step_run_data, status_code = process_step_run.update_dashboard_step_run_by_id(client=client, step_run_id=step_run_id, update_data=step_run_update_data)

    return updated_step_run_data, status_code


def get_items_from_query_with_params(connection_string, query, params):
    """
    Execute a parameterized SQL query and return results as dictionaries.

    Ensures:
    - Safe parameter binding
    - Automatic column-to-value mapping
    - Consistent string cleanup

    Args:
        connection_string (str): SQL Server connection string.
        query (str): SQL query with placeholders.
        params (list): Parameters for the query.

    Returns:
        list[dict]: Query results.
    """

    try:
        with pyodbc.connect(connection_string) as conn:
            with conn.cursor() as cursor:
                cursor.execute(query, params or [])
                rows = cursor.fetchall()
                columns = [c[0] for c in cursor.description]

                return [
                    {
                        col: val.strip() if isinstance(val, str) else val
                        for col, val in zip(columns, row)
                    }
                    for row in rows
                ]

    except Exception as e:
        print(f"Database error: {e}")

        raise


def fetch_workqueue(workqueue_name: str, dev: bool = False):
    """
    Helper function to fetch the desired workqueue based on a provided workqueue_name
    """

    if dev:
        os.environ["ATS_URL"] = ATS_URL_DEV
        os.environ["ATS_TOKEN"] = ATS_TOKEN_DEV

    else:
        os.environ["ATS_URL"] = ATS_URL
        os.environ["ATS_TOKEN"] = ATS_TOKEN

    token = os.getenv("ATS_TOKEN")
    url = os.getenv("ATS_URL")

    headers = {"Authorization": f"Bearer {token}"}

    full_url = f"{url}/workqueues/by_name/{workqueue_name}"

    response_json = requests.get(full_url, headers=headers, timeout=60).json()
    workqueue_id = response_json.get("id")

    os.environ["ATS_WORKQUEUE_OVERRIDE"] = str(workqueue_id)  # override it
    ats = AutomationServer.from_environment()
    workqueue = ats.workqueue()

    return workqueue


def fetch_workqueue_workitems(workqueue: Workqueue):
    """
    Helper function to fetch all workitems for a given workqueue (with pagination).
    """

    token = os.getenv("ATS_TOKEN")
    url = os.getenv("ATS_URL")

    ats_headers = {"Authorization": f"Bearer {token}"}

    all_items = []

    page = 1

    size = 200  # use max allowed per page to minimize requests

    while True:
        full_url = f"{url}/workqueues/{workqueue.id}/items?page={page}&size={size}"
        response = requests.get(full_url, headers=ats_headers, timeout=60)
        response.raise_for_status()

        response_json = response.json()
        items = response_json.get("items", [])
        all_items.extend(items)

        total_pages = response_json.get("total_pages", 1)

        if page >= total_pages:
            break

        page += 1

    return all_items


def get_workqueue_item_references(workqueue: Workqueue, dev: bool = False):
    """
    Retrieve items from the specified workqueue.
    If the queue is empty, return an empty list.
    """

    if dev:
        os.environ["ATS_URL"] = ATS_URL_DEV
        os.environ["ATS_TOKEN"] = ATS_TOKEN_DEV

    else:
        os.environ["ATS_URL"] = ATS_URL
        os.environ["ATS_TOKEN"] = ATS_TOKEN

    token = os.getenv("ATS_TOKEN")
    url = os.getenv("ATS_URL")

    if not url or not token:
        raise OSError("ATS_URL or ATS_TOKEN is not set in the environment")

    headers = {"Authorization": f"Bearer {token}"}

    workqueue_items = set()

    page = 1
    size = 200  # max allowed

    while True:
        full_url = f"{url}/workqueues/{workqueue.id}/items?page={page}&size={size}"
        response = requests.get(full_url, headers=headers, timeout=60)
        response.raise_for_status()

        res_json = response.json().get("items", [])

        if not res_json:
            break

        for row in res_json:
            ref = row.get("reference")
            if ref:
                workqueue_items.add(ref)

        page += 1

    return workqueue_items
