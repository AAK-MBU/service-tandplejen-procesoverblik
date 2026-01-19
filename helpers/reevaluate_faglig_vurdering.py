"""Helper module to ensure faglig vurdering is reexamined if the other flow has finished"""

import os

import logging

import requests

import pyodbc

PATH_TO_REQUESTS_CA_BUNDLE = R"C:\tmp\certs\aarhus_root.pem"
os.environ["REQUESTS_CA_BUNDLE"] = PATH_TO_REQUESTS_CA_BUNDLE

API_ADMIN_TOKEN = os.getenv("API_ADMIN_TOKEN")


def main():
    """Main function to execute the script."""

    result = []

    connection_string = os.environ.get("DBCONNECTIONSTRINGPROCESSDASHBOARDPROD")

    sql = """
        WITH all_steps AS (
            SELECT
                pr.entity_id,
                pr.id AS run_id,
                psr.step_id,
                psr.status,
                psr.id AS step_run_id
            FROM dbo.process_run pr
            INNER JOIN dbo.process_step_run psr
                ON pr.id = psr.run_id
        ),
        -- Track 1 must be fully completed (SUCCESS)
        valid_runs AS (
            SELECT
                entity_id,
                run_id
            FROM all_steps
            WHERE step_id NOT IN (3, 8)
            GROUP BY entity_id, run_id
            HAVING COUNT(*) = SUM(CASE WHEN status = 'SUCCESS' THEN 1 ELSE 0 END)
        ),
        -- Track 2 must NOT be completed
        invalid_steps AS (
            SELECT
                entity_id,
                run_id
            FROM all_steps
            WHERE step_id IN (3, 8)
            AND status = 'SUCCESS'
            GROUP BY entity_id, run_id
        )
        SELECT
            v.entity_id,
            v.run_id,

            -- Step 3 fields
            MAX(CASE WHEN s.step_id = 3 THEN s.step_run_id END) AS step3_id,
            MAX(CASE WHEN s.step_id = 3 THEN s.status END)       AS step3_status,

            -- Step 8 fields
            MAX(CASE WHEN s.step_id = 8 THEN s.step_run_id END) AS step8_id,
            MAX(CASE WHEN s.step_id = 8 THEN s.status END)       AS step8_status

        FROM valid_runs v
        INNER JOIN all_steps s
            ON v.run_id = s.run_id
        AND s.step_id IN (3, 8)     -- Only track 2 steps
        LEFT JOIN invalid_steps i
            ON v.entity_id = i.entity_id
        AND v.run_id = i.run_id
        WHERE i.run_id IS NULL         -- Track 2 must NOT be SUCCESS
        GROUP BY 
            v.entity_id,
            v.run_id;
    """

    try:
        with pyodbc.connect(connection_string) as conn:
            with conn.cursor() as cursor:

                cursor.execute(sql)

                rows = cursor.fetchall()

                # Get column names from cursor description
                columns = [column[0] for column in cursor.description]

                # Convert to list of dictionaries
                result = [
                    {
                        column: value.strip() if isinstance(value, str) else value
                        for column, value in zip(columns, row)
                    }
                    for row in rows
                ]

    except pyodbc.Error as e:
        logging.info(f"Database error: {str(e)}")
        logging.info(f"{connection_string}")

        raise e

    except ValueError as e:
        logging.info(f"Value error: {str(e)}")

        raise e

    # pylint: disable-next = broad-exception-caught
    except Exception as e:
        logging.info(f"An unexpected error occurred: {str(e)}")

        raise e

    if len(result) == 0:
        return None

    for row in result:
        print(row)

        faglig_vurdering_step_id = row.get("step3_id")

        headers = {
            "X-API-Key": os.getenv("API_ADMIN_TOKEN"),
            "Content-Type": "application/json"
        }

        base_url = "https://mbu-dashboard-api.adm.aarhuskommune.dk/api/v1/"

        url = f"{base_url}step-runs/{faglig_vurdering_step_id}"

        json_data = {
            "status": "failed"
        }

        response = requests.patch(url=url, json=json_data, headers=headers, timeout=10)

        if response.status_code != 200:
            print("ERROR")
            logging.info("ERROR")

    return


if __name__ == '__main__':
    main()
