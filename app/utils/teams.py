from app.services.db import get_db_connection
import os
from psycopg2.extras import RealDictCursor
import json
from dotenv import load_dotenv

load_dotenv()

# =============================================================================
# DUMMY DATA FOR TESTING
# To disable: Set USE_DUMMY_DATA=false in .env file
# To remove: Delete this section and the if block in get_team() function
# =============================================================================
DUMMY_TEAM = {
    "team_id": "team_001",
    "client_name": "Essex Mortgage",
    "phone_number": "+918956580955",
    "team_name": "Collections Team"
}
# =============================================================================
# END DUMMY DATA
# =============================================================================

def get_team(phone: str):
    # --- DUMMY DATA CHECK: Remove this if block to use real database ---
    if os.getenv("USE_DUMMY_DATA", "false").lower() == "true":
        return DUMMY_TEAM
    # --- END DUMMY DATA CHECK ---

    conn = get_db_connection("DB_AGENTS")
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT * FROM public.teams WHERE phone_number = %s", (str(phone),))
    team = cursor.fetchone()
    cursor.close()
    conn.close()

    return team