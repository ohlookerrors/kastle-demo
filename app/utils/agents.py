from app.services.db import get_db_connection
import os
from psycopg2.extras import RealDictCursor
import json
from dotenv import load_dotenv

load_dotenv()
# =============================================================================
# DUMMY DATA FOR TESTING
# To disable: Set USE_DUMMY_DATA=false in .env file
# To remove: Delete this section and the if block in get_agents() function
# =============================================================================
DUMMY_AGENTS = [
    {
        "name": "Sarah Mitchell",
        "voice": {
            "en": "aura-asteria-en",
            "es": "aura-stella-en"
        },
        "language": "en",
        "personality": "professional, friendly, empathetic",
        "greetings": {
            "en": ["Hi, this is Essex Mortgage, how can I help you today?"],
            "es": ["Hola, esto es Essex Mortgage, como puedo ayudarle hoy?"]
        }
    }
]
# =============================================================================
# END DUMMY DATA
# =============================================================================

def get_agents(team_id: str, client_name):
    # --- DUMMY DATA CHECK: Remove this if block to use real database ---
    if os.getenv("USE_DUMMY_DATA", "false").lower() == "true":
        return DUMMY_AGENTS
    # --- END DUMMY DATA CHECK ---

    conn = get_db_connection("DB_AGENTS")
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT * FROM public.agents WHERE team_id = %s", (str(team_id),))
    agents = cursor.fetchall()
    cursor.close()
    conn.close()

    parsed_agents = [parse_agent(agent, client_name) for agent in agents]
    return parsed_agents


def parse_agent(agent, client_name):
    greeting_string_en = format_greetings(agent['greetings_en'], client_name)
    greeting_string_es = format_greetings(agent['greetings_es'], client_name)

    return {
        "name": agent["agent_name"],
            "voice": {
                "en": agent["voice_model_en"],
                "es": agent["voice_model_es"]  
            },
            "language": "en",
            "personality": agent["personality"],
            "greetings": {
                "en": greeting_string_en,
                "es": greeting_string_es
            }
    }

def format_greetings(greetings, client_name):
    if greetings.startswith('{') and greetings.endswith('}'):
        greeting_string = '[' + greetings[1:-1] + ']'
    else:
        greeting_string = greetings
    
    greeting_templates = json.loads(greeting_string)
    formatted_greetings = [template.format(client=client_name) for template in greeting_templates]
    
    return formatted_greetings